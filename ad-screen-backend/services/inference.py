"""
AI 分析结果生成服务
- 优先使用真实 TransMF 模型推理（model_inference）
- 模型不可用或影像文件缺失时，降级为基于病例种子的模拟数据生成
"""
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from services.utils import mulberry32, str_seed, format_date_time, format_date, score_to_risk_level

# 尝试导入真实推理服务（不可用时静默，真正加载在首次推理时发生）
_REAL_MODULE = None
try:
    from services import model_inference as _mi_module
    _REAL_MODULE = _mi_module
except Exception as e:
    logger.warning(f"[inference] 真实模型模块无法加载，将使用模拟数据：{e}")


# ---------- 常量 ----------
REGION_POOL = ["海马", "颞叶皮层", "内嗅皮层", "顶叶皮层", "后扣带回", "额叶皮层", "楔前叶"]

ROLE_NAME_MAP = {
    "radiologist": "放射科医师",
    "neurologist": "神经内科医师",
    "researcher": "科研管理员",
    "admin": "超级管理员",
}

# ---------- 2026 版 PET/MRI 指南 表2：PET 显像剂推荐参数 ----------
# 字段：剂量 / 注射后等待时间 / 采集时长 / 显像剂类型 / 中文名
# 用于病例上传时联动展示推荐参数、AI 分析结果中显像剂参数卡片
PET_TRACER_PARAMETERS = {
    "fdg": {
        "code": "fdg", "name": "¹⁸F-FDG", "cnName": "¹⁸F-氟代脱氧葡萄糖",
        "type": "metabolic", "typeName": "代谢显像",
        "dose": "37 MBq/kg", "uptakeMinutes": 60, "acquisitionMinutes": 10,
        "halfLife": "109.8 min",
        "indication": "评估脑葡萄糖代谢，用于退行性病变鉴别诊断（B 级推荐）",
    },
    "pib": {
        "code": "pib", "name": "¹¹C-PIB", "cnName": "¹¹C-匹兹堡复合物 B",
        "type": "amyloid", "typeName": "Aβ 淀粉样蛋白显像",
        "dose": "500 MBq", "uptakeMinutes": 50, "acquisitionMinutes": 20,
        "halfLife": "20.4 min",
        "indication": "脑内 Aβ 沉积定性诊断与药物疗效评估（A 级推荐）",
    },
    "flutemetamol": {
        "code": "flutemetamol", "name": "¹⁸F-flutemetamol", "cnName": "¹⁸F-氟美他莫",
        "type": "amyloid", "typeName": "Aβ 淀粉样蛋白显像",
        "dose": "185 MBq", "uptakeMinutes": 90, "acquisitionMinutes": 20,
        "halfLife": "109.8 min",
        "indication": "脑内 Aβ 沉积定性诊断与药物疗效评估（A 级推荐）",
    },
    "florbetapir": {
        "code": "florbetapir", "name": "¹⁸F-florbetapir", "cnName": "¹⁸F-氟比他吡",
        "type": "amyloid", "typeName": "Aβ 淀粉样蛋白显像",
        "dose": "370 MBq", "uptakeMinutes": 50, "acquisitionMinutes": 15,
        "halfLife": "109.8 min",
        "indication": "脑内 Aβ 沉积定性诊断与药物疗效评估（A 级推荐）",
    },
    "florbetaben": {
        "code": "florbetaben", "name": "¹⁸F-florbetaben", "cnName": "¹⁸F-氟贝苯",
        "type": "amyloid", "typeName": "Aβ 淀粉样蛋白显像",
        "dose": "300 MBq", "uptakeMinutes": 90, "acquisitionMinutes": 20,
        "halfLife": "109.8 min",
        "indication": "脑内 Aβ 沉积定性诊断与药物疗效评估（A 级推荐）",
    },
    "flortaucipir": {
        "code": "flortaucipir", "name": "¹⁸F-flortaucipir", "cnName": "¹⁸F-氟托西吡",
        "type": "tau", "typeName": "Tau 蛋白显像",
        "dose": "370 MBq", "uptakeMinutes": 80, "acquisitionMinutes": 20,
        "halfLife": "109.8 min",
        "indication": "脑内 tau 病理分布评估与生物学分期（A 级推荐）",
        "suvrThreshold": 1.65,
    },
    "mk6240": {
        "code": "mk6240", "name": "¹⁸F-MK6240", "cnName": "¹⁸F-MK6240",
        "type": "tau", "typeName": "Tau 蛋白显像",
        "dose": "185 MBq", "uptakeMinutes": 90, "acquisitionMinutes": 20,
        "halfLife": "109.8 min",
        "indication": "脑内 tau 病理分布评估与生物学分期（A 级推荐）",
        "suvrThreshold": 1.65,
    },
}

# 默认回退（未指定显像剂时）
PET_TRACER_DEFAULT = PET_TRACER_PARAMETERS["fdg"]


def _build_prep_instructions(pet_tracer: str, case_data: dict) -> dict:
    """
    2026 版 PET/MRI 指南 检查前准备规则引擎
    根据显像剂类型生成检查前准备清单：
    - FDG：空腹 6h、避免含糖饮料/咖啡因/药物、前 1d 避免剧烈运动、糖尿病血糖<11.1mmol/L
    - Aβ/tau：无需禁食
    - 共同：可摘义齿需去除、幽闭恐惧需镇静并注明、Down 综合征需家属陪同
    - 纵向随访需一致标准化成像协议
    返回 { items, fasting, glucoseControl, specialNotes, longitudinalProtocol }
    """
    tracer = PET_TRACER_PARAMETERS.get((pet_tracer or "fdg").lower(), PET_TRACER_DEFAULT)
    ttype = tracer["type"]
    items = []
    fasting_required = False
    glucose_control = False

    if ttype == "metabolic":
        # FDG 类：必须空腹 6h、避免含糖饮料/咖啡因、前 1d 避免剧烈运动
        fasting_required = True
        glucose_control = True
        items.extend([
            "空腹至少 6 小时（可饮水），避免含糖饮料",
            "检查前 24 小时避免剧烈运动，减少肌肉本底摄取",
            "避免咖啡因及镇静药物（影响脑代谢评估）",
            "糖尿病患者：血糖需控制在 < 11.1 mmol/L，必要时按医嘱调整降糖方案",
            "注射后暗室静卧 60 分钟，避免视听刺激",
        ])
    elif ttype in ("amyloid", "tau"):
        # Aβ / tau 类：无需禁食
        items.extend([
            "无需禁食",
            "注射后安静候诊 50~90 分钟（按显像剂说明书）",
            "注射前后鼓励饮水促进膀胱排空，降低盆腔本底",
        ])

    # 共同准备（所有显像剂）
    items.extend([
        "可摘义齿、耳环、发卡等金属物品需去除",
        "幽闭恐惧症患者需提前镇静并注明",
        "检查前询问育龄女性妊娠可能性（必要时妊娠试验）",
    ])

    # 特殊人群
    special_pop = (case_data.get("specialPopulation") or "").lower()
    special_notes = []
    if "down" in special_pop or "trisomy" in special_pop:
        special_notes.append("Down 综合征 AD 患者：检查全程需家属陪同，必要时镇静")
    if "claustrophobia" in special_pop or "幽闭" in special_pop:
        special_notes.append("幽闭恐惧症：需镇静并提前预约，检查中保留对讲通道")
    if "diabetes" in special_pop or "糖尿病" in special_pop:
        special_notes.append("糖尿病：检查当日晨测血糖，≥ 11.1 mmol/L 需推迟或控糖后重约")
    if "implant" in special_pop or "植入" in special_pop:
        special_notes.append("体内植入物：核对 MRI 安全等级，必要时调整序列参数")

    return {
        "tracerType": ttype,
        "tracerName": tracer["name"],
        "items": items,
        "fastingRequired": fasting_required,
        "glucoseControl": glucose_control,
        "specialNotes": special_notes or ["无特殊人群标记"],
        "longitudinalProtocol": (
            "纵向随访需保持一致标准化成像协议：同机型 / 同场强 / 同注射及采集方案 / 同重建参数"
        ),
    }


