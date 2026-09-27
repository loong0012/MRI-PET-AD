"""
主动学习标注优先级队列路由（researcher / admin 可见）
------------------------------------------------------------------
基于模型不确定性筛选高价值待标注病例，按不确定性得分排序输出标注队列。
- GET  /active-learning/queue           获取标注优先级队列（按不确定性降序）
- GET  /active-learning/stats           主动学习统计概览（不确定性分布 + 模型稳定性）
- POST /active-learning/assign          分配标注任务（内存存储，不强制建表）
- GET  /active-learning/recommendations 模型迭代建议

不确定性得分（0-100）综合以下因子：
  base = 50 - |risk_score - 50| * 2            （概率越接近 0.5 越高）
  + 30  若最新版本 review_status == rejected    （模型误诊）
  + 多版本 risk_score 标准差 * 5                （波动大说明模型不稳定）
  - 20  若该病例已有标注                         （已标注降权，避免重复）
  + 5   若单模态（has_mri 与 has_pet 互斥）      （单模态模型更易错）

口径约定（与 model_monitor.py / analytics.py 一致）：
- 仅统计未删除病例（CaseRecord.is_deleted == False）
- 每病例仅取最新分析版本进入不确定性计算
- 多版本标准差纳入所有历史版本（手动 sqrt，不依赖 numpy）
"""
import json
import math
from collections import Counter

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok
from models.case import CaseRecord
from models.analysis import AnalysisVersionRecord
from models.annotation import AnnotationRecord
from services.auth import require_role

router = APIRouter(
    prefix="/active-learning",
    tags=["主动学习"],
    # 队列条目含患者姓名/风险评分等科研视图，整 router 限科研角色与管理员
    dependencies=[Depends(require_role("researcher", "admin"))],
)

# 不确定性桶边界（0-100 共 10 桶，与 model_monitor 口径一致）
BUCKET_LABELS = [f"{i * 10}-{i * 10 + 10}" for i in range(10)]
# 高不确定性阈值
HIGH_UNCERTAINTY_THRESHOLD = 70.0
# 多版本不稳定阈值（标准差 > 10 视为不稳定）
UNSTABLE_STD_THRESHOLD = 10.0


# ---------- 内存标注任务分配存储 ----------
# { case_id: assignee }，不强制建表，进程重启后清空
_assign_store: dict[str, str] = {}


class AssignBody(BaseModel):
    """分配标注任务请求体"""
    # 单次分配上限 500，与批量操作上限一致
    caseIds: list[str] = Field(default_factory=list, max_length=500)
    assignee: str = Field(max_length=50)


# ---------- 工具函数 ----------


def _safe_score(v: AnalysisVersionRecord) -> float | None:
    """安全解析风险分数（缺失/非法返回 None）"""
    try:
        return float(v.risk_score) if v.risk_score is not None else None
    except (TypeError, ValueError):
        return None


def _std_dev(scores: list[float]) -> float:
    """手动计算样本标准差（分母 n，不依赖 numpy）"""
    if len(scores) < 2:
        return 0.0
    mean = sum(scores) / len(scores)
    var = sum((x - mean) ** 2 for x in scores) / len(scores)
    return math.sqrt(var)


