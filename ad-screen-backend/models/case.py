"""
病例 ORM 模型
- CaseRecord：病例主表，存储患者信息与影像元数据
- 嵌套字段（patient / riskLevel 等）用 JSON 字段存储
"""
from sqlalchemy import Column, String, Text, Boolean, Float

from database import Base


class CaseRecord(Base):
    __tablename__ = "case_records"

    id = Column(String(30), primary_key=True)  # AD260001 格式
    patient_json = Column(Text, default="{}")  # { patientNo, name, gender, age }
    modality = Column(String(20), nullable=False)
    exam_date = Column(String(30), nullable=False)
    department = Column(String(100), default="")
    status = Column(String(20), default="pending")  # pending / analyzing / completed / reported
    diag_status = Column(String(20), default="待AI分析")
    risk_level = Column(String(20), nullable=True)  # low / mci / ad-early / ad-late
    risk_score = Column(Float, nullable=True)
    has_mri = Column(Boolean, default=False)
    has_pet = Column(Boolean, default=False)
    create_time = Column(String(30), default="")
    cloud_saved = Column(Boolean, default=False)
    # 软删除标记（医疗合规：不物理删除，保留审计痕迹）
    is_deleted = Column(Boolean, default=False)
    # 影像文件存储路径（DICOM 上传时保存）
    mri_path = Column(String(500), default="")
    pet_path = Column(String(500), default="")
    # 多中心队列标识（ADNI1/ADNI2/ADNI3/UNKNOWN），用于队列对比分析
    cohort = Column(String(60), default="UNKNOWN")
    # 扫描仪信息（JSON 字符串：{field_strength, manufacturer}），用于检测扫描仪捷径学习
    scanner_info = Column(String(200), default="")
    # DICOM 元数据 JSON（首个 .dcm 抽取的 12 个标准标签，camelCase 后存这里）
    # database.py 的 _migrate_columns 已配置该列迁移，表里已有此列
    dicom_meta = Column(Text, default="{}")
    # 检查适应证（2026 版 PET/MRI 指南四类）：diagnosis / staging / pre_dmt / dmt_monitoring
    exam_indication = Column(String(30), default="diagnosis")
    # PET 显像剂类型（指南表2）：fdg / amyloid / tau
    pet_tracer = Column(String(20), default="fdg")
    # 禁忌证筛查 JSON（指南"检查前评估"）：
    # {"absolute": [...], "relative": [...], "cleared": bool}
    # 绝对禁忌：起搏器/除颤器/人工耳蜗/DBS 等有源植入物
    # 相对禁忌：钢钉/钢板/人工关节/动脉瘤夹/冠脉支架等金属植入物
    contraindications = Column(Text, default="{}")
    # 特殊人群标记（指南"检查前准备"）：
    # normal / down_synodrome / claustrophobia / diabetes / implanted_device
    special_population = Column(String(30), default="normal")
