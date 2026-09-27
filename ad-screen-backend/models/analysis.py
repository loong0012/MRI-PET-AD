"""
AI 分析结果 ORM 模型
- AnalysisRecord：完整分析结果（JSON 存储嵌套字段）
- InterventionSection：干预方案（按模块拆行存储）
- FollowUpPlan：随访计划
"""
from sqlalchemy import Column, Integer, String, Text, Float

from database import Base


class AnalysisRecord(Base):
    """AI 分析结果（一个病例一条）"""
    __tablename__ = "analysis_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(30), unique=True, nullable=False, index=True)
    result_json = Column(Text, default="{}")  # 完整 AnalysisResult 序列化


class InterventionRecord(Base):
    """干预方案（一个病例一条，JSON 存储全部模块）"""
    __tablename__ = "intervention_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(30), unique=True, nullable=False, index=True)
    sections_json = Column(Text, default="[]")  # InterventionSection[] 序列化


class FollowUpRecord(Base):
    """随访计划（一个病例一条）"""
    __tablename__ = "followup_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(30), unique=True, nullable=False, index=True)
    plan_json = Column(Text, default="{}")  # FollowUpPlan 序列化


class FollowUpVisit(Base):
    """
    随访执行记录（一个病例可有多条，按随访日期倒序展示）
    记录每次复诊/电话随访的认知量表、认知变化趋势、用药依从性等临床信息。
    """
    __tablename__ = "followup_visits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(30), nullable=False, index=True)
    patient_name = Column(String(60), default="")
    visit_date = Column(String(30), nullable=False, index=True)   # 本次随访日期 YYYY-MM-DD
    visit_type = Column(String(20), default="门诊复诊")           # 门诊复诊 / 电话随访 / 影像复查 / 线上随访
    mmse = Column(Float, nullable=True)                           # MMSE 评分 0-30
    moca = Column(Float, nullable=True)                           # MoCA 评分 0-30
    cognition_change = Column(String(20), default="稳定")          # 改善 / 稳定 / 轻度下降 / 明显下降
    medication_adherence = Column(String(20), default="规律")     # 规律 / 偶有漏服 / 经常漏服 / 已停药
    adverse_event = Column(String(20), default="无")              # 无 / 轻微 / 需处理
    notes = Column(Text, default="")                              # 随访备注（症状/家属反馈/处置）
    next_date = Column(String(30), default="")                    # 约定的下次随访日期
    operator = Column(String(60), default="")
    created_at = Column(String(30), default="")



class AnalysisVersionRecord(Base):
    """
    分析结果历史版本（每次 AI 分析/保存追加一条，支持多人协作审核）
    - review_status: pending(待审核) / approved(已通过) / rejected(已驳回)
    """
    __tablename__ = "analysis_version_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(30), nullable=False, index=True)
    version = Column(Integer, nullable=False)                 # 版本号（病例内自增）
    result_json = Column(Text, default="{}")
    model_version = Column(String(60), default="")
    fusion_strategy = Column(String(30), default="")
    risk_level = Column(String(20), default="")
    risk_score = Column(Float, default=0.0)
    source = Column(String(20), default="simulated")         # real / simulated
    operator = Column(String(60), default="rad01")
    created_at = Column(String(30), default="")
    review_status = Column(String(20), default="pending")
    reviewer = Column(String(60), default="")
    review_comment = Column(Text, default="")
    reviewed_at = Column(String(30), default="")
