"""
随访管理路由
- GET  /followup/{caseId}            获取随访计划
- POST /followup/{caseId}            保存随访计划
- GET  /followup/worklist/summary    随访工作台汇总（逾期 / 7 天内 / 30 天内 / 总数）
- GET  /followup/worklist/list       随访工作台列表（scope 筛选 + 关键词）
- POST /followup/visit/{caseId}      记录一次随访执行（同步滚动计划下次随访日）
- GET  /followup/visits/{caseId}     某病例的随访执行历史
"""
import json
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from schemas.analysis import FollowUpPlan, FollowUpVisitIn
from models.analysis import FollowUpRecord, FollowUpVisit
from models.case import CaseRecord as CaseModel
from services.auth import get_current_user_qs
from services.inference import build_follow_up

router = APIRouter(
    prefix="/followup",
    tags=["随访管理"],
    dependencies=[Depends(get_current_user_qs)],
)

DATE_FMT = "%Y-%m-%d"


def _parse_date(s: str):
    """宽松解析 YYYY-MM-DD（含时间戳时取前 10 位）"""
    try:
        return datetime.strptime((s or "")[:10], DATE_FMT).date()
    except (ValueError, TypeError):
        return None


def _visit_to_dict(v: FollowUpVisit) -> dict:
    return {
        "id": v.id,
        "caseId": v.case_id,
        "patientName": v.patient_name,
        "visitDate": v.visit_date,
        "visitType": v.visit_type,
        "mmse": v.mmse,
        "moca": v.moca,
        "cognitionChange": v.cognition_change,
        "medicationAdherence": v.medication_adherence,
        "adverseEvent": v.adverse_event,
        "notes": v.notes,
        "nextDate": v.next_date,
        "operator": v.operator,
        "createdAt": v.created_at,
    }


def _build_worklist_item(
    plan_rec: FollowUpRecord,
    today,
    case_map: dict[str, CaseModel],
    last_visit_map: dict[str, FollowUpVisit | None],
    visit_count_map: dict[str, int],
) -> dict:
    """组装一条随访工作台记录（病例/末次随访/次数均来自预取索引，不再逐行查库）"""
    case = case_map.get(plan_rec.case_id)
    plan = json.loads(plan_rec.plan_json or "{}")
    next_date = _parse_date(plan.get("nextDate", ""))
    days_diff = (next_date - today).days if next_date else None

    patient = {}
    if case:
        try:
            patient = json.loads(case.patient_json or "{}")
        except json.JSONDecodeError:
            patient = {}

    last_visit = last_visit_map.get(plan_rec.case_id)
    visit_count = visit_count_map.get(plan_rec.case_id, 0)

    return {
        "caseId": plan_rec.case_id,
        "patientName": patient.get("name", ""),
        "gender": patient.get("gender", ""),
        "age": patient.get("age"),
        "department": case.department if case else "",
        "caseStatus": case.status if case else "",
        "riskLevel": case.risk_level if case else None,
        "riskScore": case.risk_score if case else None,
        "cycleMonths": plan.get("cycleMonths", 6),
        "nextDate": plan.get("nextDate", ""),
        "daysDiff": days_diff,
        "note": plan.get("note", ""),
        "visitCount": visit_count,
        "lastVisit": _visit_to_dict(last_visit) if last_visit else None,
    }


# ==================== 随访计划（单病例） ====================

@router.get("/worklist/summary")
def worklist_summary(db: Session = Depends(get_db)):
    """工作台顶部汇总：已逾期 / 7 天内到期 / 30 天内到期 / 在管随访总数"""
    today = datetime.now().date()
    plans = db.query(FollowUpRecord).all()
    overdue = due7 = due30 = 0
    for p in plans:
        plan = json.loads(p.plan_json or "{}")
        d = _parse_date(plan.get("nextDate", ""))
        if d is None:
            continue
        diff = (d - today).days
        if diff < 0:
            overdue += 1
        if diff <= 7:
            due7 += 1
        if diff <= 30:
            due30 += 1
    return ok({"overdue": overdue, "due7": due7, "due30": due30, "total": len(plans)})


