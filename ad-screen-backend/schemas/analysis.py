"""
AI 分析结果 Pydantic 模型（对应前端 types/analysis.d.ts）
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from schemas.case import RiskLevel


class AbnormalRegion(BaseModel):
    region: str
    side: Literal["左侧", "右侧", "双侧"]
    atrophy: Literal["轻度萎缩", "中度萎缩", "重度萎缩"]
    metabolism: float
    zScore: float


class QuantMetric(BaseModel):
    key: str
    label: str
    value: float
    unit: str
    refRange: str
    status: Literal["normal", "warn", "abnormal"]


class AnalysisResult(BaseModel):
    caseId: str
    riskScore: float
    riskLevel: RiskLevel
    stage: str
    stageCode: Literal["CN", "MCI", "AD-E", "AD-L"]
    stageDesc: str
    confidence: float
    hippocampusVolumeL: float
    hippocampusVolumeR: float
    meanSUV: float
    corticalThickness: float
    ventricleVolume: float
    mtaScore: str
    abnormalRegions: list[AbnormalRegion]
    metrics: list[QuantMetric]
    summary: str
    modelVersion: str
    fusionStrategy: Literal["feature", "pixel"]
    inferenceTime: float
    finishTime: str


InterventionKey = Literal["cognitive", "lifestyle", "followup", "clinical"]


class InterventionItem(BaseModel):
    id: str
    title: str
    content: str
    frequency: str
    duration: str
    source: Literal["AI", "医生"]


class InterventionSection(BaseModel):
    key: InterventionKey
    title: str
    items: list[InterventionItem]


class FollowUpPlan(BaseModel):
    # 随访周期限定 1-24 个月（标准 AD:3/MCI:6/低风险:12，允许医生在此范围内自定义）
    cycleMonths: int = Field(ge=1, le=24)
    nextDate: str
    reminders: list[str]
    note: str


class FollowUpVisitIn(BaseModel):
    """随访执行记录入参"""
    visitDate: str
    visitType: str = "门诊复诊"
    # MMSE/MoCA 量表满分 30：越界值（如 999/-5）会污染预警/预后/CDSS/统计下游，必须拒绝
    mmse: Optional[float] = Field(default=None, ge=0, le=30)
    moca: Optional[float] = Field(default=None, ge=0, le=30)
    cognitionChange: str = "稳定"
    medicationAdherence: str = "规律"
    adverseEvent: str = "无"
    notes: str = ""
    nextDate: str = ""
    operator: str = ""

