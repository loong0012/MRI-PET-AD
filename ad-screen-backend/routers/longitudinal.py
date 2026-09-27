"""
纵向影像量化路由
==================================================================
- GET  /case/longitudinal/{patient_no}            查询多期指标 + 变化率 + NIA-AA 分级
- POST /case/longitudinal/{patient_no}/recompute  强制重算（admin/researcher）
- GET  /case/longitudinal/by-case/{case_id}        按病例 ID 反查患者纵向时间线

计算逻辑见 services/longitudinal_service.py；查询参数均通过 ?token=<JWT> 鉴权
（与 imaging.py 一致，便于 <img src> 直接加载切片）。
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord
from services.auth import get_current_user_qs, require_role
from services import longitudinal_service as svc

router = APIRouter(
    prefix="/case/longitudinal",
    tags=["纵向影像量化"],
    dependencies=[Depends(get_current_user_qs)],
)


def _resolve_patient_no(case_id: str, db: Session) -> str:
    """由 case_id 反查 patient_no（存在 case.patient_json.patientNo）"""
    case = db.query(CaseRecord).filter(
        CaseRecord.id == case_id, CaseRecord.is_deleted.is_(False)
    ).first()
    if not case:
        raise HTTPException(status_code=404, detail="病例不存在或已删除")
    try:
        pj = json.loads(case.patient_json or "{}")
        return pj.get("patientNo", "")
    except (json.JSONDecodeError, TypeError):
        return ""


@router.get("/{patient_no}")
def get_longitudinal(patient_no: str, db: Session = Depends(get_db)):
    """查询该患者多期量化指标 + 年化变化率 + NIA-AA 纵向进展分级"""
    result = svc.compute_metrics(patient_no, db)
    # available=False 时仍返回 200 + 结构化 reason，由前端展示占位
    return ok(result)


@router.post("/{patient_no}/recompute")
def recompute_longitudinal(
    patient_no: str,
    db: Session = Depends(get_db),
    _user=Depends(require_role("admin", "researcher")),
):
    """强制重算纵向指标（仅 admin / researcher 角色）"""
    result = svc.compute_metrics(patient_no, db)
    return ok({"recomputed": True, **result})


@router.get("/by-case/{case_id}")
def get_longitudinal_by_case(case_id: str, db: Session = Depends(get_db)):
    """按病例 ID 反查患者纵向时间线（前端从病例详情页直接进入）"""
    patient_no = _resolve_patient_no(case_id, db)
    if not patient_no:
        return fail("病例缺少 patientNo，无法查询纵向数据", 400)
    result = svc.compute_metrics(patient_no, db)
    return ok(result)
