"""
数据字典中心路由（全角色）
------------------------------------------------------------------
面向医护/科研人员，提供医学术语与系统字段的含义说明库。
按模块分类展示（病例/AI分析/随访/报告/影像/风险分级），支持关键词搜索。
便于新医生培训与字段含义查询。

端点：
- GET /dictionary/categories  获取所有分类
- GET /dictionary/items       获取字段列表（可按分类过滤+关键词搜索）
- GET /dictionary/items/{key} 获取单条字段详情
"""
from fastapi import APIRouter, Depends, Query

from schemas.common import ok
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/dictionary",
    tags=["数据字典"],
    dependencies=[Depends(get_current_user_qs)],
)

# ==================== 字典数据（静态知识库） ====================

DICT_ITEMS: list[dict] = [
    # ---------- 病例字段 ----------
    {
        "key": "case_id", "category": "case", "name": "病例编号",
        "type": "string", "example": "AD260001",
        "desc": "系统唯一病例编号，AD+年份+4位流水。用于全院病例检索与追踪。",
        "standard": "院内 HIS 编号规范",
    },
    {
        "key": "patient_no", "category": "case", "name": "患者编号",
        "type": "string", "example": "P-2026-00123",
        "desc": "跨检查的患者唯一编号（同一患者多次就诊归并）。由 HIS 传入或登记时生成。",
        "standard": "院内主索引（EMPI）",
    },
    {
        "key": "modality", "category": "case", "name": "影像模态",
        "type": "enum", "example": "MRI+PET",
        "desc": "本次筛查的影像模态组合。MRI=结构成像（T1 海马定量），PET=功能成像（FDG 代谢）。支持多模态融合分析。",
        "enum": ["MRI", "PET", "MRI+PET"],
    },
    {
        "key": "exam_date", "category": "case", "name": "检查日期",
        "type": "date", "example": "2026-09-14",
        "desc": "影像检查实际执行日期（非报告日期）。用于按月统计筛查量与趋势分析。",
        "standard": "ISO 8601 日期格式",
    },
    {
        "key": "diag_status", "category": "case", "name": "诊断状态",
        "type": "enum", "example": "待AI分析",
        "desc": "病例流转状态：待AI分析 → AI分析中 → 分析完成 → 报告待审 → 报告完成 / 已删除（软删）",
        "enum": ["待AI分析", "AI分析中", "分析完成", "报告待审", "报告完成", "已删除"],
    },
    {
        "key": "department", "category": "case", "name": "送检科室",
        "type": "string", "example": "神经内科",
        "desc": "开具影像检查的临床科室。统计科室筛查量分布，支持科室维度的筛查质量评估。",
    },
    {
        "key": "is_deleted", "category": "case", "name": "软删除标记",
        "type": "boolean", "example": "false",
        "desc": "医疗合规：病例不物理删除，仅标记 is_deleted=true 并保留审计痕迹。所有统计均排除软删病例。",
    },

    # ---------- AI 分析字段 ----------
    {
        "key": "risk_score", "category": "analysis", "name": "AI 风险评分",
        "type": "float", "example": "72.5",
        "desc": "TransMF 多模态融合模型输出的 AD 风险概率（0-100）。融合 MRI 结构特征 + PET 代谢特征。≥70=AD中晚期，55-69=AD早期，30-54=MCI，<30=低风险。",
        "refRange": "0-100（阈值 41.05 为 Youden's J 全局最优）",
    },
    {
        "key": "risk_level", "category": "analysis", "name": "AI 风险等级",
        "type": "enum", "example": "ad-early",
        "desc": "基于风险评分自动分级。low=低风险，mci=轻度认知障碍，ad-early=AD早期，ad-late=AD中晚期。",
        "enum": ["low", "mci", "ad-early", "ad-late"],
    },
    {
        "key": "model_version", "category": "analysis", "name": "模型版本",
        "type": "string", "example": "TransMF-v1.2-ensemble",
        "desc": "本次分析使用的 AI 模型版本标识。快照集成（5 seeds×5 folds×3 snapshots=75 模型概率平均）标记为 ensemble。",
    },
    {
        "key": "fusion_strategy", "category": "analysis", "name": "融合策略",
        "type": "string", "example": "cross-attention",
        "desc": "多模态融合方式：cross-attention=跨模态注意力（TransMF 默认），early-fusion=早期特征拼接，late-fusion=后期 logits 融合。",
    },
    {
        "key": "review_status", "category": "analysis", "name": "审核状态",
        "type": "enum", "example": "pending",
        "desc": "AI 分析结果的医生审核状态：pending=待审核，approved=已通过，rejected=已驳回。每病例取最新版本参与一致率统计。",
        "enum": ["pending", "approved", "rejected"],
    },
    {
        "key": "hv_l", "category": "analysis", "name": "左侧海马体积",
        "type": "float", "example": "2.45",
        "desc": "左侧海马体积（cm³）。正常参考 2.9-3.5，<2.5 提示萎缩。AD 早期即可见海马萎缩。",
        "refRange": "2.9-3.5 cm³",
    },
    {
        "key": "hv_r", "category": "analysis", "name": "右侧海马体积",
        "type": "float", "example": "2.50",
        "desc": "右侧海马体积（cm³）。正常参考 2.95-3.55，<2.55 提示萎缩。左右不对称 >0.3cm³ 需关注。",
        "refRange": "2.95-3.55 cm³",
    },
    {
        "key": "suv", "category": "analysis", "name": "FDG SUV",
        "type": "float", "example": "0.85",
        "desc": "FDG-PET 标准摄取值（颞顶叶皮层）。正常 1.0-1.3，<0.9 提示代谢减低。AD 早期后扣带回/楔前叶代谢减低。",
        "refRange": "1.0-1.3",
    },
    {
        "key": "cort", "category": "analysis", "name": "皮层厚度",
        "type": "float", "example": "2.45",
        "desc": "全脑平均皮层厚度（mm）。正常 2.5-3.0，进行性变薄提示神经退变。",
        "refRange": "2.5-3.0 mm",
    },
    {
        "key": "mta", "category": "analysis", "name": "MTA 评分",
        "type": "int", "example": "2",
        "desc": "内侧颞叶萎缩视觉评分（Scheltens 量表 0-4）。0=正常，1-2=轻度，3-4=明显萎缩。年龄校正：<75岁≥2异常，≥75岁≥3异常。",
        "refRange": "0-4（年龄校正）",
    },
    {
        "key": "wmh", "category": "analysis", "name": "白质高信号",
        "type": "int", "example": "1",
        "desc": "脑白质高信号 Fazekas 评分（0-3）。0=无，1=点状，2=开始融合，3=大片融合。≥2 提示血管性负担。",
        "refRange": "0-3",
    },

    # ---------- 随访字段 ----------
    {
        "key": "mmse", "category": "followup", "name": "MMSE 评分",
        "type": "float", "example": "24",
        "desc": "简易精神状态检查（0-30）。24-30=正常，18-23=轻度，10-17=中度，<10=重度。AD 筛查首选量表。",
        "refRange": "24-30（<24 提示认知障碍）",
    },
    {
        "key": "moca", "category": "followup", "name": "MoCA 评分",
        "type": "float", "example": "22",
        "desc": "蒙特利尔认知评估（0-30，教育年限+1 校正）。<26 提示认知障碍。对 MCI 敏感度高于 MMSE。",
        "refRange": "≥26（<22 明显障碍）",
    },
    {
        "key": "cognition_change", "category": "followup", "name": "认知变化",
        "type": "enum", "example": "轻度下降",
        "desc": "本次随访 vs 上次的认知变化趋势。改善/稳定/轻度下降/明显下降。年化下降速率 ≥3 分/年需警惕。",
        "enum": ["改善", "稳定", "轻度下降", "明显下降"],
    },
    {
        "key": "medication_adherence", "category": "followup", "name": "用药依从性",
        "type": "enum", "example": "规律",
        "desc": "抗痴呆药物服药依从性。规律/偶有漏服/经常漏服/已停药。依从性差影响治疗效果，需药学干预。",
        "enum": ["规律", "偶有漏服", "经常漏服", "已停药"],
    },
    {
        "key": "cycle_months", "category": "followup", "name": "随访周期",
        "type": "int", "example": "3",
        "desc": "随访间隔（月）。AD=3 个月，MCI=6 个月，低风险=12 个月。系统自动根据风险等级推荐。",
        "enum": [3, 6, 12],
    },
    {
        "key": "visit_type", "category": "followup", "name": "随访方式",
        "type": "enum", "example": "门诊复诊",
        "desc": "随访执行方式。门诊复诊=线下量表评估，电话随访=远程问询，影像复查=MRI/PET 复查，线上随访=APP/视频。",
        "enum": ["门诊复诊", "电话随访", "影像复查", "线上随访"],
    },

    # ---------- 报告字段 ----------
    {
        "key": "report_template", "category": "report", "name": "报告模板",
        "type": "enum", "example": "standard",
        "desc": "筛查报告模板：标准=完整影像+量化+建议，简明=核心结论+关键指标，科研=量化数据+统计格式。",
        "enum": ["standard", "concise", "research"],
    },
    {
        "key": "cloud_saved", "category": "report", "name": "云存档",
        "type": "boolean", "example": "true",
        "desc": "报告是否已上传云端存档。医疗合规要求报告双备份（本地+云端），云存档支持院间调阅。",
    },

    # ---------- 影像字段 ----------
    {
        "key": "has_mri", "category": "imaging", "name": "MRI 可用",
        "type": "boolean", "example": "true",
        "desc": "是否已上传 MRI 影像（T1 加权 3D）。TransMF 要求 128³ 体素，分辨率 1.5mm 各向同性。",
    },
    {
        "key": "has_pet", "category": "imaging", "name": "PET 可用",
        "type": "boolean", "example": "true",
        "desc": "是否已上传 FDG-PET 影像。PET 提供脑代谢信息，与 MRI 结构信息互补。单模态分析精度低于多模态融合。",
    },
    {
        "key": "mri_path", "category": "imaging", "name": "MRI 文件路径",
        "type": "string", "example": "/data/mri/AD260001.nii.gz",
        "desc": "MRI NIfTI 文件存储路径。后端校验文件存在性，质控规则 file-missing 会标记缺失文件为 high 级问题。",
    },
    {
        "key": "pet_path", "category": "imaging", "name": "PET 文件路径",
        "type": "string", "example": "/data/pet/AD260001.nii.gz",
        "desc": "PET NIfTI 文件存储路径。Mnet 模型要求 (91,109,91) 分辨率，TransMF 支持 (128,128,128)。",
    },

    # ---------- CDSS 决策因子 ----------
    {
        "key": "cdss_priority", "category": "cdss", "name": "CDSS 优先级",
        "type": "enum", "example": "high",
        "desc": "临床决策支持系统输出的处置优先级。urgent=紧急介入，high=1周内，moderate=1月内，low=常规。基于 AI 评分+MMSE+认知趋势+年龄综合判定。",
        "enum": ["urgent", "high", "moderate", "low"],
    },
    {
        "key": "cdss_modifier", "category": "cdss", "name": "CDSS 修正因子",
        "type": "string", "example": "mmse_severe",
        "desc": "在基础分级上叠加的修正因子：MMSE 重度/轻度障碍、认知明显/轻度下降、高龄（≥75）、用药依从性差、MoCA 偏低。每命中一个修正因子，风险等级可能升一级。",
    },
]

