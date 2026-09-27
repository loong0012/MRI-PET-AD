"""
病例评论/会诊讨论 ORM 模型
- CaseComment：病例下的评论记录（支持回复）
"""
from sqlalchemy import Column, Integer, String, Text

from database import Base


class CaseComment(Base):
    """病例评论（支持回复，parent_id 指向被回复的评论 id）"""
    __tablename__ = "case_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(30), nullable=False, index=True)
    user = Column(String(60), default="")           # 用户名
    real_name = Column(String(60), default="")        # 真实姓名
    content = Column(Text, default="")                 # 评论内容
    parent_id = Column(Integer, default=0)             # 回复的评论 id（0=顶级评论）
    created_at = Column(String(30), default="")
