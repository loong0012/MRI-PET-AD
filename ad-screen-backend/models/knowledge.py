"""
患者教育/科普知识库 ORM 模型
- KnowledgeArticle：科普文章
"""
from sqlalchemy import Column, Integer, String, Text, Boolean

from database import Base


class KnowledgeArticle(Base):
    """科普文章（按 category 分类）"""
    __tablename__ = "knowledge_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(30), default="")        # 疾病概述/早期症状/诊断方法/治疗方案/护理建议/预防措施
    title = Column(String(120), nullable=False)
    summary = Column(String(300), default="")         # 摘要
    content = Column(Text, default="")                 # 正文（Markdown 格式）
    tags = Column(String(200), default="")             # 逗号分隔标签
    icon = Column(String(30), default="")              # 图标名
    sort_order = Column(Integer, default=0)            # 排序
    is_published = Column(Boolean, default=True)       # 是否发布
    created_at = Column(String(30), default="")
