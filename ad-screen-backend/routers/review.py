"""
报告会签审核工作流路由
- GET    /review/pending              待审报告列表（工作台）
- GET    /review/{case_id}           单病例审核详情
- POST   /review/{case_id}/submit    提交审核（医生提交报告进入待审）
- POST   /review/{case_id}/decision  审核决策（approve / reject / sign）
- POST   /review/{case_id}/withdraw  撤回审核（提交人可撤回回到 draft）

状态机：
  draft → submit → pending_review
  pending_review → decision(approve/sign) → in_review (1签)
                 → decision(approve/sign) → signed (2签，触发 reported)
                 → decision(reject) → rejected
  in_review → decision(reject) → rejected
  rejected → submit → pending_review (重新提交)
  pending_review/in_review → withdraw → draft

签发条件：
  - approve 或 sign 计入签字数
  - 签字数 ≥2 且至少 1 名 reviewer_role="radiologist" → 签发
  - 签发后更新 CaseRecord.status="reported", diag_status="已出报告"
"""
import json
import threading
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord as CaseModel
from models.analysis import AnalysisVersionRecord
from models.review import ReportReview
from services.auth import get_current_user_qs
from services.utils import now_str

router = APIRouter(
    prefix="/review",
    tags=["报告审核"],
    dependencies=[Depends(get_current_user_qs)],
)

# 会签签发所需人数
REQUIRED_SIGN_COUNT = 2

# 审核决策 per-case 互斥锁：签字状态校验（check）与决策记录插入（insert）必须原子，
# 否则同一审核人双击/并发两个 approve 会双签，单人即可把报告置为已签发
# 有界 LRU：随累计病例数增长淘汰最久未用且未占用的锁，避免字典无界膨胀
_review_locks: "OrderedDict[str, threading.Lock]" = OrderedDict()
_review_locks_guard = threading.Lock()
_REVIEW_LOCK_MAX = 4096


def _get_review_lock(case_id: str) -> threading.Lock:
    with _review_locks_guard:
        lock = _review_locks.get(case_id)
        if lock is None:
            lock = threading.Lock()
            _review_locks[case_id] = lock
            if len(_review_locks) > _REVIEW_LOCK_MAX:
                for old_id, old_lock in list(_review_locks.items()):
                    if old_id == case_id:
                        continue
                    if not old_lock.locked():
                        del _review_locks[old_id]
                        break
        else:
            _review_locks.move_to_end(case_id)
        return lock
# 允许审核的角色
ALLOWED_REVIEW_ROLES = {"radiologist", "neurologist", "admin"}
# 允许提交的状态
SUBMITTABLE_STATUS = {"draft", "rejected", "withdrawn"}
# 允许撤回的状态
WITHDRAWABLE_STATUS = {"pending_review", "in_review"}
# 允许审核决策的状态
DECISIONABLE_STATUS = {"pending_review", "in_review"}


# ---------- Pydantic 请求体 ----------


class SubmitBody(BaseModel):
    """提交审核请求体"""
    comment: str = ""


class DecisionBody(BaseModel):
    """审核决策请求体"""
    decision: str  # approve / reject / sign
    comment: str = ""


# ---------- 工具函数 ----------


def _to_review_dict(r: ReportReview) -> dict:
    """ORM 行 → 前端字段（驼峰）"""
    return {
        "id": r.id,
        "caseId": r.case_id,
        "versionId": r.version_id,
        "reviewerUsername": r.reviewer_username or "",
        "reviewerName": r.reviewer_name or "",
        "reviewerRole": r.reviewer_role or "",
        "decision": r.decision or "",
        "comment": r.comment or "",
        "signedAt": r.signed_at or "",
    }


def _parse_patient_name(patient_json: str) -> str:
    """从 patient_json 解析患者姓名"""
    try:
        p = json.loads(patient_json or "{}")
        return p.get("name", "") or ""
    except (json.JSONDecodeError, TypeError):
        return ""


def _get_latest_version(db: Session, case_id: str) -> Optional[AnalysisVersionRecord]:
    """获取病例最新分析版本"""
    return (
        db.query(AnalysisVersionRecord)
        .filter(AnalysisVersionRecord.case_id == case_id)
        .order_by(AnalysisVersionRecord.version.desc())
        .first()
    )


