"""
临床决策支持系统（CDSS）路由（临床角色）
------------------------------------------------------------------
基于规则引擎，根据 AI 风险评分 + 年龄 + MMSE/MoCA + 认知变化趋势 +
用药依从性等多维度因子，自动生成分级诊疗建议与干预路径。

规则引擎说明：
- 基础分级由 AI 风险评分主导（low/mci/ad-early/ad-late）
- 叠加修正因子：MMSE 严重度、认知下降速度、高龄、用药依从性差
- 输出：优先级（urgent/high/moderate/low）+ 建议措施列表 + 转诊 + 用药

端点：
- GET /cdss/recommend/{case_id}  生成单病例 CDSS 建议
- GET /cdss/recommend             批量生成（分页）
- GET /cdss/rules                 规则配置列表（只读）
- GET /cdss/stats                 CDSS 建议分布统计
"""
import json
from datetime import datetime
from collections import Counter

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord
from models.analysis import AnalysisVersionRecord, FollowUpVisit
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/cdss",
    tags=["临床决策支持"],
    dependencies=[Depends(get_current_user_qs)],
)

CLINICAL_ROLES = ("radiologist", "neurologist", "admin")


def _require_clinical(user: dict) -> bool:
    return user.get("role") in CLINICAL_ROLES


# ==================== 规则引擎核心 ====================

# 优先级定义
PRIORITY_META = {
    "urgent":   {"name": "紧急", "color": "#C94F4F", "bg": "#FAEDED", "desc": "需立即临床介入"},
    "high":     {"name": "高优先级", "color": "#D97A2B", "bg": "#FBF4E6", "desc": "建议 1 周内就诊"},
    "moderate": {"name": "中等优先级", "color": "#2F6DA3", "bg": "#E8F1F8", "desc": "建议 1 个月内随访"},
    "low":      {"name": "低优先级", "color": "#5B8C5A", "bg": "#EEF6ED", "desc": "常规健康宣教与复查"},
}

# 基础分级规则（按 AI 风险评分区间）
def _base_level(score: float) -> str:
    if score >= 70:
        return "ad-late"
    elif score >= 55:
        return "ad-early"
    elif score >= 30:
        return "mci"
    return "low"


# 各风险等级对应的基础建议措施
LEVEL_MEASURES: dict[str, list[str]] = {
    "low": [
        "维持健康生活方式（规律运动、地中海饮食、社交活动）",
        "控制血管危险因素（血压、血糖、血脂）",
        "每年 1 次认知功能复查（MMSE/MoCA）",
        "AI 辅助影像复查（建议 12 个月）",
    ],
    "mci": [
        "6 个月复查认知量表（MMSE + MoCA），追踪下降速率",
        "认知训练（记忆训练、执行功能训练）",
        "强化生活方式干预（MIND 饮食、有氧运动 ≥150min/周）",
        "控制血管危险因素，排查睡眠呼吸暂停",
        "AI 辅助影像复查（建议 6 个月）",
    ],
    "ad-early": [
        "神经内科就诊，完善神经心理学全套评估",
        "考虑胆碱酯酶抑制剂（多奈哌齐/卡巴拉汀）治疗",
        "脑脊液 Aβ42/p-tau 或血液生物标志物检测",
        "安全评估（驾驶、独居、跌倒风险）",
        "家属教育与社会支持规划",
    ],
    "ad-late": [
        "立即神经内科就诊，启动/调整抗痴呆药物治疗",
        "完善脑 MRI 排除其他病因（血管性、肿瘤）",
        "全面功能评估（ADL/IADL），制定照护计划",
        "安全监测（走失风险、误吸、跌倒）",
        "多学科会诊（神经科、老年科、精神科、营养科）",
        "评估照护者负担，提供社会资源转介",
    ],
}

# 转诊建议
LEVEL_REFERRAL: dict[str, str] = {
    "low": "社区健康管理中心 / 全科医学门诊",
    "mci": "神经内科记忆障碍门诊",
    "ad-early": "神经内科记忆障碍门诊（优先挂号）",
    "ad-late": "神经内科住院 / 日间病房（紧急评估）",
}

