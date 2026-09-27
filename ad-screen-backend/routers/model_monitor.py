"""
模型性能监控路由（researcher / admin 可见）
------------------------------------------------------------------
跟踪生产环境中 AI 模型的实际表现，全部基于真实病例库（排除软删除）：
- GET /model-monitor/score-distribution  风险分数分布直方图 + 累积分布（10 桶 0-100）
- GET /model-monitor/confusion-matrix    混淆矩阵与性能指标（用 review_status 作为代理真实标签）
- GET /model-monitor/drift-detection      数据漂移检测（PSI + KS 统计量，当前窗口 vs 历史基线）
- GET /model-monitor/alerts               模型性能告警（漂移 / 性能下降 / 样本量）

口径约定：
- 每病例仅取最新分析版本（避免多版本重复计数）
- review_status: approved = AI 预测正确 / rejected = AI 预测错误 / pending 不参与混淆矩阵
- risk_level: low / mci 视为"非 AD"；ad-early / ad-late 视为"AD"
"""
import math
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok
from models.case import CaseRecord
from models.analysis import AnalysisVersionRecord
from services.auth import require_role

router = APIRouter(
    prefix="/model-monitor",
    tags=["模型性能监控"],
    # 模型漂移/性能监控属科研视图，限科研角色与管理员，与前端路由 meta.roles 对齐
    dependencies=[Depends(require_role("researcher", "admin"))],
)

# 分数桶边界（0-100 共 10 桶）
BUCKET_LABELS = [f"{i * 10}-{i * 10 + 10}" for i in range(10)]
# 风险等级分类：AD 正例 vs 非AD 负例
AD_LEVELS = {"ad-early", "ad-late"}
NON_AD_LEVELS = {"low", "mci"}


def _latest_versions(db: Session) -> dict[str, AnalysisVersionRecord]:
    """每病例取最新分析版本（与 analytics.py 口径一致，过滤软删除）"""
    versions = (
        db.query(AnalysisVersionRecord)
        .join(CaseRecord, AnalysisVersionRecord.case_id == CaseRecord.id)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
    )
    latest: dict[str, AnalysisVersionRecord] = {}
    for v in versions.all():
        cur = latest.get(v.case_id)
        if cur is None or (v.version or 0) > (cur.version or 0):
            latest[v.case_id] = v
    return latest


def _cutoff_str(days: int) -> str:
    """生成 N 天前的时间字符串（YYYY-MM-DD HH:MM:SS），用于字符串比较过滤"""
    cutoff = datetime.now() - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def _score_to_bucket(score: float) -> int:
    """将 0-100 分数映射到桶索引（100 归入最后一桶）"""
    if score < 0:
        return 0
    if score >= 100:
        return 9
    return int(score // 10)


def _safe_score(v: AnalysisVersionRecord) -> float | None:
    """安全解析风险分数（缺失/非法返回 None）"""
    try:
        s = float(v.risk_score) if v.risk_score is not None else None
    except (TypeError, ValueError):
        return None
    return s


@router.get("/score-distribution")
def get_score_distribution(
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
):
    """
    风险分数分布直方图 + 累积分布。
    - 时间窗口：最近 N 天（按 created_at 字符串比较过滤）
    - 每病例取最新版本，避免多版本重复计数
    - 10 桶（0-100），返回每桶计数与累积占比
    """
    cutoff = _cutoff_str(days)
    latest = _latest_versions(db)

    counts = [0] * 10
    scores: list[float] = []
    for v in latest.values():
        created = v.created_at or ""
        if created and created < cutoff:
            continue
        s = _safe_score(v)
        if s is None:
            continue
        idx = _score_to_bucket(s)
        counts[idx] += 1
        scores.append(s)

    total = sum(counts)
    cumulative = []
    running = 0
    for c in counts:
        running += c
        cumulative.append(round(running / total * 100, 1) if total else 0.0)

    avg = round(sum(scores) / len(scores), 2) if scores else 0.0
    # 样本标准差（分母 n）
    std = round(math.sqrt(sum((x - avg) ** 2 for x in scores) / len(scores)), 2) if scores else 0.0

    return ok({
        "buckets": BUCKET_LABELS,
        "counts": counts,
        "cumulative": cumulative,
        "totalCases": total,
        "avgScore": avg,
        "stdScore": std,
        "days": days,
    })


@router.get("/confusion-matrix")
def get_confusion_matrix(db: Session = Depends(get_db)):
    """
    混淆矩阵与性能指标。
    用 review_status 作为代理真实标签：
      approved + risk_level ∈ (ad-early/ad-late) = TP（正确识别 AD）
      rejected + risk_level ∈ (ad-early/ad-late) = FN（漏诊）
      approved + risk_level ∈ (low/mci)         = TN（正确识别非 AD）
      rejected + risk_level ∈ (low/mci)          = FP（误诊）
      pending 不参与计算
    返回：matrix [[TP, FN],[FP, TN]] + 5 项核心指标
    """
    latest = _latest_versions(db)
    tp = fn = fp = tn = 0
    pending = 0

    for v in latest.values():
        status = v.review_status or "pending"
        level = v.risk_level or ""
        if status == "pending":
            pending += 1
            continue
        if status not in ("approved", "rejected"):
            pending += 1
            continue
        is_ad = level in AD_LEVELS
        approved = status == "approved"
        if approved and is_ad:
            tp += 1
        elif not approved and is_ad:
            fn += 1
        elif approved and not is_ad and level in NON_AD_LEVELS:
            tn += 1
        elif not approved and not is_ad and level in NON_AD_LEVELS:
            fp += 1
        # risk_level 不在四个等级内则不计入

    reviewed = tp + fn + fp + tn
    accuracy = round((tp + tn) / reviewed * 100, 1) if reviewed else 0.0
    sensitivity = round(tp / (tp + fn) * 100, 1) if (tp + fn) else 0.0
    specificity = round(tn / (tn + fp) * 100, 1) if (tn + fp) else 0.0
    precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) else 0.0
    f1_denom = 2 * tp + fp + fn
    f1 = round(2 * tp / f1_denom * 100, 1) if f1_denom else 0.0

    return ok({
        "matrix": [[tp, fn], [fp, tn]],
        "metrics": {
            "accuracy": accuracy,
            "sensitivity": sensitivity,
            "specificity": specificity,
            "f1": f1,
            "precision": precision,
        },
        "totalReviewed": reviewed,
        "pendingCount": pending,
    })