# ---------- 2026 版指南：MRI 定量评估工具 ----------
# 指南推荐意见 2：MRI 视觉评估 + 定量评估
# 定量工具：SPM（统计参数图）/ CAT12（计算解剖学工具箱）/ Freesurfer
# 输出：体素级灰质体积图 / 皮层厚度参数图 / 海马亚区体积
# 项目当前为模拟标记（无真实 SPM/CAT12 环境），用于报告规范化展示
_MRI_QUANT_TOOLS = [
    {
        "code": "spm", "name": "SPM12",
        "outputs": ["灰质体积参数图", "脑脊液分割图", "标准化脑模板配准图"],
        "purpose": "体素级形态学分析（VBM），评估全脑灰质萎缩模式",
    },
    {
        "code": "cat12", "name": "CAT12（Computational Anatomy Toolbox）",
        "outputs": ["皮层厚度参数图", "区域灰质体积表", "脑表面网格模型"],
        "purpose": "基于体素的形态学分析，更高精度的皮层厚度测量",
    },
    {
        "code": "freesurfer", "name": "FreeSurfer 7",
        "outputs": ["海马亚区体积表", "皮层厚度图（Desikan-Killiany 图谱）", "皮下核团分割"],
        "purpose": "基于表面形态分析，海马亚区精细分割与皮层厚度",
    },
]


def _build_mri_quantification(level: str, risk_score: float, m: dict, rand) -> dict:
    """
    2026 版指南推荐意见 2：MRI 定量评估（SPM/CAT12/Freesurfer）
    生成定量工具列表 + 当前病例的核心定量指标（灰质体积/皮层厚度/海马亚区）
    - level/risk_score 用于模拟生成与病程匹配的数值
    - 标记 status='simulated' 表示当前为系统模拟，未触发真实 SPM/CAT12 流水线
    """
    # 核心定量指标
    total_gm_volume = round(580 - (risk_score / 100) * 130 - next(rand) * 10, 1)
    mean_cort_thick = m["cort"]
    hippo_subfields = [
        {"name": "CA1", "left": round(1.6 - (risk_score / 100) * 0.5 - next(rand) * 0.05, 2),
         "right": round(1.65 - (risk_score / 100) * 0.5 - next(rand) * 0.05, 2)},
        {"name": "CA2-3", "left": round(0.55 - (risk_score / 100) * 0.15 - next(rand) * 0.02, 2),
         "right": round(0.58 - (risk_score / 100) * 0.15 - next(rand) * 0.02, 2)},
        {"name": "CA4-DG", "left": round(0.42 - (risk_score / 100) * 0.12 - next(rand) * 0.02, 2),
         "right": round(0.44 - (risk_score / 100) * 0.12 - next(rand) * 0.02, 2)},
        {"name": "Subiculum", "left": round(0.52 - (risk_score / 100) * 0.13 - next(rand) * 0.02, 2),
         "right": round(0.54 - (risk_score / 100) * 0.13 - next(rand) * 0.02, 2)},
    ]

    # ---------- 2026 版指南：MTA 视觉评分详细描述（推荐意见 2） ----------
    # MTA（Medial Temporal Atrophy）评分：T1WI 冠状位海马高度变化
    # 0 级：正常；1 级：轻度；2 级：中度；3 级：重度；4 级：极重度
    # 年龄校正：≥75 岁评分≥2 级提示异常；<75 岁评分≥1 级提示异常
    mta_value = m["mta"]
    mta_grades = [
        {"grade": 0, "desc": "海马结构正常，脑脊液间隙无扩大"},
        {"grade": 1, "desc": "轻度萎缩，海马高度轻微减少，脉络裂轻度扩大"},
        {"grade": 2, "desc": "中度萎缩，海马高度明显减少，脉络裂明显扩大"},
        {"grade": 3, "desc": "重度萎缩，海马高度显著减少，侧脑室颞角明显扩大"},
        {"grade": 4, "desc": "极重度萎缩，海马结构难以辨认"},
    ]
    mta_abnormal_threshold = 1  # <75 岁时 ≥1 异常
    mta_detail = {
        "score": mta_value,
        "slicePosition": "T1WI 斜冠状位（垂直于海马长轴），通过脑干前方红核层面",
        "grading": mta_grades,
        "currentGrade": mta_grades[mta_value] if mta_value < len(mta_grades) else mta_grades[-1],
        "abnormalThreshold": mta_abnormal_threshold,
        "isAbnormal": mta_value >= mta_abnormal_threshold,
        "ageAdjustment": "≥75 岁评分≥2 级提示异常；<75 岁评分≥1 级提示异常",
    }

    # ---------- 2026 版指南：Fazekas 评分详细描述（报告模板表5） ----------
    # Fazekas 评分：脑白质高信号（WMH）严重程度
    # PV-WMH（脑室旁白质高信号）+ DW-WMH（深部白质高信号）各 0-3 级
    fazekas_value = m["wmh"]
    fazekas_pv_grades = [
        {"grade": 0, "desc": "无脑室旁白质高信号"},
        {"grade": 1, "desc": "帽状或线样高信号"},
        {"grade": 2, "desc": "光滑的晕圈高信号"},
        {"grade": 3, "desc": "不规则白质高信号延伸至深部"},
    ]
    fazekas_dw_grades = [
        {"grade": 0, "desc": "无深部白质高信号"},
        {"grade": 1, "desc": "点状高信号"},
        {"grade": 2, "desc": "病灶开始融合"},
        {"grade": 3, "desc": "大片融合病灶"},
    ]
    pv_grade_idx = min(fazekas_value, 3)
    dw_grade_idx = min(max(fazekas_value, 0), 3)
    fazekas_detail = {
        "totalScore": fazekas_value,
        "pvWmh": fazekas_pv_grades[pv_grade_idx],
        "dwWmh": fazekas_dw_grades[dw_grade_idx],
        "isAbnormal": fazekas_value >= 2,
        "clinicalSignificance": (
            "Fazekas≥2 级为重度白质损害，ARIA 风险增加，抗 Aβ 单抗治疗前需重点评估"
            if fazekas_value >= 2
            else "Fazekas<2 级白质损害较轻"
        ),
        "imagingSequence": "T2-FLAIR 或 PD 加权像",
    }

    # ---------- 2026 版指南：SWI 序列出血点检测（推荐意见 3：MRI+SWI 监测 ARIA） ----------
    # SWI（Susceptibility Weighted Imaging）用于检测微出血、含铁血黄素沉积
    # 微出血数量与 ARIA 风险正相关，是抗 Aβ 单抗治疗的重要基线指标
    micro_bleed_count = 0
    if next(rand) > 0.7:
        micro_bleed_count = int(next(rand) * 5) + 1
    hemosiderin_deposit = next(rand) > 0.85
    bleed_locations = []
    if micro_bleed_count > 0:
        loc_pool = ["额叶皮层下", "顶叶皮层下", "颞叶皮层下", "基底节区", "丘脑", "小脑"]
        for i in range(min(micro_bleed_count, len(loc_pool))):
            bleed_locations.append(loc_pool[i])
    swi_findings = {
        "sequence": "SWI（磁敏感加权成像）",
        "microBleedCount": micro_bleed_count,
        "microBleedLocations": bleed_locations or ["未见明确微出血灶"],
        "hemosiderinDeposit": hemosiderin_deposit,
        "hemosiderinNote": (
            "检测到含铁血黄素沉积，提示既往微小血管病变"
            if hemosiderin_deposit else "未见明显含铁血黄素沉积"
        ),
        "ariaRelevance": (
            "微出血≥5 个或含铁血黄素沉积阳性提示 ARIA-H 风险增高，"
            "抗 Aβ 单抗治疗需谨慎评估"
            if micro_bleed_count >= 5 or hemosiderin_deposit
            else "SWI 基线无明确出血征象"
        ),
        "status": "simulated",
    }

    return {
        "tools": _MRI_QUANT_TOOLS,
        "metrics": {
            "totalGrayMatterVolume": total_gm_volume,  # 单位 cm³
            "meanCorticalThickness": mean_cort_thick,  # 单位 mm
            "hippocampalSubfields": hippo_subfields,  # 海马亚区体积 cm³
            "unit": {"volume": "cm³", "thickness": "mm"},
        },
        "atrophyPattern": (
            "广泛皮层萎缩，以颞叶、顶叶、后扣带回为著" if risk_score > 70
            else "内侧颞叶为主的轻度萎缩" if risk_score > 40
            else "未见明显萎缩征象"
        ),
        "mtaDetail": mta_detail,
        "fazekasDetail": fazekas_detail,
        "swiFindings": swi_findings,
        "status": "simulated",  # simulated / real
        "note": "当前为系统模拟数值；真实环境下需调用 SPM12/CAT12/FreeSurfer 流水线生成参数图",
    }


