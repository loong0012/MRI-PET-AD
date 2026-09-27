"""
干预方案路由
- GET /intervention/{caseId}：获取干预方案（无则按病例生成 AI 默认方案）
- POST /intervention/{caseId}：保存干预方案
"""
import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord as CaseModel
from models.analysis import InterventionRecord
from models.log import CaseLog
from services.auth import get_current_user_qs
from services.inference import build_interventions
from services.data_init import case_record_to_dict
from services.utils import now_str, next_seq_id

router = APIRouter(
    prefix="/intervention",
    tags=["干预方案"],
    dependencies=[Depends(get_current_user_qs)],
)


@router.get("/{case_id}")
def get_interventions(case_id: str, db: Session = Depends(get_db)):
    """获取干预方案"""
    rec = db.query(InterventionRecord).filter(InterventionRecord.case_id == case_id).first()
    if rec:
        return ok(json.loads(rec.sections_json))

    # 惰性生成 AI 默认方案
    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在", 404)

    case_data = case_record_to_dict(case)
    sections = build_interventions(case_data)

    # 缓存
    db.add(InterventionRecord(
        case_id=case_id,
        sections_json=json.dumps(sections, ensure_ascii=False),
    ))
    db.commit()
    return ok(sections)


@router.post("/{case_id}")
def save_interventions(
    case_id: str,
    sections: list[dict],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """保存干预方案（医生编辑后）"""
    # 分组数量上限：防止异常超长 JSON 入库
    if len(sections) > 50:
        return fail("干预方案分组数量超过上限（50）", 400)

    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在或已删除", 404)
    try:
        patient_name = json.loads(case.patient_json or "{}").get("name", "")
    except json.JSONDecodeError:
        patient_name = ""

    rec = db.query(InterventionRecord).filter(InterventionRecord.case_id == case_id).first()
    if rec:
        rec.sections_json = json.dumps(sections, ensure_ascii=False)
    else:
        db.add(InterventionRecord(
            case_id=case_id,
            sections_json=json.dumps(sections, ensure_ascii=False),
        ))

    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id=case_id,
        patient_name=patient_name,
        action="编辑干预方案",
        operator=current_user["username"],
        time=now_str(),
        detail="更新干预方案内容",
    ))
    db.commit()
    return ok(None, "保存成功")
