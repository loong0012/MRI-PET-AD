"""
报告模板 ORM 模型
- ReportTemplate：筛查报告模板（多套可切换：简版 / 详版 / 科研版）
- 字段显隐配置 content_structure（JSON）+ 医院抬头 header_config（JSON）
- 通过 is_default 标识当前默认模板，前端首次进入报告页时使用
"""
from sqlalchemy import Column, Integer, String, Text, Boolean

from database import Base


class ReportTemplate(Base):
    """报告模板（简版/详版/科研版，字段可配置）"""
    __tablename__ = "report_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(60), nullable=False)                    # 模板名称
    template_type = Column(String(20), default="detailed")       # brief / detailed / research / patient
    # content_structure 默认 JSON：13 个字段显隐开关
    # 10 个基础字段 + 3 个新增字段（NIA-AA 框架对齐 / 矛盾证据 / 患者通俗版）
    content_structure = Column(
        Text,
        default='{"patientInfo": true, "examInfo": true, "aiResult": true, '
                 '"imagingDesc": true, "riskStratification": true, '
                 '"interventionAdvice": true, "followupPlan": true, '
                 '"gradcamImage": false, "brainMetrics": false, "disclaimer": true, '
                 '"niaaAlignment": false, "conflictEvidence": false, "patientFriendly": false}'
    )                                                            # JSON: 字段显隐配置（13 字段）
    header_config = Column(Text, default="{}")                  # JSON: {hospitalName, department, logoUrl, title}
    is_default = Column(Boolean, default=False)                 # 是否默认模板
    created_by = Column(String(60), default="")
    created_at = Column(String(30), default="")
    updated_at = Column(String(30), default="")
