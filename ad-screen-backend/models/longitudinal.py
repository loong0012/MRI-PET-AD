"""
纵向影像量化 ORM 模型
- LongitudinalMetric：按 patient_no 聚合同一患者多期影像的量化指标与年化变化率
- 字段派生自 AnalysisResult（hippocampusVolumeL/R / meanSUV / corticalThickness /
  ventricleVolume / mtaScore），避免与 build_analysis 输出全量复制
- 单期患者：annual_rate_* 为 null，niaaa_stage = "baseline" 待随访
"""
from sqlalchemy import Column, Integer, String, Float, Text

from database import Base


class LongitudinalMetric(Base):
    __tablename__ = "longitudinal_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 关联键：patient_no 跨多期聚合，case_id 对应单期 CaseRecord
    patient_no = Column(String(60), index=True, nullable=False)
    case_id = Column(String(30), index=True, nullable=False)
    exam_date = Column(String(30), default="")  # yyyy-mm-dd
    timepoint_idx = Column(Integer, default=0)  # 该患者第几期（0,1,2...）

    # 量化快照（派生自 AnalysisResult，缺指标时为 null）
    hippocampus_vol_l = Column(Float, nullable=True)  # cm³
    hippocampus_vol_r = Column(Float, nullable=True)
    mean_suv = Column(Float, nullable=True)  # SUV
    cortical_thickness = Column(Float, nullable=True)  # mm
    ventricle_vol = Column(Float, nullable=True)  # cm³
    mta_score = Column(String(10), nullable=True)  # 0-3 级

    # 年化变化率（单期为 null；多期取最近一对相邻期）
    annual_rate_hv = Column(Float, nullable=True)  # Δ cm³/年
    annual_rate_suv = Column(Float, nullable=True)  # Δ SUV/年
    annual_rate_cort = Column(Float, nullable=True)  # Δ mm/年

    # NIA-AA 纵向进展分级
    niaaa_stage = Column(String(20), nullable=True)  # CN / MCI / AD-E / AD-L / baseline
    progression_note = Column(Text, default="")  # 备注：间隔过短/指标缺失等

    compute_time = Column(String(30), default="")  # 计算时间戳