# 用药建议
LEVEL_MEDICATION: dict[str, str] = {
    "low": "暂无需特殊用药，建议补充维生素 D（800IU/d）",
    "mci": "暂不推荐抗痴呆药物，可考虑认知增强营养补充",
    "ad-early": "推荐胆碱酯酶抑制剂（多奈哌齐 5→10mg/d），评估联合美金刚",
    "ad-late": "胆碱酯酶抑制剂 + 美金刚（20mg/d）联合治疗，评估精神行为症状用药",
}


# ==================== 用药交互警示 ====================
# 抗痴呆药物与常见合并用药的相互作用警示库
# 每条记录：{ drugs: 冲突药物关键词, mechanism: 机制, severity: 警示级别, recommendation: 处置 }
# severity: severe(禁用) / caution(慎用) / monitor(需监测)
# 在 _medication_interactions 中根据推荐用药 + 随访 notes 解析的当前用药做匹配
_MEDICATION_INTERACTIONS = {
    # 胆碱酯酶抑制剂（多奈哌齐/卡巴拉汀/加兰他敏）相关
    "donepezil": [
        {
            "target": ["抗胆碱能药", "苯海拉明", "奥昔布宁", "阿米替林", "氯丙嗪"],
            "mechanism": "抗胆碱能药物直接拮抗胆碱酯酶抑制剂的胆碱能效应，降低疗效并加重认知障碍",
            "severity": "severe", "severityName": "禁用",
            "recommendation": "停用或替换为非抗胆碱能药物（如 SSRIs 替代阿米替林）；若必须使用，需评估疗效下降风险",
        },
        {
            "target": ["β受体阻滞剂", "美托洛尔", "比索洛尔", "普萘洛尔", "地高辛"],
            "mechanism": "胆碱能增强+迷走张力↑，与β阻滞剂/洋地黄叠加可致心动过缓、房室传导阻滞",
            "severity": "caution", "severityName": "慎用",
            "recommendation": "用药前心电图评估心率与传导；定期监测心率，<55 次/分需调整方案",
        },
        {
            "target": ["NSAIDs", "布洛芬", "双氯芬酸", "阿司匹林", "萘普生"],
            "mechanism": "ChEI 增加胃酸分泌，与 NSAIDs 合用显著增加消化道出血风险",
            "severity": "caution", "severityName": "慎用",
            "recommendation": "若需使用 NSAIDs，加用质子泵抑制剂（PPI）保护胃黏膜；监测黑便、呕血",
        },
        {
            "target": ["琥珀胆碱", "罗库溴铵", "维库溴铵"],
            "mechanism": "胆碱酯酶抑制剂抑制血浆胆碱酯酶，延长肌松药作用时间",
            "severity": "monitor", "severityName": "需监测",
            "recommendation": "外科手术前告知麻醉医生正在使用 ChEI；调整肌松药剂量并延长术后观察",
        },
    ],
    # 美金刚（NMDA 拮抗剂）相关
    "memantine": [
        {
            "target": ["右美沙芬", "氯胺酮", "金刚烷胺", "美金刚"],
            "mechanism": "均作用于 NMDA 受体，合用增加神经精神不良反应（幻觉、激越、意识模糊）",
            "severity": "caution", "severityName": "慎用",
            "recommendation": "避免与右美沙芬镇咳药同用；复方感冒药需检查成分；监测精神症状变化",
        },
        {
            "target": ["氢氯噻嗪", "呋塞米", "螺内酯"],
            "mechanism": "美金刚主要经肾脏排泄，碱性尿（利尿剂改变尿 pH）可降低清除率致血药浓度升高",
            "severity": "monitor", "severityName": "需监测",
            "recommendation": "合用期间监测神经精神症状；必要时调整美金刚剂量（减量 50%）",
        },
    ],
}

# 抗痴呆药物中文别名 → 标准关键词（用于 notes 解析当前用药）
_DRUG_ALIAS = {
    "多奈哌齐": "donepezil", "donepezil": "donepezil", "aricept": "donepezil",
    "卡巴拉汀": "donepezil", "rivastigmine": "donepezil", "利凡斯的明": "donepezil",
    "加兰他敏": "donepezil", "galantamine": "donepezil",
    "美金刚": "memantine", "memantine": "memantine", "易倍申": "memantine",
}

