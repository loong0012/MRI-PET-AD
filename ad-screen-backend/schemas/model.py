"""
模型科研配置 Pydantic 模型（对应前端 types/model.d.ts）
"""
from typing import Literal

from pydantic import BaseModel, Field

FusionStrategy = Literal["feature", "pixel"]


class ModelConfig(BaseModel):
    strategy: FusionStrategy
    # 模态权重为 0-1 比例；风险阈值为 0-1 概率（前端滑杆 0.3-0.8）。
    # ge/le 边界比较对 NaN 恒为 False，因此同时拒绝 NaN/Infinity 等非有限值，
    # 防止越界配置下发推理引擎导致全部病例风险分级错误。
    mriWeight: float = Field(ge=0, le=1)
    riskThreshold: float = Field(ge=0, le=1)
    # ROI 脑区列表限长，防止异常超长配置入库
    roiRegions: list[str] = Field(default_factory=list, max_length=20)
    snapshotVersion: str
    updatedAt: str
    updatedBy: str

    model_config = {"from_attributes": True}


class FoldMetric(BaseModel):
    fold: int
    auc: float
    acc: float
    sen: float
    spe: float
    f1: float


class TrainCurvePoint(BaseModel):
    epoch: int
    trainLoss: float
    valAuc: float


class InferenceLog(BaseModel):
    id: str
    caseId: str
    patientName: str
    modelVersion: str
    strategy: FusionStrategy
    durationSec: float
    riskScore: float
    status: Literal["成功", "失败"]
    operator: str
    time: str

    model_config = {"from_attributes": True}
