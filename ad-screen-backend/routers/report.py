"""
报告路由
- GET  /report/{caseId}：聚合报告数据
- POST /report/save：云端保存报告
- POST /report/batch-export：批量导出多例报告汇总 CSV
"""
import csv
import io
import json
import urllib.parse
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord as CaseModel
from models.analysis import AnalysisRecord, InterventionRecord, FollowUpRecord, FollowUpVisit
from models.model_config import ModelConfigRecord
from models.log import CaseLog, SystemLog as SystemLogModel
from services.auth import get_current_user_qs
from services.inference import build_analysis, build_interventions, build_follow_up
from services.data_init import case_record_to_dict
from services.utils import format_date, format_date_time, now_str, next_seq_id, csv_sanitize

# 单次批量导出上限（导出内容含患者姓名等 PHI，限量降低批量泄露风险）
BATCH_EXPORT_LIMIT = 500

router = APIRouter(
    prefix="/report",
    tags=["报告"],
    dependencies=[Depends(get_current_user_qs)],
)


# ---------- 报告扩展字段计算（规则驱动，无 LLM 依赖） ----------

def _compute_niaa(analysis_dict: dict) -> dict:
    """NIA-AA A/T/N 框架标签（基于影像代理，无 CSF 数据）"""
    # A (amyloid) - 用 PET SUV 代理
    suv = analysis_dict.get('meanSUV')
    if suv is None:
        a_label, a_reason = 'A_unknown', '无 PET SUV 数据'
    elif suv < 0.85:
        a_label, a_reason = 'A+', f'PET 平均 SUV={suv:.2f}<0.85，提示淀粉样蛋白沉积可能'
    else:
        a_label, a_reason = 'A-', f'PET 平均 SUV={suv:.2f}≥0.85，无明显淀粉样蛋白沉积征象'
    # T (tau) - 用海马萎缩代理
    regions = analysis_dict.get('abnormalRegions', []) or []
    hippo = [r for r in regions if '海马' in (r.get('region') or '')]
    if not hippo:
        t_label, t_reason = 'T-', '海马区域无明显萎缩征象'
    else:
        severe = [r for r in hippo if r.get('zScore', 0) < -2]
        if severe:
            t_label, t_reason = 'T+', f'海马 zScore<{severe[0]["zScore"]:.1f}<-2，提示 tau 病理可能'
        else:
            t_label, t_reason = 'T-', '海马萎缩未达 tau 阳性阈值'
    # N (neurodegeneration) - 海马体积总和
    hv_l = analysis_dict.get('hippocampusVolumeL') or 0
    hv_r = analysis_dict.get('hippocampusVolumeR') or 0
    total_hv = hv_l + hv_r
    if total_hv < 6.0:
        n_label, n_reason = 'N+', f'双侧海马体积总和={total_hv:.2f}cm³<6.0，神经变性证据'
    else:
        n_label, n_reason = 'N-', f'双侧海马体积总和={total_hv:.2f}cm³≥6.0，在正常下限以上'
    # 2026 版指南：生物学分期（Stage 0/A/B/C/D），直接取分析结果中已计算的值
    bio_stage = analysis_dict.get('biologicalStage', 'Stage 0')
    bio_stage_desc = analysis_dict.get('biologicalStageDesc', '')
    return {
        'A': a_label, 'A_reason': a_reason,
        'T': t_label, 'T_reason': t_reason,
        'N': n_label, 'N_reason': n_reason,
        'biologicalStage': bio_stage,
        'biologicalStageDesc': bio_stage_desc,
        'reasoning': f"{a_reason}；{t_reason}；{n_reason}"
    }


def _compute_conflict(analysis_dict: dict, latest_visit) -> dict:
    """影像-认知矛盾证据"""
    items = []
    risk_level = analysis_dict.get('riskLevel', '')
    # 项 1: 影像-认知矛盾
    if risk_level in ('ad-early', 'ad-late') and latest_visit and latest_visit.mmse and latest_visit.mmse > 24:
        items.append({
            'type': 'imaging_cognition',
            'description': f'影像 AI 风险等级为 {risk_level}（提示 AD 可能），但最新随访 MMSE={latest_visit.mmse} 分（>24，正常范围），存在影像-认知不一致'
        })
    # 项 2: 模态内部矛盾（海马重度萎缩 + PET 代谢正常）
    regions = analysis_dict.get('abnormalRegions', []) or []
    has_severe_hippo = any(
        '海马' in (r.get('region') or '') and '重度' in (r.get('atrophy') or '')
        for r in regions
    )
    suv = analysis_dict.get('meanSUV') or 0
    if has_severe_hippo and suv > 0.9:
        items.append({
            'type': 'modality_internal',
            'description': f'MRI 显示海马重度萎缩，但 PET 平均 SUV={suv:.2f}>0.9（代谢尚可），存在结构-代谢不一致'
        })
    return {'hasConflict': len(items) > 0, 'items': items}