# 各风险等级推荐使用的抗痴呆药物标准键
_LEVEL_DRUG_KEYS = {
    "low": [],
    "mci": [],
    "ad-early": ["donepezil"],
    "ad-late": ["donepezil", "memantine"],
}


def _parse_current_meds(notes: str | None) -> list[str]:
    """
    从随访 notes 文本中弱规则匹配当前用药关键词（不依赖结构化字段）。
    返回匹配到的标准药物键列表（donepezil/memantine）+ 原始关键字。
    """
    if not notes:
        return []
    text = notes.lower()
    matched: list[str] = []
    for alias, std_key in _DRUG_ALIAS.items():
        if alias.lower() in text and std_key not in matched:
            matched.append(std_key)
    return matched


def _medication_interactions(level: str, current_meds_notes: str | None) -> dict:
    """
    根据推荐用药（按风险等级）+ 随访 notes 解析的当前用药，
    匹配药物交互警示库，生成警示列表。
    - 仅针对本次推荐用药与历史用药的交集生成警示（避免无关药物干扰）
    - 同时识别"notes 中提到、但本次未推荐"的用药，作为继续用药提醒
    """
    recommended = _LEVEL_DRUG_KEYS.get(level, [])
    current = _parse_current_meds(current_meds_notes)

    # 待评估的药物集合 = 推荐用药 + 已识别的当前用药
    target_drugs = list(dict.fromkeys(recommended + current))
    if not target_drugs:
        return {
            "hasWarnings": False,
            "items": [],
            "recommendedDrugs": recommended,
            "currentMedsDetected": current,
            "summary": "本次评估暂未匹配到需警示的合并用药",
        }

    alerts: list[dict] = []
    for drug_key in target_drugs:
        interactions = _MEDICATION_INTERACTIONS.get(drug_key, [])
        for it in interactions:
            alerts.append({
                "drug": drug_key,
                "targetKeywords": it["target"],
                "mechanism": it["mechanism"],
                "severity": it["severity"],
                "severityName": it["severityName"],
                "recommendation": it["recommendation"],
            })

    # 取严重度最高的几条作为顶层提示
    severity_order = {"severe": 3, "caution": 2, "monitor": 1}
    alerts.sort(key=lambda x: severity_order.get(x["severity"], 0), reverse=True)

    return {
        "hasWarnings": len(alerts) > 0,
        "items": alerts,
        "recommendedDrugs": recommended,
        "currentMedsDetected": current,
        "summary": (
            f"共匹配 {len(alerts)} 条潜在药物相互作用警示"
            if alerts else
            "推荐用药范围内未发现明确相互作用警示"
        ),
    }