@router.get("/worklist/list")
def worklist_list(
    scope: str = Query("all", pattern="^(overdue|week|month|all)$"),
    keyword: str = Query(""),
    db: Session = Depends(get_db),
):
    """
    随访工作台列表。
    scope: overdue=已逾期  week=7 天内到期  month=30 天内到期  all=全部在管
    """
    today = datetime.now().date()
    plans = db.query(FollowUpRecord).all()

    # 一次性预取：未删病例索引 + 全部随访按病例分组（组内 visit_date/id 倒序，首条即末次随访）
    case_map = {
        c.id: c
        for c in db.query(CaseModel).filter(CaseModel.is_deleted.is_(False)).all()
    }
    visits_by_case: dict[str, list[FollowUpVisit]] = {}
    for v in (
        db.query(FollowUpVisit)
        .order_by(FollowUpVisit.visit_date.desc(), FollowUpVisit.id.desc())
        .all()
    ):
        visits_by_case.setdefault(v.case_id, []).append(v)
    last_visit_map = {cid: vs[0] for cid, vs in visits_by_case.items()}
    visit_count_map = {cid: len(vs) for cid, vs in visits_by_case.items()}

    items = []
    kw = keyword.strip()
    for p in plans:
        item = _build_worklist_item(p, today, case_map, last_visit_map, visit_count_map)
        if kw and kw not in item["caseId"] and kw not in (item["patientName"] or ""):
            continue
        d = item["daysDiff"]
        if scope == "overdue" and (d is None or d >= 0):
            continue
        if scope == "week" and (d is None or d > 7):
            continue
        if scope == "month" and (d is None or d > 30):
            continue
        items.append(item)

    # 排序：逾期最久 → 最近到期 → 未来到期
    items.sort(key=lambda x: (x["daysDiff"] is None, x["daysDiff"] if x["daysDiff"] is not None else 0))
    return ok({"list": items, "total": len(items)})


