"""
报告 Pydantic 模型（对应前端 types/report.d.ts）
"""
from typing import Literal

from pydantic import BaseModel

from schemas.case import CaseRecord
from schemas.analysis import AnalysisResult, InterventionSection, FollowUpPlan


class ReportTemplate(BaseModel):
    key: Literal["standard", "brief", "research"]
    name: str
    desc: str


class ReportData(BaseModel):
    caseInfo: CaseRecord
    analysis: AnalysisResult
    interventions: list[InterventionSection]
    followUp: FollowUpPlan
    reportNo: str
    reportDate: str
    hospital: str
    doctorName: str