# ---------- 2026 版指南：标准化成像协议参数 ----------
# 纵向随访要求：同机型 / 同场强 / 同注射及采集方案 / 同重建参数
# 项目用模拟机型库生成 scannerInfo，用于报告展示和随访一致性校验
_SCANNER_POOL = [
    {"manufacturer": "Siemens", "model": "Biograph mMR", "fieldStrength": "3.0T PET/MRI",
     "petDetector": "APD", "reconMethod": "OSEM 3D", "slices": "T1-MPRAGE / T2-FLAIR / SWI"},
    {"manufacturer": "GE Healthcare", "model": "Signa PET/MR", "fieldStrength": "3.0T PET/MRI",
     "petDetector": "SiPM", "reconMethod": "Q.Clear (TOF+BSREM)", "slices": "T1-BRAVO / T2-FLAIR / SWAN"},
    {"manufacturer": "Philips", "model": "Ingenuity PET/MR", "fieldStrength": "3.0T PET/MRI",
     "petDetector": "PMT", "reconMethod": "B-OS-TOF", "slices": "T1-FFE / T2-FLAIR / SWI"},
]


def _build_scanner_info(case_data: dict, rand) -> dict:
    """
    2026 版指南：标准化成像协议参数
    用于报告展示（机型/场强/序列）和纵向随访一致性校验基础
    """
    seed_val = next(rand)
    scanner = _SCANNER_POOL[int(seed_val * len(_SCANNER_POOL)) % len(_SCANNER_POOL)]
    return {
        "manufacturer": scanner["manufacturer"],
        "model": scanner["model"],
        "fieldStrength": scanner["fieldStrength"],
        "petDetector": scanner["petDetector"],
        "reconMethod": scanner["reconMethod"],
        "mriSequences": scanner["slices"],
        "longitudinalConsistency": (
            "纵向随访需保持上述参数一致，避免机型/场强差异导致定量指标不可比"
        ),
        "status": "simulated",
    }



def _estimate_metrics_from_score(score: float, rand) -> dict:
    """根据风险评分估算量化指标（模拟数据用）"""
    return {
        "hvL": round(2.9 - (score / 100) * 0.9 - next(rand) * 0.1, 2),
        "hvR": round(2.95 - (score / 100) * 0.95 - next(rand) * 0.1, 2),
        "suv": round(1.12 - (score / 100) * 0.3 + next(rand) * 0.04, 2),
        "cort": round(2.9 - (score / 100) * 0.55 + next(rand) * 0.08, 2),
        "ven": round(24 + (score / 100) * 26 + next(rand) * 4, 1),
        "mta": 3 if score > 75 else (2 if score > 55 else (1 if score > 35 else 0)),
        "wmh": 1 if next(rand) > 0.6 else 0,
    }