# 患者通俗版映射表（按病程分期映射通俗结论与生活建议）
_PATIENT_STAGE_MAP = {
    'CN': {
        'summary': '当前脑部影像与认知功能均在正常范围，未发现阿尔茨海默病征象。建议保持健康生活方式并定期体检。',
        'lifestyle': ['每周 150 分钟中等强度有氧运动', '地中海饮食模式', '保证 7-8 小时睡眠', '保持社交与认知活动', '控制血压、血糖、血脂'],
        'followUp': '建议 2-3 年复查一次脑部影像与认知评估。',
        # 家属常见问题（通俗科普，避免医学术语）
        'faq': [
            {'q': '这次检查结果正常，以后还会得阿尔茨海默病吗？',
             'a': '当前检查未发现异常，但年龄增长仍是风险因素。保持健康生活方式、控制血压血糖、定期复查，可以显著降低风险。'},
            {'q': '需要吃什么保健品预防吗？',
             'a': '目前没有哪种保健品被证明能预防阿尔茨海默病。均衡饮食、规律运动、充足睡眠、社交活动比任何保健品都有效。'},
            {'q': '偶尔忘事是不是早期表现？',
             'a': '偶尔忘记名字或钥匙位置是正常衰老现象。如频繁忘记近期发生的事、重复问同一问题、迷路，建议尽早做认知评估。'},
        ],
        # 家属/照护者日常建议
        'caregiverAdvice': [
            '鼓励家人保持规律运动与社交活动',
            '关注家人情绪与睡眠状态，长期失眠需及时干预',
            '每年陪同做一次认知功能筛查（MMSE/MoCA）',
        ],
        # 需立即就诊的警示信号
        'warningSigns': [
            '短期内认知功能明显下降',
            '出现情绪性格突然改变',
            '反复迷路或走失',
        ],
    },
    'MCI': {
        'summary': '当前存在轻度认知障碍，部分脑区出现早期变化。这不等于阿尔茨海默病，但需要密切随访和积极干预以延缓进展。',
        'lifestyle': ['坚持规律有氧运动（如快走、游泳）', '认知训练（阅读、棋类、学习新技能）', '地中海/MIND 饮食', '保证睡眠质量', '控制心血管危险因素', '避免独居与社交隔离'],
        'followUp': '建议每 6 个月复查认知功能（MMSE/MoCA），每年复查脑部 MRI。',
        'faq': [
            {'q': '轻度认知障碍一定会发展成阿尔茨海默病吗？',
             'a': '不是。约 10-15% 的 MCI 患者每年转化为 AD，但有部分人会稳定甚至改善。积极干预（运动、认知训练、控制血管危险因素）可显著延缓进展。'},
            {'q': '现在需要吃药吗？',
             'a': '目前不推荐抗痴呆药物（如多奈哌齐），但可考虑认知增强营养支持。建议先通过生活方式干预与认知训练，定期随访评估进展。'},
            {'q': '还会自己开车吗？',
             'a': '若 MMSE>26 且无空间定向障碍，可继续驾驶但建议避免夜间与长途。如出现迷路、判断迟疑，应及时停止并评估。'},
            {'q': '家属能做什么？',
             'a': '陪伴坚持运动与认知训练，协助管理用药与血压血糖，留意情绪变化，定期陪同复查。避免过度包办，鼓励保持独立性。'},
        ],
        'caregiverAdvice': [
            '协助建立规律作息表与用药提醒（药盒、手机闹钟）',
            '陪伴参加认知训练课程或社区活动',
            '关注情绪变化，出现抑郁/焦虑及时干预',
            '每 6 个月陪同复查认知功能',
            '家中可设置简单标识（物品标签、紧急联系方式）',
        ],
        'warningSigns': [
            '认知功能在 3-6 个月内明显下降',
            '出现行为改变（淡漠、易怒、多疑）',
            '出现日常生活能力下降（理财、做饭困难）',
        ],
    },
    'AD-E': {
        'summary': '当前提示阿尔茨海默病早期改变，脑部影像已出现特征性萎缩与代谢异常。早期诊断有助于尽早开始干预治疗，建议尽快到神经内科就诊。',
        'lifestyle': ['在家人陪伴下保持适度活动', '简化日常事务、使用备忘工具', '规律作息、避免夜间躁动', '继续认知刺激训练', '注意营养与水分摄入'],
        'followUp': '建议每 3 个月复查认知与功能评估，与神经内科医生讨论是否需要药物治疗（如胆碱酯酶抑制剂）。',
        'faq': [
            {'q': '是不是确诊阿尔茨海默病了？',
             'a': '影像检查高度提示阿尔茨海默病早期改变，但确诊需结合临床表现、神经心理学评估与生物标志物。建议尽快到神经内科记忆门诊完善评估。'},
            {'q': '现在吃药能控制住吗？',
             'a': '目前药物不能治愈但可延缓进展。胆碱酯酶抑制剂（多奈哌齐等）可改善认知与日常功能，越早开始效果越好。需在医生指导下用药。'},
            {'q': '还能工作或独自外出吗？',
             'a': '早期可保留部分工作能力，但复杂事务（理财、用药管理）建议家属协助。外出建议携带定位设备，避免独自长途出行。'},
            {'q': '生活能自理多久？',
             'a': '个体差异较大，平均 3-8 年内进展至中度。规律用药、认知训练、良好照护可延缓进展，定期评估调整方案。'},
        ],
        'caregiverAdvice': [
            '协助管理所有用药（药盒分装、定时提醒、记录漏服）',
            '简化家居环境，移除绊倒风险物品',
            '准备备忘本与重要信息卡片（姓名、地址、紧急联系人）',
            '陪同就诊并参与治疗决策',
            '关注自身情绪健康，必要时寻求家庭医生或社工支持',
            '提前规划法律与财务事宜（委托书、长期照护预算）',
        ],
        'warningSigns': [
            '认知功能在 1-3 个月内迅速恶化',
            '出现精神行为症状（幻觉、妄想、激越）',
            '出现走失或迷路事件',
            '夜间躁动影响照护者睡眠',
        ],
    },
    'AD-L': {
        'summary': '当前提示阿尔茨海默病中晚期改变，脑部萎缩与代谢异常较为显著。需要综合照护方案，建议尽快与神经内科、老年科医生共同制定治疗与照护计划。',
        'lifestyle': ['24 小时照护与防跌倒', '协助进食、穿衣、如厕等日常活动', '防走失（定位手环、家中门锁）', '规律作息、避免环境剧变', '关注吞咽与营养支持'],
        'followUp': '建议每 3 个月复查，重点关注并发症预防、照护负担评估与家庭支持。',
        'faq': [
            {'q': '还能治好吗？',
             'a': '目前无法治愈，治疗目标是维持生活质量、延缓并发症、减轻照护负担。药物联合（多奈哌齐+美金刚）有一定延缓作用，但需评估整体状况。'},
            {'q': '病人还能活多久？',
             'a': '中晚期平均生存期 3-10 年，差异较大。常见终末原因是感染（肺炎、尿路感染）、营养不良、跌倒并发症。良好照护可延长生存期并改善生活质量。'},
            {'q': '还能自己吃饭吗？',
             'a': '随病情进展会出现吞咽困难。建议改为软食、糊状食物，少量多餐，进食时坐起 90 度。出现反复呛咳、体重下降需评估吞咽功能，必要时鼻饲或胃造瘘。'},
            {'q': '什么时候需要送养老机构？',
             'a': '当居家照护无法满足需求（24 小时照护、专业护理、家属身心透支）时，可考虑专业照护机构。建议提前了解社区资源，做好规划。'},
        ],
        'caregiverAdvice': [
            '建立 24 小时照护轮替机制，避免单一照护者身心透支',
            '申请长期照护保险、残疾证、民政救助等社会支持',
            '使用防走失定位手环，家中门窗加锁，浴室加装扶手',
            '准备急救联系（家属、社区医生、120）与近期用药清单',
            '定期评估照护者自身健康状况（每年体检+心理评估）',
            '关注吞咽与营养支持，必要时咨询营养科与言语治疗师',
            '联系社区社工或志愿者组织，安排喘息服务',
        ],
        'warningSigns': [
            '出现呛咳、反复肺炎或体重持续下降',
            '出现跌倒、骨折或皮肤压疮',
            '出现严重激越、攻击行为或抑郁',
            '照护者出现严重身心疲惫或抑郁',
            '出现急性意识模糊（警惕感染、药物、代谢紊乱）',
        ],
    }
}