@router.post("/visit/{case_id}")
def record_visit(
    case_id: str,
    payload: FollowUpVisitIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    记录一次随访执行：
    1. 落库随访记录（MMSE/MoCA/认知变化/依从性/不良反应/备注）
    2. 同步滚动随访计划的 nextDate（传入则用传入值，否则按周期自 visitDate 顺延）
    病例必须存在且未删除；operator 强制取 JWT 登录人，禁止请求体伪造；
    下次随访日期非空时必须格式合法且不早于本次随访日期。
    """
    visit_date = _parse_date(payload.visitDate)
    if visit_date is None:
        return fail("随访日期格式不正确")

    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在或已删除", 404)
    try:
        patient_name = json.loads(case.patient_json or "{}").get("name", "")
    except json.JSONDecodeError:
        patient_name = ""

    # 下次随访日期：非空时校验格式与先后顺序（早于本次随访日会让病例立即变"逾期"）
    next_date_param = None
    if payload.nextDate:
        next_date_param = _parse_date(payload.nextDate)
        if next_date_param is None:
            return fail("下次随访日期格式不正确")
        if next_date_param < visit_date:
            return fail("下次随访日期不能早于本次随访日期")

    # operator 以 JWT 登录人为准，忽略请求体中可能伪造的 operator
    operator = current_user.get("username", "") or payload.operator
    visit = FollowUpVisit(
        case_id=case_id,
        patient_name=patient_name,
        visit_date=visit_date.strftime(DATE_FMT),
        visit_type=payload.visitType,
        mmse=payload.mmse,
        moca=payload.moca,
        cognition_change=payload.cognitionChange,
        medication_adherence=payload.medicationAdherence,
        adverse_event=payload.adverseEvent,
        notes=payload.notes.strip(),
        next_date=payload.nextDate,
        operator=operator,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(visit)

    # 滚动随访计划
    plan_rec = db.query(FollowUpRecord).filter(FollowUpRecord.case_id == case_id).first()
    if plan_rec is None:
        plan_rec = FollowUpRecord(case_id=case_id, plan_json=json.dumps(build_follow_up(), ensure_ascii=False))
        db.add(plan_rec)
        db.flush()
    plan = json.loads(plan_rec.plan_json or "{}")

    if payload.nextDate:
        next_d = next_date_param
    else:
        cycle = plan.get("cycleMonths", 6)
        next_d = visit_date + relativedelta(months=cycle)
    if next_d:
        next_str = next_d.strftime(DATE_FMT)
        reminder_str = (next_d - timedelta(days=6)).strftime(DATE_FMT)
        plan["nextDate"] = next_str
        plan["reminders"] = [next_str, reminder_str]
        plan_rec.plan_json = json.dumps(plan, ensure_ascii=False)

    # ---------- 2026 版指南：纵向随访协议一致性提醒 ----------
    # 指南要求：纵向随访需保持标准化成像协议一致（机型/场强/注射及采集方案/重建参数）
    visits_count = db.query(FollowUpVisit).filter(FollowUpVisit.case_id == case_id).count()
    protocol_reminder = None
    if visits_count >= 2:
        protocol_reminder = (
            "【指南提醒】纵向随访需保持标准化成像协议一致："
            "建议下次复查使用同机型、同场强、同显像剂及剂量、同注射-采集时间窗、同重建参数，"
            "以保证 SUVR 等定量指标的可比性"
        )
    # 检查计划中是否已记录扫描仪信息（用于跨次随访一致性比对）
    scanner_info = ""
    try:
        scanner_info = json.loads(case.scanner_info or "{}").get("manufacturer", "") if case.scanner_info else ""
    except (json.JSONDecodeError, TypeError):
        scanner_info = ""
    if not scanner_info and visits_count >= 1:
        if protocol_reminder is None:
            protocol_reminder = "【指南提醒】建议在随访计划中记录扫描仪信息，便于纵向随访协议一致性比对"

    db.commit()
    return ok(
        {"protocolReminder": protocol_reminder},
        "随访记录已保存，下次随访日期已更新",
    )


@router.get("/visits/{case_id}")
def list_visits(case_id: str, db: Session = Depends(get_db)):
    """某病例的随访执行历史（日期倒序）"""
    rows = (
        db.query(FollowUpVisit)
        .filter(FollowUpVisit.case_id == case_id)
        .order_by(FollowUpVisit.visit_date.desc(), FollowUpVisit.id.desc())
        .all()
    )
    return ok([_visit_to_dict(v) for v in rows])


# ==================== 随访计划（单病例，放通配路径前注册） ====================

@router.get("/{case_id}")
def get_follow_up(case_id: str, db: Session = Depends(get_db)):
    """获取随访计划"""
    rec = db.query(FollowUpRecord).filter(FollowUpRecord.case_id == case_id).first()
    if rec:
        return ok(json.loads(rec.plan_json))
    # 惰性生成默认计划
    plan = build_follow_up()
    db.add(FollowUpRecord(
        case_id=case_id,
        plan_json=json.dumps(plan, ensure_ascii=False),
    ))
    db.commit()
    return ok(plan)


@router.post("/{case_id}")
def save_follow_up(case_id: str, plan: FollowUpPlan, db: Session = Depends(get_db)):
    """保存随访计划"""
    # 写入前校验病例存在且未删除，避免产生孤儿计划行
    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在或已删除", 404)
    plan_dict = plan.model_dump()
    rec = db.query(FollowUpRecord).filter(FollowUpRecord.case_id == case_id).first()
    if rec:
        rec.plan_json = json.dumps(plan_dict, ensure_ascii=False)
    else:
        db.add(FollowUpRecord(
            case_id=case_id,
            plan_json=json.dumps(plan_dict, ensure_ascii=False),
        ))
    db.commit()
    return ok(None, "保存成功")


# ==================== 批量随访计划生成 ====================

def _cycle_by_risk(risk_level: str) -> int:
    """按风险等级返回随访周期（月）：AD=3 / MCI=6 / low=12"""
    if risk_level in ("ad-early", "ad-late"):
        return 3
    if risk_level == "mci":
        return 6
    return 12


def _build_plan_from_cycle(cycle_months: int) -> dict:
    """按周期生成随访计划 JSON"""
    next_dt = datetime.now() + relativedelta(months=cycle_months)
    next_str = next_dt.strftime(DATE_FMT)
    reminder_str = (next_dt - timedelta(days=6)).strftime(DATE_FMT)
    return {
        "cycleMonths": cycle_months,
        "nextDate": next_str,
        "reminders": [next_str, reminder_str],
        "note": "随访内容：认知量表复评 + 头颅 MRI 海马定量",
    }


@router.get("/batch/preview")
def batch_preview(
    scope: str = Query(default="all", pattern="^(all|missing|urgent|ad|mci|low)$"),
    db: Session = Depends(get_db),
):
    """
    批量生成预览（不落库）：
    - all=全部未删除病例
    - missing=仅无随访计划的病例
    - urgent=AD 早/中晚期病例
    - ad=AD 早/中晚期（同 urgent）
    - mci=MCI 病例
    - low=低风险病例
    返回每个病例应分配的随访周期与下次日期，供前端确认后批量保存。
    """
    q = db.query(CaseModel).filter(CaseModel.is_deleted == False)  # noqa: E712
    if scope in ("urgent", "ad"):
        q = q.filter(CaseModel.risk_level.in_(["ad-early", "ad-late"]))
    elif scope == "mci":
        q = q.filter(CaseModel.risk_level == "mci")
    elif scope == "low":
        q = q.filter(CaseModel.risk_level == "low")
    cases = q.order_by(CaseModel.id).all()

    existing_ids = {r.case_id for r in db.query(FollowUpRecord.case_id).all()}
    items = []
    for c in cases:
        has_plan = c.id in existing_ids
        if scope == "missing" and has_plan:
            continue
        try:
            patient = json.loads(c.patient_json or "{}")
        except json.JSONDecodeError:
            patient = {}
        risk_level = c.risk_level or "low"
        cycle = _cycle_by_risk(risk_level)
        plan = _build_plan_from_cycle(cycle)
        items.append({
            "caseId": c.id,
            "patientName": patient.get("name", ""),
            "gender": patient.get("gender", ""),
            "age": patient.get("age"),
            "modality": c.modality,
            "riskLevel": risk_level,
            "riskScore": c.risk_score,
            "hasPlan": has_plan,
            "cycleMonths": cycle,
            "nextDate": plan["nextDate"],
        })

    return ok({
        "list": items,
        "total": len(items),
        "newCount": sum(1 for i in items if not i["hasPlan"]),
        "updateCount": sum(1 for i in items if i["hasPlan"]),
        "scope": scope,
    })


@router.post("/batch/generate")
def batch_generate(
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    批量生成/更新随访计划（确认后落库）。
    请求体：{ caseIds: ["AD260001", ...] } 或 { scope: "ad" }（二选一）
    """
    case_ids: list[str] = payload.get("caseIds") or []
    scope: str = payload.get("scope", "")

    if not case_ids and not scope:
        return fail("请指定病例 ID 列表或范围")

    # 显式 ID 列表上限 500（scope 全量模式服务端按风险过滤，天然受病例总量约束）
    BATCH_FOLLOWUP_MAX = 500
    if len(case_ids) > BATCH_FOLLOWUP_MAX:
        return fail(f"单次最多生成 {BATCH_FOLLOWUP_MAX} 例，请分批提交")

    # scope 白名单（与 batch/preview 的 pattern 对齐）：未知值显式拒绝，杜绝 fail-open 成全部病例
    VALID_SCOPES = {"all", "missing", "urgent", "ad", "mci", "low"}
    if scope and scope not in VALID_SCOPES:
        return fail(f"不支持的范围：{scope}")

    q = db.query(CaseModel).filter(CaseModel.is_deleted == False)  # noqa: E712
    if case_ids:
        q = q.filter(CaseModel.id.in_(case_ids))
    elif scope in ("urgent", "ad"):
        q = q.filter(CaseModel.risk_level.in_(["ad-early", "ad-late"]))
    elif scope == "mci":
        q = q.filter(CaseModel.risk_level == "mci")
    elif scope == "low":
        q = q.filter(CaseModel.risk_level == "low")
    elif scope == "missing":
        # 仅纳入尚无随访计划的病例：不会覆盖医生手工调整过的计划
        existing_ids = [r[0] for r in db.query(FollowUpRecord.case_id).all()]
        if existing_ids:
            q = q.filter(~CaseModel.id.in_(existing_ids))
    cases = q.all()

    created = updated = 0
    for c in cases:
        risk_level = c.risk_level or "low"
        cycle = _cycle_by_risk(risk_level)
        plan = _build_plan_from_cycle(cycle)
        plan_json = json.dumps(plan, ensure_ascii=False)
        rec = db.query(FollowUpRecord).filter(FollowUpRecord.case_id == c.id).first()
        if rec:
            rec.plan_json = plan_json
            updated += 1
        else:
            db.add(FollowUpRecord(case_id=c.id, plan_json=plan_json))
            created += 1
    db.commit()
    return ok({
        "created": created,
        "updated": updated,
        "total": created + updated,
    }, f"批量生成完成：新建 {created} 个，更新 {updated} 个")