def _build_pet_patterns(tracer: str, level: str, risk_score: float, regions: list,
                        metrics: dict, rand, bio_stage: str) -> dict:
    """
    2026 版 PET/MRI 指南 PET 视觉判读征象生成
    - 指南推荐意见 5：Aβ PET 视觉判读阳/阴性（阴性见"山脊征""钻石征""卡通手征""冬树征"，
      阳性见"平原征""亲吻征""夏树征"）
    - 指南推荐意见 6：tau PET 视觉判读阳性范围（SUVR>1.65 阳性阈值，flortaucipir）
    - 指南推荐意见 4：FDG PET 视觉评估代谢减低范围（双侧顶颞额叶海马扣带回）
    """
    if tracer == "amyloid":
        # Aβ PET 视觉判读（阳性阈值用风险评分代理）
        is_positive = risk_score >= 45
        if is_positive:
            patterns = ["平原征", "亲吻征", "夏树征"]
            conclusion = "Aβ PET 阳性：皮层弥漫性摄取增高，符合淀粉样蛋白沉积表现"
            affected_regions = ["额叶", "顶叶", "颞叶", "后扣带回", "楔前叶"]
            suvr = round(1.0 + (risk_score / 100) * 0.85 + next(rand) * 0.08, 2)
            stage_hint = bio_stage if bio_stage != "Stage 0" else "Stage A"
        else:
            patterns = ["山脊征", "钻石征", "卡通手征", "冬树征"]
            conclusion = "Aβ PET 阴性：皮层摄取低于白质，脑沟脑脊液信号清晰，无明显淀粉样蛋白沉积"
            affected_regions = []
            suvr = round(0.95 + next(rand) * 0.18, 2)
            stage_hint = "Stage 0"
        # 2026 版指南 + ADNI 标准：Aβ PET 半定量用 Centiloid（CL）量度
        # CL≈(SUVR-1)×100 简化估算，CL>30 为阳性阈值
        centiloid = round(max(0, (suvr - 1.0) * 100), 1)
        centiloid_positive = centiloid > 30
        return {
            "tracer": "amyloid",
            "tracerName": "Aβ PET（18F-florbetapir / florbetaben / flutemetamol / 11C-PIB）",
            "positive": is_positive,
            "patterns": patterns,
            "conclusion": conclusion,
            "affectedRegions": affected_regions,
            "suvr": suvr,
            "suvrThreshold": 1.30,  # SUVR 参考下限（不同显像剂略有差异）
            "thresholdNote": "Aβ PET 视觉判读以皮层-白质信号对比为准；半定量 Centiloid>30 提示阳性",
            "centiloid": centiloid,
            "centiloidThreshold": 30,
            "centiloidPositive": centiloid_positive,
            "biologicalStageHint": stage_hint,
            "quantificationTool": "SPM/PMOD（半定量 SUVR + Centiloid）",
            "tracerSpecificArtifacts": [
                {"artifact": "白质生理性摄取", "note": "florbetapir / florbetaben 早期白质摄取明显，延时 30min 后降低，需鉴别"},
                {"artifact": "脑池/静脉窦生理性摄取", "note": "flutemetamol 可见上矢状窦生理性摄取，勿误判为阳性"},
                {"artifact": "11C-PIB 非特异性白质滞留", "note": "11C-PIB 半衰期短（20.4min），注射后早期白质滞留，需按指南 50min 时相判读"},
            ],
        }

    if tracer == "tau":
        # tau PET 视觉判读（指南推荐：SUVR>1.65 阳性阈值，flortaucipir）
        suvr = round(1.05 + (risk_score / 100) * 0.95 + next(rand) * 0.06, 2)
        is_positive = suvr > 1.65
        # tau 分布范围按 Braak 分期
        if risk_score < 35:
            distribution = "未见明显异常 tau 摄取"
            braak_stage = "Braak I-"
            affected_regions = []
        elif risk_score < 55:
            distribution = "tau 局限于内侧颞叶（海马/内嗅皮层）"
            braak_stage = "Braak I-II"
            affected_regions = ["海马", "内嗅皮层"]
        elif risk_score < 70:
            distribution = "tau 累及外侧颞叶与基底前脑"
            braak_stage = "Braak III-IV"
            affected_regions = ["海马", "内嗅皮层", "颞叶皮层", "基底前脑"]
        elif risk_score < 85:
            distribution = "tau 广泛累及新皮层（顶叶/颞叶/外侧额叶）"
            braak_stage = "Braak V"
            affected_regions = ["海马", "颞叶皮层", "顶叶皮层", "外侧额叶皮层"]
        else:
            distribution = "tau 广泛累及新皮层（顶叶/颞叶/外侧额叶/初级感觉皮层）"
            braak_stage = "Braak VI"
            affected_regions = ["海马", "颞叶皮层", "顶叶皮层", "额叶皮层", "初级感觉皮层"]
        conclusion = (
            f"Tau PET 阳性：SUVR={suvr}>1.65，{distribution}"
            if is_positive else f"Tau PET 阴性：SUVR={suvr}≤1.65，{distribution}"
        )
        return {
            "tracer": "tau",
            "tracerName": "Tau PET（18F-flortaucipir / 18F-MK6240）",
            "positive": is_positive,
            "patterns": [],  # tau PET 以分布范围为主，无典型"征象"标签
            "conclusion": conclusion,
            "affectedRegions": affected_regions,
            "suvr": suvr,
            "suvrThreshold": 1.65,
            "thresholdNote": "tau PET 阳性阈值 SUVR>1.65（指南推荐 18F-flortaucipir）",
            "braakStage": braak_stage,
            "biologicalStageHint": bio_stage,
            "quantificationTool": "SPM/PMOD（半定量 SUVR + Braak 分期）",
            "tracerSpecificArtifacts": [
                {"artifact": "纹状体脱靶摄取", "note": "flortaucipir 纹状体生理性高摄取，影响 Braak 分期判读，需结合解剖定位"},
                {"artifact": "脉络丛脱靶摄取", "note": "flortaucipir / MK6240 脉络丛脱靶摄取明显，邻近海马判读需谨慎"},
                {"artifact": "黑质脱靶摄取", "note": "flortaucipir 黑质生理性摄取，勿误判为中脑 tau 阳性"},
                {"artifact": "MK6240 脉络丛 / 脑神经脱靶", "note": "MK6240 脉络丛脱靶频繁，建议结合 SUVR 阈值 1.65 综合判读"},
            ],
        }

    # 默认 FDG PET 视觉评估（指南推荐意见 4：代谢减低范围）
    # FDG PET 视觉判读以双侧顶颞额叶海马扣带回代谢减低范围为准
    affected_regions = []
    for r in regions:
        affected_regions.append(f"{r.get('side', '')}{r.get('region', '')}")
    if not affected_regions:
        affected_regions = ["未见明显代谢减低区域"]
    # FDG 模式按风险等级匹配典型分布
    if level == "ad-early":
        pattern_desc = "双侧后扣带回/楔前叶代谢减低，符合 AD 早期典型分布"
    elif level == "ad-late":
        pattern_desc = "双侧顶颞叶、后扣带回及额叶代谢广泛减低，符合 AD 中晚期分布"
    elif level == "mci":
        pattern_desc = "内侧颞叶及后扣带回轻度代谢减低，符合 MCI 早期改变"
    else:
        pattern_desc = "未见明显异常代谢减低区，FDG 摄取分布对称"
    suvr = round(1.15 - (risk_score / 100) * 0.4 - next(rand) * 0.05, 2)
    return {
        "tracer": "fdg",
        "tracerName": "18F-FDG（葡萄糖代谢）",
        "positive": level != "low",
        "patterns": [],
        "conclusion": pattern_desc,
        "affectedRegions": affected_regions,
        "suvr": suvr,
        "suvrThreshold": None,
        "thresholdNote": "FDG PET 视觉评估以代谢减低范围为准，无固定 SUVR 阈值",
        "biologicalStageHint": bio_stage,
        "quantificationTool": "SPM（体素级代谢减低参数图）",
        "tracerSpecificArtifacts": [
            {"artifact": "注射部位本底", "note": "FDG 注射对侧上肢本底摄取，影响半定量基线，建议记录注射部位"},
            {"artifact": "肌肉生理性摄取", "note": "检查前运动或紧张致咀嚼肌 / 颈部肌群摄取增高，影响皮层定量"},
            {"artifact": "泌尿系统排泄", "note": "FDG 经肾排泄，肾盂 / 输尿管高摄取，可造成伪影，需饮水促排"},
        ],
    }


