"""
病例收藏 ORM 模型
- CaseFavorite：用户-病例 收藏关系（按用户独立保存，仅自己可见）
"""
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from datetime import datetime

from database import Base


class CaseFavorite(Base):
    __tablename__ = "case_favorites"
    __table_args__ = (
        UniqueConstraint("username", "case_id", name="uq_user_case_fav"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, index=True)
    case_id = Column(String(30), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now)