def _compute_patient_friendly(analysis_dict: dict) -> dict:
    """患者通俗科普版（按病程分期查表，规则驱动）"""
    stage_code = analysis_dict.get('stageCode', 'CN')
    return _PATIENT_STAGE_MAP.get(stage_code, _PATIENT_STAGE_MAP['CN'])


def _compute_exam_findings(analysis_dict: dict, case_data: dict, niaa: dict) -> dict:
    """
    2026 版 PET/MRI 指南表5"检查所见"结构化分节
    - MRI 表现：对称性 / 白质高信号 / 脑室脑沟 / 海马萎缩 / SWI 出血点
    - FDG PET：代谢减低区域
    - Aβ PET：阳/阴性 + 视觉征象
    - Tau PET：分布区域 + Braak/Stage 分级
    """
    regions = analysis_dict.get('abnormalRegions', []) or []
    mta = analysis_dict.get('mtaScore', '0')
    fazekas = next((m['value'] for m in (analysis_dict.get('metrics') or []) if m.get('key') == 'wmh'), 0)
    hv_l = analysis_dict.get('hippocampusVolumeL') or 0
    hv_r = analysis_dict.get('hippocampusVolumeR') or 0
    ven = analysis_dict.get('ventricleVolume') or 0
    cort = analysis_dict.get('corticalThickness') or 0

    # MRI 表现（指南要求结构化描述）
    hippo_atrophy = [r for r in regions if '海马' in (r.get('region') or '')]
    if not hippo_atrophy:
        hippo_desc = "双侧海马体积在正常范围，未见明显萎缩"
    else:
        sides = []
        for r in hippo_atrophy:
            sides.append(f"{r.get('side', '')}{r.get('region', '')} {r.get('atrophy', '')}（zScore={r.get('zScore', 0)}）")
        hippo_desc = "；".join(sides)
    mri_findings = {
        "symmetry": "脑实质信号对称性良好，未见明显不对称征象" if len(regions) <= 2 else "双侧皮层信号存在不对称",
        "whiteMatterHyperintensity": f"Fazekas {fazekas} 级（{'白质高信号未见明显异常' if fazekas == 0 else '可见室周/深部白质高信号'})",
        "ventricleSulci": f"脑室体积 {ven} cm³，脑沟脑池{'正常' if ven < 35 else '增宽'}",
        "hippocampusAtrophy": f"MTA 评分 {mta} 级；{hippo_desc}",
        "hippocampusVolumeL": hv_l,
        "hippocampusVolumeR": hv_r,
        "corticalThickness": cort,
        "swiHemorrhage": "SWI 序列未见明确微出血灶" if not any(
            '微出血' in f for f in (analysis_dict.get('ariaRisk', {}) or {}).get('factors', [])
        ) else "SWI 序列检测到可疑微出血灶，建议结合 ARIA 风险评估",
    }

    # PET 检查所见（按显像剂类型分支）
    pet_patterns = analysis_dict.get('petPatterns', {}) or {}
    tracer = pet_patterns.get('tracer', analysis_dict.get('petTracer', 'fdg'))
    pet_findings = {"tracer": tracer, "tracerName": pet_patterns.get('tracerName', '')}
    if tracer == 'fdg':
        pet_findings['fdgFindings'] = {
            "metabolicReduction": pet_patterns.get('affectedRegions', []),
            "patternDescription": pet_patterns.get('conclusion', ''),
            "meanSUV": analysis_dict.get('meanSUV'),
        }
    elif tracer == 'amyloid':
        pet_findings['amyloidFindings'] = {
            "positive": pet_patterns.get('positive', False),
            "patterns": pet_patterns.get('patterns', []),
            "conclusion": pet_patterns.get('conclusion', ''),
            "suvr": pet_patterns.get('suvr'),
            "affectedRegions": pet_patterns.get('affectedRegions', []),
        }
    elif tracer == 'tau':
        pet_findings['tauFindings'] = {
            "positive": pet_patterns.get('positive', False),
            "distribution": pet_patterns.get('conclusion', ''),
            "braakStage": pet_patterns.get('braakStage', ''),
            "suvr": pet_patterns.get('suvr'),
            "suvrThreshold": pet_patterns.get('suvrThreshold'),
            "affectedRegions": pet_patterns.get('affectedRegions', []),
        }

    return {
        "mriFindings": mri_findings,
        "petFindings": pet_findings,
    }