# ==================== 照护者负担评估 ====================
# 基于 Zarit Burden Interview 简化代理评分（不依赖家属自填问卷，由病程/认知/依从性综合推算）
# 输出负担风险等级 + 照护者支持建议 + 何时需专业介入信号
def _caregiver_assessment(
    level: str,
    age: int,
    mmse: float | None,
    cognition_trend: str,
    medication_adherence: str,
) -> dict:
    """
    规则驱动估算照护者负担：
    - 基线由病程决定（ad-late 高、ad-early 中、mci 轻、low 极低）
    - 叠加修正：MMSE 重度障碍 / 认知明显下降 / 高龄 / 用药依从性差
    返回 burdenRisk: low/moderate/high + zaritProxyScore 0-88 + recommendations + warningSigns
    """
    # 基线分数（参考 Zarit 简版 0-88 量表区间）
    base_score = {"low": 8, "mci": 22, "ad-early": 42, "ad-late": 62}.get(level, 10)
    modifiers_desc: list[str] = []

    # 修正项 1：MMSE 重度认知障碍 → 照护强度显著增加
    if mmse is not None:
        if mmse < 14:
            base_score += 14
            modifiers_desc.append(f"MMSE={mmse:.0f}（重度障碍<14）显著增加照护强度")
        elif mmse < 20:
            base_score += 8
            modifiers_desc.append(f"MMSE={mmse:.0f}（中重度<20）增加照护强度")
        elif mmse < 24:
            base_score += 4
            modifiers_desc.append(f"MMSE={mmse:.0f}（轻度<24）轻度增加照护负担")

    # 修正项 2：认知明显下降 → 照护者精神压力增加
    if cognition_trend == "明显下降":
        base_score += 8
        modifiers_desc.append("认知明显下降，照护者精神压力显著上升")
    elif cognition_trend == "轻度下降":
        base_score += 4
        modifiers_desc.append("认知轻度下降，照护者需更密切监测")

    # 修正项 3：高龄 → 照护者体力负担与多重合并症风险
    if age >= 80:
        base_score += 6
        modifiers_desc.append(f"高龄（{age} 岁 ≥80），照护者体力负担加重")
    elif age >= 70:
        base_score += 3
        modifiers_desc.append(f"老年（{age} 岁），照护需关注合并症")

    # 修正项 4：用药依从性差 → 提示照护者监督负担
    if medication_adherence in ("经常漏服", "已停药"):
        base_score += 5
        modifiers_desc.append(f"用药依从性差（{medication_adherence}），提示照护监督负担重")

    # 钳制 0-88
    score = max(0, min(88, base_score))

    # 分级：参考 Zarit 简化版临床常用切点
    if score >= 61:
        risk = "high"
        risk_name = "高负担风险"
    elif score >= 41:
        risk = "moderate"
        risk_name = "中等负担风险"
    elif score >= 21:
        risk = "mild"
        risk_name = "轻度负担风险"
    else:
        risk = "low"
        risk_name = "负担较低"

    # 风险等级对应的支持建议（区分照护者与患者维度）
    support_map = {
        "high": [
            "建议照护者接受系统性支持：日间照料中心或喘息服务（每周 ≥2 次）",
            "联系社区社工进行家庭照护能力评估，必要时申请长期照护保险",
            "鼓励照护者每月参与 Alzheimer 协会家属互助小组",
            "评估照护者自身抑郁/焦虑症状（PHQ-9 / GAD-7），必要时转介心理科",
            "提供防走失定位设备与居家安全改造（浴室防滑、夜间照明）",
        ],
        "moderate": [
            "建议安排定期喘息（每周 ≥1 次亲友轮替或日间照料）",
            "鼓励照护者参与家属教育课程，了解病程与沟通技巧",
            "评估家庭照护资源，提前规划后续照护方案",
            "建立社区随访联系，关注照护者身心状态",
        ],
        "mild": [
            "鼓励照护者维持社交与个人活动，避免长期孤立",
            "提供认知障碍科普资料，增强照护信心",
            "建议每 3 个月评估一次照护负担变化",
        ],
        "low": [
            "鼓励照护者保持健康作息与社交活动",
            "提供预防认知下降的科普资料",
            "每年一次评估照护负担与家庭支持网络",
        ],
    }

    # 需立即专业介入的警示信号（与负担等级无关，提示急危信号）
    warning_signs = []
    if mmse is not None and mmse < 14:
        warning_signs.append("患者出现重度认知障碍（MMSE<14），需评估是否需要专业照护机构介入")
    if cognition_trend == "明显下降":
        warning_signs.append("认知功能明显下降，需警惕可逆性病因（感染、药物、代谢紊乱）")
    if medication_adherence == "已停药":
        warning_signs.append("已停用抗痴呆药物，需评估停药原因与症状反弹风险")
    if age >= 80 and level in ("ad-early", "ad-late"):
        warning_signs.append("高龄+中重度病程，跌倒、误吸、走失风险显著升高，需加强居家安全防护")

    return {
        "burdenRisk": risk,
        "burdenRiskName": risk_name,
        "zaritProxyScore": score,
        "scoreRange": "0-88（参考 Zarit 简化版）",
        "modifiers": modifiers_desc,
        "supportRecommendations": support_map[risk],
        "warningSigns": warning_signs,
    }


