"""
患者全景档案路由（Patient 360）
- GET /patient/list                患者清单（按 patientNo 聚合病例数/最近检查/最新风险）
- GET /patient/{patient_no}        患者全景：基本信息 + 病例 + 风险趋势 + 认知趋势 + 时间轴

以"患者"为中心串联：多次检查病例、AI 分析版本、干预方案、随访记录与报告状态，
弥补病例库以单次检查为维度的不足，支撑纵向病程管理。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from schemas.common import ok
from models.case import CaseRecord as CaseModel
from models.analysis import (
    AnalysisVersionRecord, FollowUpVisit, InterventionRecord,
)
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/patient",
    tags=["患者全景档案"],
    dependencies=[Depends(get_current_user_qs)],
)


def _load_patient(case: CaseModel) -> dict:
    try:
        return json.loads(case.patient_json or "{}")
    except json.JSONDecodeError:
        return {}


def _parse_dt(s: str):
    """宽松解析日期时间（兼容 YYYY-MM-DD 与 YYYY-MM-DD HH:MM:SS，忽略微秒）"""
    s = (s or "").strip()
    if not s:
        return None
    for text in (s[:19], s[:10]):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def _case_brief(c: CaseModel) -> dict:
    return {
        "caseId": c.id,
        "examDate": c.exam_date,
        "modality": c.modality,
        "department": c.department or "",
        "status": c.status,
        "diagStatus": c.diag_status or "",
        "riskLevel": c.risk_level,
        "riskScore": c.risk_score,
        "hasMRI": bool(c.has_mri),
        "hasPET": bool(c.has_pet),
        "createTime": c.create_time or "",
    }


@router.get("/list")
def patient_list(keyword: str = Query(default=""), db: Session = Depends(get_db)):
    """患者清单：按 patientNo 聚合（同一患者多次检查只显示一行）"""
    cases = (
        db.query(CaseModel)
        .filter(CaseModel.is_deleted == False)  # noqa: E712
        .order_by(CaseModel.exam_date.desc())
        .all()
    )
    grouped: dict[str, dict] = {}
    for c in cases:
        p = _load_patient(c)
        no = (p.get("patientNo") or "").strip()
        if not no:
            no = f"UNKNOWN-{c.id}"
        g = grouped.get(no)
        if g is None:
            g = {
                "patientNo": no,
                "name": p.get("name", ""),
                "gender": p.get("gender", ""),
                "age": p.get("age"),
                "caseCount": 0,
                "latestExamDate": c.exam_date,
                "latestRiskLevel": c.risk_level,
                "latestRiskScore": c.risk_score,
                "department": c.department or "",
            }
            grouped[no] = g
        g["caseCount"] += 1
        # 列表已按 exam_date 倒序，首次出现即最近一次检查
    if keyword.strip():
        kw = keyword.strip()
        grouped = {
            no: g for no, g in grouped.items()
            if kw in no or kw in (g["name"] or "")
        }
    return ok({"list": list(grouped.values()), "total": len(grouped)})


@router.get("/{patient_no}")
def patient_profile(patient_no: str, db: Session = Depends(get_db)):
    """患者全景聚合页"""
    cases = (
        db.query(CaseModel)
        .filter(CaseModel.is_deleted == False)  # noqa: E712
        .order_by(CaseModel.exam_date.asc())
        .all()
    )
    target = [c for c in cases if (_load_patient(c).get("patientNo") or f"UNKNOWN-{c.id}") == patient_no]
    if not target:
        return ok(None)

    case_ids = [c.id for c in target]
    first_p = _load_patient(target[0])

    # ---------- 病例（按检查时间倒序展示） ----------
    case_briefs = [_case_brief(c) for c in sorted(target, key=lambda x: x.exam_date or "", reverse=True)]

    # ---------- AI 风险趋势：每例取最新版本，按检查时间升序 ----------
    risk_trend = []
    for c in sorted(target, key=lambda x: x.exam_date or ""):
        ver = (
            db.query(AnalysisVersionRecord)
            .filter(AnalysisVersionRecord.case_id == c.id)
            .order_by(AnalysisVersionRecord.version.desc())
            .first()
        )
        if ver and ver.risk_score is not None:
            risk_trend.append({
                "date": (c.exam_date or "")[:10],
                "caseId": c.id,
                "score": round(float(ver.risk_score), 1),
                "level": ver.risk_level or c.risk_level or "",
                "version": ver.version,
                "reviewStatus": ver.review_status,
            })

    # ---------- 认知量表趋势：跨全部病例的随访记录 ----------
    visits = (
        db.query(FollowUpVisit)
        .filter(FollowUpVisit.case_id.in_(case_ids))
        .order_by(FollowUpVisit.visit_date.asc(), FollowUpVisit.id.asc())
        .all()
    )
    cognition = [{
        "date": (v.visit_date or "")[:10],
        "caseId": v.case_id,
        "mmse": v.mmse,
        "moca": v.moca,
        "cognitionChange": v.cognition_change,
        "visitType": v.visit_type,
    } for v in visits]

    # ---------- 干预方案覆盖情况 ----------
    interv_cases = {
        r.case_id for r in db.query(InterventionRecord)
        .filter(InterventionRecord.case_id.in_(case_ids)).all()
    }
    for b in case_briefs:
        b["hasIntervention"] = b["caseId"] in interv_cases

    # ---------- 纵向时间轴 ----------
    timeline = []
    for c in target:
        if c.create_time:
            timeline.append({
                "time": c.create_time,
                "kind": "case",
                "kindLabel": "建档",
                "caseId": c.id,
                "title": f"建立病例档案（{c.modality}）",
                "desc": f"检查日期 {(c.exam_date or '')[:10]} · {c.department or '科室未填'}",
            })
        vers = (
            db.query(AnalysisVersionRecord)
            .filter(AnalysisVersionRecord.case_id == c.id)
            .order_by(AnalysisVersionRecord.version.asc())
            .all()
        )
        for ver in vers:
            review_text = {"approved": "已审核通过", "rejected": "审核驳回", "pending": "待审核"}.get(
                ver.review_status, ver.review_status
            )
            timeline.append({
                "time": ver.created_at or (c.exam_date or "")[:10],
                "kind": "analysis",
                "kindLabel": "AI 分析",
                "caseId": c.id,
                "title": f"AI 分析 v{ver.version} · 风险 {ver.risk_score:g} 分",
                "desc": f"{ver.model_version or '模型版本未知'} · {review_text}"
                          + (f"（{ver.reviewer}）" if ver.reviewer else ""),
            })
        if c.id in interv_cases:
            # 干预表无独立时间戳，挂靠该例最近一次分析版本时间
            anchor = ver.created_at if vers else c.create_time
            timeline.append({
                "time": anchor or (c.exam_date or "")[:10],
                "kind": "intervention",
                "kindLabel": "干预",
                "caseId": c.id,
                "title": "保存个体化干预方案",
                "desc": "非药物干预 / 药物治疗 / 家属照护建议",
            })
    for v in visits:
        score_txt = []
        if v.mmse is not None:
            score_txt.append(f"MMSE {v.mmse:g}")
        if v.moca is not None:
            score_txt.append(f"MoCA {v.moca:g}")
        timeline.append({
            "time": (v.visit_date or "")[:10],
            "kind": "visit",
            "kindLabel": "随访",
            "caseId": v.case_id,
            "title": f"{v.visit_type} · {v.cognition_change}" + (f"（{'/'.join(score_txt)}）" if score_txt else ""),
            "desc": (v.notes or f"用药依从性：{v.medication_adherence}")[:80],
        })

    def _tkey(ev: dict):
        dt = _parse_dt(ev["time"])
        return dt or datetime.min

    timeline.sort(key=_tkey, reverse=True)

    latest = case_briefs[0] if case_briefs else {}
    summary = {
        "caseCount": len(target),
        "followUpCount": len(visits),
        "interventionCount": len(interv_cases),
        "firstExamDate": (target[0].exam_date or "")[:10],
        "latestExamDate": (latest.get("examDate") or "")[:10],
        "latestRiskLevel": latest.get("riskLevel"),
        "latestRiskScore": latest.get("riskScore"),
        "mmseTrend": None,
    }
    mmse_vals = [v.mmse for v in visits if v.mmse is not None]
    if len(mmse_vals) >= 2:
        summary["mmseTrend"] = round(float(mmse_vals[-1] - mmse_vals[0]), 1)

    return ok({
        "patient": {
            "patientNo": patient_no,
            "name": first_p.get("name", ""),
            "gender": first_p.get("gender", ""),
            "age": first_p.get("age"),
        },
        "summary": summary,
        "cases": case_briefs,
        "riskTrend": risk_trend,
        "cognition": cognition,
        "timeline": timeline,
    })


# ==================== 队列纵向轨迹对比 ====================

class CohortRequest(BaseModel):
    """队列轨迹对比请求体"""
    patientNos: list[str] = []


@router.post("/cohort-trajectory")
def cohort_trajectory(req: CohortRequest, db: Session = Depends(get_db)):
    """
    队列纵向轨迹对比：批量获取多名患者的 AI 风险评分趋势与 MMSE 趋势，
    供前端叠加折线图对比（最多 8 名患者）。
    """
    patient_nos = req.patientNos[:8]
    if not patient_nos:
        return ok({"patients": []})

    cases = (
        db.query(CaseModel)
        .filter(CaseModel.is_deleted == False)  # noqa: E712
        .order_by(CaseModel.exam_date.asc())
        .all()
    )
    # 按患者编号分组
    grouped: dict[str, list[CaseModel]] = {}
    for c in cases:
        p = _load_patient(c)
        no = (p.get("patientNo") or "").strip()
        if no and no in patient_nos:
            grouped.setdefault(no, []).append(c)

    result = []
    for no in patient_nos:
        target = grouped.get(no, [])
        if not target:
            continue
        first_p = _load_patient(target[0])
        case_ids = [c.id for c in target]

        # 风险趋势
        risk_trend = []
        for c in target:
            ver = (
                db.query(AnalysisVersionRecord)
                .filter(AnalysisVersionRecord.case_id == c.id)
                .order_by(AnalysisVersionRecord.version.desc())
                .first()
            )
            if ver and ver.risk_score is not None:
                risk_trend.append({
                    "date": (c.exam_date or "")[:10],
                    "score": round(float(ver.risk_score), 1),
                })

        # MMSE 趋势
        visits = (
            db.query(FollowUpVisit)
            .filter(FollowUpVisit.case_id.in_(case_ids))
            .order_by(FollowUpVisit.visit_date.asc())
            .all()
        )
        mmse_trend = [
            {"date": (v.visit_date or "")[:10], "mmse": v.mmse}
            for v in visits if v.mmse is not None
        ]

        result.append({
            "patientNo": no,
            "name": first_p.get("name", ""),
            "gender": first_p.get("gender", ""),
            "age": first_p.get("age"),
            "riskTrend": risk_trend,
            "mmseTrend": mmse_trend,
            "caseCount": len(target),
            "visitCount": len(visits),
        })

    return ok({"patients": result})