@router.get("/drift-detection")
def get_drift_detection(
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
):
    """
    数据漂移检测。
    - 当前窗口：最近 N 天分数分布
    - 基线窗口：全部历史分数分布（训练集代理）
    - PSI = Σ (curr_pct - base_pct) * ln(curr_pct / base_pct)，桶计数为 0 时用 0.0001 避免除零
    - KS = 两组累积分布的最大绝对差
    - 漂移分级：PSI < 0.1 green / 0.1-0.25 yellow / >0.25 red
    """
    cutoff = _cutoff_str(days)
    latest = _latest_versions(db)

    current_counts = [0] * 10
    baseline_counts = [0] * 10
    for v in latest.values():
        s = _safe_score(v)
        if s is None:
            continue
        idx = _score_to_bucket(s)
        baseline_counts[idx] += 1
        created = v.created_at or ""
        if created and created >= cutoff:
            current_counts[idx] += 1

    base_total = sum(baseline_counts)
    curr_total = sum(current_counts)

    # 转换为占比（避免零值导致 ln / 除零）
    base_pct = [(c / base_total if base_total else 0.0) for c in baseline_counts]
    curr_pct = [(c / curr_total if curr_total else 0.0) for c in current_counts]
    base_pct_safe = [max(p, 0.0001) for p in base_pct]
    curr_pct_safe = [max(p, 0.0001) for p in curr_pct]

    # PSI 计算
    psi = 0.0
    for cp, bp in zip(curr_pct_safe, base_pct_safe):
        psi += (cp - bp) * math.log(cp / bp)
    psi = round(psi, 4)

    # 累积分布与 KS 统计量
    base_cum = []
    curr_cum = []
    running_b = 0.0
    running_c = 0.0
    for i in range(10):
        running_b += base_pct_safe[i]
        running_c += curr_pct_safe[i]
        base_cum.append(round(running_b, 4))
        curr_cum.append(round(running_c, 4))
    ks = round(max(abs(c - b) for c, b in zip(curr_cum, base_cum)) if base_cum else 0.0, 4)

    # 漂移分级
    if psi < 0.1:
        drift_level = "green"
    elif psi <= 0.25:
        drift_level = "yellow"
    else:
        drift_level = "red"

    return ok({
        "psi": psi,
        "ksStatistic": ks,
        "driftLevel": drift_level,
        "currentDist": curr_pct,
        "baselineDist": base_pct,
        "currentCounts": current_counts,
        "baselineCounts": baseline_counts,
        "buckets": BUCKET_LABELS,
        "currentTotal": curr_total,
        "baselineTotal": base_total,
        "days": days,
    })