def _build_analysis_synthetic(case_data: dict, model_config: dict, risk_score: float,
                              inference_source: str = "simulated", ensemble_meta: dict = None) -> dict:
    """
    基于病例种子生成稳定的 AI 分析结果（报告结构化部分）
    - risk_score：风险评分（真实模型概率换算或模拟种子生成）
    - inference_source：'real' | 'simulated'
    - ensemble_meta：真实集成推理详情 {prob, probMin, probMax, probStd, nModels, device, elapsed}
    """
    seed = str_seed(case_data["id"])
    rand = mulberry32(seed)
    next(rand)  # 跳过第一个值，保持与前端 Mock 一致

    level = score_to_risk_level(risk_score, model_config.get("riskThreshold", 0.8))
    stage_map = {
        "low": {"code": "CN", "stage": "CN（认知正常）", "desc": "各脑区代谢与形态学指标均在正常范围内，未见显著认知障碍相关影像学征象。"},
        "mci": {"code": "MCI", "stage": "MCI（轻度认知障碍）", "desc": "内侧颞叶轻度萎缩伴局部代谢减低，符合轻度认知障碍影像学表现，建议定期随访复查。"},
        "ad-early": {"code": "AD-E", "stage": "AD 早期", "desc": "双侧海马体积缩小，后扣带回/楔前叶代谢减低，符合阿尔兹海默病早期影像学改变。"},
        "ad-late": {"code": "AD-L", "stage": "AD 中晚期", "desc": "广泛皮层萎缩伴多脑区代谢显著减低，脑室扩大，符合阿尔兹海默病中晚期影像学表现。"},
    }
    st = stage_map[level]

    # 异常脑区
    regions = []
    region_count = 0 if level == "low" else (2 if level == "mci" else (4 if level == "ad-early" else 6))
    for i in range(region_count):
        region = REGION_POOL[i % len(REGION_POOL)]
        r = next(rand)
        side = "双侧" if r > 0.62 else ("左侧" if r > 0.31 else "右侧")
        atrophy = "重度萎缩" if risk_score > 75 else ("中度萎缩" if risk_score > 55 else "轻度萎缩")
        regions.append({
            "region": region, "side": side, "atrophy": atrophy,
            "metabolism": round(0.92 - (risk_score / 100) * 0.3 - next(rand) * 0.06, 2),
            "zScore": round(-(1.2 + (risk_score / 100) * 2.2 + next(rand) * 0.6), 1),
        })

    # 量化指标
    m = _estimate_metrics_from_score(risk_score, rand)
    metrics = [
        {"key": "hvL", "label": "左侧海马体积", "value": m["hvL"], "unit": "cm³", "refRange": "2.6 ~ 3.6", "status": "abnormal" if risk_score > 55 else ("warn" if risk_score > 35 else "normal")},
        {"key": "hvR", "label": "右侧海马体积", "value": m["hvR"], "unit": "cm³", "refRange": "2.6 ~ 3.6", "status": "abnormal" if risk_score > 55 else ("warn" if risk_score > 35 else "normal")},
        {"key": "suv", "label": "全脑平均代谢 SUV", "value": m["suv"], "unit": "SUV", "refRange": "0.95 ~ 1.25", "status": "abnormal" if risk_score > 62 else "normal"},
        {"key": "cort", "label": "皮层平均厚度", "value": m["cort"], "unit": "mm", "refRange": "2.6 ~ 3.2", "status": "abnormal" if risk_score > 70 else ("warn" if risk_score > 45 else "normal")},
        {"key": "ven", "label": "脑室体积", "value": m["ven"], "unit": "cm³", "refRange": "< 38", "status": "abnormal" if risk_score > 60 else ("warn" if risk_score > 40 else "normal")},
        {"key": "mta", "label": "MTA 视觉评分", "value": float(m["mta"]), "unit": "级", "refRange": "0 ~ 1", "status": "abnormal" if risk_score > 55 else ("warn" if risk_score > 35 else "normal")},
        {"key": "wmh", "label": "白质高信号 Fazekas", "value": float(m["wmh"]), "unit": "级", "refRange": "0", "status": "normal"},
    ]

    confidence = round(0.86 + next(rand) * 0.12, 3)
    patient = case_data.get("patient", {})
    gender_text = "男" if patient.get("gender") == "M" else "女"

    is_real = inference_source == "real"
    prefix = "（真实 TransMF 模型推理）" if is_real else "（模拟推理演示）"
    if is_real and ensemble_meta:
        duration = ensemble_meta.get("elapsed", round(2.4 + next(rand) * 1.6, 1))
        # 真实模型：用集成一致度修正置信度（标准差越小置信度越高）
        std = ensemble_meta.get("probStd", 0.1)
        confidence = round(max(0.82, min(0.99, 0.98 - std)), 3)
    else:
        duration = round(2.4 + next(rand) * 1.6, 1)

    # ---------- 2026 版 PET/MRI 指南：生物学分期（Stage 0/A/B/C/D） ----------
    # 指南推荐 tau PET 对 Aβ 阳性患者进行生物学分期：
    #   Stage 0 = Aβ 阴性（A-）；Stage A = A+T-；Stage B = A+T_MTL+；
    #   Stage C = A+T_MOD+；Stage D = A+T_HIGH+
    # 项目无真实 tau PET，用风险评分代理 Aβ，海马萎缩/异常区域代理 tau 分布
    if risk_score < 35:
        bio_stage, bio_stage_desc = "Stage 0", "Aβ 阴性（A-T-），不符合 AD 生物学诊断标准"
    elif risk_score < 55:
        bio_stage, bio_stage_desc = "Stage A", "Aβ 阳性但 tau 阴性（A+T-），处于 AD 连续谱系临床前期"
    elif risk_score < 70:
        bio_stage, bio_stage_desc = "Stage B", "tau 沉积局限于内侧颞叶（A+T_MTL+），对应早期 AD"
    elif risk_score < 85:
        bio_stage, bio_stage_desc = "Stage C", "tau 新皮层中度摄取（A+T_MOD+），对应中期 AD"
    else:
        bio_stage, bio_stage_desc = "Stage D", "tau 新皮层广泛高摄取（A+T_HIGH+），对应晚期 AD"

    # ---------- ARIA 风险筛查（指南推荐意见 3：MRI+SWI 监测） ----------
    # 淀粉样蛋白相关影像异常（ARIA）：抗 Aβ 单抗治疗的重要并发症
    # 风险因素：年龄≥75、Fazekas≥2、微出血/含铁血黄素沉积、APOE4 携带
    age = patient.get("age", 65)
    fazekas = m["wmh"]
    ariha_risk_score = 0
    ariha_factors = []
    if age >= 75:
        ariha_risk_score += 2
        ariha_factors.append(f"年龄≥75岁（{age}岁）")
    if fazekas >= 2:
        ariha_risk_score += 2
        ariha_factors.append(f"白质高信号 Fazekas {fazekas} 级")
    if risk_score > 70:
        ariha_risk_score += 1
        ariha_factors.append("广泛皮层萎缩提示病程较晚")
    # 微出血风险：模拟数据代理（SWI 序列尚未接入时）
    micro_bleed = next(rand) > 0.85
    if micro_bleed:
        ariha_risk_score += 2
        ariha_factors.append("SWI 检测到可疑微出血灶")
    ariha_level = "低风险" if ariha_risk_score <= 1 else ("中风险" if ariha_risk_score <= 3 else "高风险")
    ariha = {
        "riskLevel": ariha_level,
        "riskScore": ariha_risk_score,
        "factors": ariha_factors or ["未发现明确 ARIA 风险因素"],
        "recommendation": (
            "抗 Aβ 单抗治疗前需完善常规 MRI + SWI 基线评估，治疗期间定期复查监测 ARIA"
            if ariha_risk_score > 1
            else "目前 ARIA 风险较低，可按标准方案进行治疗评估"
        ),
    }

    # ---------- 2026 版指南：图像质量控制（运动伪影 + PET-MRI 配准质量） ----------
    # 指南要求：运动伪影识别（头皮轮廓/脑室/解剖结构在 PET 与 MRI 不重合），
    # 配准质量评估影响后续定量分析可靠性
    motion_score = next(rand)
    registration_score = next(rand)
    # 高风险病例运动伪影概率略高（认知障碍患者配合度低）
    if level in ("ad-early", "ad-late"):
        motion_score = max(motion_score, next(rand))
    has_motion_artifact = motion_score > 0.82
    registration_quality = "good" if registration_score > 0.7 else ("acceptable" if registration_score > 0.4 else "poor")
    if has_motion_artifact:
        registration_quality = "poor" if registration_score < 0.5 else "acceptable"
    quality_warnings = []
    if has_motion_artifact:
        quality_warnings.append("检测到运动伪影：PET 与 MRI 头皮轮廓/脑室边界不重合，建议复核或重新采集")
    if registration_quality == "poor":
        quality_warnings.append("PET-MRI 配准质量较差，定量指标参考价值受限，建议结合视觉评估综合判断")
    elif registration_quality == "acceptable":
        quality_warnings.append("PET-MRI 配准质量可接受，定量指标仅供参考")

    # ---------- 2026 版指南：常见伪影识别扩充 ----------
    # 除运动伪影外，指南要求识别其他常见伪影并给出处理建议
    scalp_activity_score = next(rand)
    injection_leak_score = next(rand)
    has_scalp_activity = scalp_activity_score > 0.88
    has_injection_leak = injection_leak_score > 0.92
    if has_scalp_activity:
        quality_warnings.append("头皮活动性摄取增高：可能为注射外渗或头皮炎症，建议核对注射部位")
    if has_injection_leak:
        quality_warnings.append("注射点漏液可疑：上肢或腋下异常摄取，影响 SUV 定量准确性，建议记录注射部位")

    # ---------- 2026 版指南：图像质量失败处置流程 ----------
    # 当配准 poor 或运动伪影明显时，给出具体的重新采集参数调整建议
    acquisition_adjustment = None
    if registration_quality == "poor" or has_motion_artifact:
        acquisition_adjustment = {
            "required": True,
            "reason": (
                "运动伪影或配准质量差"
                if has_motion_artifact and registration_quality == "poor"
                else "运动伪影" if has_motion_artifact else "PET-MRI 配准质量差"
            ),
            "adjustments": [
                "重新摆位并使用头托固定，必要时镇静",
                "延长采集时间至 15~20 min（指南推荐值），提高 SNR",
                "采用 list-mode 数据 + 运动校正重建（若机型支持）",
                "复核 PET-MRI 配准算法，必要时手动调整 ROI",
            ],
            "priority": "high" if registration_quality == "poor" else "medium",
            "reimbursementNote": "重新采集可能影响医保结算，建议与临床医师确认必要性",
        }
    elif has_scalp_activity or has_injection_leak:
        acquisition_adjustment = {
            "required": False,
            "reason": "头皮活动 / 注射点漏液（可补救，无需重采）",
            "adjustments": [
                "核对注射部位，必要时重新建立静脉通路",
                "头皮活动区可在重建时使用掩模排除",
                "在报告中注明注射部位和重建参数",
            ],
            "priority": "low",
            "reimbursementNote": "可继续使用，但定量指标需谨慎解读",
        }

    image_quality = {
        "motionArtifact": has_motion_artifact,
        "motionScore": round(motion_score, 2),
        "registrationQuality": registration_quality,  # good / acceptable / poor
        "registrationScore": round(registration_score, 2),
        "scalpActivity": has_scalp_activity,
        "injectionLeak": has_injection_leak,
        "warnings": quality_warnings or ["图像质量良好，可支撑后续定量分析"],
        "recommendation": (
            "图像质量良好，可进行后续定量分析"
            if registration_quality == "good" and not has_motion_artifact
                and not has_scalp_activity and not has_injection_leak
            else "建议结合图像质量评估结论谨慎解读定量指标"
        ),
        "acquisitionAdjustment": acquisition_adjustment,
    }

    # ---------- 2026 版指南：PET 视觉判读征象 ----------
    # 指南推荐意见 5/6/7：Aβ、tau、FDG PET 视觉判读需描述征象与分布
    # 显像剂类型决定 PET 判读征象分支（fdg / amyloid / tau）
    pet_tracer = case_data.get("petTracer", "fdg") or "fdg"
    pet_patterns = _build_pet_patterns(pet_tracer, level, risk_score, regions, m, rand, bio_stage)

    # ---------- 2026 版指南 表2：PET 显像剂推荐参数 ----------
    tracer_parameters = dict(PET_TRACER_PARAMETERS.get(pet_tracer.lower(), PET_TRACER_DEFAULT))

    # ---------- 2026 版指南：检查前准备规则引擎 ----------
    prep_instructions = _build_prep_instructions(pet_tracer, case_data)

    # ---------- 第 26 轮新增：MRI 定量评估工具 + 标准化成像协议 ----------
    mri_quantification = _build_mri_quantification(level, risk_score, m, rand)
    scanner_info = _build_scanner_info(case_data, rand)

    result = {
        "caseId": case_data["id"],
        "riskScore": risk_score,
        "riskLevel": level,
        "stage": st["stage"],
        "stageCode": st["code"],
        "stageDesc": st["desc"],
        "confidence": confidence,
        "hippocampusVolumeL": metrics[0]["value"],
        "hippocampusVolumeR": metrics[1]["value"],
        "meanSUV": metrics[2]["value"],
        "corticalThickness": metrics[3]["value"],
        "ventricleVolume": metrics[4]["value"],
        "mtaScore": str(m["mta"]),
        "abnormalRegions": regions,
        "metrics": metrics,
        "summary": (
            f"多模态融合模型（{model_config.get('snapshotVersion', 'TransMF-15ens-v4')}）{prefix}"
            f"对 {patient.get('name', '未知')}（{gender_text}，{patient.get('age', 65)} 岁）"
            f"的 MRI+PET 影像完成{model_config.get('strategy', 'feature')}级联合分析。"
            f"综合海马体积、皮层厚度及 FDG 代谢分布，{st['desc']}"
            f"AI 综合风险评分 {risk_score}/100，置信度 {confidence * 100:.1f}%，"
            f"建议结合临床量表（MMSE/MoCA）由专科医师审核确认。"
        ),
        "modelVersion": model_config.get("snapshotVersion", "TransMF-15ens-v4"),
        "fusionStrategy": model_config.get("strategy", "feature"),
        "inferenceTime": duration,
        "finishTime": format_date_time(),
        "inferenceSource": inference_source,
        # 2026 版 PET/MRI 指南对齐
        "biologicalStage": bio_stage,
        "biologicalStageDesc": bio_stage_desc,
        "ariaRisk": ariha,
        "imageQuality": image_quality,
        "petPatterns": pet_patterns,
        "petTracer": pet_tracer,
        # 第 25 轮新增：指南表2 + 检查前准备规则
        "tracerParameters": tracer_parameters,
        "prepInstructions": prep_instructions,
        # 第 26 轮新增：MRI 定量评估工具 + 标准化成像协议
        "mriQuantification": mri_quantification,
        "scannerInfo": scanner_info,
    }

    # 真实模型：附加集成推理元信息
    if is_real and ensemble_meta:
        result["ensembleProb"] = ensemble_meta.get("prob")
        result["probMin"] = ensemble_meta.get("probMin")
        result["probMax"] = ensemble_meta.get("probMax")
        result["probStd"] = ensemble_meta.get("probStd")
        result["modelCount"] = ensemble_meta.get("nModels", 75)
        result["device"] = ensemble_meta.get("device", "cpu")

    return result


