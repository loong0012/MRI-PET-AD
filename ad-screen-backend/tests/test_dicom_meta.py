"""
DICOM 元数据抽取与 imaging/meta API 单元测试
覆盖：
- extract_dicom_meta：12 标签标准抽取（合成 pydicom.Dataset）
- extract_dicom_meta：缺标签 → 对应字段 None，无异常
- imaging/meta：case.dicom_meta 有内容 → 返回 camelCase 字段
- imaging/meta：历史 case（dicom_meta='{}'）→ dicomMeta=None
"""
import json
import os
import tempfile

import pytest

from services.dicom_service import extract_dicom_meta


# ---------- 工具：合成最小 DICOM 文件 ----------
def _write_synthetic_dicom(tmp_dir: str, name: str = "slice.dcm", tags: dict = None) -> str:
    """
    用 pydicom.Dataset 构造最小合成 DICOM 文件并落盘。
    tags 控制要写入的字段；缺省写一组典型 MRI 标签。
    """
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, CTImageStorage, generate_uid

    ds = Dataset()
    # 文件元信息
    file_meta = FileMetaDataset()
    # pydicom 3.x：CTImageStorage 是 UID 实例，不可再调用
    file_meta.MediaStorageSOPClassUID = CTImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = file_meta
    # 字符集：ISO_IR 192 = UTF-8，让中文 PatientName 正确编码
    ds.SpecificCharacterSet = "ISO_IR 192"

    # 默认 12 个标签（与 extract 抽取的 12 字段对齐）
    default_tags = {
        "PatientName": "张三",
        "PatientID": "P0001",
        "StudyDate": "20240115",
        "StudyTime": "093000",
        "SeriesDescription": "T1 MPRAGE",
        "Modality": "MR",
        "Manufacturer": "SIEMENS",
        "MagneticFieldStrength": "3.0",
        "WindowCenter": "40",
        "WindowWidth": "80",
        "SliceThickness": "1.0",
        "PixelSpacing": ["0.5", "0.5"],
    }
    applied = {**default_tags, **(tags or {})}
    for k, v in applied.items():
        setattr(ds, k, v)

    # 必填最小像素数据以保证 dcmwrite 不崩（meta 抽取不依赖像素）
    import numpy as np
    arr = np.zeros((8, 8), dtype=np.uint16)
    ds.Rows = arr.shape[0]
    ds.Columns = arr.shape[1]
    ds.PixelData = arr.tobytes()
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0

    fpath = os.path.join(tmp_dir, name)
    pydicom.dcmwrite(fpath, ds)
    return fpath


# ---------- P9-1: extract_dicom_meta 标准抽取 ----------
def test_extract_dicom_meta_standard():
    """合成 DICOM 含 12 个标准标签 → 全部抽取正确"""
    with tempfile.TemporaryDirectory() as d:
        _write_synthetic_dicom(d)
        meta = extract_dicom_meta(d)

    assert meta["patientName"] == "张三"
    assert meta["patientId"] == "P0001"
    assert meta["studyDate"] == "20240115"
    assert meta["studyTime"] == "093000"
    assert meta["seriesDescription"] == "T1 MPRAGE"
    assert meta["modality"] == "MR"
    assert meta["manufacturer"] == "SIEMENS"
    assert meta["fieldStrength"] == pytest.approx(3.0)
    assert meta["windowCenter"] == pytest.approx(40.0)
    assert meta["windowWidth"] == pytest.approx(80.0)
    assert meta["sliceThickness"] == pytest.approx(1.0)
    assert meta["pixelSpacing"] == [pytest.approx(0.5), pytest.approx(0.5)]


# ---------- P9-2: 缺标签 → None ----------
def test_extract_dicom_meta_missing_tags():
    """合成 DICOM 仅含 PatientID 一个标签 → 其余字段 None，不抛异常"""
    with tempfile.TemporaryDirectory() as d:
        _write_synthetic_dicom(d, tags={
            "PatientName": "李四",
            "PatientID": "X123",
            # 其余字段不写
            "StudyDate": None,
            "StudyTime": None,
            "SeriesDescription": None,
            "Modality": None,
            "Manufacturer": None,
            "MagneticFieldStrength": None,
            "WindowCenter": None,
            "WindowWidth": None,
            "SliceThickness": None,
            "PixelSpacing": None,
        })
        meta = extract_dicom_meta(d)

    assert meta["patientName"] == "李四"
    assert meta["patientId"] == "X123"
    # 缺失字段全部 None
    assert meta["studyDate"] is None
    assert meta["studyTime"] is None
    assert meta["seriesDescription"] is None
    assert meta["modality"] is None
    assert meta["manufacturer"] is None
    assert meta["fieldStrength"] is None
    assert meta["windowCenter"] is None
    assert meta["windowWidth"] is None
    assert meta["sliceThickness"] is None
    assert meta["pixelSpacing"] is None