def _eval_rules(
    score: float,
    age: int,
    gender: str,
    mmse: float | None,
    moca: float | None,
    cognition_trend: str,
    medication_adherence: str,
    risk_level: str,
    current_meds_notes: str | None = None,
) -> dict:
    """
    规则引擎主入口：综合多维度因子生成 CDSS 建议。
    返回 { priority, level, measures, referral, medication, modifiers, reasoning,
            medicationInteractions, caregiverAssessment }
    """
    base = _base_level(score) if risk_level not in ("ad-late", "ad-early", "mci", "low") else risk_level
    level = base
    priority = {"low": "low", "mci": "moderate", "ad-early": "high", "ad-late": "urgent"}[level]
    modifiers: list[dict] = []
    reasoning: list[str] = []

    score_desc = f"AI 风险评分 {score:.1f}（{ {'low':'低风险','mci':'轻度认知障碍','ad-early':'AD 早期','ad-late':'AD 中晚期'}[level] }）"
    reasoning.append(score_desc)

    # 修正因子 1：MMSE 严重度
    if mmse is not None:
        if mmse < 20:
            level = _escalate_level(level)
            modifiers.append({"key": "mmse_severe", "label": f"MMSE {mmse:.0f} 分（重度障碍 <20）", "impact": "level+1"})
            reasoning.append(f"MMSE {mmse:.0f} 分提示重度认知障碍，升级至 { {'low':'低风险','mci':'轻度认知障碍','ad-early':'AD 早期','ad-late':'AD 中晚期'}[level] }")
        elif mmse < 24:
            if level in ("low", "mci"):
                level = _escalate_level(level)
            modifiers.append({"key": "mmse_mild", "label": f"MMSE {mmse:.0f} 分（轻度障碍 20-23）", "impact": "level+1"})
            reasoning.append(f"MMSE {mmse:.0f} 分提示轻度认知障碍（<24），升级评估")

    # 修正因子 2：认知下降趋势
    if cognition_trend in ("轻度下降", "明显下降"):
        if cognition_trend == "明显下降":
            level = _escalate_level(level)
            modifiers.append({"key": "cog_severe", "label": "认知明显下降", "impact": "level+1"})
            reasoning.append("随访显示认知明显下降，需紧急评估病情进展")
        else:
            modifiers.append({"key": "cog_mild", "label": "认知轻度下降", "impact": "watch"})
            reasoning.append("认知轻度下降趋势，建议缩短随访间隔")

    # 修正因子 3：高龄
    if age >= 75:
        modifiers.append({"key": "elderly", "label": f"高龄（{age} 岁 ≥75）", "impact": "referral"})
        reasoning.append(f"患者 {age} 岁（高龄），建议老年医学科会诊评估合并症与多重用药")
    elif age >= 65:
        modifiers.append({"key": "senior", "label": f"老年（{age} 岁）", "impact": "watch"})
        reasoning.append(f"患者 {age} 岁，关注老年综合评估")

    # 修正因子 4：用药依从性
    if medication_adherence in ("经常漏服", "已停药"):
        modifiers.append({"key": "adherence_poor", "label": f"用药依从性差（{medication_adherence}）", "impact": "referral"})
        reasoning.append(f"用药依从性差（{medication_adherence}），建议药学门诊或家属监督服药")
    elif medication_adherence == "偶有漏服":
        modifiers.append({"key": "adherence_mild", "label": "偶有漏服", "impact": "watch"})
        reasoning.append("用药偶有漏服，建议加强用药提醒")

    # 修正因子 5：MoCA 补充
    if moca is not None and moca < 22:
        if level in ("low", "mci"):
            level = _escalate_level(level)
        modifiers.append({"key": "moca_low", "label": f"MoCA {moca:.0f} 分（<22）", "impact": "level+1"})
        reasoning.append(f"MoCA {moca:.0f} 分（<22）提示认知障碍，支持升级评估")

    # 重新确定优先级
    priority = {"low": "low", "mci": "moderate", "ad-early": "high", "ad-late": "urgent"}[level]

    # 组装措施列表（在基础措施上叠加修正建议）
    measures = list(LEVEL_MEASURES.get(level, LEVEL_MEASURES["low"]))
    extra_measures: list[str] = []
    for m in modifiers:
        if m["impact"] == "referral":
            if m["key"] == "elderly":
                extra_measures.append("老年综合评估（CGA）：跌倒、营养、多重用药、衰弱评估")
            elif m["key"] == "adherence_poor":
                extra_measures.append("药学门诊就诊，评估用药方案简化或剂型调整")
        if m["key"] == "cog_severe":
            extra_measures.append("紧急复查头颅 MRI + 神经心理学全套，排查快速进展原因")

    level_name = {"low": "低风险", "mci": "轻度认知障碍", "ad-early": "AD 早期", "ad-late": "AD 中晚期"}[level]
    pmeta = PRIORITY_META[priority]

    return {
        "priority": priority,
        "priorityName": pmeta["name"],
        "priorityColor": pmeta["color"],
        "priorityDesc": pmeta["desc"],
        "level": level,
        "levelName": level_name,
        "measures": measures + extra_measures,
        "referral": LEVEL_REFERRAL.get(level, ""),
        "medication": LEVEL_MEDICATION.get(level, ""),
        "modifiers": modifiers,
        "reasoning": reasoning,
        "disclaimer": "AI辅助筛查，不可替代临床诊断。建议由执业医师综合判断后制定个体化方案。",
        # 用药交互警示（第 32 轮新增）：基于推荐用药 + 随访 notes 解析的当前用药匹配相互作用
        "medicationInteractions": _medication_interactions(level, current_meds_notes),
        # 照护者负担评估（第 32 轮新增）：Zarit 简化代理评分 + 分级支持建议
        "caregiverAssessment": _caregiver_assessment(
            level, age, mmse, cognition_trend, medication_adherence,
        ),
    }