def _compute_diagnostic_conclusions(analysis_dict: dict, niaa: dict, exam_findings: dict,
                                    aria_risk: dict, case_data: dict) -> dict:
    """
    2026 版 PET/MRI 指南表5"诊断意见"5 段式
    1. FDG 代谢特征
    2. Aβ PET 结论
    3. tau PET Braak/Stage 分期
    4. MRI Fazekas/MTA 结构评估
    5. 综合 A-T-N 生物学诊断
    """
    pet_patterns = analysis_dict.get('petPatterns', {}) or {}
    tracer = pet_patterns.get('tracer', analysis_dict.get('petTracer', 'fdg'))
    mri_findings = exam_findings.get('mriFindings', {})

    # 1. FDG 代谢特征
    if tracer == 'fdg':
        fdg_conclusion = pet_patterns.get('conclusion', '')
    else:
        # 非 FDG 检查时，从分析结果推断（兼容历史病例）
        level = analysis_dict.get('riskLevel', 'low')
        fdg_conclusion = {
            'low': 'FDG 代谢分布对称，未见明显代谢减低区',
            'mci': '内侧颞叶及后扣带回轻度代谢减低',
            'ad-early': '双侧后扣带回/楔前叶代谢减低，符合 AD 早期分布',
            'ad-late': '双侧顶颞叶、后扣带回及额叶代谢广泛减低，符合 AD 中晚期分布',
        }.get(level, 'FDG 代谢评估待补')
    fdg_section = {
        "title": "1. FDG PET 代谢特征",
        "content": fdg_conclusion,
        "meanSUV": analysis_dict.get('meanSUV'),
    }

    # 2. Aβ PET 结论
    if tracer == 'amyloid':
        amyloid_section = {
            "title": "2. Aβ PET 结论",
            "content": pet_patterns.get('conclusion', ''),
            "positive": pet_patterns.get('positive', False),
            "suvr": pet_patterns.get('suvr'),
        }
    elif tracer == 'tau':
        # tau 检查时已有 Aβ 信息（指南推荐 A+T- 框架），从生物学分期推断
        bio_stage = analysis_dict.get('biologicalStage', 'Stage 0')
        is_a_pos = bio_stage != 'Stage 0'
        amyloid_section = {
            "title": "2. Aβ PET 结论",
            "content": (
                f"基于 tau PET 检查时的 Aβ 状态推断：{'Aβ 阳性' if is_a_pos else 'Aβ 阴性'}"
                f"（生物学分期 {bio_stage}）。建议补充 Aβ PET 检查以确认淀粉样蛋白状态"
            ),
            "positive": is_a_pos,
            "suvr": None,
        }
    else:
        # FDG 检查时无 Aβ PET 数据
        amyloid_section = {
            "title": "2. Aβ PET 结论",
            "content": "本次检查未包含 Aβ PET 数据，建议在疾病修饰治疗前评估时补充 Aβ PET 检查",
            "positive": None,
            "suvr": None,
        }

    # 3. tau PET Braak/Stage 分期
    if tracer == 'tau':
        tau_section = {
            "title": "3. Tau PET 分期",
            "content": pet_patterns.get('conclusion', ''),
            "braakStage": pet_patterns.get('braakStage', ''),
            "biologicalStage": analysis_dict.get('biologicalStage', ''),
            "suvr": pet_patterns.get('suvr'),
        }
    else:
        # 非 tau 检查时使用生物学分期代理
        bio_stage = analysis_dict.get('biologicalStage', 'Stage 0')
        bio_desc = analysis_dict.get('biologicalStageDesc', '')
        tau_section = {
            "title": "3. Tau 生物学分期（基于多模态代理，建议补充 tau PET 确认）",
            "content": f"{bio_stage}：{bio_desc}",
            "braakStage": None,
            "biologicalStage": bio_stage,
            "suvr": None,
        }

    # 4. MRI 结构评估
    mri_section = {
        "title": "4. MRI 结构评估",
        "content": (
            f"海马 MTA 评分 {mri_findings.get('hippocampusAtrophy', '').split('；')[0]}；"
            f"白质高信号 {mri_findings.get('whiteMatterHyperintensity', '')}；"
            f"{mri_findings.get('swiHemorrhage', '')}"
        ),
        "mta": analysis_dict.get('mtaScore'),
        "fazekas": mri_findings.get('whiteMatterHyperintensity'),
        "ariaRisk": aria_risk.get('riskLevel', '低风险'),
    }

    # 5. 综合 A-T-N 生物学诊断
    a_label = niaa.get('A', 'A_unknown')
    t_label = niaa.get('T', 'T_unknown')
    n_label = niaa.get('N', 'N_unknown')
    bio_stage = analysis_dict.get('biologicalStage', 'Stage 0')
    bio_desc = analysis_dict.get('biologicalStageDesc', '')
    stage_code = analysis_dict.get('stageCode', 'CN')
    stage_desc = analysis_dict.get('stageDesc', '')
    composite_section = {
        "title": "5. 综合 A-T-N 生物学诊断",
        "content": (
            f"NIA-AA A/T/N 框架：{a_label} / {t_label} / {n_label}；"
            f"生物学分期 {bio_stage}（{bio_desc}）；"
            f"临床阶段 {stage_code}（{stage_desc}）"
        ),
        "aLabel": a_label,
        "tLabel": t_label,
        "nLabel": n_label,
        "biologicalStage": bio_stage,
        "clinicalStage": stage_code,
    }

    return {
        "sections": [fdg_section, amyloid_section, tau_section, mri_section, composite_section],
    }