@router.get("/alerts")
def get_alerts(db: Session = Depends(get_db)):
    """
    模型性能告警聚合：
    1. 漂移告警：PSI > 0.25 (red) 或 > 0.1 (yellow)
    2. 性能下降告警：最近 7 天 sensitivity vs 历史平均值下降 > 5 个百分点
    3. 样本量告警：最近 7 天分析量 < 5
    """
    alerts: list[dict] = []
    detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---------- 漂移告警（默认 30 天窗口）----------
    latest = _latest_versions(db)
    cutoff_30 = _cutoff_str(30)
    base_counts = [0] * 10
    curr_counts = [0] * 10
    for v in latest.values():
        s = _safe_score(v)
        if s is None:
            continue
        idx = _score_to_bucket(s)
        base_counts[idx] += 1
        created = v.created_at or ""
        if created and created >= cutoff_30:
            curr_counts[idx] += 1

    base_total = sum(base_counts)
    curr_total = sum(curr_counts)
    base_pct_safe = [max((c / base_total if base_total else 0.0), 0.0001) for c in base_counts]
    curr_pct_safe = [max((c / curr_total if curr_total else 0.0), 0.0001) for c in curr_counts]
    psi = 0.0
    for cp, bp in zip(curr_pct_safe, base_pct_safe):
        psi += (cp - bp) * math.log(cp / bp)
    psi = round(psi, 4)

    if psi > 0.25:
        alerts.append({
            "type": "drift",
            "level": "high",
            "message": f"检测到显著数据漂移，PSI={psi}（>0.25），建议排查输入数据分布与模型版本一致性",
            "value": psi,
            "threshold": 0.25,
            "detectedAt": detected_at,
        })
    elif psi > 0.1:
        alerts.append({
            "type": "drift",
            "level": "medium",
            "message": f"检测到轻微数据漂移，PSI={psi}（0.1-0.25），建议持续关注并缩短监测周期",
            "value": psi,
            "threshold": 0.1,
            "detectedAt": detected_at,
        })

    # ---------- 性能下降告警（最近 7 天 vs 历史）----------
    cutoff_7 = _cutoff_str(7)
    # 历史窗口 = 7 天前到 30 天前，避免与"最近 7 天"重叠
    cutoff_30_only = _cutoff_str(30)
    hist_tp = hist_ad_total = 0
    curr_tp = curr_ad_total = 0
    curr_review_count = 0
    for v in latest.values():
        status = v.review_status or "pending"
        if status == "pending":
            continue
        level = v.risk_level or ""
        is_ad = level in AD_LEVELS
        approved = status == "approved"
        created = v.created_at or ""

        if created and created >= cutoff_7:
            # 最近 7 天
            if is_ad:
                curr_ad_total += 1
                if approved:
                    curr_tp += 1
            curr_review_count += 1
        elif created and created >= cutoff_30_only:
            # 7-30 天历史窗口
            if is_ad:
                hist_ad_total += 1
                if approved:
                    hist_tp += 1

    curr_sensitivity = (curr_tp / curr_ad_total * 100) if curr_ad_total else None
    hist_sensitivity = (hist_tp / hist_ad_total * 100) if hist_ad_total else None
    if curr_sensitivity is not None and hist_sensitivity is not None:
        drop = hist_sensitivity - curr_sensitivity
        if drop > 5:
            alerts.append({
                "type": "performance",
                "level": "medium",
                "message": f"模型灵敏度下降 {round(drop, 1)} 个百分点（历史 {round(hist_sensitivity, 1)}% → 近 7 天 {round(curr_sensitivity, 1)}%）",
                "value": round(drop, 1),
                "threshold": 5,
                "detectedAt": detected_at,
            })

    # ---------- 样本量告警（最近 7 天分析量 < 5）----------
    if curr_review_count < 5:
        alerts.append({
            "type": "sampleVolume",
            "level": "low",
            "message": f"近 7 天仅 {curr_review_count} 例已审核样本（< 5），统计结论可靠性不足，建议扩大样本量或延长监测周期",
            "value": curr_review_count,
            "threshold": 5,
            "detectedAt": detected_at,
        })

    # 汇总：按 level 分级统计
    level_counts = Counter(a["level"] for a in alerts)
    return ok({
        "alerts": alerts,
        "summary": {
            "total": len(alerts),
            "high": level_counts.get("high", 0),
            "medium": level_counts.get("medium", 0),
            "low": level_counts.get("low", 0),
        },
    })
