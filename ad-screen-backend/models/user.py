"""
用户 ORM 模型
- User：系统账号
- RolePermission：角色权限点配置
"""
from sqlalchemy import Column, Integer, String, Text

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False, default="")
    real_name = Column(String(50), nullable=False)
    role = Column(String(20), nullable=False)  # radiologist / neurologist / researcher / admin
    role_name = Column(String(50), nullable=False)
    department = Column(String(100), default="")
    phone = Column(String(20), default="")
    email = Column(String(100), default="")          # 联系邮箱（临床协作/报告推送）
    title = Column(String(50), default="")           # 职称（主任医师/主治医师等）
    avatar = Column(Text, default="")                # 头像（base64 data URL）
    signature = Column(Text, default="")             # 个人简介/临床专长
    status = Column(Integer, default=1)  # 1 启用 / 0 禁用 / 2 待审核（注册审核流程）
    # 令牌版本号：登出/改密/被禁用时自增，使此前签发的 JWT 立即失效（无状态 JWT 的会话失效方案）
    token_version = Column(Integer, default=0, nullable=False, server_default="0")
    create_time = Column(String(30), default="")
    last_login_time = Column(String(30), default="—")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(20), unique=True, nullable=False)
    role_name = Column(String(50), nullable=False)
    permission_keys = Column(Text, default="[]")  # JSON 字符串
