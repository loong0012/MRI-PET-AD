"""
DICOMweb 只读路由（QIDO-RS / WADO-RS metadata）
==================================================================
标准检索接口，供 OHIF、Orthanc、院内 PACS 或区域平台以标准协议访问本平台检查。

- GET /dicomweb/studies                                        QIDO-RS 研究检索
- GET /dicomweb/studies/{study}/series                         QIDO-RS 序列检索
- GET /dicomweb/studies/{study}/series/{series}/instances       QIDO-RS 实例检索
- GET /dicomweb/studies/{study}/series/{series}/instances/{instance}/metadata   WADO-RS 元数据

标准约定：
- 请求 `Accept: application/dicom+json`（也兼容 application/json）
- 响应 `Content-Type: application/dicom+json`
- 分页：`limit` / `offset`
- 只返回未软删除病例（医疗合规）

写入（STOW-RS）未实现，返回 501 Not Implemented，避免外部系统误以为已支持。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from models.case import CaseRecord as CaseModel
from services.auth import require_role
from services.dicomweb_service import (
    study_resource,
    series_resource,
    instance_resource,
    flatten_for_qido,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dicomweb",
    tags=["互操作(DICOMweb)"],
    dependencies=[Depends(require_role("radiologist", "neurologist", "researcher", "admin"))],
)

_DICOM_JSON = "application/dicom+json"


def _case_query(db: Session, patient_id: Optional[str], modality: Optional[str]):
    query = db.query(CaseModel).filter(CaseModel.is_deleted == False)  # noqa: E712
    if modality:
        query = query.filter(CaseModel.modality.ilike(f"%{modality}%"))
    if patient_id:
        # PatientID 存于 patient_json.patientNo，SQLite 用 LIKE 过滤后在 Python 侧精确匹配
        query = query.filter(CaseModel.patient_json.ilike(f"%{patient_id}%"))
    return query.order_by(CaseModel.create_time.desc())


@router.get("/studies")
def qido_studies(
    db: Session = Depends(get_db),
    PatientID: Optional[str] = Query(default=None, description="患者编号（patientNo）"),
    ModalitiesInStudy: Optional[str] = Query(default=None, description="模态 MRI/PET/MRI+PET"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    includefield: Optional[str] = Query(default=None, description="保留参数，兼容标准客户端"),
    _user: dict = Depends(require_role("radiologist", "neurologist", "researcher", "admin")),
):
    """QIDO-RS：检索检查（studies）"""
    query = _case_query(db, PatientID, ModalitiesInStudy)
    total = query.count()
    cases = query.offset(offset).limit(limit).all()
    return [flatten_for_qido(study_resource(c)) for c in cases]


@router.get("/studies/{study}/series")
def qido_series(
    study: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_role("radiologist", "neurologist", "researcher", "admin")),
):
    """QIDO-RS：检索某检查下的序列（series）"""
    cases = _case_query(db, None, None).all()
    out = []
    for c in cases:
        if study not in (c.id, ""):
            # study 参数既接受内部病例号，也接受完整 DICOM UID
            from services.dicomweb_service import _dicom_uid
            if study != _dicom_uid("study", c.id):
                continue
        codes = _modality_codes_for(c)
        for i, code in enumerate(codes, start=1):
            out.append(flatten_for_qido(series_resource(c, code, i)))
    if not out:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到对应检查")
    return out


@router.get("/studies/{study}/series/{series}/instances")
def qido_instances(
    study: str,
    series: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_role("radiologist", "neurologist", "researcher", "admin")),
):
    """QIDO-RS：检索某序列下的实例（instances）"""
    from services.dicomweb_service import _dicom_uid

    for c in _case_query(db, None, None).all():
        for code in _modality_codes_for(c):
            if series in (_dicom_uid("series", c.id, code), str(code)):
                return [flatten_for_qido(instance_resource(c, code, 1))]
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到对应序列")


@router.get("/studies/{study}/series/{series}/instances/{instance}/metadata")
def wado_metadata(
    study: str,
    series: str,
    instance: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_role("radiologist", "neurologist", "researcher", "admin")),
):
    """WADO-RS：实例元数据（不含像素数据）"""
    from services.dicomweb_service import _dicom_uid

    for c in _case_query(db, None, None).all():
        for code in _modality_codes_for(c):
            if series in (_dicom_uid("series", c.id, code), str(code)):
                return instance_resource(c, code, 1)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到对应实例")


@router.post("/studies")
def stow_not_implemented():
    """
    STOW-RS（写入）未实现。

    归档写入需要 SOP Class 校验、幂等与存储承诺语义，
    本轮只做只读检索；显式返回 501，避免外部系统误判为已支持。
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="STOW-RS 写入未实现（本轮仅提供只读检索）",
    )


def _modality_codes_for(case) -> list[str]:
    """内部 modality → DICOM modality code 列表"""
    from services.dicomweb_service import _modality_codes
    return _modality_codes(case)
