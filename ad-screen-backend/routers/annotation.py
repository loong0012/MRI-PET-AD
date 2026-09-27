"""
影像 ROI 标注路由
- GET  /annotation/{case_id}          获取该病例所有标注
- POST /annotation/{case_id}          新增标注
- DELETE /annotation/{annotation_id}  删除标注
- GET  /annotation/{case_id}/export   导出标注 JSON
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.annotation import AnnotationRecord
from models.case import CaseRecord as CaseModel
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/annotation",
    tags=["影像ROI标注"],
    dependencies=[Depends(get_current_user_qs)],
)


def _active_case_exists(db: Session, case_id: str) -> bool:
    """
    校验病例存在且未被软删除。
    读取/写入标注前必须通过：已删病例的 ROI 属 PHI 不得继续暴露，
    也禁止对不存在 / 已删 case_id 写入孤儿标注。
    """
    return db.query(CaseModel.id).filter(
        CaseModel.id == case_id,
        CaseModel.is_deleted.is_(False),
    ).first() is not None


class AnnotationCreate(BaseModel):
    """新增标注请求体"""
    sliceIndex: int
    modality: str = "MRI"
    roiType: str = "sphere"  # sphere/rect/polygon
    coords: dict            # {cx,cy,r} or {x,y,w,h} or {points:[...]}
    label: str = ""
    area: float = 0.0
    notes: str = ""


def _to_dict(rec: AnnotationRecord) -> dict:
    """ORM 行 → 前端字段（驼峰）"""
    try:
        coords = json.loads(rec.coords or "{}")
    except json.JSONDecodeError:
        coords = {}
    return {
        "id": rec.id,
        "caseId": rec.case_id,
        "user": rec.user or "",
        "sliceIndex": rec.slice_index,
        "modality": rec.modality or "MRI",
        "roiType": rec.roi_type or "sphere",
        "coords": coords,
        "label": rec.label or "",
        "area": float(rec.area or 0.0),
        "notes": rec.notes or "",
        "createdAt": rec.created_at or "",
    }


@router.get("/{case_id}")
def list_annotations(case_id: str, db: Session = Depends(get_db)):
    """获取该病例所有 ROI 标注，按创建时间倒序"""
    if not _active_case_exists(db, case_id):
        return fail("病例不存在或已删除", 404)
    rows = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.case_id == case_id)
        .order_by(AnnotationRecord.created_at.desc())
        .all()
    )
    return ok([_to_dict(r) for r in rows])


@router.post("/{case_id}")
def create_annotation(
    case_id: str,
    body: AnnotationCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """新增 ROI 标注，返回新记录 id"""
    if not _active_case_exists(db, case_id):
        return fail("病例不存在或已删除", 404)
    user = current_user.get("realName") or current_user.get("username") or ""
    rec = AnnotationRecord(
        case_id=case_id,
        user=user,
        slice_index=body.sliceIndex,
        modality=body.modality,
        roi_type=body.roiType,
        coords=json.dumps(body.coords, ensure_ascii=False),
        label=body.label,
        area=body.area,
        notes=body.notes,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return ok({"id": rec.id})


@router.delete("/{annotation_id}")
def delete_annotation(
    annotation_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """删除指定标注（仅标注创建者本人或管理员可删，防止越权删除他人 ROI）"""
    rec = db.query(AnnotationRecord).filter(AnnotationRecord.id == annotation_id).first()
    if not rec:
        return fail("标注不存在")
    # 创建时 user 落的是 realName 或 username，两者任一匹配即视为本人
    username = current_user.get("username", "")
    real_name = current_user.get("realName", "")
    if current_user.get("role") != "admin" and rec.user not in (username, real_name):
        return fail("无权删除他人标注", 403)
    db.delete(rec)
    db.commit()
    return ok(None, "已删除")


@router.get("/{case_id}/export")
def export_annotations(case_id: str, db: Session = Depends(get_db)):
    """导出该病例的所有标注 JSON（用于训练数据导出）"""
    if not _active_case_exists(db, case_id):
        return fail("病例不存在或已删除", 404)
    rows = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.case_id == case_id)
        .order_by(AnnotationRecord.created_at.asc())
        .all()
    )
    return ok({
        "caseId": case_id,
        "annotations": [_to_dict(r) for r in rows],
    })