def _compute_recommendations_summary(analysis_dict: dict) -> dict:
    """
    2026 版 PET/MRI 指南 7 条推荐意见及等级汇总
    - 推荐等级：A 级（强推荐）/ B 级（推荐）/ D 级（可选）/ 专家共识
    - 用于报告页展示指南依据，提示医师遵循规范
    """
    bio_stage = analysis_dict.get('biologicalStage', 'Stage 0')
    has_abnormal_mta = (analysis_dict.get('mtaScore') and
                        float(analysis_dict.get('mtaScore', '0') or '0') >= 1)
    aria_risk = analysis_dict.get('ariaRisk', {})
    has_aria_risk = aria_risk.get('riskScore', 0) > 1

    recommendations = [
        {
            "id": 1,
            "topic": "Aβ / tau / FDG PET 适应证",
            "recommendation": (
                "Aβ PET 用于 Aβ 沉积定性诊断与药物疗效评估；tau PET 用于生物学分期；"
                "FDG PET 用于退行性变评估；A-T-N 框架联合检查"
            ),
            "grade": "A 级（Aβ、tau）/ B 级（FDG）/ 专家共识（A-T-N 联合）",
            "applied": True,
            "note": f"当前病例生物学分期：{bio_stage}",
        },
        {
            "id": 2,
            "topic": "海马 MRI 斜冠状位 T1WI + MTA 评分",
            "recommendation": "海马 MRI 斜冠状位 T1WI + MTA 视觉评分筛查内侧颞叶萎缩",
            "grade": "B 级",
            "applied": has_abnormal_mta,
            "note": f"MTA 评分={analysis_dict.get('mtaScore', 'N/A')}",
        },
        {
            "id": 3,
            "topic": "MRI + SWI 监测 ARIA",
            "recommendation": "抗 Aβ 单抗治疗前及治疗期间，常规 MRI + SWI 评估 ARIA（水肿/微出血）",
            "grade": "A 级",
            "applied": has_aria_risk,
            "note": f"ARIA 风险：{aria_risk.get('riskLevel', '低风险')}",
        },
        {
            "id": 4,
            "topic": "FDG PET 视觉评估代谢减低范围",
            "recommendation": "FDG PET 视觉评估双侧顶颞额叶海马扣带回代谢减低范围",
            "grade": "D 级",
            "applied": True,
            "note": "项目已通过 PET 视觉判读征象卡片展示",
        },
        {
            "id": 5,
            "topic": "Aβ PET 视觉判读阳/阴性标准",
            "recommendation": (
                "Aβ PET 视觉判读：阴性见山脊征/钻石征/卡通手征/冬树征；"
                "阳性见平原征/亲吻征/夏树征"
            ),
            "grade": "B 级",
            "applied": True,
            "note": "项目已通过 PET 视觉判读征象卡片展示",
        },
        {
            "id": 6,
            "topic": "Tau PET 视觉判读阳性范围",
            "recommendation": (
                "Tau PET 视觉判读阳性范围；SUVR>1.65 为阳性阈值（flortaucipir）；"
                "Braak 分期 I-VI"
            ),
            "grade": "B 级",
            "applied": True,
            "note": f"生物学分期：{bio_stage}",
        },
        {
            "id": 7,
            "topic": "标准化报告",
            "recommendation": (
                "标准化报告：白质高信号 Fazekas + 海马萎缩 MTA + 代谢减低区域 + "
                "Aβ 阳性结论 + tau Braak/Stage 分级"
            ),
            "grade": "B 级",
            "applied": True,
            "note": "项目已对齐指南表 5 报告模板（检查所见 + 诊断意见 5 段式）",
        },
    ]

    return {
        "title": "2026 版 PET/MRI 指南推荐意见汇总",
        "totalCount": len(recommendations),
        "appliedCount": sum(1 for r in recommendations if r.get("applied")),
        "recommendations": recommendations,
        "guidelineReference": "中华医学会核医学分会. 阿尔茨海默病一体化 PET/MRI 脑成像临床应用指南（2026 版）",
    }