CATEGORY_META = [
    {"key": "case",      "name": "病例字段",   "icon": "Notebook",      "desc": "病例基本信息与流转状态"},
    {"key": "analysis",  "name": "AI 分析",    "icon": "DataAnalysis",  "desc": "AI 风险评分、量化指标与模型版本"},
    {"key": "followup",  "name": "随访管理",   "icon": "Calendar",      "desc": "认知量表、变化趋势与依从性"},
    {"key": "report",    "name": "筛查报告",   "icon": "Document",      "desc": "报告模板与存档状态"},
    {"key": "imaging",   "name": "影像元数据", "icon": "Picture",       "desc": "MRI/PET 文件与分辨率"},
    {"key": "cdss",      "name": "CDSS 决策",  "icon": "Connection",    "desc": "临床决策支持规则与修正因子"},
]


@router.get("/categories")
def get_categories():
    """获取所有分类"""
    return ok(CATEGORY_META)


@router.get("/items")
def list_items(
    category: str = Query(default=""),
    keyword: str = Query(default=""),
):
    """字段列表（可按分类+关键词过滤）"""
    kw = keyword.strip().lower()
    items = []
    for it in DICT_ITEMS:
        if category and it["category"] != category:
            continue
        if kw:
            haystack = f"{it['name']} {it['key']} {it.get('desc','')}".lower()
            if kw not in haystack:
                continue
        items.append(it)
    return ok(items)


@router.get("/items/{key}")
def get_item(key: str):
    """单条字段详情"""
    for it in DICT_ITEMS:
        if it["key"] == key:
            return ok(it)
    return ok(None, "字段不存在")