def _escalate_level(level: str) -> str:
    """风险等级升一级"""
    chain = ["low", "mci", "ad-early", "ad-late"]
    idx = chain.index(level) if level in chain else 0
    return chain[min(idx + 1, len(chain) - 1)]


def _load_case_context(db: Session, case_id: str) -> dict | None:
    """加载病例上下文：AI 评分 + 患者信息 + 最新随访"""
    case = db.query(CaseRecord).filter(
        CaseRecord.id == case_id,
        CaseRecord.is_deleted == False,  # noqa: E712
    ).first()
    if not case:
        return None

    try:
        patient = json.loads(case.patient_json or "{}")
    except json.JSONDecodeError:
        patient = {}

    age = 0
    try:
        age = int(patient.get("age", 0))
    except (TypeError, ValueError):
        pass
    gender = str(patient.get("gender", ""))

    # 最新随访执行记录
    last_visit = (
        db.query(FollowUpVisit)
        .filter(FollowUpVisit.case_id == case_id)
        .order_by(FollowUpVisit.visit_date.desc(), FollowUpVisit.id.desc())
        .first()
    )

    mmse = last_visit.mmse if last_visit else None
    moca = last_visit.moca if last_visit else None
    cognition = last_visit.cognition_change if last_visit else "稳定"
    adherence = last_visit.medication_adherence if last_visit else "规律"
    # 随访备注中可能记录患者当前用药，用于药物相互作用警示的弱规则匹配
    current_meds_notes = last_visit.notes if last_visit else ""

    # 取最新 AI 分析版本
    versions = (
        db.query(AnalysisVersionRecord)
        .filter(AnalysisVersionRecord.case_id == case_id)
        .order_by(AnalysisVersionRecord.version.desc())
        .all()
    )
    latest_version = versions[0] if versions else None
    score = float(latest_version.risk_score) if latest_version and latest_version.risk_score is not None else (
        float(case.risk_score) if case.risk_score is not None else 0.0
    )
    risk_level = latest_version.risk_level if latest_version and latest_version.risk_level else (case.risk_level or _base_level(score))

    return {
        "case_id": case_id,
        "patient_name": patient.get("name", ""),
        "age": age,
        "gender": gender,
        "modality": case.modality,
        "risk_score": score,
        "risk_level": risk_level,
        "mmse": mmse,
        "moca": moca,
        "cognition_trend": cognition,
        "medication_adherence": adherence,
        "current_meds_notes": current_meds_notes,
        "model_version": latest_version.model_version if latest_version else "",
        "version": latest_version.version if latest_version else 0,
        "visit_count": len(versions),
    }


