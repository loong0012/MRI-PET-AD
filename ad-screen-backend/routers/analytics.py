"""
科研统计分析路由（researcher / admin）
------------------------------------------------------------------
面向科研管理员的队列统计看板数据源，全部基于真实病例库（过滤软删除）：
- GET /analytics/demographics     队列人群画像（年龄段 / 性别分布）
- GET /analytics/score-histogram  AI 风险评分直方图（0-100 分 10 桶）
- GET /analytics/agreement        AI 分级 vs 医生审核一致率（每病例取最新版本，按风险等级细分）
- GET /analytics/followup-stats   随访质量统计（覆盖率 / 量表均值 / 认知变化 / 依从性）
- GET /analytics/monthly-trend    筛查量与高风险占比（按检查日期真实统计，支持 3/6/12 月）

通用筛选参数（query string）：
- modality: MRI / PET / MRI+PET（可选，缺省=全部模态）
- months:   monthly-trend 专用，3 / 6 / 12（缺省 6）
"""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, Integer
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok
from models.case import CaseRecord
from models.analysis import AnalysisVersionRecord, FollowUpRecord, FollowUpVisit, InterventionRecord
from services.auth import require_role

router = APIRouter(
    prefix="/analytics",
    tags=["科研统计"],
    # 科研聚合视图（含全院风险分布/随访质量）限科研角色与管理员，与前端路由 meta.roles 对齐
    dependencies=[Depends(require_role("researcher", "admin"))],
)

# 风险等级展示名（与前端统一）
RISK_LABELS = {"low": "低风险", "mci": "轻度认知障碍", "ad-early": "AD 早期", "ad-late": "AD 中晚期"}


def _case_filter(db: Session, modality: str):
    """统一病例基础过滤：未软删除 + 可选模态"""
    q = db.query(CaseRecord).filter(CaseRecord.is_deleted == False)  # noqa: E712
    if modality:
        q = q.filter(CaseRecord.modality == modality)
    return q


def _validate_modality(modality: str) -> str:
    """模态参数白名单校验（防脏值静默返回全空）"""
    return modality if modality in ("MRI", "PET", "MRI+PET") else ""


@router.get("/demographics")
def get_demographics(db: Session = Depends(get_db), modality: str = Query(default="")):
    """队列人群画像：年龄段分布 + 性别分布"""
    modality = _validate_modality(modality)
    AGE_BUCKETS = ["<50", "50-59", "60-69", "70-79", "80+"]
    age_counter: Counter = Counter()
    gender_counter: Counter = Counter()

    for c in _case_filter(db, modality).all():
        try:
            patient = json.loads(c.patient_json or "{}")
        except json.JSONDecodeError:
            continue
        gender_counter[{"M": "男", "F": "女"}.get(str(patient.get("gender", "")), str(patient.get("gender", "未知")))] += 1
        try:
            age = int(patient.get("age", 0))
        except (TypeError, ValueError):
            continue
        if age <= 0:
            continue
        if age < 50:
            age_counter["<50"] += 1
        elif age < 60:
            age_counter["50-59"] += 1
        elif age < 70:
            age_counter["60-69"] += 1
        elif age < 80:
            age_counter["70-79"] += 1
        else:
            age_counter["80+"] += 1

    return ok({
        "total": sum(gender_counter.values()),
        "ageGroups": [{"name": b, "value": int(age_counter.get(b, 0))} for b in AGE_BUCKETS],
        "genderDist": [{"name": g, "value": int(v)} for g, v in gender_counter.most_common()],
    })


