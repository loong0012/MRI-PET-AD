"""
高危预警处置记录 ORM 模型
- WarningHandling：预警处置闭环（标记已处理 / 忽略 / 重新打开）
- dedup_key 规则：{预警类型}|{病例编号}，如 followup-overdue|AD260001
  预警本身由随访/分析数据实时计算，处置状态独立持久化，与业务数据解耦。
"""
from sqlalchemy import Column, Integer, String, Text

from database import Base


class WarningHandling(Base):
    __tablename__ = "warning_handlings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_key = Column(String(120), unique=True, nullable=False, index=True)
    status = Column(String(20), default="handled")   # handled(已处理) / ignored(已忽略) / open(重新打开)
    note = Column(Text, default="")
    handler = Column(String(60), default="")
    handled_at = Column(String(30), default="")
