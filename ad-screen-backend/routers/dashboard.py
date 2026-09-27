"""
工作台仪表盘路由
- GET /dashboard/stats
- GET /dashboard/trend
- GET /dashboard/risk-distribution
- GET /dashboard/recent-tasks
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from schemas.common import ok
from schemas.dashboard import DashboardStats, TrendPoint, RiskDistItem, TaskItem
from models.case import CaseRecord
from models.log import InferenceLog
from models.analysis import AnalysisVersionRecord, FollowUpRecord
from models.review import ReportReview
from services.auth import get_current_user_qs
from services.utils import mulberry32
from services.data_init import case_record_to_dict
# 复用各业务模块已有的状态推导/聚合函数，保证首页数字与业务页口径完全一致
from routers.review import _derive_status
from routers.followup import _parse_date as _parse_fu_date
from routers.warning import _collect_all as collect_warnings
from routers.active_learning import _build_analysis
from datetime import datetime, timedelta
import json
import time
import threading

router = APIRouter(
    prefix="/dashboard",
    tags=["工作台"],
    dependencies=[Depends(get_current_user_qs)],
)

# 仪表盘统计类接口 TTL 缓存（30s）：首页高频刷新，数据非实时强需求，
# 减轻 SQLite 并发查询压力；单 worker 部署下内存缓存足够，多 worker 需换 Redis。
_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str) -> dict | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > time.time():
            return entry[1]
        if entry:
            del _cache[key]
    return None


def _cache_set(key: str, value: dict) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + _CACHE_TTL, value)


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """统计卡片（仅未软删除病例）；delta = 今日新增 − 昨日新增（口径一致）"""
    cached = _cache_get("stats")
    if cached is not None:
        return ok(cached)
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    pending = db.query(CaseRecord).filter(CaseRecord.status == "pending", CaseRecord.is_deleted == False).count()  # noqa: E712
    completed = db.query(CaseRecord).filter(
        CaseRecord.status.in_(["completed", "reported"]), CaseRecord.is_deleted == False  # noqa: E712
    ).count()
    high_risk = db.query(CaseRecord).filter(
        CaseRecord.risk_level.in_(["ad-early", "ad-late"]), CaseRecord.is_deleted == False  # noqa: E712
    ).count()
    # 今日推理任务数：仅返回真实日志计数，不再叠加演示基线（避免数字虚高混淆）
    today_inference = db.query(InferenceLog).filter(
        InferenceLog.time.like(f"{today}%")
    ).count()

    def _daily_new(status_set, date_str, risk=False):
        """统计某日新增病例数（按 create_time 前缀匹配；risk=True 时按风险等级筛）"""
        q = db.query(CaseRecord).filter(CaseRecord.is_deleted == False)  # noqa: E712
        if risk:
            q = q.filter(CaseRecord.risk_level.in_(["ad-early", "ad-late"]))
        else:
            q = q.filter(CaseRecord.status.in_(status_set))
        return q.filter(CaseRecord.create_time.like(f"{date_str}%")).count()

    pending_delta = _daily_new(("pending",), today) - _daily_new(("pending",), yesterday)
    completed_delta = _daily_new(("completed", "reported"), today) - _daily_new(("completed", "reported"), yesterday)
    high_risk_delta = _daily_new((), today, risk=True) - _daily_new((), yesterday, risk=True)
    today_inference_delta = today_inference - db.query(InferenceLog).filter(
        InferenceLog.time.like(f"{yesterday}%")
    ).count()

    result = DashboardStats(
        pendingCases=pending,
        completedScreening=completed,
        highRiskCases=high_risk,
        todayInference=today_inference,
        pendingDelta=pending_delta,
        completedDelta=completed_delta,
        highRiskDelta=high_risk_delta,
        todayDelta=today_inference_delta,
    ).model_dump()
    _cache_set("stats", result)
    return ok(result)


@router.get("/trend")
def get_trend():
    """近 6 个月筛查趋势"""
    rand = mulberry32(88)
    today = datetime.now()
    points = []
    for i in range(6):
        d = today.replace(month=today.month - (5 - i) if today.month - (5 - i) >= 1 else today.month - (5 - i) + 12,
                          year=today.year if today.month - (5 - i) >= 1 else today.year - 1)
        points.append(TrendPoint(
            month=f"{d.month}月",
            total=150 + int(next(rand) * 60) + i * 18,
            highRisk=18 + int(next(rand) * 12) + i * 4,
        ).model_dump())
    return ok(points)


@router.get("/risk-distribution")
def get_risk_distribution(db: Session = Depends(get_db)):
    """风险等级分布"""
    cached = _cache_get("risk-distribution")
    if cached is not None:
        return ok(cached)
    items = [
        RiskDistItem(name="低风险", value=db.query(CaseRecord).filter(CaseRecord.risk_level == "low", CaseRecord.is_deleted == False).count(), key="low"),  # noqa: E712
        RiskDistItem(name="轻度认知障碍", value=db.query(CaseRecord).filter(CaseRecord.risk_level == "mci", CaseRecord.is_deleted == False).count(), key="mci"),  # noqa: E712
        RiskDistItem(name="AD 早期", value=db.query(CaseRecord).filter(CaseRecord.risk_level == "ad-early", CaseRecord.is_deleted == False).count(), key="ad-early"),  # noqa: E712
        RiskDistItem(name="AD 中晚期", value=db.query(CaseRecord).filter(CaseRecord.risk_level == "ad-late", CaseRecord.is_deleted == False).count(), key="ad-late"),  # noqa: E712
    ]
    result = [i.model_dump() for i in items]
    _cache_set("risk-distribution", result)
    return ok(result)


@router.get("/recent-tasks")
def get_recent_tasks(db: Session = Depends(get_db)):
    """近期任务列表"""
    cases = db.query(CaseRecord).filter(CaseRecord.is_deleted == False).order_by(CaseRecord.create_time.desc()).limit(8).all()  # noqa: E712
    task_types = ["初筛分析", "复筛分析", "随访复筛", "报告生成"]
    operators = ["rad01", "neu01"]
    tasks = []
    for i, c in enumerate(cases):
        cd = case_record_to_dict(c)
        tasks.append(TaskItem(
            id=f"T{9000 + i}",
            caseId=c.id,
            patientName=cd["patient"]["name"],
            type=task_types[i % 4],
            status="排队中" if c.status == "pending" else ("执行中" if i == 1 else "已完成"),
            operator=operators[i % 2],
            time=c.exam_date,
        ).model_dump())
    return ok(tasks)


@router.get("/distributions")
def get_distributions(db: Session = Depends(get_db)):
    """
    看板增强分布统计：
    - avgRiskScore：已分析病例平均风险评分
    - modalityDistribution：MRI / PET / MRI+PET 模态分布
    - departmentDistribution：申请科室分布（Top 6）
    - reviewPending：待协作审核的分析版本数
    """
    cached = _cache_get("distributions")
    if cached is not None:
        return ok(cached)
    # 平均风险评分（仅统计已出分病例）
    scored = db.query(CaseRecord).filter(CaseRecord.risk_score.isnot(None), CaseRecord.is_deleted == False).all()  # noqa: E712
    avg_score = round(sum(c.risk_score for c in scored) / len(scored), 1) if scored else 0.0

    # 模态分布
    modality_rows = (
        db.query(CaseRecord.modality, func.count(CaseRecord.id))
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
        .group_by(CaseRecord.modality)
        .all()
    )
    modality_map = [("MRI", "仅 MRI"), ("PET", "仅 PET"), ("MRI+PET", "MRI+PET 多模态")]
    modality_dist = []
    for key, label in modality_map:
        val = next((cnt for mod, cnt in modality_rows if mod == key), 0)
        modality_dist.append({"name": label, "value": int(val), "key": key})

    # 科室分布（Top 6）
    dept_rows = (
        db.query(CaseRecord.department, func.count(CaseRecord.id))
        .filter(CaseRecord.is_deleted == False)  # noqa: E712
        .group_by(CaseRecord.department)
        .order_by(func.count(CaseRecord.id).desc())
        .limit(6)
        .all()
    )
    department_dist = [
        {"name": dept or "未填写", "value": int(cnt)} for dept, cnt in dept_rows
    ]

    # 待审核版本数
    review_pending = db.query(AnalysisVersionRecord).filter(
        AnalysisVersionRecord.review_status == "pending"
    ).count()

    result = {
        "avgRiskScore": avg_score,
        "scoredCount": len(scored),
        "modalityDistribution": modality_dist,
        "departmentDistribution": department_dist,
        "reviewPending": int(review_pending),
    }
    _cache_set("distributions", result)
    return ok(result)


@router.get("/todo-summary")
def get_todo_summary(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    首页"我的待办"聚合：一次拉取当前角色相关的待处理事项，点击卡片直达业务页。
    口径与各业务列表页完全一致（直接复用其推导函数）：
    - review     报告待审核（pending_review + in_review，临床/管理员可见）
    - followup   随访 7 天内到期（含已逾期，副标题展示逾期数）
    - warning    高危预警未处理（handleStatus=open）
    - annotation 主动学习待标注高价值病例（科研/管理员可见）
    """
    role = current_user.get("role", "")
    # 各角色与侧边栏/路由守卫保持一致的可见范围
    clinical = role in ("radiologist", "neurologist", "admin")
    research = role in ("researcher", "admin")

    items: list[dict] = []

    if clinical:
        # 1) 报告待审核：待审 + 会签中（已签 1 人待第 2 人）都需要处理
        #    一次性查所有 completed/reported 病例的 ReportReview 记录，
        #    按 case_id 内存分组后传入 _derive_status，避免 N+1 查询。
        review_case_ids = [
            cid for (cid,) in (
                db.query(CaseRecord.id)
                .filter(CaseRecord.status.in_(("completed", "reported")))
                .filter(CaseRecord.is_deleted == False)  # noqa: E712
                .all()
            )
        ]
        review_count = 0
        if review_case_ids:
            all_reviews = (
                db.query(ReportReview)
                .filter(ReportReview.case_id.in_(review_case_ids))
                .order_by(ReportReview.id.asc())
                .all()
            )
            reviews_by_case: dict[str, list] = {}
            for r in all_reviews:
                reviews_by_case.setdefault(r.case_id, []).append(r)
            review_count = sum(
                1 for cid in review_case_ids
                if _derive_status(reviews_by_case.get(cid, []))[0] in ("pending_review", "in_review")
            )
        items.append({
            "key": "review",
            "title": "报告待审核",
            "count": review_count,
            "unit": "例",
            "route": "/review",
            "tone": "blue",
            "desc": "双签会签流程中待你处理的报告",
        })

        # 2) 随访临期/逾期（7 天窗口，与随访工作台 summary 口径一致）
        today = datetime.now().date()
        overdue = due7 = 0
        for p in db.query(FollowUpRecord).all():
            plan = json.loads(p.plan_json or "{}")
            d = _parse_fu_date(plan.get("nextDate", ""))
            if d is None:
                continue
            diff = (d - today).days
            if diff < 0:
                overdue += 1
            if diff <= 7:
                due7 += 1
        items.append({
            "key": "followup",
            "title": "随访临期/逾期",
            "count": due7,
            "unit": "例",
            "route": "/followup",
            "tone": "amber",
            "desc": f"7 天内到期 {due7 - overdue} 例，已逾期 {overdue} 例",
        })

        # 3) 高危预警未处理（与预警中心 /summary 口径一致）
        warning_items = collect_warnings(db)
        open_warnings = [it for it in warning_items if it.get("handleStatus") == "open"]
        items.append({
            "key": "warning",
            "title": "高危预警待处理",
            "count": len(open_warnings),
            "unit": "条",
            "route": "/warning",
            "tone": "red",
            "desc": "随访逾期 / 认知下降 / 风险升高等未处理预警",
        })

    if research:
        # 4) 主动学习待标注（复用不确定性分析的聚合统计）
        _, al_stats = _build_analysis(db)
        annotate_count = int(al_stats.get("pendingReview", 0))
        items.append({
            "key": "annotation",
            "title": "待标注高价值病例",
            "count": annotate_count,
            "unit": "例",
            "route": "/active-learning",
            "tone": "green",
            "desc": "高不确定性、对模型迭代价值最大的待标注病例",
        })

    total = sum(it["count"] for it in items)
    return ok({"items": items, "total": total})
