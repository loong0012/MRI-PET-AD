"""
模型配置 ORM 模型
- ModelConfig：单行配置（ID 固定为 1）
"""
from sqlalchemy import Column, Integer, String, Text, Float

from database import Base


class ModelConfigRecord(Base):
    __tablename__ = "model_config"

    id = Column(Integer, primary_key=True, default=1)  # 固定单行
    strategy = Column(String(20), default="feature")  # feature / pixel
    mri_weight = Column(Float, default=0.55)
    risk_threshold = Column(Float, default=0.8)
    roi_regions = Column(Text, default="[]")  # JSON 字符串
    snapshot_version = Column(String(50), default="TransMF-15ens-v4")
    updated_at = Column(String(30), default="")
    updated_by = Column(String(50), default="")