@router.get("/score-histogram")
def get_score_histogram(db: Session = Depends(get_db), modality: str = Query(default="")):
    """AI 风险评分直方图（0-100 分 10 桶，仅已出分病例）

    优化：risk_score 为真实列，分桶在 SQL 内完成（CASE WHEN → GROUP BY），
    不再把全表行加载进 Python 逐行计数。病例量大时 IO/内存从 O(N) 降为 O(10)。
    """
    modality = _validate_modality(modality)
    # 桶索引：CAST(risk_score/10 AS INT)，100 分归入第 9 桶（min(idx,9) 语义一致）
    bucket_idx = func.min(func.cast(CaseRecord.risk_score / 10.0, Integer), 9)
    q = (
        db.query(bucket_idx.label("b"), func.count().label("cnt"))
        .filter(
            CaseRecord.is_deleted == False,  # noqa: E712
            CaseRecord.risk_score.isnot(None),
        )
    )
    if modality:
        q = q.filter(CaseRecord.modality == modality)
    rows = q.group_by("b").all()

    buckets = [{"bucket": f"{i * 10}-{i * 10 + 10}", "count": 0} for i in range(10)]
    total = 0
    for r in rows:
        if r.b is None:
            continue
        b = int(r.b)
        if 0 <= b <= 9:
            buckets[b]["count"] += int(r.cnt)
            total += int(r.cnt)
    return ok({"total": total, "histogram": buckets})


@router.get("/agreement")
def get_agreement(db: Session = Depends(get_db), modality: str = Query(default="")):
    """
    AI 分级 vs 医生审核一致率（按风险等级细分）。
    口径：每病例仅取**最新版本**（多版本重复分析不重复计数），
    医生已处理（approved+rejected）版本中 approved 占比为"医生认可率"；
    各风险等级分别统计，反映模型在高风险段的临床可信度。
    """
    modality = _validate_modality(modality)
    # 关联有效病例（软删除/模态过滤），按病例分组取最大版本号（版本量级小，Python 分组即可）
    versions = (
        db.query(AnalysisVersionRecord)
        .join(CaseRecord, AnalysisVersionRecord.case_id == CaseRecord.id)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
    )
    if modality:
        versions = versions.filter(CaseRecord.modality == modality)
    latest: dict[str, AnalysisVersionRecord] = {}
    for v in versions.all():
        cur = latest.get(v.case_id)
        if cur is None or (v.version or 0) > (cur.version or 0):
            latest[v.case_id] = v

    overall = {"total": 0, "pending": 0, "approved": 0, "rejected": 0}
    by_level: dict[str, dict] = {}

    for v in latest.values():
        st = v.review_status or "pending"
        if st not in ("pending", "approved", "rejected"):
            continue
        overall["total"] += 1
        overall[st] += 1
        lv = v.risk_level or "unknown"
        if lv not in by_level:
            by_level[lv] = {"level": lv, "levelName": RISK_LABELS.get(lv, "未知"), "total": 0, "approved": 0}
        by_level[lv]["total"] += 1
        if st == "approved":
            by_level[lv]["approved"] += 1

    reviewed = overall["approved"] + overall["rejected"]
    approval_rate = round(overall["approved"] / reviewed * 100, 1) if reviewed else 0.0

    level_rows = []
    for lv in ("low", "mci", "ad-early", "ad-late"):
        item = by_level.get(lv)
        if not item or item["total"] == 0:
            continue
        item["approvalRate"] = round(item["approved"] / item["total"] * 100, 1)
        level_rows.append(item)

    return ok({
        **overall,
        "reviewed": reviewed,
        "approvalRate": approval_rate,
        "byLevel": level_rows,
    })


