"""
站内通知 ORM 模型
- NotificationRecord：顶栏通知中心消息（随访到期提醒 / 审核待办 / 系统公告）
  由后端在查询时按当前用户角色**懒生成**（dedup_key 幂等去重），避免定时任务依赖
"""
from sqlalchemy import Column, Integer, String, Text, Boolean

from database import Base


class NotificationRecord(Base):
    __tablename__ = "notification_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(60), nullable=False, index=True)  # 接收人账号
    ntype = Column(String(30), default="system")               # followup / review / system / announce
    title = Column(String(120), default="")
    content = Column(Text, default="")
    link = Column(String(200), default="")                     # 前端跳转路由
    dedup_key = Column(String(150), default="", index=True)    # 幂等去重键（类型:关联ID:日期）
    is_read = Column(Boolean, default=False)
    created_at = Column(String(30), default="")


class AnnouncementRecord(Base):
    """系统公告主记录（admin 发布，发布时为全部用户生成通知）"""
    __tablename__ = "announcement_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(120), default="")
    content = Column(Text, default="")
    publisher = Column(String(60), default="")
    is_active = Column(Boolean, default=True)                  # False=已下线
    created_at = Column(String(30), default="")
    revoked_at = Column(String(30), default="")
