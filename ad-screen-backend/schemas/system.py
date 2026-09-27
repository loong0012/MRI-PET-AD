"""
系统日志 Pydantic 模型（对应前端 types/system.d.ts）
"""
from typing import Literal

from pydantic import BaseModel


class SystemLog(BaseModel):
    id: str
    module: str
    action: str
    operator: str
    role: str
    ip: str
    result: Literal["成功", "失败"]
    time: str
    detail: str

    model_config = {"from_attributes": True}


class CaseLog(BaseModel):
    id: str
    caseId: str
    patientName: str
    action: str
    operator: str
    time: str
    detail: str

    model_config = {"from_attributes": True}