# ---------- P9-3: imaging/meta 返回 dicomMeta（camelCase） ----------
def test_imaging_meta_with_dicom_meta(db):
    """case.dicom_meta 含中文 + 12 字段 → API 返回 camelCase 解析结果"""
    from models.case import CaseRecord
    from datetime import datetime

    meta_obj = {
        "patientName": "张三",
        "patientId": "P0001",
        "studyDate": "20240115",
        "studyTime": "093000",
        "seriesDescription": "T1",
        "modality": "MR",
        "manufacturer": "SIEMENS",
        "fieldStrength": 3.0,
        "windowCenter": 40.0,
        "windowWidth": 80.0,
        "sliceThickness": 1.0,
        "pixelSpacing": [0.5, 0.5],
    }
    case = CaseRecord(
        id="AD260099",
        patient_json=json.dumps({"patientNo": "P0001", "name": "张三", "gender": "M", "age": 70}),
        modality="MRI+PET",
        exam_date="2024-01-15",
        department="放射科",
        status="pending",
        diag_status="待AI分析",
        risk_level=None,
        risk_score=None,
        has_mri=True,
        has_pet=True,
        create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        cloud_saved=False,
        mri_path="",
        pet_path="",
        is_deleted=False,
        cohort="UNKNOWN",
        scanner_info="",
        dicom_meta=json.dumps(meta_obj, ensure_ascii=False),
    )
    db.add(case)
    db.commit()

    # 直接调用路由函数（绕过鉴权依赖，走业务逻辑）
    # schemas.common.ok 返回 dict（FastAPI 再序列化为 JSONResponse），
    # 这里直接拿 dict 断言
    from routers.imaging import imaging_meta
    resp = imaging_meta("AD260099", db)
    payload = resp if isinstance(resp, dict) else None
    if payload is None and hasattr(resp, "body"):
        import json as _json
        payload = _json.loads(resp.body.decode("utf-8"))
    assert payload is not None
    data = payload.get("data") or payload
    assert "dicomMeta" in data
    assert data["dicomMeta"] is not None
    assert data["dicomMeta"]["patientName"] == "张三"
    assert data["dicomMeta"]["patientId"] == "P0001"
    assert data["dicomMeta"]["studyDate"] == "20240115"
    assert data["dicomMeta"]["modality"] == "MR"
    assert data["dicomMeta"]["fieldStrength"] == pytest.approx(3.0)
    assert data["dicomMeta"]["windowCenter"] == pytest.approx(40.0)
    assert data["dicomMeta"]["windowWidth"] == pytest.approx(80.0)
    assert data["dicomMeta"]["pixelSpacing"] == [pytest.approx(0.5), pytest.approx(0.5)]


# ---------- P9-4: 历史 case → dicomMeta: None ----------
def test_imaging_meta_historical_case(db):
    """case.dicom_meta='{}' → 返回 dicomMeta=None"""
    from models.case import CaseRecord
    from datetime import datetime

    case = CaseRecord(
        id="AD260098",
        patient_json=json.dumps({"patientNo": "P0098", "name": "历史患者", "gender": "F", "age": 68}),
        modality="MRI",
        exam_date="2020-01-01",
        department="神经内科",
        status="completed",
        diag_status="已完成",
        risk_level="low",
        risk_score=20.0,
        has_mri=True,
        has_pet=False,
        create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        cloud_saved=False,
        mri_path="",
        pet_path="",
        is_deleted=False,
        cohort="UNKNOWN",
        scanner_info="",
        dicom_meta="{}",  # 历史 case：未抽取过
    )
    db.add(case)
    db.commit()

    from routers.imaging import imaging_meta
    resp = imaging_meta("AD260098", db)
    payload = resp if isinstance(resp, dict) else None
    if payload is None and hasattr(resp, "body"):
        import json as _json
        payload = _json.loads(resp.body.decode("utf-8"))
    assert payload is not None
    data = payload.get("data") or payload
    assert "dicomMeta" in data
    assert data["dicomMeta"] is None