def _derive_status(reviews: list[ReportReview]) -> tuple[str, int, int, bool, list[ReportReview]]:
    """
    根据 ReportReview 记录推导当前审核状态
    返回: (status, signedCount, requiredCount, hasRadiologist, currentCycleReviews)
    currentCycleReviews: 当前审核周期内的审核记录（不含 submit/withdraw 边界）
    """
    if not reviews:
        return ("draft", 0, REQUIRED_SIGN_COUNT, False, [])

    # 找最近一次 submit 或 withdraw 的边界，标记当前审核周期起点
    boundary_idx = -1
    for i, r in enumerate(reviews):
        if r.decision in ("submit", "withdraw"):
            boundary_idx = i

    if boundary_idx == -1:
        # 没有明确的 submit/withdraw 边界（脏数据），按 draft 处理
        return ("draft", 0, REQUIRED_SIGN_COUNT, False, [])

    boundary = reviews[boundary_idx]
    if boundary.decision == "withdraw":
        # 最近一次是撤回，状态回到 draft
        return ("draft", 0, REQUIRED_SIGN_COUNT, False, [])

    # 最近一次是 submit，统计 submit 之后的决策
    subsequent = reviews[boundary_idx + 1:]
    signed_count = 0
    has_radiologist = False
    has_reject = False
    for r in subsequent:
        if r.decision in ("approve", "sign"):
            signed_count += 1
            if r.reviewer_role == "radiologist":
                has_radiologist = True
        elif r.decision == "reject":
            has_reject = True

    if has_reject:
        return ("rejected", signed_count, REQUIRED_SIGN_COUNT, has_radiologist, subsequent)

    if signed_count >= REQUIRED_SIGN_COUNT and has_radiologist:
        return ("signed", signed_count, REQUIRED_SIGN_COUNT, has_radiologist, subsequent)

    if signed_count >= 1:
        return ("in_review", signed_count, REQUIRED_SIGN_COUNT, has_radiologist, subsequent)

    return ("pending_review", signed_count, REQUIRED_SIGN_COUNT, has_radiologist, subsequent)


def _get_case_reviews(db: Session, case_id: str) -> list[ReportReview]:
    """获取病例所有会签审核记录（按 id 正序，便于推导状态）"""
    return (
        db.query(ReportReview)
        .filter(ReportReview.case_id == case_id)
        .order_by(ReportReview.id.asc())
        .all()
    )


def _get_submitter(reviews: list[ReportReview]) -> Optional[ReportReview]:
    """获取最近一次 submit 记录（提交人）"""
    for r in reversed(reviews):
        if r.decision == "submit":
            return r
    return None


def _has_user_signed(reviews: list[ReportReview], username: str) -> bool:
    """判断当前用户是否已在当前周期内签过字（防重复签字）"""
    boundary_idx = -1
    for i, r in enumerate(reviews):
        if r.decision in ("submit", "withdraw"):
            boundary_idx = i
    if boundary_idx == -1:
        return False
    subsequent = reviews[boundary_idx + 1:]
    for r in subsequent:
        if r.decision in ("approve", "sign") and r.reviewer_username == username:
            return True
    return False


# ---------- 端点 ----------


@router.get("/pending")
def list_pending_reviews(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    status: str = Query("pending_review", description="筛选状态：draft/pending_review/in_review/signed/rejected/withdrawn/all"),
    db: Session = Depends(get_db),
):
    """待审报告列表（工作台）"""
    # 查询状态为 completed 或 reported 且未删除的病例
    cases = (
        db.query(CaseModel)
        .filter(CaseModel.status.in_(("completed", "reported")))
        .filter(CaseModel.is_deleted == False)  # noqa: E712
        .order_by(CaseModel.create_time.desc())
        .all()
    )

    # 组装每条记录并推导审核状态
    items = []
    for c in cases:
        reviews = _get_case_reviews(db, c.id)
        review_status, signed_count, required_count, has_radiologist, _ = _derive_status(reviews)
        latest_version = _get_latest_version(db, c.id)

        # 状态筛选
        if status != "all" and review_status != status:
            continue

        # 取最近审核记录（用于展示，按时间倒序）
        display_reviews = sorted(reviews, key=lambda x: x.signed_at or "", reverse=True)

        items.append({
            "caseId": c.id,
            "patientName": _parse_patient_name(c.patient_json),
            "modality": c.modality or "",
            "examDate": c.exam_date or "",
            "riskLevel": c.risk_level or "",
            "riskScore": c.risk_score if c.risk_score is not None else None,
            "reviewStatus": review_status,
            "signedCount": signed_count,
            "requiredCount": required_count,
            "hasRadiologist": has_radiologist,
            "versionId": latest_version.id if latest_version else None,
            "version": latest_version.version if latest_version else None,
            "reviews": [_to_review_dict(r) for r in display_reviews],
        })

    total = len(items)
    start = (page - 1) * pageSize
    paged = items[start:start + pageSize]
    return ok({"list": paged, "total": total, "page": page, "pageSize": pageSize})