def _emit_progress(cb, percent, stage):
    """安全调用进度回调"""
    if cb is None:
        return
    try:
        cb(max(0, min(100, int(percent))), stage)
    except Exception as e:
        # 进度回调失败（常见于 SSE 客户端提前断开），不影响推理主流程
        logger.debug("推理进度回调异常（可能客户端已断开）：%s", e)


# 模拟推理的阶段进度（无真实影像时使用）
_SIM_STAGES = [
    (12, "加载 MRI / PET 序列数据…"),
    (30, "影像预处理（偏置场校正 / 强度归一化）…"),
    (52, "MRI-PET 空间配准与融合…"),
    (76, "TransMF 多模态融合模型推理中…"),
    (92, "海马定量与代谢量化分析…"),
]


def build_analysis(case_data: dict, model_config: dict, on_progress=None) -> dict:
    """
    AI 综合分析入口
    - 优先尝试真实 TransMF 模型推理（首次调用时才加载模型）
    - 不可用时使用病例种子 + 历史风险评分生成模拟数据
    - on_progress: 可选回调 (percent:int, stage:str)
    返回结果中 inferenceSource 标识 'real' | 'simulated'
    """
    # 1) 优先尝试真实模型（惰性加载）
    mri = case_data.get("mriPath")
    pet = case_data.get("petPath")
    if _REAL_MODULE is not None and mri and pet and os.path.isfile(mri) and os.path.isfile(pet):
        try:
            if _REAL_MODULE.is_real_model_available():
                logger.info(f"[inference] 使用真实 TransMF 模型推理: MRI={mri}, PET={pet}")
                detail = _REAL_MODULE.infer_case_detail(mri, pet, on_progress=on_progress)
                if detail is not None:
                    risk_score = round(detail["prob"] * 100, 1)
                    _emit_progress(on_progress, 96, "生成结构化分析报告…")
                    return _build_analysis_synthetic(
                        case_data, model_config, risk_score,
                        inference_source="real", ensemble_meta=detail,
                    )
                logger.warning("[inference] 真实模型推理返回 None，回退模拟")
        except Exception as e:
            logger.warning(f"[inference] 真实模型异常，回退模拟: {e}")
    elif mri and pet:
        logger.info("[inference] 病例有影像路径但文件不存在，回退模拟")

    # 2) 回退：模拟推理时序 + 病例种子 / 历史 riskScore
    import time as _time
    for pct, stage in _SIM_STAGES:
        _emit_progress(on_progress, pct, stage)
        _time.sleep(0.12)

    seed = str_seed(case_data["id"])
    rand = mulberry32(seed)
    next(rand)
    base_score = case_data.get("riskScore")
    if base_score is not None:
        risk_score = float(base_score)
    else:
        r = next(rand)
        if r < 0.45:
            risk_score = 12 + int(next(rand) * 22)
        elif r < 0.73:
            risk_score = 38 + int(next(rand) * 22)
        elif r < 0.91:
            risk_score = 62 + int(next(rand) * 18)
        else:
            risk_score = 82 + int(next(rand) * 17)

    _emit_progress(on_progress, 100, "分析完成")
    return _build_analysis_synthetic(
        case_data, model_config, risk_score, inference_source="simulated",
    )


