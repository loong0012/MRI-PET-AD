"""
pytest 全局 fixture
- db: 临时内存 SQLite Session，建表后回填测试数据，测试结束自动销毁
- 不依赖 services.imaging_service（避免 numpy 在 Windows + Py3.13 上的崩溃）
"""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 复用项目 Base：必须先导入所有 model 让 metadata 注册全部表
from database import Base
import models.user  # noqa: F401
import models.case  # noqa: F401
import models.analysis  # noqa: F401
import models.model_config  # noqa: F401
import models.log  # noqa: F401
import models.favorite  # noqa: F401
import models.login_log  # noqa: F401
import models.warning  # noqa: F401
import models.report_template  # noqa: F401
import models.annotation  # noqa: F401
import models.case_comment  # noqa: F401
import models.knowledge  # noqa: F401
import models.review  # noqa: F401
import models.longitudinal  # noqa: F401
# 具名导入：供下方工厂函数的类型注解使用（避免前向引用字符串注解报未定义名）
from models.case import CaseRecord  # noqa: F401
from models.analysis import AnalysisRecord  # noqa: F401


@pytest.fixture
def db():
    """内存 SQLite Session：每测试一个独立数据库，互不污染"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_case(case_id: str, patient_no: str, exam_date: str,
              has_mri: bool = True, has_pet: bool = True) -> "CaseRecord":
    """构造测试 CaseRecord（未持久化，由调用方 db.add）"""
    return CaseRecord(
        id=case_id,
        patient_json=json.dumps({"patientNo": patient_no, "name": f"测试患者_{patient_no}",
                                 "gender": "M", "age": 70}),
        modality="MRI+PET",
        exam_date=exam_date,
        department="神经内科",
        status="completed",
        diag_status="已完成AI分析",
        risk_level="mci",
        risk_score=50.0,
        has_mri=has_mri,
        has_pet=has_pet,
        create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        cloud_saved=False,
        mri_path="/data/test/mri.nii" if has_mri else "",
        pet_path="/data/test/pet.nii" if has_pet else "",
        is_deleted=False,
        cohort="UNKNOWN",
        scanner_info="",
    )


def make_analysis_record(case_id: str, hv_l: float, hv_r: float, suv: float,
                         cort: float, ventricle: float = 30.0,
                         mta_score: str = "1") -> "AnalysisRecord":
    """构造测试 AnalysisRecord（result_json 包含量化指标，camelCase 键）"""
    result = {
        "caseId": case_id,
        "hippocampusVolumeL": hv_l,
        "hippocampusVolumeR": hv_r,
        "meanSUV": suv,
        "corticalThickness": cort,
        "ventricleVolume": ventricle,
        "mtaScore": mta_score,
        "riskScore": 50.0,
        "riskLevel": "mci",
    }
    return AnalysisRecord(
        case_id=case_id,
        result_json=json.dumps(result),
    )