@router.get("/{case_id}")
def get_review_detail(case_id: str, db: Session = Depends(get_db)):
    """单病例审核详情：所有会签记录 + 当前状态 + 最新版本信息 + 会签进度"""
    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在", 404)

    reviews = _get_case_reviews(db, case_id)
    review_status, signed_count, required_count, has_radiologist, current_cycle = _derive_status(reviews)
    latest_version = _get_latest_version(db, case_id)
    submitter = _get_submitter(reviews)

    return ok({
        "caseId": case.id,
        "patientName": _parse_patient_name(case.patient_json),
        "modality": case.modality or "",
        "examDate": case.exam_date or "",
        "department": case.department or "",
        "riskLevel": case.risk_level or "",
        "riskScore": case.risk_score if case.risk_score is not None else None,
        "caseStatus": case.status or "",
        "diagStatus": case.diag_status or "",
        "reviewStatus": review_status,
        "signedCount": signed_count,
        "requiredCount": required_count,
        "hasRadiologist": has_radiologist,
        "submitter": {
            "username": submitter.reviewer_username if submitter else "",
            "name": submitter.reviewer_name if submitter else "",
            "at": submitter.signed_at if submitter else "",
        } if submitter else None,
        "latestVersion": {
            "id": latest_version.id,
            "version": latest_version.version,
            "riskLevel": latest_version.risk_level or "",
            "riskScore": latest_version.risk_score if latest_version.risk_score is not None else None,
            "modelVersion": latest_version.model_version or "",
            "fusionStrategy": latest_version.fusion_strategy or "",
            "operator": latest_version.operator or "",
            "createdAt": latest_version.created_at or "",
        } if latest_version else None,
        # 所有会签记录（按时间倒序，便于时间线展示）
        "reviews": [_to_review_dict(r) for r in sorted(reviews, key=lambda x: x.signed_at or "", reverse=True)],
        # 当前周期内的有效审核（不含 submit/withdraw 边界）
        "currentCycleReviews": [_to_review_dict(r) for r in current_cycle],
    })