def _compute_clinical_decision_support(analysis_dict: dict, case_data: dict) -> dict:
    """
    2026 版指南：临床决策辅助
    根据病例情况（风险等级、生物学分期、ARIA 风险、图像质量、显像剂类型）生成下一步建议
    - 推荐补充检查（如"建议补充 tau PET"）
    - 推荐治疗评估（如"建议 DMT 治疗前基线评估"）
    - 推荐复查时间（如"建议 6 个月复查 FDG PET"）
    - 质量控制建议（如"建议重新采集"）
    """
    recommendations = []

    bio_stage = analysis_dict.get('biologicalStage', 'Stage 0')
    risk_level = analysis_dict.get('riskLevel', 'low')
    risk_score = analysis_dict.get('riskScore', 50)
    pet_tracer = analysis_dict.get('petTracer', 'fdg') or 'fdg'
    aria_risk = analysis_dict.get('ariaRisk', {})
    aria_risk_level = aria_risk.get('riskLevel', '低风险')
    image_quality = analysis_dict.get('imageQuality', {}) or {}
    exam_indication = case_data.get('examIndication', 'diagnosis')
    mri_quant = analysis_dict.get('mriQuantification', {}) or {}
    swi = mri_quant.get('swiFindings', {}) or {}

    # 1. 生物学分期相关建议
    if bio_stage == "Stage 0":
        recommendations.append({
            "category": "进一步检查",
            "priority": "low",
            "action": "目前不符合 AD 生物学诊断，建议 2-3 年复查 FDG PET + MRI",
            "guidelineRef": "推荐意见 1（A-T-N 联合）",
        })
    elif bio_stage in ("Stage A", "Stage B"):
        recommendations.append({
            "category": "补充检查",
            "priority": "high",
            "action": (
                "建议补充 tau PET（flortaucipir 或 MK6240）进行 tau 病理确认与 Braak 分期"
                if bio_stage == "Stage A"
                else "tau PET 用于确认 Braak 分期并监测进展"
            ),
            "guidelineRef": "推荐意见 1（tau PET 用于生物学分期）",
        })
    elif bio_stage in ("Stage C", "Stage D"):
        recommendations.append({
            "category": "治疗评估",
            "priority": "high",
            "action": "建议进行抗 Aβ 单抗治疗前基线评估（CSF 或血液 biomarker）",
            "guidelineRef": "推荐意见 3（DMT 治疗前 ARIA 基线）",
        })

    # 2. ARIA 风险相关建议
    if aria_risk_level in ("中风险", "高风险"):
        recommendations.append({
            "category": "治疗前评估",
            "priority": "high",
            "action": (
                "抗 Aβ 单抗治疗前需完善 MRI + SWI 基线评估，"
                "治疗期间每 3 个月复查 ARIA（指南推荐意见 3）"
            ),
            "guidelineRef": "推荐意见 3（MRI+SWI 监测 ARIA）",
        })

    # 3. SWI 出血点异常建议
    if swi and (swi.get('microBleedCount', 0) >= 5 or swi.get('hemosiderinDeposit')):
        recommendations.append({
            "category": "治疗前评估",
            "priority": "high",
            "action": "SWI 提示 ARIA-H 风险（微出血≥5 或含铁血黄素阳性），DMT 治疗需谨慎",
            "guidelineRef": "推荐意见 3（SWI 基线）",
        })

    # 4. 图像质量相关建议
    acquisition_adj = image_quality.get('acquisitionAdjustment')
    if acquisition_adj and acquisition_adj.get('required'):
        recommendations.append({
            "category": "图像质量",
            "priority": acquisition_adj.get('priority', 'high'),
            "action": f"建议重新采集：{acquisition_adj.get('reason')}；" + "；".join(acquisition_adj.get('adjustments', [])[:2]),
            "guidelineRef": "图像质量控制章节",
        })

    # 5. 显像剂类型相关建议
    if pet_tracer == "fdg" and risk_score > 50:
        recommendations.append({
            "category": "补充检查",
            "priority": "medium",
            "action": "FDG PET 提示代谢减低，建议补充 Aβ PET 进行淀粉样蛋白确认",
            "guidelineRef": "推荐意见 5（Aβ PET 视觉判读）",
        })
    elif pet_tracer == "amyloid" and bio_stage != "Stage 0":
        recommendations.append({
            "category": "补充检查",
            "priority": "high",
            "action": "Aβ PET 阳性，建议补充 tau PET 进行生物学分期（Stage B/C/D）",
            "guidelineRef": "推荐意见 6（Tau PET 视觉判读）",
        })

    # 6. 适应证相关建议
    if exam_indication == 'treatment_eval':
        recommendations.append({
            "category": "疗效监测",
            "priority": "medium",
            "action": "建议按 DMT 治疗方案定期复查（3-6 个月一次），纵向对比需保持一致标准化成像协议",
            "guidelineRef": "推荐意见 1（疗效监测）+ 纵向随访一致性",
        })

    # 默认建议
    if not recommendations:
        recommendations.append({
            "category": "常规随访",
            "priority": "low",
            "action": "建议 12 个月复查 FDG PET + MRI，监测认知功能进展",
            "guidelineRef": "推荐意见 7（标准化报告）",
        })

    return {
        "title": "临床决策辅助",
        "totalRecommendations": len(recommendations),
        "recommendations": recommendations,
        "guidelineReference": "中华医学会核医学分会. 阿尔茨海默病一体化 PET/MRI 脑成像临床应用指南（2026 版）",
    }


