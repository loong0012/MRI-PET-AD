"""
影像 ROI 标注 ORM 模型
- AnnotationRecord：单病例单切片的 ROI 标注记录
"""
from sqlalchemy import Column, Integer, String, Text, Float

from database import Base


class AnnotationRecord(Base):
    """ROI 标注记录（一个病例可有多条，按创建时间倒序展示）"""
    __tablename__ = "annotation_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(30), nullable=False, index=True)
    user = Column(String(60), default="")           # 标注用户
    slice_index = Column(Integer, default=0)          # 切片索引
    modality = Column(String(20), default="MRI")      # MRI / PET
    roi_type = Column(String(20), default="sphere")  # sphere / rect / polygon
    coords = Column(Text, default="{}")               # JSON: sphere={cx,cy,r}, rect={x,y,w,h}, polygon={points:[{x,y}...]}
    label = Column(String(60), default="")            # 海马体/内嗅皮层/自定义
    area = Column(Float, default=0.0)                  # 像素面积（自动计算）
    notes = Column(Text, default="")                   # 标注备注
    created_at = Column(String(30), default="")
