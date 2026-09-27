"""
医学影像切片路由
==================================================================
- GET /case/{case_id}/imaging/meta   真实影像可用性与体数据元信息
- GET /case/{case_id}/imaging/slice  单张轴位切片 PNG（MRI/PET）

真实影像不可用时 meta.available=false，前端回退到合成影像；
slice 接口在任何失败情况下返回 404，由前端按层回退。
"""
import os
import json

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord
from services.auth import get_current_user_qs
from services import imaging_service as svc

# 影像端点要求登录；切片/Grad-CAM 经 <img src> 加载、体数据经 fetch 加载，
# 均通过 ?token=<JWT> 传递凭证（get_current_user_qs 支持查询参数鉴权）
router = APIRouter(
    prefix="/case",
    tags=["医学影像"],
    dependencies=[Depends(get_current_user_qs)],
)


def _resolve_paths(case: CaseRecord):
    mri_path = case.mri_path or ""
    pet_path = case.pet_path or ""
    mri_ok = bool(mri_path) and os.path.isfile(mri_path)
    pet_ok = bool(pet_path) and os.path.isfile(pet_path)
    return mri_path, pet_path, mri_ok, pet_ok


@router.get("/{case_id}/imaging/meta")
def imaging_meta(case_id: str, db: Session = Depends(get_db)):
    """真实 NIfTI 影像元信息（层数 / 尺寸 / MRI/PET 可用性 + DICOM 元数据）"""
    case = db.query(CaseRecord).filter(CaseRecord.id == case_id, CaseRecord.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在", 404)

    mri_path, pet_path, mri_ok, pet_ok = _resolve_paths(case)
    available = mri_ok or pet_ok

    # 轴位层数（用于默认阅片）
    axial_count = 0
    for p in (mri_path, pet_path):
        if p and os.path.isfile(p):
            n = svc.peek_slice_count(p)
            if n:
                axial_count = n
                break
    if axial_count <= 0:
        axial_count = 128

    # DICOM 元数据：JSON 反序列化（失败/历史 case 的 '{}' → None）
    dicom_meta = None
    raw = case.dicom_meta
    if raw and raw != "{}":
        try:
            dicom_meta = json.loads(raw)
        except Exception:
            dicom_meta = None

    return ok({
        "available": available,
        "mri": mri_ok,
        "pet": pet_ok,
        "sliceCount": axial_count,
        "sliceCountAxial": axial_count,
        "sliceCountSagittal": axial_count,  # 128³ 体数据三轴同尺寸
        "sliceCountCoronal": axial_count,
        "sliceSize": svc.OUT_SIZE,
        "orientation": "axial",
        "orientations": ["axial", "sagittal", "coronal"],
        "dicomMeta": dicom_meta,
    })


@router.get("/{case_id}/imaging/slice")
def imaging_slice(
    case_id: str,
    modality: str = "MRI",
    idx: int = 0,
    orientation: str = "axial",
    wc: float = None,
    ww: float = None,
    db: Session = Depends(get_db),
):
    """单张切片 PNG（8-bit 单通道，支持轴位/矢状位/冠状位）

    可选查询参数 wc/ww（窗位/窗宽）覆盖默认显示窗，用于 MPR 工具栏窗宽窗位调节；
    仅传其中一个时无效，ww<=0 时返回 400。
    """
    case = db.query(CaseRecord).filter(CaseRecord.id == case_id, CaseRecord.is_deleted.is_(False)).first()
    if not case:
        return Response(status_code=404)

    mri_path, pet_path, mri_ok, pet_ok = _resolve_paths(case)
    mod = modality.upper()
    if mod == "MRI" and not mri_ok:
        return Response(status_code=404)
    if mod == "PET" and not pet_ok:
        return Response(status_code=404)

    # 窗宽窗位校验：用户传 ww 但 <=0 直接拒绝；只传 wc 或都不传则回退默认窗
    if ww is not None and ww <= 0:
        return Response(status_code=400, content="窗宽必须为正数")

    png = svc.render_slice_png(case_id, mri_path, pet_path, mod, idx, orientation, wc=wc, ww=ww)
    if png is None:
        return Response(status_code=404)

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{case_id}/imaging/gradcam")
def imaging_gradcam(
    case_id: str,
    idx: int = 0,
    orientation: str = "axial",
    db: Session = Depends(get_db),
):
    """Grad-CAM 可解释性热力图（PET 代谢偏差图，RGBA 半透明 PNG，可叠加 MRI）"""
    case = db.query(CaseRecord).filter(CaseRecord.id == case_id, CaseRecord.is_deleted.is_(False)).first()
    if not case:
        return Response(status_code=404)

    mri_path, pet_path, mri_ok, pet_ok = _resolve_paths(case)
    if not pet_ok:
        return Response(status_code=404)

    png = svc.render_gradcam_png(case_id, mri_path, pet_path, idx, orientation)
    if png is None:
        return Response(status_code=404)

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{case_id}/imaging/volume")
def imaging_volume(
    case_id: str,
    modality: str = "MRI",
    size: int = 64,
    db: Session = Depends(get_db),
):
    """3D 体绘制数据：下采样后的 uint8 体数据（application/octet-stream）"""
    case = db.query(CaseRecord).filter(CaseRecord.id == case_id, CaseRecord.is_deleted.is_(False)).first()
    if not case:
        return Response(status_code=404)

    mri_path, pet_path, mri_ok, pet_ok = _resolve_paths(case)
    mod = modality.upper()
    if mod not in ("MRI", "PET"):
        mod = "MRI"
    if mod == "MRI" and not mri_ok:
        return Response(status_code=404)
    if mod == "PET" and not pet_ok:
        return Response(status_code=404)

    target = max(16, min(int(size), 96))
    result = svc.render_volume_bytes(case_id, mri_path, pet_path, mod, target)
    if result is None:
        return Response(status_code=404)

    raw, dims = result
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "X-Vol-Dims": f"{dims[0]},{dims[1]},{dims[2]}",
            "X-Vol-Modality": mod,
            "Cache-Control": "public, max-age=3600"},
    )
