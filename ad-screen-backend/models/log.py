"""
审计日志 ORM 模型
- InferenceLog：AI 推理日志
- SystemLog：系统操作日志
- CaseLog：病例操作记录
"""
from sqlalchemy import Column, String, Text, Float

from database import Base


class InferenceLog(Base):
    __tablename__ = "inference_logs"

    id = Column(String(30), primary_key=True)
    case_id = Column(String(30), nullable=False, index=True)
    patient_name = Column(String(50), default="")
    model_version = Column(String(50), default="")
    strategy = Column(String(20), default="feature")
    duration_sec = Column(Float, default=0.0)
    risk_score = Column(Float, default=0.0)
    status = Column(String(10), default="成功")  # 成功 / 失败
    source = Column(String(12), default="simulated")  # real（真实模型）/ simulated（模拟）
    operator = Column(String(50), default="")
    time = Column(String(30), default="")


class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(String(30), primary_key=True)
    module = Column(String(50), default="")
    action = Column(String(100), default="")
    operator = Column(String(50), default="")
    role = Column(String(50), default="")
    ip = Column(String(50), default="")
    result = Column(String(10), default="成功")
    time = Column(String(30), default="")
    detail = Column(Text, default="")


class CaseLog(Base):
    __tablename__ = "case_logs"

    id = Column(String(30), primary_key=True)
    case_id = Column(String(30), default="")
    patient_name = Column(String(50), default="")
    action = Column(String(100), default="")
    operator = Column(String(50), default="")
    time = Column(String(30), default="")
    detail = Column(Text, default="")