@router.post("/{case_id}/submit")
def submit_review(
    case_id: str,
    body: SubmitBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """提交审核（医生提交报告进入待审）
    仅当当前状态为 draft / rejected / withdrawn 时可提交
    """
    # 提交审核与签发同属临床审核闭环，角色口径必须与 decision 端点一致（researcher 只读）
    user_role = current_user.get("role", "")
    if user_role not in ALLOWED_REVIEW_ROLES:
        return fail(f"当前角色（{user_role}）无权提交报告审核", 403)

    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在", 404)

    # 校验病例状态：必须已完成 AI 分析（completed / reported）
    if case.status not in ("completed", "reported"):
        return fail(f"当前病例状态为 {case.status}，无法提交审核（需先完成 AI 分析）")

    reviews = _get_case_reviews(db, case_id)
    review_status, _, _, _, _ = _derive_status(reviews)

    if review_status not in SUBMITTABLE_STATUS:
        status_text = {
            "pending_review": "待审中",
            "in_review": "会签中",
            "signed": "已签发",
        }.get(review_status, review_status)
        return fail(f"当前审核状态为「{status_text}」，无法重复提交")

    # 创建 submit 记录
    rec = ReportReview(
        case_id=case_id,
        version_id=None,
        reviewer_username=current_user.get("username", ""),
        reviewer_name=current_user.get("realName", "") or current_user.get("username", ""),
        reviewer_role=current_user.get("role", ""),
        decision="submit",
        comment=(body.comment or "").strip(),
        signed_at=now_str(),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    # 重新推导新状态
    reviews = _get_case_reviews(db, case_id)
    new_status, signed_count, required_count, has_radiologist, _ = _derive_status(reviews)
    return ok({
        "reviewStatus": new_status,
        "signedCount": signed_count,
        "requiredCount": required_count,
        "hasRadiologist": has_radiologist,
    }, "报告已提交审核")


@router.post("/{case_id}/decision")
def review_decision(
    case_id: str,
    body: DecisionBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """审核决策（approve / reject / sign）
    - reject 时 comment 必填
    - 防止自己审自己提交的报告
    - 满足签发条件（≥2 签 + 至少1名 radiologist）→ 更新 CaseRecord.status="reported"
    """
    decision = (body.decision or "").strip().lower()
    if decision not in ("approve", "reject", "sign"):
        return fail("decision 必须为 approve / reject / sign 之一")

    if decision == "reject" and not (body.comment or "").strip():
        return fail("退回报告时必须填写审核意见")

    # 校验审核人角色
    user_role = current_user.get("role", "")
    if user_role not in ALLOWED_REVIEW_ROLES:
        return fail(f"当前角色（{user_role}）无权参与报告审核")

    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在", 404)

    # per-case 串行化：状态校验 → 插入决策 → 重新推导/签发必须原子，防止并发双签
    with _get_review_lock(case_id):
        reviews = _get_case_reviews(db, case_id)
        review_status, signed_count, required_count, has_radiologist, _ = _derive_status(reviews)

        # 校验当前状态可审核
        if review_status not in DECISIONABLE_STATUS:
            status_text = {
                "draft": "待提交",
                "signed": "已签发",
                "rejected": "已退回",
                "withdrawn": "已撤回",
            }.get(review_status, review_status)
            return fail(f"当前审核状态为「{status_text}」，无法执行审核决策")

        # 防止自己审自己提交的报告
        submitter = _get_submitter(reviews)
        if submitter and submitter.reviewer_username == current_user.get("username", ""):
            return fail("不能审核自己提交的报告")

        # 防止同一用户在同一周期内重复签字（approve/sign）
        if decision in ("approve", "sign") and _has_user_signed(reviews, current_user.get("username", "")):
            return fail("您已在该周期内签过字，不可重复签字")

        # 创建审核决策记录
        rec = ReportReview(
            case_id=case_id,
            version_id=None,
            reviewer_username=current_user.get("username", ""),
            reviewer_name=current_user.get("realName", "") or current_user.get("username", ""),
            reviewer_role=user_role,
            decision=decision,
            comment=(body.comment or "").strip(),
            signed_at=now_str(),
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)

        # 重新推导新状态
        reviews = _get_case_reviews(db, case_id)
        new_status, new_signed_count, required_count, has_radiologist, _ = _derive_status(reviews)

        # 若达到签发条件，更新病例状态为 reported
        if new_status == "signed":
            case.status = "reported"
            case.diag_status = "已出报告"
            db.commit()

        return ok({
            "reviewStatus": new_status,
            "signedCount": new_signed_count,
            "requiredCount": required_count,
            "hasRadiologist": has_radiologist,
        }, "审核决策已记录")


@router.post("/{case_id}/withdraw")
def withdraw_review(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """撤回审核（提交人可撤回回到 draft）
    仅在 pending_review / in_review 状态可撤回
    """
    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在", 404)

    reviews = _get_case_reviews(db, case_id)
    review_status, _, _, _, _ = _derive_status(reviews)

    if review_status not in WITHDRAWABLE_STATUS:
        status_text = {
            "draft": "待提交",
            "signed": "已签发",
            "rejected": "已退回",
            "withdrawn": "已撤回",
        }.get(review_status, review_status)
        return fail(f"当前审核状态为「{status_text}」，无法撤回")

    # 仅提交人本人可撤回
    submitter = _get_submitter(reviews)
    if not submitter or submitter.reviewer_username != current_user.get("username", ""):
        return fail("仅报告提交人可撤回审核")

    # 创建 withdraw 记录
    rec = ReportReview(
        case_id=case_id,
        version_id=None,
        reviewer_username=current_user.get("username", ""),
        reviewer_name=current_user.get("realName", "") or current_user.get("username", ""),
        reviewer_role=current_user.get("role", ""),
        decision="withdraw",
        comment="撤回审核",
        signed_at=now_str(),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    # 重新推导新状态（应回到 draft）
    reviews = _get_case_reviews(db, case_id)
    new_status, signed_count, required_count, has_radiologist, _ = _derive_status(reviews)
    return ok({
        "reviewStatus": new_status,
        "signedCount": signed_count,
        "requiredCount": required_count,
        "hasRadiologist": has_radiologist,
    }, "审核已撤回")