# ---------- 旧记录字段补齐（2026 版指南新增字段） ----------
def enrich_legacy_analysis(legacy_result: dict, case_data: dict, model_config: dict) -> dict:
    """
    给旧版 AnalysisRecord 的 result_json 补齐 2026 版指南新增字段：
    biologicalStage / biologicalStageDesc / ariaRisk / imageQuality / petPatterns / petTracer

    场景：第 23/24 轮指南对齐升级前生成的旧分析记录缺少上述字段，
    前端 v-if="analysis?.biologicalStage" 等条件渲染导致 4 张新卡片不显示。
    本函数用旧记录中的 riskScore 重新走 _build_analysis_synthetic，仅提取 5 个新字段补齐，
    其余字段（用户审核状态、人工标注等）一律保留，保证审计与已审核数据不变。

    - 旧记录其他字段保留
    - 用旧 risk_score 重新生成，确保种子稳定（相同 caseId+risk_score → 相同新字段）
    - in-place 修改 legacy_result 并返回
    """
    if legacy_result.get("biologicalStage") and legacy_result.get("tracerParameters") and legacy_result.get("mriQuantification"):
        return legacy_result
    risk_score = legacy_result.get("riskScore", 50)
    fresh = _build_analysis_synthetic(
        case_data, model_config, risk_score,
        inference_source="simulated", ensemble_meta=None,
    )
    for key in ("biologicalStage", "biologicalStageDesc", "ariaRisk",
                "imageQuality", "petPatterns", "petTracer",
                "tracerParameters", "prepInstructions",
                "mriQuantification", "scannerInfo"):
        if key in fresh:
            legacy_result[key] = fresh[key]
    return legacy_result