@router.get("/followup-stats")
def get_followup_stats(db: Session = Depends(get_db), modality: str = Query(default="")):
    """随访质量统计：计划/执行覆盖、量表均值、认知变化与依从性分布（关联有效病例）"""
    modality = _validate_modality(modality)
    plan_q = (
        db.query(FollowUpRecord)
        .join(CaseRecord, FollowUpRecord.case_id == CaseRecord.id)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
    )
    visit_q = (
        db.query(FollowUpVisit)
        .join(CaseRecord, FollowUpVisit.case_id == CaseRecord.id)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
    )
    if modality:
        plan_q = plan_q.filter(CaseRecord.modality == modality)
        visit_q = visit_q.filter(CaseRecord.modality == modality)

    plans = plan_q.all()
    visits = visit_q.all()

    mmse_vals = [v.mmse for v in visits if v.mmse is not None]
    moca_vals = [v.moca for v in visits if v.moca is not None]
    cognition = Counter((v.cognition_change or "稳定") for v in visits)
    adherence = Counter((v.medication_adherence or "规律") for v in visits)
    visit_type = Counter((v.visit_type or "门诊复诊") for v in visits)

    # 认知变化按固定顺序输出（便于前端图序稳定）
    COGNITION_ORDER = ["改善", "稳定", "轻度下降", "明显下降"]
    ADHERENCE_ORDER = ["规律", "偶有漏服", "经常漏服", "已停药"]

    return ok({
        "planCount": len(plans),
        "visitCount": len(visits),
        "coveredCases": len({v.case_id for v in visits}),
        "avgMmse": round(sum(mmse_vals) / len(mmse_vals), 1) if mmse_vals else None,
        "avgMoca": round(sum(moca_vals) / len(moca_vals), 1) if moca_vals else None,
        "cognitionDist": [{"name": k, "value": int(cognition.get(k, 0))} for k in COGNITION_ORDER],
        "adherenceDist": [{"name": k, "value": int(adherence.get(k, 0))} for k in ADHERENCE_ORDER],
        "visitTypeDist": [{"name": k, "value": int(v)} for k, v in visit_type.most_common()],
    })


