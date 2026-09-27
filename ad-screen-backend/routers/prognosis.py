"""
预后预测与认知衰退轨迹路由
- GET  /prognosis/{patient_no}            单患者认知衰退轨迹预测
- POST /prognosis/cohort-forecast         批量队列预测（最多 8 名患者）

基于纵向随访 MMSE/MoCA 数据 + AI 风险评分，使用最小二乘法线性回归
预测未来 12/24/36 个月认知衰退轨迹，并预估转 AD 时间窗。
"""
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok
from models.case import CaseRecord as CaseModel
from models.analysis import AnalysisVersionRecord, FollowUpVisit
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/prognosis",
    tags=["预后预测"],
    dependencies=[Depends(get_current_user_qs)],
)

DATE_FMT = "%Y-%m-%d"
# MMSE=24 为 AD 诊断常用阈值
AD_THRESHOLD_MMSE = 24.0


def _load_patient(case: CaseModel) -> dict:
    """解析病例 patient_json（与 patient.py 一致，异常时兜底空字典）"""
    try:
        return json.loads(case.patient_json or "{}")
    except json.JSONDecodeError:
        return {}


def _parse_date(s: str):
    """宽松解析 YYYY-MM-DD（兼容 YYYY-MM-DD HH:MM:SS）"""
    try:
        return datetime.strptime((s or "")[:10], DATE_FMT)
    except (ValueError, TypeError):
        return None


def _linear_regression(xs: list, ys: list):
    """
    纯 Python 最小二乘线性回归 y = a*x + b
    返回 (斜率 a, 截距 b, 拟合优度 r2)
    数据点不足 2 个时返回零斜率、首值截距、r2=0
    """
    n = len(xs)
    if n < 2:
        return (0.0, float(ys[0]) if ys else 0.0, 0.0)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    a = num / den if den else 0.0
    b = mean_y - a * mean_x
    # r2 拟合优度
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return (a, b, r2)


def _confidence_level(n: int) -> str:
    """根据数据点数评估置信度：n<3 low / 3-5 medium / >5 high"""
    if n < 3:
        return "low"
    if n <= 5:
        return "medium"
    return "high"


def _risk_stratification(score: Optional[float], decline_rate: Optional[float]) -> tuple:
    """
    风险分层：结合当前 AI 风险评分与 MMSE 年衰退速率
    - high：当前评分≥70 或衰退速率≤-3 分/年
    - medium：评分 40-70 或衰退速率 -3~-1 分/年
    - low：其余
    返回 (level, factors[])
    """
    factors = []
    score_val = score if score is not None else 0.0
    decline_val = decline_rate if decline_rate is not None else 0.0

    # 评分因素
    if score is not None:
        factors.append(f"AI 风险评分 {score:g} 分")

    # 衰退速率因素
    if decline_rate is not None:
        factors.append(f"MMSE 年衰退 {abs(decline_rate):.1f} 分")

    # 分层逻辑
    if score_val >= 70 or (decline_rate is not None and decline_rate <= -3.0):
        level = "high"
    elif (40 <= score_val < 70) or (decline_rate is not None and -3.0 < decline_rate <= -1.0):
        level = "medium"
    else:
        level = "low"

    return level, factors


def _preload_prognosis_data(db: Session):
    """
    一次性批量预取预后预测所需数据（单患者/队列端点共用，避免队列最多 8 次重复全表扫描）：
    - 全部未删病例（exam_date 升序，保持与原查询一致）
    - 全部随访记录（visit_date、id 双升序，按病例集合 Python 过滤后顺序不变）
    - 全部分析版本（version 降序）按 case_id 分组，组内首条即该病例最新版本
    """
    cases = (
        db.query(CaseModel)
        .filter(CaseModel.is_deleted == False)  # noqa: E712
        .order_by(CaseModel.exam_date.asc())
        .all()
    )
    visits = (
        db.query(FollowUpVisit)
        .order_by(FollowUpVisit.visit_date.asc(), FollowUpVisit.id.asc())
        .all()
    )
    latest_ver_map: dict[str, AnalysisVersionRecord] = {}
    for ver in db.query(AnalysisVersionRecord).order_by(AnalysisVersionRecord.version.desc()).all():
        # 全局已按 version 降序，组内首条即最新版本
        if ver.case_id not in latest_ver_map:
            latest_ver_map[ver.case_id] = ver
    return cases, visits, latest_ver_map