def _parse_patient(patient_json: str) -> dict:
    """从 patient_json 解析患者信息"""
    try:
        return json.loads(patient_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _score_to_bucket(score: float) -> int:
    """将 0-100 分数映射到桶索引（100 归入最后一桶）"""
    if score < 0:
        return 0
    if score >= 100:
        return 9
    return int(score // 10)


def _all_versions_by_case(db: Session) -> dict[str, list[AnalysisVersionRecord]]:
    """按 case_id 分组返回所有分析版本（仅未删除病例）"""
    versions = (
        db.query(AnalysisVersionRecord)
        .join(CaseRecord, AnalysisVersionRecord.case_id == CaseRecord.id)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
        .all()
    )
    by_case: dict[str, list[AnalysisVersionRecord]] = {}
    for v in versions:
        by_case.setdefault(v.case_id, []).append(v)
    return by_case


def _latest_from_group(versions: list[AnalysisVersionRecord]) -> AnalysisVersionRecord:
    """取一组版本中的最新版本（version 最大）"""
    return max(versions, key=lambda v: v.version or 0)


def _build_suggestion(review_status: str, near_boundary: bool, high_variance: bool) -> str:
    """根据不确定性类型生成标注建议"""
    parts: list[str] = []
    if review_status == "rejected":
        parts.append("模型误诊病例，建议重点标注病变区域")
    if near_boundary:
        parts.append("模型决策边界病例，建议标注海马体区域辅助判断")
    if high_variance:
        parts.append("模型不稳定病例，建议多标注者交叉标注")
    if not parts:
        return "建议补充标注，扩充训练样本"
    return "；".join(parts)


def _build_case_item(
    case: CaseRecord,
    versions: list[AnalysisVersionRecord],
    ann_count: int,
) -> dict | None:
    """
    为单例病例构建不确定性分析项。
    无分析版本或无有效分数时返回 None（不计入队列）。
    """
    if not versions:
        return None
    latest = _latest_from_group(versions)
    score = _safe_score(latest)
    if score is None:
        return None

    # 多版本 risk_score 标准差
    version_scores = [s for s in (_safe_score(v) for v in versions) if s is not None]
    std = _std_dev(version_scores)

    review_status = latest.review_status or "pending"

    # ---- 不确定性因子计算 ----
    # 基础：概率接近 0.5（|score-50| 越小越高）
    base = 50.0 - abs(score - 50.0) * 2.0
    near_boundary = abs(score - 50.0) <= 5.0

    # 审核状态分歧：rejected 加权 +30
    rejected_bonus = 30.0 if review_status == "rejected" else 0.0

    # 多版本波动：标准差 * 5
    std_bonus = std * 5.0
    high_variance = std_bonus > 10.0

    # 已标注检查：降权 -20
    has_ann = ann_count > 0
    ann_penalty = -20.0 if has_ann else 0.0

    # 模态完整性：单模态加权 +5（has_mri 与 has_pet 互斥）
    single_modality = bool(case.has_mri) != bool(case.has_pet)
    modality_bonus = 5.0 if single_modality else 0.0

    uncertainty = base + rejected_bonus + std_bonus + ann_penalty + modality_bonus
    uncertainty = max(0.0, min(100.0, uncertainty))

    # ---- 不确定性因素标签 ----
    factors: list[str] = []
    if near_boundary:
        factors.append(f"决策边界(|score-50|={round(abs(score - 50.0), 1)})")
    if review_status == "rejected":
        factors.append("模型误诊(已驳回)")
    if high_variance:
        factors.append(f"多版本波动(σ={round(std, 1)})")
    if single_modality:
        factors.append("单模态(易错)")
    if has_ann:
        factors.append("已标注(降权)")

    suggestion = _build_suggestion(review_status, near_boundary, high_variance)

    return {
        "caseId": case.id,
        "patientName": _parse_patient(case.patient_json).get("name", ""),
        "modality": case.modality or "",
        "examDate": case.exam_date or "",
        "riskScore": round(score, 1),
        "riskLevel": latest.risk_level or "",
        "reviewStatus": review_status,
        "versionCount": len(versions),
        "scoreStdDev": round(std, 2),
        "hasAnnotation": has_ann,
        "annotationCount": ann_count,
        "uncertaintyScore": round(uncertainty, 1),
        "uncertaintyFactors": factors,
        "suggestion": suggestion,
    }


def _build_analysis(db: Session) -> tuple[list[dict], dict]:
    """
    构建所有病例的不确定性分析列表 + 聚合统计。
    返回: (case_items, stats)
    case_items: 每病例一项（含 uncertaintyScore），未排序
    stats: {totalCases, analyzedCases, annotatedCases, pendingReview,
            highUncertainty, avgUncertainty, rejectedCount, unstableCases,
            uncertaintyDistribution, modelStability}
    """
    by_case = _all_versions_by_case(db)
    # 标注计数：一次查询全部，按 case_id 聚合
    ann_rows = db.query(AnnotationRecord.case_id).all()
    ann_counter: Counter = Counter(r[0] for r in ann_rows)

    # 全部未删除病例
    cases = (
        db.query(CaseRecord)
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
        .all()
    )

    items: list[dict] = []
    annotated_cases = 0
    pending_review = 0
    rejected_count = 0
    high_uncertainty = 0
    unstable_cases = 0
    uncertainty_sum = 0.0
    bucket_counts = [0] * 10
    std_devs: list[float] = []

    for c in cases:
        versions = by_case.get(c.id, [])
        ann_count = ann_counter.get(c.id, 0)
        if ann_count > 0:
            annotated_cases += 1
        item = _build_case_item(c, versions, ann_count)
        if item is None:
            continue  # 无分析版本，不计入已分析
        items.append(item)

        # 聚合统计
        review_status = item["reviewStatus"]
        if review_status == "pending":
            pending_review += 1
        if review_status == "rejected":
            rejected_count += 1
        if item["uncertaintyScore"] > HIGH_UNCERTAINTY_THRESHOLD:
            high_uncertainty += 1
        if item["scoreStdDev"] > UNSTABLE_STD_THRESHOLD:
            unstable_cases += 1
        uncertainty_sum += item["uncertaintyScore"]
        bucket_counts[_score_to_bucket(item["uncertaintyScore"])] += 1
        std_devs.append(item["scoreStdDev"])

    analyzed_cases = len(items)
    avg_uncertainty = round(uncertainty_sum / analyzed_cases, 1) if analyzed_cases else 0.0
    avg_std_dev = round(sum(std_devs) / len(std_devs), 2) if std_devs else 0.0

    uncertainty_distribution = [
        {"bucket": BUCKET_LABELS[i], "count": bucket_counts[i]}
        for i in range(10)
    ]

    stats = {
        "totalCases": len(cases),
        "analyzedCases": analyzed_cases,
        "annotatedCases": annotated_cases,
        "pendingReview": pending_review,
        "highUncertainty": high_uncertainty,
        "avgUncertainty": avg_uncertainty,
        "rejectedCount": rejected_count,
        "unstableCases": unstable_cases,
        "uncertaintyDistribution": uncertainty_distribution,
        "modelStability": {
            "avgStdDev": avg_std_dev,
            "unstableCases": unstable_cases,
        },
    }
    return items, stats


# ---------- 端点 ----------


@router.get("/queue")
def get_queue(
    limit: int = Query(20, ge=1, le=200, description="返回队列长度"),
    onlyUnannotated: bool = Query(False, description="仅返回未标注病例"),
    onlyHighUncertainty: bool = Query(False, description="仅返回高不确定性病例(>70)"),
    db: Session = Depends(get_db),
):
    """
    获取标注优先级队列。
    - 按不确定性得分降序排序，取 top N
    - 支持筛选：仅未标注 / 仅高不确定性
    - 返回队列 + 队列内统计概览
    """
    items, stats = _build_analysis(db)

    # 队列统计概览基于全部已分析病例（items 未筛选前即为全集）
    queue_stats = {
        "totalCases": stats["totalCases"],
        "analyzedCases": stats["analyzedCases"],
        "annotatedCases": stats["annotatedCases"],
        "highUncertainty": stats["highUncertainty"],
        "avgUncertainty": stats["avgUncertainty"],
    }

    # 筛选
    if onlyUnannotated:
        items = [x for x in items if not x["hasAnnotation"]]
    if onlyHighUncertainty:
        items = [x for x in items if x["uncertaintyScore"] > HIGH_UNCERTAINTY_THRESHOLD]

    # 按不确定性降序排序，取 top N
    items.sort(key=lambda x: x["uncertaintyScore"], reverse=True)
    queue = items[:limit]

    return ok({
        "queue": queue,
        "total": len(queue),
        "stats": queue_stats,
    })


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """
    主动学习统计概览。
    - uncertaintyDistribution: 10 桶（0-100，每 10 一桶）
    - modelStability: 多版本病例 risk_score 标准差统计
    """
    _, stats = _build_analysis(db)
    return ok({
        "totalCases": stats["totalCases"],
        "analyzedCases": stats["analyzedCases"],
        "annotatedCases": stats["annotatedCases"],
        "pendingReview": stats["pendingReview"],
        "highUncertainty": stats["highUncertainty"],
        "uncertaintyDistribution": stats["uncertaintyDistribution"],
        "modelStability": stats["modelStability"],
    })


@router.post("/assign")
def assign_task(
    body: AssignBody,
    current_user: dict = Depends(require_role("researcher", "admin")),
):
    """
    分配标注任务（内存存储，不强制建表；仅科研/管理员）。
    - body: { caseIds: string[], assignee: string }
    - 返回分配数量与分配人
    """
    if not body.caseIds:
        return ok({"assigned": 0, "assignee": body.assignee})
    for cid in body.caseIds:
        _assign_store[cid] = body.assignee
    return ok({"assigned": len(body.caseIds), "assignee": body.assignee})


@router.get("/recommendations")
def get_recommendations(db: Session = Depends(get_db)):
    """
    模型迭代建议。
    基于当前不确定性分布给出模型迭代建议：
    - 高不确定性占比 > 20% → 建议收集更多边界病例标注后增量训练（high）
    - 多版本波动病例占比 > 15% → 模型稳定性不足，建议增加集成多样性（medium）
    - rejected 占比 > 10% → 误诊率偏高，建议检查数据质量与标签噪声（high）
    healthScore = 100 - (高不确定性占比*0.4 + 不稳定占比*0.3 + 误诊占比*0.3) * 100
    """
    _, stats = _build_analysis(db)
    analyzed = stats["analyzedCases"]
    recommendations: list[dict] = []

    if analyzed == 0:
        return ok({
            "recommendations": [],
            "healthScore": 100.0,
        })

    high_unc_ratio = stats["highUncertainty"] / analyzed
    unstable_ratio = stats["unstableCases"] / analyzed
    rejected_ratio = stats["rejectedCount"] / analyzed

    if high_unc_ratio > 0.20:
        recommendations.append({
            "type": "highUncertainty",
            "priority": "high",
            "message": "高不确定性病例占比偏高，建议优先收集模型决策边界附近的病例标注后进行增量训练，提升决策边界清晰度",
            "metric": f"高不确定性占比 {round(high_unc_ratio * 100, 1)}%（{stats['highUncertainty']}/{analyzed}），阈值 20%",
        })
    if unstable_ratio > 0.15:
        recommendations.append({
            "type": "modelStability",
            "priority": "medium",
            "message": "模型稳定性不足，多版本预测波动较大，建议增加集成多样性或优化训练数据一致性",
            "metric": f"多版本波动占比 {round(unstable_ratio * 100, 1)}%（{stats['unstableCases']}/{analyzed}），阈值 15%",
        })
    if rejected_ratio > 0.10:
        recommendations.append({
            "type": "misdiagnosis",
            "priority": "high",
            "message": "误诊率偏高，建议检查数据质量与标签噪声，复核训练集标注一致性",
            "metric": f"误诊(rejected)占比 {round(rejected_ratio * 100, 1)}%（{stats['rejectedCount']}/{analyzed}），阈值 10%",
        })

    # 健康分：100 - 加权惩罚（占比 * 权重 * 100）
    penalty = (high_unc_ratio * 0.4 + unstable_ratio * 0.3 + rejected_ratio * 0.3) * 100
    health_score = round(max(0.0, 100.0 - penalty), 1)

    return ok({
        "recommendations": recommendations,
        "healthScore": health_score,
    })