@router.get("/monthly-trend")
def get_monthly_trend(
    db: Session = Depends(get_db),
    modality: str = Query(default=""),
    months: int = Query(default=6, ge=3, le=12),
):
    """
    近 N 个月筛查量与高风险占比（按检查日期真实聚合，替代演示随机趋势）。
    month 格式 YYYY-MM；positiveRate = 高风险病例（ad-early/ad-late）占比 %。
    """
    modality = _validate_modality(modality)
    today = datetime.now()
    month_keys: list[str] = []
    for i in range(months - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        month_keys.append(f"{y:04d}-{m:02d}")

    stat = {m: {"total": 0, "highRisk": 0} for m in month_keys}
    for c in _case_filter(db, modality).all():
        key = (c.exam_date or "")[:7]
        if key in stat:
            stat[key]["total"] += 1
            if c.risk_level in ("ad-early", "ad-late"):
                stat[key]["highRisk"] += 1

    points = []
    for m in month_keys:
        total = stat[m]["total"]
        high = stat[m]["highRisk"]
        points.append({
            "month": m,
            "total": total,
            "highRisk": high,
            "positiveRate": round(high / total * 100, 1) if total else 0.0,
        })
    return ok(points)


@router.get("/cognitive-trend")
def get_cognitive_trend(months: int = Query(default=12, ge=3, le=24), db: Session = Depends(get_db)):
    """
    认知功能纵向趋势：聚合所有非软删病例的随访记录，按月平均 MMSE / MoCA。
    返回最近 N 个月（默认 12）每月：
    - avgMMSE / avgMoCA：当月所有随访的平均认知评分（无数据为 null）
    - patientCount：当月随访患者数（按 case_id 去重）
    - adConversionCount：当月随访中属于 ad-early / ad-late 风险的病例数
    """
    # 取所有非软删病例 id 集
    valid_cases = db.query(CaseRecord).filter(CaseRecord.is_deleted == False).all()  # noqa: E712
    valid_case_ids = [c.id for c in valid_cases]
    if not valid_case_ids:
        return ok([], "暂无有效病例")

    # 拉取所有随访记录（在有效病例范围内）
    visits = (
        db.query(FollowUpVisit)
        .filter(FollowUpVisit.case_id.in_(valid_case_ids))
        .all()
    )

    # 按月份分桶
    bucket: dict[str, list] = defaultdict(list)
    for v in visits:
        if not v.visit_date:
            continue
        # visit_date 存储为 'YYYY-MM-DD' 字符串，直接取前 7 位
        month_key = str(v.visit_date)[:7]
        if len(month_key) < 7 or (v.mmse is None and v.moca is None):
            continue
        bucket[month_key].append(v)

    if not bucket:
        return ok([], "暂无随访数据")

    # 构建最近 N 个月的有序列表（与 monthly-trend 一致的口径）
    today = datetime.now()
    month_keys: list[str] = []
    for i in range(months - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        month_keys.append(f"{y:04d}-{m:02d}")

    # 预拉取 AD 风险病例集（用于 adConversionCount）
    ad_case_ids = {
        c.id for c in valid_cases if c.risk_level in ("ad-early", "ad-late")
    }

    result = []
    for mk in month_keys:
        v_list = bucket.get(mk, [])
        mmse_vals = [v.mmse for v in v_list if v.mmse is not None]
        moca_vals = [v.moca for v in v_list if v.moca is not None]
        patient_count = len({v.case_id for v in v_list})
        ad_count = len({v.case_id for v in v_list if v.case_id in ad_case_ids})
        result.append({
            "month": mk,
            "avgMMSE": round(sum(mmse_vals) / len(mmse_vals), 2) if mmse_vals else None,
            "avgMoCA": round(sum(moca_vals) / len(moca_vals), 2) if moca_vals else None,
            "patientCount": patient_count,
            "adConversionCount": ad_count,
        })

    return ok(result)


# ==================== 风险因子关联分析 ====================

def _pearson(xs: list[float], ys: list[float]) -> float:
    """皮尔逊相关系数（两序列等长，空值已剔除配对）"""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return round(num / (dx * dy), 3)


def _linear_regression(xs: list[float], ys: list[float]) -> dict:
    """最小二乘线性回归 + R²"""
    n = len(xs)
    if n < 2:
        return {"slope": 0, "intercept": 0, "r2": 0}
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / syy if syy else 0
    return {"slope": round(slope, 4), "intercept": round(intercept, 4), "r2": round(r2, 4)}


@router.get("/correlation")
def get_correlation(db: Session = Depends(get_db), modality: str = Query(default="")):
    """
    风险因子关联分析：年龄 / MMSE / MoCA / AI 风险评分 的皮尔逊相关矩阵、
    散点回归（年龄 vs 评分、MMSE vs 评分）、分组箱线图（性别 vs 评分、风险等级 vs MMSE）。
    """
    modality = _validate_modality(modality)
    cases = _case_filter(db, modality).all()

    # 收集每病例的最新随访 MMSE / MoCA
    case_ids = [c.id for c in cases]
    latest_visits: dict[str, FollowUpVisit] = {}
    if case_ids:
        for v in db.query(FollowUpVisit).filter(FollowUpVisit.case_id.in_(case_ids)).order_by(FollowUpVisit.visit_date.desc()).all():
            if v.case_id not in latest_visits:
                latest_visits[v.case_id] = v

    FACTOR_LABELS = ["年龄", "MMSE", "MoCA", "AI 风险评分"]
    # 收集因子数据（每行=一个病例，四列因子）
    rows: list[dict] = []  # {caseId, patientName, age, mmse, moca, riskScore, gender, riskLevel}
    for c in cases:
        try:
            p = json.loads(c.patient_json or "{}")
        except json.JSONDecodeError:
            continue
        age = _safe_int(p.get("age"))
        gender = str(p.get("gender", ""))
        name = p.get("name", "")
        risk = _safe_float(c.risk_score)
        visit = latest_visits.get(c.id)
        mmse = visit.mmse if visit else None
        moca = visit.moca if visit else None
        rows.append({
            "caseId": c.id, "patientName": name, "age": age, "gender": gender,
            "mmse": mmse, "moca": moca, "riskScore": risk,
            "riskLevel": c.risk_level or "",
        })

    # 皮尔逊相关矩阵（配对剔除缺失值）
    factors = ["age", "mmse", "moca", "riskScore"]
    matrix = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            if i == j:
                matrix[i][j] = 1.0
            elif i < j:
                pairs = [(r[factors[i]], r[factors[j]]) for r in rows
                         if r[factors[i]] is not None and r[factors[j]] is not None]
                xs = [p[0] for p in pairs]
                ys = [p[1] for p in pairs]
                corr = _pearson(xs, ys)
                matrix[i][j] = corr
                matrix[j][i] = corr

    # 散点 + 回归线：年龄 vs 评分
    age_risk_pairs = [(r["age"], r["riskScore"], r["caseId"], r["patientName"])
                      for r in rows if r["age"] is not None and r["riskScore"] is not None]
    age_risk_scatter = [{"x": p[0], "y": p[1], "caseId": p[2], "name": p[3]} for p in age_risk_pairs]
    age_risk_reg = _linear_regression([p[0] for p in age_risk_pairs], [p[1] for p in age_risk_pairs])

    # 散点 + 回归线：MMSE vs 评分
    mmse_risk_pairs = [(r["mmse"], r["riskScore"], r["caseId"], r["patientName"])
                       for r in rows if r["mmse"] is not None and r["riskScore"] is not None]
    mmse_risk_scatter = [{"x": p[0], "y": p[1], "caseId": p[2], "name": p[3]} for p in mmse_risk_pairs]
    mmse_risk_reg = _linear_regression([p[0] for p in mmse_risk_pairs], [p[1] for p in mmse_risk_pairs])

    # 分组箱线图：性别 vs 评分
    gender_groups: dict[str, list[float]] = {}
    for r in rows:
        if r["riskScore"] is not None and r["gender"]:
            g = "男" if r["gender"] == "M" else "女" if r["gender"] == "F" else "未知"
            gender_groups.setdefault(g, []).append(r["riskScore"])
    gender_box = [
        {"name": g, "values": sorted(vals)} for g, vals in gender_groups.items() if vals
    ]

    # 分组箱线图：风险等级 vs MMSE
    level_mmse: dict[str, list[float]] = {}
    for r in rows:
        if r["mmse"] is not None and r["riskLevel"]:
            lv = RISK_LABELS.get(r["riskLevel"], "未知")
            level_mmse.setdefault(lv, []).append(r["mmse"])
    level_order = ["低风险", "轻度认知障碍", "AD 早期", "AD 中晚期"]
    level_box = [
        {"name": lv, "values": sorted(level_mmse.get(lv, []))}
        for lv in level_order if level_mmse.get(lv)
    ]

    return ok({
        "factorLabels": FACTOR_LABELS,
        "matrix": matrix,
        "scatter": {
            "ageRisk": {"points": age_risk_scatter, "regression": age_risk_reg, "xLabel": "年龄（岁）", "yLabel": "AI 风险评分"},
            "mmseRisk": {"points": mmse_risk_scatter, "regression": mmse_risk_reg, "xLabel": "MMSE 评分", "yLabel": "AI 风险评分"},
        },
        "boxplots": {
            "genderRisk": gender_box,
            "levelMmse": level_box,
        },
        "sampleCount": len(rows),
    })


def _safe_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ==================== 临床路径流转桑基图 ====================

@router.get("/pathway")
def get_pathway(db: Session = Depends(get_db), modality: str = Query(default="")):
    """
    临床路径流转桑基图：按病例当前所处阶段聚合，展示全队列流转分布与瓶颈。
    阶段定义：建档 → 待阅片 → AI 分析中 → 分析完成 → 报告待审 → 报告完成 → 随访中 → 已干预
    """
    modality = _validate_modality(modality)
    cases = _case_filter(db, modality).all()
    case_ids = [c.id for c in cases]

    # 辅助：判断病例是否已有随访/干预
    has_followup = set()
    has_intervention = set()
    if case_ids:
        has_followup = {r.case_id for r in db.query(FollowUpVisit).filter(FollowUpVisit.case_id.in_(case_ids)).all()}
        has_intervention = {r.case_id for r in db.query(InterventionRecord).filter(InterventionRecord.case_id.in_(case_ids)).all()}
    has_report = {c.id for c in cases if c.status == "reported"}
    # 最新版本审核状态
    latest_versions: dict[str, AnalysisVersionRecord] = {}
    if case_ids:
        for v in db.query(AnalysisVersionRecord).filter(AnalysisVersionRecord.case_id.in_(case_ids)).all():
            cur = latest_versions.get(v.case_id)
            if cur is None or (v.version or 0) > (cur.version or 0):
                latest_versions[v.case_id] = v

    STAGES = ["建档", "待阅片", "AI 分析中", "分析完成", "报告待审", "报告完成", "随访中", "已干预"]
    stage_counts = Counter()

    for c in cases:
        if c.id in has_intervention:
            stage_counts["已干预"] += 1
        elif c.id in has_followup:
            stage_counts["随访中"] += 1
        elif c.status == "reported" or c.id in has_report:
            stage_counts["报告完成"] += 1
        elif c.status == "completed":
            ver = latest_versions.get(c.id)
            if ver and ver.review_status == "pending":
                stage_counts["报告待审"] += 1
            elif ver and ver.review_status in ("approved", "rejected"):
                stage_counts["报告完成"] += 1
            else:
                stage_counts["分析完成"] += 1
        elif c.status == "analyzing":
            stage_counts["AI 分析中"] += 1
        else:
            stage_counts["待阅片"] += 1

    # 建档为入口节点（全部病例都经过建档）
    stage_counts["建档"] = len(cases)

    # 桑基 links：相邻阶段流转
    links = []
    for i in range(len(STAGES) - 1):
        src = STAGES[i]
        tgt = STAGES[i + 1]
        # 流入下一阶段的量 = 该阶段及之后所有阶段病例数
        remaining = sum(stage_counts.get(STAGES[j], 0) for j in range(i + 1, len(STAGES)))
        if remaining > 0:
            links.append({"source": src, "target": tgt, "value": remaining})

    nodes = [{"name": s} for s in STAGES]
    bottlenecks = [
        {"stage": s, "count": stage_counts.get(s, 0),
         "pct": round(stage_counts.get(s, 0) / len(cases) * 100, 1) if cases else 0}
        for s in STAGES if stage_counts.get(s, 0) > 0
    ]

    return ok({
        "nodes": nodes,
        "links": links,
        "bottlenecks": bottlenecks,
        "totalCases": len(cases),
    })


# ==================== AI 模型版本对比 ====================

@router.get("/model-comparison")
def get_model_comparison(db: Session = Depends(get_db), modality: str = Query(default="")):
    """
    AI 模型版本对比：按不同 model_version 分组，对比各版本在真实病例库上的表现。
    指标：样本量、风险评分均值、高风险占比、医生通过率、各风险等级分布。
    用于评估模型迭代效果（A/B 测试视角）。
    """
    modality = _validate_modality(modality)
    versions_q = (
        db.query(AnalysisVersionRecord)
        .join(CaseRecord, AnalysisVersionRecord.case_id == CaseRecord.id)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
    )
    if modality:
        versions_q = versions_q.filter(CaseRecord.modality == modality)

    all_versions = versions_q.all()

    # 每病例取最新版本（对比基于最新口径）
    latest: dict[str, AnalysisVersionRecord] = {}
    for v in all_versions:
        cur = latest.get(v.case_id)
        if cur is None or (v.version or 0) > (cur.version or 0):
            latest[v.case_id] = v

    # 按模型版本分组
    groups: dict[str, list[AnalysisVersionRecord]] = {}
    for v in latest.values():
        mv = v.model_version or "未标注"
        groups.setdefault(mv, []).append(v)

    # 对比每组的指标
    results = []
    for mv, records in groups.items():
        scores = [float(v.risk_score) for v in records if v.risk_score is not None]
        high_risk = sum(1 for v in records if v.risk_level in ("ad-early", "ad-late"))
        approved = sum(1 for v in records if v.review_status == "approved")
        rejected = sum(1 for v in records if v.review_status == "rejected")
        pending = sum(1 for v in records if v.review_status == "pending")
        reviewed = approved + rejected
        level_dist: Counter = Counter(v.risk_level or "unknown" for v in records)

        results.append({
            "modelVersion": mv,
            "sampleCount": len(records),
            "avgScore": round(sum(scores) / len(scores), 1) if scores else 0,
            "minScore": round(min(scores), 1) if scores else 0,
            "maxScore": round(max(scores), 1) if scores else 0,
            "highRiskCount": high_risk,
            "highRiskRate": round(high_risk / len(records) * 100, 1) if records else 0,
            "approvedCount": approved,
            "rejectedCount": rejected,
            "pendingCount": pending,
            "approvalRate": round(approved / reviewed * 100, 1) if reviewed else 0,
            "rejectionRate": round(rejected / reviewed * 100, 1) if reviewed else 0,
            "levelDist": {
                "low": level_dist.get("low", 0),
                "mci": level_dist.get("mci", 0),
                "ad-early": level_dist.get("ad-early", 0),
                "ad-late": level_dist.get("ad-late", 0),
            },
        })

    # 按样本量排序（主模型在前）
    results.sort(key=lambda x: -x["sampleCount"])

    # 评分分布对比（每个版本的直方图）
    score_dist: list[dict] = []
    buckets = [f"{i * 10}-{i * 10 + 10}" for i in range(10)]
    for r in results:
        mv = r["modelVersion"]
        records = [v for v in latest.values() if (v.model_version or "未标注") == mv]
        hist = [0] * 10
        for v in records:
            if v.risk_score is not None:
                idx = min(int(float(v.risk_score) // 10), 9)
                hist[idx] += 1
        score_dist.append({"modelVersion": mv, "buckets": buckets, "counts": hist})

    return ok({
        "models": results,
        "scoreDist": score_dist,
        "totalVersions": len(results),
        "totalCases": len(latest),
    })


# ==================== 多中心队列对比分析 ====================

def _five_number_summary(sorted_vals: list[float]) -> list[float]:
    """五数概括 [min, Q1, median, Q3, max]（输入需已升序）"""
    n = len(sorted_vals)
    if n == 0:
        return [0.0, 0.0, 0.0, 0.0, 0.0]
    if n == 1:
        v = round(float(sorted_vals[0]), 1)
        return [v, v, v, v, v]
    # 线性插值法计算分位数（与 numpy default 一致）
    def _quantile(p: float) -> float:
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return round(float(sorted_vals[lo]) * (1 - frac) + float(sorted_vals[hi]) * frac, 1)
    return [
        round(float(sorted_vals[0]), 1),
        _quantile(0.25),
        _quantile(0.50),
        _quantile(0.75),
        round(float(sorted_vals[-1]), 1),
    ]


def _fisher_exact_p(a: int, b: int, c: int, d: int) -> float | None:
    """
    Fisher 精确检验双侧 p 值（2x2 列联表）。
    scipy 可用时走 scipy.stats.fisher_exact；不可用时返回 None（前端展示为"未检验"）。
    输入：[[a, b], [c, d]]
    """
    try:
        from scipy.stats import fisher_exact  # type: ignore
        _, p = fisher_exact([[a, b], [c, d]], alternative="two-sided")
        return round(float(p), 4)
    except Exception:
        return None


@router.get("/cohort-comparison")
def get_cohort_comparison(db: Session = Depends(get_db), modality: str = Query(default="")):
    """
    多中心队列对比分析：按 cohort 分组对比模型性能。
    直接针对"扫描仪队列捷径学习"痛点（如 ADNI1 队列 AD 率偏高 vs ADNI2+）。
    口径：每病例取最新 AnalysisVersionRecord，按 cohort 分组聚合。
    """
    modality = _validate_modality(modality)
    versions_q = (
        db.query(AnalysisVersionRecord)
        .join(CaseRecord, AnalysisVersionRecord.case_id == CaseRecord.id)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
    )
    if modality:
        versions_q = versions_q.filter(CaseRecord.modality == modality)

    all_versions = versions_q.all()

    # 每病例取最新版本（与 model-comparison 口径一致）
    latest: dict[str, AnalysisVersionRecord] = {}
    for v in all_versions:
        cur = latest.get(v.case_id)
        if cur is None or (v.version or 0) > (cur.version or 0):
            latest[v.case_id] = v

    # 拉取每个 case 的 cohort 字段（无 cohort 标注用 UNKNOWN）
    case_ids = list(latest.keys())
    cohort_map: dict[str, str] = {}
    if case_ids:
        for c in db.query(CaseRecord).filter(CaseRecord.id.in_(case_ids), CaseRecord.is_deleted.is_(False)).all():
            cohort_map[c.id] = (c.cohort or "UNKNOWN").strip() or "UNKNOWN"

    # 按 cohort 分组
    groups: dict[str, list[AnalysisVersionRecord]] = {}
    for case_id, v in latest.items():
        cohort = cohort_map.get(case_id, "UNKNOWN")
        groups.setdefault(cohort, []).append(v)

    # 固定 cohort 展示顺序（已知队列在前，UNKNOWN 最后）
    COHORT_ORDER = ["ADNI1", "ADNI2", "ADNI3", "UNKNOWN"]
    ordered_cohorts: list[str] = [c for c in COHORT_ORDER if c in groups]
    for c in groups:
        if c not in ordered_cohorts:
            ordered_cohorts.append(c)

    groups_out: list[dict] = []
    level_stack: list[dict] = []
    score_box: list[dict] = []
    # 各组样本量（用于显著性检验排序）
    cohort_sample: list[tuple[str, int]] = []

    for cohort in ordered_cohorts:
        records = groups[cohort]
        scores = [float(v.risk_score) for v in records if v.risk_score is not None]
        sorted_scores = sorted(scores)
        high_risk = sum(1 for v in records if v.risk_level in ("ad-early", "ad-late"))
        ad_count = high_risk  # ad-early + ad-late 即 AD 病例
        level_dist: Counter = Counter(v.risk_level or "unknown" for v in records)

        groups_out.append({
            "cohort": cohort,
            "sampleCount": len(records),
            "avgScore": round(sum(scores) / len(scores), 1) if scores else 0.0,
            "minScore": round(min(scores), 1) if scores else 0.0,
            "maxScore": round(max(scores), 1) if scores else 0.0,
            "highRiskCount": high_risk,
            "highRiskRate": round(high_risk / len(records) * 100, 1) if records else 0.0,
            "adRate": round(ad_count / len(records) * 100, 1) if records else 0.0,
            "levelDist": {
                "low": level_dist.get("low", 0),
                "mci": level_dist.get("mci", 0),
                "adEarly": level_dist.get("ad-early", 0),
                "adLate": level_dist.get("ad-late", 0),
            },
        })
        level_stack.append({
            "cohort": cohort,
            "low": level_dist.get("low", 0),
            "mci": level_dist.get("mci", 0),
            "adEarly": level_dist.get("ad-early", 0),
            "adLate": level_dist.get("ad-late", 0),
        })
        score_box.append({
            "cohort": cohort,
            "boxStats": _five_number_summary(sorted_scores),
        })
        cohort_sample.append((cohort, len(records)))

    # 统计显著性检验：两两 Fisher exact test（AD vs 非 AD）
    # 仅当有 2 个及以上组时才有意义；少于 2 组时返回空列表
    sig_pairs: list[dict] = []
    if len(ordered_cohorts) >= 2:
        # 按 AD 数 / 总数构建列联表
        cohort_ad = {g["cohort"]: (g["highRiskCount"], g["sampleCount"] - g["highRiskCount"]) for g in groups_out}
        for i in range(len(ordered_cohorts)):
            for j in range(i + 1, len(ordered_cohorts)):
                a_c, b_c = ordered_cohorts[i], ordered_cohorts[j]
                a_ad, a_non = cohort_ad[a_c]
                b_ad, b_non = cohort_ad[b_c]
                p_val = _fisher_exact_p(a_ad, a_non, b_ad, b_non)
                sig_pairs.append({
                    "cohortA": a_c,
                    "cohortB": b_c,
                    "adA": a_ad,
                    "totalA": a_ad + a_non,
                    "adB": b_ad,
                    "totalB": b_ad + b_non,
                    "pValue": p_val,
                })

    return ok({
        "groups": groups_out,
        "levelStack": level_stack,
        "scoreBoxplot": score_box,
        "significance": sig_pairs,
        "totalCases": len(latest),
    })