def _predict_patient(db: Session, patient_no: str, cache=None) -> dict:
    """
    单患者预后预测核心逻辑：
    1. 查询该患者所有未删除病例（按 exam_date 升序）
    2. 收集 FollowUpVisit MMSE 时间序列（按 visit_date 升序）
    3. 收集 AnalysisVersionRecord 最新版本风险评分（每病例取最新版本，按 exam_date 升序）
    4. 数据点 ≥2 时做线性回归，预测 12/24/36 月 MMSE 与转 AD 时间窗

    cache 为 _preload_prognosis_data 的批量预取结果（队列内多患者复用）；
    不传时函数内部自行预取一次。
    """
    if cache is None:
        cache = _preload_prognosis_data(db)
    cases, all_visits, latest_ver_map = cache

    # ---------- 查询患者所有病例（按 exam_date 升序，参考 patient.py） ----------
    target = [
        c for c in cases
        if (_load_patient(c).get("patientNo") or f"UNKNOWN-{c.id}") == patient_no
    ]
    if not target:
        return {
            "patientNo": patient_no,
            "patient": {"name": "", "gender": "", "age": None},
            "historical": {"mmse": [], "risk": []},
            "prediction": None,
            "message": "未找到该患者病例记录",
        }

    first_p = _load_patient(target[0])
    patient_info = {
        "name": first_p.get("name", ""),
        "gender": first_p.get("gender", ""),
        "age": first_p.get("age"),
    }

    case_ids = [c.id for c in target]

    # ---------- MMSE 历史序列（按 visit_date 升序） ----------
    # 全量随访已按 visit_date/id 双升序预取，按本患者病例集合过滤后相对顺序与原查询一致
    case_id_set = set(case_ids)
    visits = [v for v in all_visits if v.case_id in case_id_set]
    mmse_history = [
        {"date": (v.visit_date or "")[:10], "mmse": v.mmse, "moca": v.moca}
        for v in visits
    ]

    # ---------- AI 风险评分历史（每病例取最新版本，按 exam_date 升序） ----------
    risk_history = []
    for c in target:
        ver = latest_ver_map.get(c.id)
        if ver and ver.risk_score is not None:
            risk_history.append({
                "date": (c.exam_date or "")[:10],
                "score": round(float(ver.risk_score), 1),
                "level": ver.risk_level or c.risk_level or "",
            })

    # ---------- 构建回归样本（MMSE 非空点且日期可解析） ----------
    sample = []
    for v in visits:
        if v.mmse is None:
            continue
        d = _parse_date(v.visit_date)
        if d is None:
            continue
        sample.append((d, float(v.mmse)))

    # ---------- 数据不足时返回 ----------
    if len(sample) < 2:
        return {
            "patientNo": patient_no,
            "patient": patient_info,
            "historical": {"mmse": mmse_history, "risk": risk_history},
            "prediction": None,
            "message": "随访数据不足 2 次，无法预测",
        }

    # ---------- 线性回归 ----------
    base_date = sample[0][0]
    xs = [(d - base_date).days for d, _ in sample]
    ys = [y for _, y in sample]
    a, b, r2 = _linear_regression(xs, ys)

    # 年衰退速率（分/年）= 每日斜率 * 365
    decline_rate = a * 365.0

    # ---------- 预测 12/24/36 个月 MMSE ----------
    today = datetime.now()
    forecast = []
    for months in (12, 24, 36):
        future_date = today + timedelta(days=months * 30)
        x_future = (future_date - base_date).days
        y_pred = a * x_future + b
        # MMSE 取值范围限制在 0-30
        y_pred = max(0.0, min(30.0, y_pred))
        forecast.append({
            "months": months,
            "mmse": round(y_pred, 1),
            "date": future_date.strftime(DATE_FMT),
        })

    # ---------- 预计转 AD 时间窗（MMSE 降至 24 分） ----------
    ad_window = None
    if a < 0:
        # (24 - b) / a = 所需天数；转月数 = 天数 / 30
        days_to_ad = (AD_THRESHOLD_MMSE - b) / a
        if days_to_ad > 0:
            months_to_ad = days_to_ad / 30.0
            ad_date = base_date + timedelta(days=days_to_ad)
            # 仅当未来时间窗合理（0-360 月内）时返回
            if 0 < months_to_ad <= 360:
                ad_window = {
                    "months": round(months_to_ad, 1),
                    "date": ad_date.strftime(DATE_FMT),
                }

    # ---------- 当前 AI 风险评分（最新一条） ----------
    current_score = risk_history[-1]["score"] if risk_history else None

    # ---------- 置信度 ----------
    confidence = _confidence_level(len(sample))

    # ---------- 风险分层 ----------
    level, factors = _risk_stratification(current_score, decline_rate)

    prediction_result = {
        "slope": round(a, 6),
        "intercept": round(b, 2),
        "declineRate": round(decline_rate, 2),
        "r2": round(r2, 3),
        "forecast": forecast,
        "adConversionWindow": ad_window,
        "confidenceLevel": confidence,
        "dataPoints": len(sample),
    }

    risk_strat = {"level": level, "factors": factors}

    return {
        "patientNo": patient_no,
        "patient": patient_info,
        "historical": {"mmse": mmse_history, "risk": risk_history},
        "prediction": prediction_result,
        "riskStratification": risk_strat,
    }


# ==================== 端点 ====================

@router.get("/{patient_no}")
def prognosis(patient_no: str, db: Session = Depends(get_db)):
    """单患者认知衰退轨迹预测"""
    cache = _preload_prognosis_data(db)
    return ok(_predict_patient(db, patient_no, cache))


class CohortRequest(BaseModel):
    """队列批量预测请求体"""
    patientNos: list[str] = []


@router.post("/cohort-forecast")
def cohort_forecast(req: CohortRequest, db: Session = Depends(get_db)):
    """
    批量队列预测（最多 8 名患者）：
    对每个患者调用预测逻辑，返回衰退速率、36 月预测值、风险层级的摘要。
    """
    patient_nos = req.patientNos[:8]
    if not patient_nos:
        return ok([])

    # 本轮队列请求只批量预取一次，8 名患者共享病例/随访/版本索引
    cache = _preload_prognosis_data(db)
    summaries = []
    for no in patient_nos:
        result = _predict_patient(db, no, cache)
        raw_pred = result.get("prediction")
        pred = raw_pred or {}
        forecast_36 = None
        if raw_pred and pred.get("forecast"):
            for f in pred["forecast"]:
                if f["months"] == 36:
                    forecast_36 = f["mmse"]
                    break
        strat = result.get("riskStratification") or {}
        summaries.append({
            "patientNo": no,
            "name": (result.get("patient") or {}).get("name", ""),
            "declineRate": pred.get("declineRate") if raw_pred else None,
            "forecast36M": forecast_36,
            "level": strat.get("level") if strat else None,
            "hasPrediction": raw_pred is not None,
        })

    return ok(summaries)