# ==================== 路由端点 ====================

@router.get("/recommend/{case_id}")
def get_recommendation(case_id: str, db: Session = Depends(get_db)):
    """生成单病例 CDSS 建议"""
    ctx = _load_case_context(db, case_id)
    if ctx is None:
        return fail("病例不存在或已删除", 404)

    recommendation = _eval_rules(
        score=ctx["risk_score"],
        age=ctx["age"],
        gender=ctx["gender"],
        mmse=ctx["mmse"],
        moca=ctx["moca"],
        cognition_trend=ctx["cognition_trend"],
        medication_adherence=ctx["medication_adherence"],
        risk_level=ctx["risk_level"],
        current_meds_notes=ctx.get("current_meds_notes"),
    )
    recommendation["caseId"] = case_id
    recommendation["patientName"] = ctx["patient_name"]
    recommendation["age"] = ctx["age"]
    recommendation["gender"] = ctx["gender"]
    recommendation["riskScore"] = ctx["risk_score"]
    recommendation["mmse"] = ctx["mmse"]
    recommendation["moca"] = ctx["moca"]
    recommendation["cognitionTrend"] = ctx["cognition_trend"]
    recommendation["medicationAdherence"] = ctx["medication_adherence"]
    recommendation["generatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return ok(recommendation)


@router.get("/recommend")
def list_recommendations(
    priority: str = Query(default="", pattern="^(urgent|high|moderate|low|)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """批量生成 CDSS 建议（基于真实病例库，分页）"""
    if not _require_clinical(current_user):
        return ok({"list": [], "total": 0, "page": page, "pageSize": pageSize}, "无权限")

    cases = (
        db.query(CaseRecord)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
        .order_by(CaseRecord.id)
        .all()
    )

    results: list[dict] = []
    for c in cases:
        ctx = _load_case_context(db, c.id)
        if ctx is None:
            continue
        rec = _eval_rules(
            score=ctx["risk_score"],
            age=ctx["age"],
            gender=ctx["gender"],
            mmse=ctx["mmse"],
            moca=ctx["moca"],
            cognition_trend=ctx["cognition_trend"],
            medication_adherence=ctx["medication_adherence"],
            risk_level=ctx["risk_level"],
            current_meds_notes=ctx.get("current_meds_notes"),
        )
        if priority and rec["priority"] != priority:
            continue
        rec["caseId"] = c.id
        rec["patientName"] = ctx["patient_name"]
        rec["age"] = ctx["age"]
        rec["gender"] = ctx["gender"]
        rec["riskScore"] = ctx["risk_score"]
        rec["mmse"] = ctx["mmse"]
        rec["cognitionTrend"] = ctx["cognition_trend"]
        results.append(rec)

    total = len(results)
    start = (page - 1) * pageSize
    page_items = results[start:start + pageSize]
    return ok({"list": page_items, "total": total, "page": page, "pageSize": pageSize})


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """CDSS 建议分布统计（看板用）"""
    cases = (
        db.query(CaseRecord)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
        .all()
    )
    priority_counter: Counter = Counter()
    level_counter: Counter = Counter()
    modifier_counter: Counter = Counter()

    for c in cases:
        ctx = _load_case_context(db, c.id)
        if ctx is None:
            continue
        rec = _eval_rules(
            score=ctx["risk_score"],
            age=ctx["age"],
            gender=ctx["gender"],
            mmse=ctx["mmse"],
            moca=ctx["moca"],
            cognition_trend=ctx["cognition_trend"],
            medication_adherence=ctx["medication_adherence"],
            risk_level=ctx["risk_level"],
            current_meds_notes=ctx.get("current_meds_notes"),
        )
        priority_counter[rec["priority"]] += 1
        level_counter[rec["level"]] += 1
        for m in rec["modifiers"]:
            modifier_counter[m["key"]] += 1

    return ok({
        "total": sum(priority_counter.values()),
        "priorityDist": [
            {"key": k, "name": PRIORITY_META[k]["name"], "value": priority_counter.get(k, 0), "color": PRIORITY_META[k]["color"]}
            for k in ("urgent", "high", "moderate", "low")
        ],
        "levelDist": [
            {"key": k, "name": {"low": "低风险", "mci": "MCI", "ad-early": "AD早期", "ad-late": "AD中晚期"}[k], "value": level_counter.get(k, 0)}
            for k in ("low", "mci", "ad-early", "ad-late")
        ],
        "modifierDist": [
            {"key": k, "name": MODIFIER_LABELS.get(k, k), "value": v}
            for k, v in modifier_counter.most_common()
        ],
    })


# 修正因子标签映射
MODIFIER_LABELS = {
    "mmse_severe": "MMSE 重度障碍",
    "mmse_mild": "MMSE 轻度障碍",
    "cog_severe": "认知明显下降",
    "cog_mild": "认知轻度下降",
    "elderly": "高龄（≥75）",
    "senior": "老年（≥65）",
    "adherence_poor": "用药依从性差",
    "adherence_mild": "偶有漏服",
    "moca_low": "MoCA 偏低",
}


@router.get("/rules")
def list_rules():
    """CDSS 规则配置（只读展示）"""
    rules = [
        {
            "id": "base_score",
            "name": "基础分级规则",
            "desc": "AI 风险评分 0-29=低风险 / 30-54=MCI / 55-69=AD早期 / ≥70=AD中晚期",
            "type": "base",
            "priority": "info",
        },
        {
            "id": "mmse_severe",
            "name": "MMSE 重度障碍修正",
            "desc": "MMSE < 20 → 风险等级升一级（提示重度认知障碍）",
            "type": "modifier",
            "priority": "high",
        },
        {
            "id": "mmse_mild",
            "name": "MMSE 轻度障碍修正",
            "desc": "MMSE 20-23 → 低/MCI 等级升一级（提示轻度认知障碍）",
            "type": "modifier",
            "priority": "moderate",
        },
        {
            "id": "cog_severe",
            "name": "认知明显下降修正",
            "desc": "随访认知变化='明显下降' → 风险等级升一级（紧急评估进展）",
            "type": "modifier",
            "priority": "high",
        },
        {
            "id": "cog_mild",
            "name": "认知轻度下降修正",
            "desc": "随访认知变化='轻度下降' → 建议缩短随访间隔",
            "type": "modifier",
            "priority": "moderate",
        },
        {
            "id": "elderly",
            "name": "高龄修正",
            "desc": "年龄 ≥ 75 → 建议老年医学科会诊（合并症与多重用药评估）",
            "type": "modifier",
            "priority": "moderate",
        },
        {
            "id": "adherence_poor",
            "name": "用药依从性差修正",
            "desc": "用药依从性='经常漏服'或'已停药' → 建议药学门诊",
            "type": "modifier",
            "priority": "moderate",
        },
        {
            "id": "moca_low",
            "name": "MoCA 偏低修正",
            "desc": "MoCA < 22 → 低/MCI 等级升一级（补充认知障碍证据）",
            "type": "modifier",
            "priority": "moderate",
        },
        {
            "id": "medication_interactions",
            "name": "抗痴呆药物相互作用警示",
            "desc": (
                "推荐胆碱酯酶抑制剂（多奈哌齐/卡巴拉汀/加兰他敏）或美金刚时，"
                "自动匹配常见合并用药相互作用（禁用/慎用/需监测三级）"
            ),
            "type": "safety",
            "priority": "high",
        },
        {
            "id": "caregiver_assessment",
            "name": "照护者负担评估",
            "desc": (
                "综合病程+MMSE+认知下降趋势+高龄+用药依从性，估算 Zarit 简化代理评分（0-88），"
                "输出负担风险等级与分级支持建议"
            ),
            "type": "support",
            "priority": "moderate",
        },
    ]
    return ok(rules)
