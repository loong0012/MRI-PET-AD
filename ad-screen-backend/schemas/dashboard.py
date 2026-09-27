"""
工作台仪表盘 Pydantic 模型（对应前端 types/dashboard.d.ts）
"""
from typing import Literal

from pydantic import BaseModel


class DashboardStats(BaseModel):
    pendingCases: int
    completedScreening: int
    highRiskCases: int
    todayInference: int
    pendingDelta: int
    completedDelta: int
    highRiskDelta: int
    todayDelta: int


class TrendPoint(BaseModel):
    month: str
    total: int
    highRisk: int


class RiskDistItem(BaseModel):
    name: str
    value: int
    key: str


class TaskItem(BaseModel):
    id: str
    caseId: str
    patientName: str
    type: Literal["初筛分析", "复筛分析", "随访复筛", "报告生成"]
    status: Literal["排队中", "执行中", "已完成", "失败"]
    operator: str
    time: str
