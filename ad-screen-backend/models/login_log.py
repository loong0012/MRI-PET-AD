"""
登录日志 ORM 模型
- LoginRecord：记录每次登录尝试（成功/失败），用于安全审计
"""
from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

from database import Base


class LoginRecord(Base):
    __tablename__ = "login_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, index=True)
    real_name = Column(String(64), default="")
    success = Column(Integer, default=1)  # 1=成功 0=失败
    fail_reason = Column(String(100), default="")  # 密码错误/账号禁用/待审核等
    ip = Column(String(50), default="")
    user_agent = Column(String(300), default="")
    login_at = Column(DateTime, default=datetime.now, index=True)
