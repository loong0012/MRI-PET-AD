"""
高危病例预警中心
- GET /warning/summary：各类型预警数量汇总（默认按未处理统计）
- GET /warning/list：预警明细（type=overdue/cognition/risk/all，status=open/handled/ignored/all）
- POST /warning/handle：预警处置（标记已处理 / 忽略 / 重新打开，形成质控闭环）

三类预警：
1. followup-overdue 随访逾期：随访计划 nextDate 已过期，逾期越久级别越高
2. cognition-decline 认知下降加速：MMSE 年化下降 ≥3 分
3. risk-rise      AI 风险上升：最新分析版本较首版风险评分升高 ≥5 分

处置状态持久化在 warning_handlings 表，dedup_key = "{type}|{caseId}"。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord as CaseModel
from models.analysis import (
    FollowUpRecord, FollowUpVisit, AnalysisVersionRecord,
)
from models.warning import WarningHandling
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/warning",
    tags=["高危预警"],
    dependencies=[Depends(get_current_user_qs)],
)


def _patient_name(case: CaseModel | None) -> str:
    if not case:
        return ""
    try:
        return json.loads(case.patient_json or "{}").get("name", "")
    except json.JSONDecodeError:
        return ""


def _parse_date(s: str):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _load_base_data(db: Session):
    """
    一次性预取三类预警共用数据，消除循环内逐病例查询（N+1）：
    - 未删除病例索引 {case_id: case}
    - 全部随访记录按 case_id 分组（保持 visit_date 升序，与原逐病例查询排序一致）
    - 全部分析版本按 case_id 分组（保持 version 升序，首末即首版/最新版）
    """
    case_map = {
        c.id: c
        for c in db.query(CaseModel).filter(CaseModel.is_deleted == False).all()  # noqa: E712
    }
    visits_by_case: dict[str, list[FollowUpVisit]] = {}
    for v in db.query(FollowUpVisit).order_by(FollowUpVisit.visit_date.asc()).all():
        visits_by_case.setdefault(v.case_id, []).append(v)
    versions_by_case: dict[str, list[AnalysisVersionRecord]] = {}
    for ver in db.query(AnalysisVersionRecord).order_by(AnalysisVersionRecord.version.asc()).all():
        versions_by_case.setdefault(ver.case_id, []).append(ver)
    return case_map, visits_by_case, versions_by_case


def _collect_overdue(db: Session, today, case_map: dict[str, CaseModel]) -> list[dict]:
    """随访逾期预警"""
    out = []
    plans = db.query(FollowUpRecord).all()
    for p in plans:
        plan = json.loads(p.plan_json or "{}")
        d = _parse_date(plan.get("nextDate", ""))
        if d is None:
            continue
        days = (today - d).days
        if days < 0:
            continue
        case = case_map.get(p.case_id)  # 病例索引命中即代表存在且未删除
        if not case:
            continue
        if days >= 30:
            level, level_text = "high", "高危"
        elif days >= 7:
            level, level_text = "medium", "中危"
        else:
            level, level_text = "low", "关注"
        out.append({
            "dedupKey": f"followup-overdue|{p.case_id}",
            "type": "followup-overdue",
            "typeLabel": "随访逾期",
            "caseId": p.case_id,
            "patientName": _patient_name(case),
            "riskLevel": case.risk_level,
            "riskScore": case.risk_score,
            "level": level,
            "levelText": level_text,
            "metric": f"逾期 {days} 天",
            "detail": f"应于 {plan.get('nextDate', '')} 随访（周期 {plan.get('cycleMonths', 6)} 个月）",
            "sortValue": days,
        })
    return out


def _collect_cognition(case_map: dict[str, CaseModel], visits_by_case: dict[str, list[FollowUpVisit]]) -> list[dict]:
    """认知下降加速预警：MMSE 年化下降 ≥3 分"""
    out = []
    for cid, visits in visits_by_case.items():
        scored = [v for v in visits if v.mmse is not None and _parse_date(v.visit_date)]
        if len(scored) < 2:
            continue
        first, last = scored[0], scored[-1]
        d0, d1 = _parse_date(first.visit_date), _parse_date(last.visit_date)
        years = ((d1 - d0).days) / 365.25 if d0 and d1 else 0
        if years <= 0:
            continue
        rate = (last.mmse - first.mmse) / years
        if rate > -3:
            continue
        case = case_map.get(cid)
        if not case:
            continue
        if rate <= -5:
            level, level_text = "high", "高危"
        elif rate <= -4:
            level, level_text = "medium", "中危"
        else:
            level, level_text = "low", "关注"
        out.append({
            "dedupKey": f"cognition-decline|{cid}",
            "type": "cognition-decline",
            "typeLabel": "认知下降加速",
            "caseId": cid,
            "patientName": _patient_name(case),
            "riskLevel": case.risk_level,
            "riskScore": case.risk_score,
            "level": level,
            "levelText": level_text,
            "metric": f"年化 {rate:+.1f} 分/年",
            "detail": f"MMSE {first.mmse:g} → {last.mmse:g}（{first.visit_date} 至 {last.visit_date}，共 {len(scored)} 次评分）",
            "sortValue": -rate,  # 下降越快值越大
        })
    return out


def _collect_risk_rise(case_map: dict[str, CaseModel], versions_by_case: dict[str, list[AnalysisVersionRecord]]) -> list[dict]:
    """AI 风险上升预警：最新版较首版风险评分升高 ≥5"""
    out = []
    for cid, vers in versions_by_case.items():
        if len(vers) < 2:
            continue
        base, latest = vers[0], vers[-1]
        if base.risk_score is None or latest.risk_score is None:
            continue
        delta = latest.risk_score - base.risk_score
        if delta < 5:
            continue
        case = case_map.get(cid)
        if not case:
            continue
        if latest.risk_score >= 70 or delta >= 15:
            level, level_text = "high", "高危"
        elif latest.risk_score >= 50 or delta >= 10:
            level, level_text = "medium", "中危"
        else:
            level, level_text = "low", "关注"
        out.append({
            "dedupKey": f"risk-rise|{cid}",
            "type": "risk-rise",
            "typeLabel": "AI 风险上升",
            "caseId": cid,
            "patientName": _patient_name(case),
            "riskLevel": latest.risk_level or case.risk_level,
            "riskScore": latest.risk_score,
            "level": level,
            "levelText": level_text,
            "metric": f"评分 +{delta:.1f}",
            "detail": f"v{base.version}（{base.risk_score:g} 分，{base.model_version or '—'}）→ v{latest.version}（{latest.risk_score:g} 分，{latest.model_version or '—'}）",
            "sortValue": delta,
        })
    return out


def _collect_all(db: Session) -> list[dict]:
    """汇总三类预警并附加处置状态"""
    today = datetime.now().date()
    # 病例/随访/版本数据本轮只查一次，供三类预警共享
    case_map, visits_by_case, versions_by_case = _load_base_data(db)
    items = (
        _collect_overdue(db, today, case_map)
        + _collect_cognition(case_map, visits_by_case)
        + _collect_risk_rise(case_map, versions_by_case)
    )
    keys = [it["dedupKey"] for it in items]
    handling_map: dict[str, WarningHandling] = {}
    if keys:
        for h in db.query(WarningHandling).filter(WarningHandling.dedup_key.in_(keys)).all():
            handling_map[h.dedup_key] = h
    for it in items:
        h = handling_map.get(it["dedupKey"])
        if h and h.status in ("handled", "ignored"):
            it["handleStatus"] = h.status
            it["handleNote"] = h.note or ""
            it["handler"] = h.handler or ""
            it["handledAt"] = h.handled_at or ""
        else:
            it["handleStatus"] = "open"
            it["handleNote"] = ""
            it["handler"] = ""
            it["handledAt"] = ""
    return items


_LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2}


@router.get("/summary")
def warning_summary(db: Session = Depends(get_db)):
    """
    预警数量汇总：
    - total/high/medium/low：未处理预警（工作台默认视图）
    - handled/ignored：已闭环数量
    - overdue/cognition/riskRise：未处理预警分类型计数
    """
    items = _collect_all(db)
    active = [it for it in items if it["handleStatus"] == "open"]
    by_type: dict[str, int] = {}
    by_level = {"high": 0, "medium": 0, "low": 0}
    for it in active:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1
        by_level[it["level"]] += 1
    handled = sum(1 for it in items if it["handleStatus"] == "handled")
    ignored = sum(1 for it in items if it["handleStatus"] == "ignored")
    return ok({
        "total": len(active),
        "high": by_level["high"],
        "medium": by_level["medium"],
        "low": by_level["low"],
        "overdue": by_type.get("followup-overdue", 0),
        "cognition": by_type.get("cognition-decline", 0),
        "riskRise": by_type.get("risk-rise", 0),
        "handled": handled,
        "ignored": ignored,
    })


@router.get("/list")
def warning_list(
    type: str = Query(default="all", pattern="^(all|overdue|cognition|risk)$"),
    level: str = Query(default="", pattern="^$|^(high|medium|low)$"),
    status: str = Query(default="open", pattern="^(open|handled|ignored|all)$"),
    db: Session = Depends(get_db),
):
    """预警明细，按紧急度（级别→指标值）排序"""
    items = _collect_all(db)
    if type != "all":
        type_map = {"overdue": "followup-overdue", "cognition": "cognition-decline", "risk": "risk-rise"}
        items = [it for it in items if it["type"] == type_map[type]]
    if level:
        items = [it for it in items if it["level"] == level]
    if status != "all":
        items = [it for it in items if it["handleStatus"] == status]
    items.sort(key=lambda x: (_LEVEL_ORDER[x["level"]], -x["sortValue"]))
    return ok({"list": items, "total": len(items)})


class HandlePayload(BaseModel):
    dedupKey: str = Field(max_length=100)
    action: str           # handled / ignored / reopen
    note: str = Field(default="", max_length=500)


@router.post("/handle")
def handle_warning(
    payload: HandlePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """处置预警：标记已处理 / 忽略 / 重新打开（留存处理人与备注，医疗质控可追溯）"""
    if payload.action not in ("handled", "ignored", "reopen"):
        return fail("非法的处置动作")
    if "|" not in payload.dedupKey:
        return fail("预警标识不正确")

    rec = db.query(WarningHandling).filter(WarningHandling.dedup_key == payload.dedupKey).first()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    operator = current_user.get("realName") or current_user.get("username") or ""

    if payload.action == "reopen":
        if rec:
            rec.status = "open"
            rec.note = payload.note.strip()
            rec.handler = operator
            rec.handled_at = now_str
        # 本就没有记录时无需操作
    else:
        if rec is None:
            rec = WarningHandling(dedup_key=payload.dedupKey)
            db.add(rec)
        rec.status = payload.action
        rec.note = payload.note.strip()
        rec.handler = operator
        rec.handled_at = now_str
    db.commit()
    msg = {"handled": "预警已标记为已处理", "ignored": "预警已忽略", "reopen": "预警已重新打开"}[payload.action]
    return ok(None, msg)