@router.get("/{case_id}")
def get_report_data(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """聚合报告数据（报告医生署名取当前登录用户）"""
    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在", 404)

    case_data = case_record_to_dict(case)

    # 模型配置
    cfg = db.query(ModelConfigRecord).first()
    model_config = {"snapshotVersion": "TransMF-15ens-v4", "strategy": "feature", "riskThreshold": 0.8}
    if cfg:
        model_config = {
            "snapshotVersion": cfg.snapshot_version, "strategy": cfg.strategy,
            "riskThreshold": cfg.risk_threshold,
        }

    # 分析结果
    analysis_rec = db.query(AnalysisRecord).filter(AnalysisRecord.case_id == case_id).first()
    if analysis_rec:
        analysis = json.loads(analysis_rec.result_json)
    else:
        analysis = build_analysis(case_data, model_config)

    # 干预方案
    intervention_rec = db.query(InterventionRecord).filter(InterventionRecord.case_id == case_id).first()
    if intervention_rec:
        interventions = json.loads(intervention_rec.sections_json)
    else:
        interventions = build_interventions(case_data)

    # 随访计划
    followup_rec = db.query(FollowUpRecord).filter(FollowUpRecord.case_id == case_id).first()
    if followup_rec:
        follow_up = json.loads(followup_rec.plan_json)
    else:
        follow_up = build_follow_up()

    # 最新一次随访执行记录（用于矛盾证据计算；无随访记录时为 None）
    latest_visit = db.query(FollowUpVisit).filter(
        FollowUpVisit.case_id == case_id
    ).order_by(FollowUpVisit.visit_date.desc()).first()

    # 报告扩展字段（规则驱动）：NIA-AA 框架对齐 / 矛盾证据 / 患者通俗版
    niaa_alignment = _compute_niaa(analysis)
    conflict_evidence = _compute_conflict(analysis, latest_visit)
    patient_friendly = _compute_patient_friendly(analysis)
    # 2026 版指南：ARIA 风险评估 + 检查适应证 + PET 显像剂类型
    aria_risk = analysis.get('ariaRisk', {
        'riskLevel': '低风险', 'riskScore': 0,
        'factors': ['未发现明确 ARIA 风险因素'],
        'recommendation': '目前 ARIA 风险较低',
    })
    # 2026 版指南表5：结构化检查所见 + 5 段式诊断意见
    image_quality = analysis.get('imageQuality', {
        'motionArtifact': False, 'motionScore': 0,
        'registrationQuality': 'good', 'registrationScore': 1.0,
        'warnings': ['图像质量良好'], 'recommendation': '图像质量良好',
    })
    exam_findings = _compute_exam_findings(analysis, case_data, niaa_alignment)
    diagnostic_conclusions = _compute_diagnostic_conclusions(
        analysis, niaa_alignment, exam_findings, aria_risk, case_data,
    )
    exam_indication = case_data.get('examIndication', 'diagnosis')
    pet_tracer = case_data.get('petTracer', 'fdg')
    contraindications = case_data.get('contraindications', {
        'absolute': [], 'relative': [], 'cleared': True, 'warnings': [],
    })
    special_population = case_data.get('specialPopulation', 'normal')
    # 适应证中文映射
    indication_map = {
        'diagnosis': '诊断及鉴别诊断',
        'staging': '分期与预后评估',
        'pre_dmt': '疾病修饰治疗前评估',
        'dmt_monitoring': '疾病修饰治疗疗效监测',
    }
    tracer_map = {
        'fdg': '18F-FDG（葡萄糖代谢）',
        'amyloid': 'Aβ PET（淀粉样蛋白）',
        'tau': 'Tau PET（tau 蛋白）',
    }
    special_population_map = {
        'normal': '普通人群',
        'down_synodrome': 'Down 综合征 AD 患者（需家属陪同）',
        'claustrophobia': '幽闭恐惧症患者（需镇静并注明）',
        'diabetes': '糖尿病患者（FDG 检查前血糖需 <11.1mmol/L）',
        'implanted_device': '体内金属植入物患者（需确认 MRI 安全等级）',
    }

    report_no = f"RPT-{case_id}-{str(int(datetime.now().timestamp()))[-4:]}"
    return ok({
        "caseInfo": case_data,
        "analysis": analysis,
        "interventions": interventions,
        "followUp": follow_up,
        "reportNo": report_no,
        "reportDate": format_date(),
        "hospital": "神经影像智能筛查中心",
        "systemName": "脑影·明衰",
        "systemVersion": "v1.0.0",
        "modelVersion": model_config.get("snapshotVersion", "TransMF-15ens-v4"),
        "guideline": "2026 版 PET/MRI 脑成像临床应用指南",
        "doctorName": current_user.get("realName") or current_user.get("username", ""),
        "niaaAlignment": niaa_alignment,
        "conflictEvidence": conflict_evidence,
        "patientFriendly": patient_friendly,
        # 2026 版 PET/MRI 指南对齐
        "ariaRisk": aria_risk,
        "examIndication": indication_map.get(exam_indication, exam_indication),
        "petTracer": tracer_map.get(pet_tracer, pet_tracer),
        "imageQuality": image_quality,
        "examFindings": exam_findings,
        "diagnosticConclusions": diagnostic_conclusions,
        "contraindications": contraindications,
        "specialPopulation": special_population_map.get(special_population, special_population),
        "specialPopulationCode": special_population,
        # 第 27 轮新增：指南 7 条推荐意见等级汇总
        "recommendationsSummary": _compute_recommendations_summary(analysis),
        # 第 28 轮新增：临床决策辅助（按推荐意见生成下一步建议）
        "clinicalDecisionSupport": _compute_clinical_decision_support(analysis, case_data),
    })


@router.post("/save")
def save_report(
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """报告云端保存"""
    # 参数校验：历史实现直接接收 dict，空 body 也会返回成功并写入 case_id 为空的审计日志
    case_info = data.get("caseInfo")
    if not isinstance(case_info, dict) or not str(case_info.get("id", "")).strip():
        return fail("报告数据不完整：缺少病例信息", 400)
    case_id = str(case_info["id"]).strip()
    case = db.query(CaseModel).filter(
        CaseModel.id == case_id, CaseModel.is_deleted.is_(False)
    ).first()
    if not case:
        return fail("病例不存在或已删除，无法保存报告", 404)

    case.cloud_saved = True
    case.diag_status = "已出报告"
    case.status = "reported"

    patient_name = case_info.get("patient", {}).get("name", "") if isinstance(case_info.get("patient"), dict) else ""
    report_no = data.get("reportNo", "")
    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id=case_id,
        patient_name=patient_name,
        action="生成筛查报告",
        operator=current_user["username"],
        time=now_str(),
        detail=f"报告编号 {report_no} 已云端保存",
    ))
    db.commit()
    return ok(None, "报告已云端保存")


