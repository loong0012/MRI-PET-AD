"""
站内通知中心路由（全角色）
------------------------------------------------------------------
顶栏铃铛通知面板数据源。消息由后端在拉取时按当前用户角色**懒生成**：
- 随访到期提醒：临床角色（逾期 / 7 天内到期），dedup_key=followup:病例:日期
- 审核待办：临床角色（未复核的分析版本，每病例最新版），dedup_key=review:病例:版本号
去重键保证同一事项只通知一次；已读状态独立管理。
- GET  /notification/list         最近通知列表（可只看未读）
- GET  /notification/unread-count 未读数（顶栏徽标）
- POST /notification/{id}/read    标记单条已读
- POST /notification/read-all     全部已读
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord
from models.analysis import AnalysisVersionRecord, FollowUpRecord
from models.notification import NotificationRecord, AnnouncementRecord
from models.user import User
from services.auth import get_current_user_qs
from services.utils import format_date_time

router = APIRouter(
    prefix="/notification",
    tags=["通知中心"],
    dependencies=[Depends(get_current_user_qs)],
)

CLINICAL_ROLES = ("radiologist", "neurologist", "admin")


def _parse_date(s: str):
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _sync_notifications(db: Session, user: dict) -> None:
    """按当前用户角色懒生成提醒（dedup_key 幂等，重复拉取不产生重复消息）"""
    username = user.get("username", "")
    if not username:
        return
    existing = {
        k for (k,) in db.query(NotificationRecord.dedup_key).filter(
            NotificationRecord.username == username,
            NotificationRecord.dedup_key != "",
        ).all()
    }
    to_add: list[NotificationRecord] = []

    def _push(ntype: str, title: str, content: str, link: str, dedup_key: str) -> None:
        if dedup_key in existing:
            return
        to_add.append(NotificationRecord(
            username=username, ntype=ntype, title=title, content=content,
            link=link, dedup_key=dedup_key, is_read=False, created_at=format_date_time(),
        ))

    # 仅临床角色接收业务提醒
    if user.get("role") in CLINICAL_ROLES:
        today = datetime.now().date()
        # 1. 随访到期提醒（计划关联未删除病例）
        plan_rows = (
            db.query(FollowUpRecord, CaseRecord.id)
            .join(CaseRecord, FollowUpRecord.case_id == CaseRecord.id)
            .filter(CaseRecord.is_deleted == False)  # noqa: E712
            .all()
        )
        import json as _json
        for plan_rec, case_id in plan_rows:
            try:
                plan = _json.loads(plan_rec.plan_json or "{}")
            except _json.JSONDecodeError:
                continue
            next_d = _parse_date(plan.get("nextDate", ""))
            if next_d is None:
                continue
            diff = (next_d - today).days
            if diff < 0:
                _push(
                    "followup",
                    f"随访已逾期：病例 {case_id}",
                    f"计划随访日 {plan.get('nextDate')} 已过 {abs(diff)} 天，请尽快安排复诊",
                    "/followup?scope=overdue",
                    f"followup:{case_id}:{plan.get('nextDate')}",
                )
            elif diff <= 7:
                _push(
                    "followup",
                    f"随访到期提醒：病例 {case_id}",
                    f"计划随访日 {plan.get('nextDate')}（{'今天' if diff == 0 else f'{diff} 天后'}），请准备复诊",
                    "/followup?scope=week",
                    f"followup:{case_id}:{plan.get('nextDate')}",
                )

        # 2. 审核待办（每病例最新未复核版本）
        versions = (
            db.query(AnalysisVersionRecord)
            .join(CaseRecord, AnalysisVersionRecord.case_id == CaseRecord.id)
            .filter(
                CaseRecord.is_deleted == False,  # noqa: E712
                AnalysisVersionRecord.review_status == "pending",
            )
            .all()
        )
        latest: dict[str, AnalysisVersionRecord] = {}
        for v in versions:
            cur = latest.get(v.case_id)
            if cur is None or (v.version or 0) > (cur.version or 0):
                latest[v.case_id] = v
        for case_id, v in latest.items():
            _push(
                "review",
                f"AI 分析待审核：病例 {case_id}",
                f"第 {v.version} 版分析结果（{v.model_version or 'AI 模型'}）等待医生复核",
                f"/analysis/{case_id}",
                f"review:{case_id}:{v.version}",
            )

    if to_add:
        db.add_all(to_add)
        db.commit()


def _base_query(db: Session, username: str):
    return db.query(NotificationRecord).filter(NotificationRecord.username == username)


@router.get("/list")
def list_notifications(
    only_unread: bool = Query(default=False, alias="onlyUnread"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """最近通知列表（拉取时懒生成提醒，幂等去重）"""
    username = current_user.get("username", "")
    _sync_notifications(db, current_user)
    q = _base_query(db, username)
    if only_unread:
        q = q.filter(NotificationRecord.is_read == False)  # noqa: E712
    rows = q.order_by(NotificationRecord.id.desc()).limit(limit).all()
    return ok([
        {
            "id": n.id,
            "type": n.ntype,
            "title": n.title,
            "content": n.content,
            "link": n.link,
            "isRead": n.is_read,
            "createdAt": n.created_at,
        }
        for n in rows
    ])


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """未读数（顶栏徽标，拉取时同步懒生成）"""
    username = current_user.get("username", "")
    _sync_notifications(db, current_user)
    count = _base_query(db, username).filter(
        NotificationRecord.is_read == False  # noqa: E712
    ).count()
    has_followup = _base_query(db, username).filter(
        NotificationRecord.is_read == False,  # noqa: E712
        NotificationRecord.ntype == "followup",
    ).count() > 0
    return ok({"count": int(count), "hasFollowUp": has_followup})


@router.post("/{nid}/read")
def mark_read(
    nid: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """标记单条已读（仅能操作本人消息）"""
    username = current_user.get("username", "")
    n = _base_query(db, username).filter(NotificationRecord.id == nid).first()
    if not n:
        return fail("通知不存在", 404)
    n.is_read = True
    db.commit()
    return ok(None, "已标记已读")


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """全部已读"""
    username = current_user.get("username", "")
    _base_query(db, username).filter(
        NotificationRecord.is_read == False  # noqa: E712
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return ok(None, "已全部标记已读")


# ==================== 系统公告管理（admin） ====================

def _require_admin(user: dict):
    """仅管理员可管理公告"""
    return user.get("role") == "admin"


@router.post("/announce")
def publish_announcement(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    发布系统公告（admin）：写公告主记录，并为全部启用用户各生成一条通知。
    请求体：{"title": "...", "content": "..."}
    """
    if not _require_admin(current_user):
        return fail("无权限发布公告", 403)
    title = (payload.get("title") or "").strip()
    content = (payload.get("content") or "").strip()
    if not title or not content:
        return fail("公告标题与内容不能为空")
    if len(title) > 100:
        return fail("公告标题不能超过 100 字")
    if len(content) > 5000:
        return fail("公告内容不能超过 5000 字")

    now = format_date_time()
    ann = AnnouncementRecord(
        title=title, content=content,
        publisher=current_user.get("username", ""),
        is_active=True, created_at=now,
    )
    db.add(ann)
    db.flush()  # 取 ann.id

    # 为所有启用状态用户生成通知（dedup 绑定公告ID+用户）
    users = db.query(User).filter(User.status == 1).all()
    db.add_all([
        NotificationRecord(
            username=u.username, ntype="announce", title=title, content=content,
            link="", dedup_key=f"announce:{ann.id}:{u.username}",
            is_read=False, created_at=now,
        )
        for u in users
    ])
    db.commit()
    return ok({"id": ann.id, "receivers": len(users)}, f"公告已发布，已通知 {len(users)} 人")


@router.get("/announcements")
def list_announcements(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """公告列表（admin 管理用）"""
    if not _require_admin(current_user):
        return fail("无权限查看公告管理", 403)
    rows = db.query(AnnouncementRecord).order_by(AnnouncementRecord.id.desc()).all()
    return ok([
        {
            "id": r.id,
            "title": r.title,
            "content": r.content,
            "publisher": r.publisher,
            "isActive": r.is_active,
            "createdAt": r.created_at,
            "revokedAt": r.revoked_at,
        }
        for r in rows
    ])


@router.post("/announce/{aid}/revoke")
def revoke_announcement(
    aid: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """下线公告（admin）：标记下线并删除对应用户未读/已读通知"""
    if not _require_admin(current_user):
        return fail("无权限下线公告", 403)
    ann = db.query(AnnouncementRecord).filter(AnnouncementRecord.id == aid).first()
    if not ann:
        return fail("公告不存在", 404)
    ann.is_active = False
    ann.revoked_at = format_date_time()
    db.query(NotificationRecord).filter(
        NotificationRecord.dedup_key.like(f"announce:{aid}:%")
    ).delete(synchronize_session=False)
    db.commit()
    return ok(None, "公告已下线")
