"""
病例 Pydantic 模型（对应前端 types/case.d.ts）
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Modality = Literal["MRI", "PET", "MRI+PET"]
RiskLevel = Literal["low", "mci", "ad-early", "ad-late"]
CaseStatus = Literal["pending", "analyzing", "completed", "reported"]
DiagStatus = Literal["待AI分析", "AI分析中", "AI已分析", "医生已审核", "已出报告"]


class CasePatient(BaseModel):
    patientNo: str
    name: str
    gender: Literal["M", "F"]
    age: int


class CaseRecord(BaseModel):
    id: str
    patient: CasePatient
    modality: Modality
    examDate: str
    department: str
    status: CaseStatus
    diagStatus: DiagStatus
    riskLevel: Optional[RiskLevel] = None
    riskScore: Optional[float] = None
    hasMRI: bool
    hasPET: bool
    createTime: str
    cloudSaved: bool

    model_config = {"from_attributes": True}


class CaseQuery(BaseModel):
    keyword: str = ""
    modality: str = ""
    status: str = ""
    riskLevel: str = ""
    diagStatus: str = ""
    department: str = ""
    gender: str = ""
    ageMin: Optional[int] = None
    ageMax: Optional[int] = None
    hasFollowup: Optional[bool] = None
    favorites: Optional[bool] = None  # 仅查当前用户收藏的病例
    dateRange: Optional[list[str]] = None
    page: int = Field(default=1, ge=1)
    # 上限 100：防止恶意超大分页一次拖取全库（与其它列表端点 le=100 保持一致）
    pageSize: int = Field(default=10, ge=1, le=100)