# ---------- 干预方案生成 ----------
def build_interventions(case_data: dict) -> list:
    """生成干预方案（四模块）"""
    case_id = case_data["id"]
    # 尝试从 build_analysis 取风险等级
    score = case_data.get("riskScore")
    if score is None:
        seed = str_seed(case_data["id"])
        rand = mulberry32(seed)
        next(rand)
        score = 12 + int(next(rand) * 85)

    level = score_to_risk_level(float(score))
    is_low = level == "low"

    return [
        {
            "key": "cognitive",
            "title": "认知干预",
            "items": [
                {"id": f"{case_id}-cg-1", "title": "认知功能训练", "content": "维持性认知训练：每周 3 次记忆卡片与图形推理练习，每次 30 分钟。" if is_low else "系统性认知训练：记忆策略训练（联想/位置法）联合执行功能任务，每周 5 次，每次 40 分钟，4 周为一疗程。", "frequency": "每周 3 次" if is_low else "每周 5 次", "duration": "30 分钟/次" if is_low else "40 分钟/次", "source": "AI"},
                {"id": f"{case_id}-cg-2", "title": "计算机辅助认知康复", "content": "基于认知康复平台的注意力与工作记忆自适应训练，难度随正确率动态调整。", "frequency": "每周 3 次", "duration": "25 分钟/次", "source": "AI"},
            ],
        },
        {
            "key": "lifestyle",
            "title": "生活方式干预",
            "items": [
                {"id": f"{case_id}-ls-1", "title": "有氧运动方案", "content": "中等强度有氧运动（快走/游泳），靶心率 =（220-年龄）×60%，单次持续 30 分钟以上。", "frequency": "每周 5 次", "duration": "30-40 分钟/次", "source": "AI"},
                {"id": f"{case_id}-ls-2", "title": "地中海饮食指导", "content": "增加深海鱼类、坚果、橄榄油摄入，控制精制糖与饱和脂肪；合并高血压者限盐 < 5g/日。", "frequency": "每日执行", "duration": "长期维持", "source": "AI"},
                {"id": f"{case_id}-ls-3", "title": "睡眠与情绪管理", "content": "维持 22:30 前入睡，保证 6.5-8 小时睡眠；PHQ-9 评分 ≥ 5 时转介心理干预。", "frequency": "每日", "duration": "长期维持", "source": "AI"},
            ],
        },
        {
            "key": "followup",
            "title": "随访复查",
            "items": [
                {"id": f"{case_id}-fu-1", "title": "认知量表随访", "content": "MMSE、MoCA、ADL 量表复评，绘制认知轨迹曲线；MCI 及以上等级每 3 个月复评。", "frequency": "每 12 个月" if is_low else "每 3 个月", "duration": "40 分钟/次", "source": "AI"},
                {"id": f"{case_id}-fu-2", "title": "影像学复查", "content": "年度常规头颅 MRI 复查。" if is_low else "6 个月后复查头颅 MRI（海马定量）± FDG-PET，对比脑萎缩进展速率。", "frequency": "每 12 个月" if is_low else "每 6 个月", "duration": "按检查科室安排", "source": "AI"},
                {"id": f"{case_id}-fu-3", "title": "血液生物标志物", "content": "血浆 Aβ42/40、p-tau181 检测，动态监测 AD 相关病理改变。", "frequency": "每 6 个月", "duration": "空腹采血", "source": "AI"},
            ],
        },
        {
            "key": "clinical",
            "title": "临床参考",
            "items": [
                {"id": f"{case_id}-cl-1", "title": "专科门诊转介", "content": "维持记忆门诊常规随访。" if is_low else "建议神经内科记忆障碍专科门诊就诊，评估胆碱酯酶抑制剂（多奈哌齐/卡巴拉汀）用药指征。", "frequency": "每年 1 次" if is_low else "2 周内就诊", "duration": "按医嘱", "source": "AI"},
                {"id": f"{case_id}-cl-2", "title": "用药安全提示", "content": "避免苯二氮卓类及抗胆碱能药物长期使用；就诊时向医师出示完整用药清单。", "frequency": "持续关注", "duration": "长期", "source": "AI"},
                {"id": f"{case_id}-cl-3", "title": "照护者教育", "content": "向家属发放 AD 照护手册，进行居家安全（防走失/防跌倒）与环境改造指导。", "frequency": "首次宣教 + 随访强化", "duration": "30 分钟/次", "source": "AI"},
            ],
        },
    ]


def build_follow_up() -> dict:
    """生成随访计划"""
    next_dt = datetime.now() + timedelta(days=180)
    next_date = format_date(next_dt)
    reminder_date = format_date(next_dt - timedelta(days=6))
    return {
        "cycleMonths": 6,
        "nextDate": next_date,
        "reminders": [next_date, reminder_date],
        "note": "随访内容：认知量表复评 + 头颅 MRI 海马定量",
    }