@router.post("/batch-export")
def batch_export_reports(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    批量导出选中病例的报告汇总 CSV（含报告编号/患者/风险/干预摘要/随访/日期/医生）。
    请求体：{"caseIds": ["AD260001", ...]}
    """
    case_ids = payload.get("caseIds") or []
    if not case_ids:
        return fail("请选择至少 1 例病例", 400)
    if len(case_ids) > BATCH_EXPORT_LIMIT:
        return fail(f"单次最多导出 {BATCH_EXPORT_LIMIT} 例报告，请缩小选择范围", 400)

    doctor = current_user.get("realName") or current_user.get("username", "")
    report_date = format_date()
    headers = [
        "报告编号", "病例ID", "患者姓名", "性别", "年龄", "影像模态",
        "风险评分", "风险分级", "诊断状态", "干预方案摘要", "随访计划摘要",
        "报告日期", "报告医生"
    ]

    buf = io.StringIO()
    # BOM 保证 Excel 中文正常
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(headers)

    cases = db.query(CaseModel).filter(CaseModel.id.in_(case_ids), CaseModel.is_deleted.is_(False)).all()
    case_map = {c.id: c for c in cases}

    # 批量取干预/随访记录，消除循环内 N+1 查询（每例 2 次 first() → 2 次 in_ 批量查询）
    # 同一 case_id 可能多条记录，此处与单例接口口径一致取第一条（按主键序）
    inter_map: dict[str, InterventionRecord] = {}
    for rec in db.query(InterventionRecord).filter(InterventionRecord.case_id.in_(case_ids)).all():
        inter_map.setdefault(rec.case_id, rec)
    fu_map: dict[str, FollowUpRecord] = {}
    for rec in db.query(FollowUpRecord).filter(FollowUpRecord.case_id.in_(case_ids)).all():
        fu_map.setdefault(rec.case_id, rec)

    for cid in case_ids:
        c = case_map.get(cid)
        if not c:
            continue
        cd = case_record_to_dict(c)
        patient = cd.get("patient", {})
        # 干预摘要
        inter_summary = "—"
        inter_rec = inter_map.get(cid)
        if inter_rec:
            try:
                sections = json.loads(inter_rec.sections_json or "[]")
                inter_summary = "；".join(s.get("title", "") for s in sections if s.get("title"))[:80] or "—"
            except json.JSONDecodeError:
                pass
        # 随访摘要
        fu_summary = "—"
        fu_rec = fu_map.get(cid)
        if fu_rec:
            try:
                plan = json.loads(fu_rec.plan_json or "{}")
                fu_summary = f"下次随访 {plan.get('nextDate', '—')}（{plan.get('interval', '—')}）"
            except json.JSONDecodeError:
                pass

        writer.writerow([csv_sanitize(x) for x in [
            f"RPT-{cid}",
            cid,
            patient.get("name", ""),
            {"M": "男", "F": "女"}.get(patient.get("gender", ""), patient.get("gender", "")),
            patient.get("age", ""),
            c.modality,
            c.risk_score if c.risk_score is not None else "未分析",
            c.risk_level or "未分析",
            c.diag_status or "—",
            inter_summary,
            fu_summary,
            report_date,
            doctor,
        ]])

    csv_bytes = buf.getvalue().encode("utf-8")

    # 敏感操作审计：导出含患者 PHI，记录操作人/IP/数量与病例范围
    xff = request.headers.get("x-forwarded-for", "")
    client_ip = (xff.split(",")[0].strip() if xff else (request.client.host if request.client else ""))[:50]
    id_preview = "、".join(str(x) for x in case_ids[:10]) + ("…" if len(case_ids) > 10 else "")
    db.add(SystemLogModel(
        id=next_seq_id(db, SystemLogModel, "S", start=1),
        module="报告管理",
        action="批量导出报告",
        operator=current_user.get("username", ""),
        role=current_user.get("roleName", ""),
        ip=client_ip,
        result="成功",
        time=format_date_time(),
        detail=f"导出 {len(cases)} 例报告 CSV（请求 {len(case_ids)} 例）：{id_preview}",
    ))
    db.commit()

    filename = f"批量报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    # RFC 5987 编码中文文件名（latin-1 无法直接编码中文）
    encoded = urllib.parse.quote(filename)
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )
