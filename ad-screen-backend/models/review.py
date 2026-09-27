"""
报告会签审核 ORM 模型
- ReportReview：报告会签审核记录（一个病例可有多条，按签字时间倒序展示）

状态机：
  draft → submit → pending_review
  pending_review → decision(approve/sign) → in_review (1签)
                 → decision(approve/sign) → signed (2签，触发 reported)
                 → decision(reject) → rejected
  in_review → decision(reject) → rejected
  rejected → submit → pending_review (重新提交)
  pending_review/in_review → withdraw → draft

签发条件：
  - approve 或 sign 计入签字数
  - 签字数 ≥2 且至少 1 名 reviewer_role="radiologist" → 签发
  - 签发后更新 CaseRecord.status="reported", diag_status="已出报告"
"""
from sqlalchemy import Column, Integer, String, Text

from database import Base


class ReportReview(Base):
    """报告会签审核记录"""
    __tablename__ = "report_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(30), nullable=False, index=True)
    version_id = Column(Integer, nullable=True)  # 关联 AnalysisVersionRecord.id
    reviewer_username = Column(String(60), nullable=False)  # 审核人账号
    reviewer_name = Column(String(60), default="")  # 审核人姓名
    reviewer_role = Column(String(30), default="")  # radiologist / neurologist / admin
    decision = Column(String(20), nullable=False)  # approve / reject / sign / submit / withdraw
    comment = Column(Text, default="")  # 审核意见
    signed_at = Column(String(30), default="")  # 签字时间
