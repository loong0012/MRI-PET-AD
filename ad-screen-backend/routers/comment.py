"""
病例评论/会诊讨论路由
- GET    /comment/{case_id}          获取病例评论列表
- POST   /comment/{case_id}          新增评论（支持回复）
- DELETE /comment/{comment_id}       删除评论（本人或 admin）
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case_comment import CaseComment
from models.case import CaseRecord as CaseModel
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/comment",
    tags=["病例评论"],
    dependencies=[Depends(get_current_user_qs)],
)


def _active_case_exists(db: Session, case_id: str) -> bool:
    """病例存在且未软删：拦截已删病例会诊讨论的读取与孤儿评论写入"""
    return db.query(CaseModel.id).filter(
        CaseModel.id == case_id,
        CaseModel.is_deleted.is_(False),
    ).first() is not None


class CommentCreateBody(BaseModel):
    # 病例评论限 1000 字，防空串刷屏与超长文本入库
    content: str = Field(min_length=1, max_length=1000)
    parentId: int = 0


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_dict(c: CaseComment) -> dict:
    return {
        "id": c.id,
        "caseId": c.case_id,
        "user": c.user,
        "realName": c.real_name,
        "content": c.content,
        "parentId": c.parent_id,
        "createdAt": c.created_at,
    }


@router.get("/{case_id}")
def list_comments(case_id: str, db: Session = Depends(get_db)):
    """获取病例评论列表（按时间正序）"""
    if not _active_case_exists(db, case_id):
        return fail("病例不存在或已删除", 404)
    comments = (
        db.query(CaseComment)
        .filter(CaseComment.case_id == case_id)
        .order_by(CaseComment.created_at.asc())
        .all()
    )
    return ok([_to_dict(c) for c in comments])


@router.post("/{case_id}")
def create_comment(
    case_id: str,
    body: CommentCreateBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """新增评论"""
    if not _active_case_exists(db, case_id):
        return fail("病例不存在或已删除", 404)
    content = body.content.strip()
    if not content:
        return fail("评论内容不能为空")
    rec = CaseComment(
        case_id=case_id,
        user=current_user.get("username", ""),
        real_name=current_user.get("realName", ""),
        content=content,
        parent_id=body.parentId,
        created_at=_now(),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return ok({"id": rec.id}, "评论已发布")


@router.delete("/{comment_id}")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """删除评论（本人或 admin）"""
    rec = db.query(CaseComment).filter(CaseComment.id == comment_id).first()
    if not rec:
        return fail("评论不存在", 404)
    is_admin = current_user.get("role") == "admin"
    is_owner = rec.user == current_user.get("username")
    if not (is_admin or is_owner):
        return fail("无权删除他人评论", 403)
    db.delete(rec)
    db.commit()
    return ok(None, "评论已删除")
