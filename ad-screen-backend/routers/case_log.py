"""
病例操作审计日志路由（admin）
------------------------------------------------------------------
满足临床合规审计：查询 CaseLog 全量操作记录（含已软删除病例的删除审计）。
- POST /case-log/list   分页查询（病例ID/姓名、操作类型、操作人、时间范围）
- GET  /case-log/actions 操作类型字典（前端下拉）
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok
from models.log import CaseLog
from services.auth import get_current_user_qs, require_role

router = APIRouter(
    prefix="/case-log",
    tags=["病例操作审计"],
    dependencies=[Depends(get_current_user_qs)],
)

# 操作类型字典（按写入点实际值汇总；前端据此渲染标签色）
ACTION_OPTIONS = [
    {"value": "上传 MRI/PET 影像", "label": "影像上传", "color": "#2F6DA3", "bg": "#E8F1F8"},
    {"value": "批量导入病例", "label": "批量导入", "color": "#7B5EA7", "bg": "#EFEAF6"},
    {"value": "启动 AI 分析", "label": "启动 AI 分析", "color": "#2E9E6B", "bg": "#EDF7F2"},
    {"value": "保存 AI 分析结果", "label": "保存分析结果", "color": "#5FA86E", "bg": "#EEF6EF"},
    {"value": "医生审核", "label": "医生审核", "color": "#D99A2B", "bg": "#FBF4E6"},
    {"value": "审核通过", "label": "审核通过", "color": "#2E9E6B", "bg": "#EDF7F2"},
    {"value": "审核驳回", "label": "审核驳回", "color": "#C94F4F", "bg": "#FAEDED"},
    {"value": "编辑干预方案", "label": "编辑干预方案", "color": "#5FA86E", "bg": "#EEF6EF"},
    {"value": "生成筛查报告", "label": "生成报告", "color": "#6FA8B0", "bg": "#EAF3F4"},
    {"value": "删除病例", "label": "删除病例", "color": "#C94F4F", "bg": "#FAEDED"},
]
ACTION_MAP = {item["value"]: item for item in ACTION_OPTIONS}


class CaseLogQuery(BaseModel):
    keyword: str = ""        # 病例ID / 患者姓名
    action: str = ""         # 操作类型精确匹配
    operator: str = ""       # 操作人
    dateRange: list[str] | None = None  # [start, end]
    page: int = Field(default=1, ge=1)
    # 上限 100：防止超大分页拖取全量审计日志
    pageSize: int = Field(default=10, ge=1, le=100)


@router.get("/actions", dependencies=[Depends(require_role("admin"))])
def get_actions():
    """操作类型字典（含展示名与配色；仅管理员，与全量审计页一致）"""
    return ok(ACTION_OPTIONS)


@router.get("/case/{case_id}")
def get_case_timeline(case_id: str, db: Session = Depends(get_db)):
    """单病例完整操作历程（时间线，供病例列表"历程"抽屉使用）"""
    rows = (
        db.query(CaseLog)
        .filter(CaseLog.case_id == case_id)
        .order_by(CaseLog.time.asc())
        .all()
    )
    return ok([
        {
            "id": r.id,
            "action": r.action,
            "actionLabel": ACTION_MAP.get(r.action, {}).get("label", r.action),
            "actionColor": ACTION_MAP.get(r.action, {}).get("color", "#93A1AF"),
            "actionBg": ACTION_MAP.get(r.action, {}).get("bg", "#F2F6FA"),
            "operator": r.operator,
            "time": r.time,
            "detail": r.detail,
        }
        for r in rows
    ])


@router.post("/list", dependencies=[Depends(require_role("admin"))])
def list_case_logs(q: CaseLogQuery, db: Session = Depends(get_db)):
    """分页查询病例操作审计日志（含已删除病例的删除记录；仅管理员）"""
    query = db.query(CaseLog)
    if q.keyword:
        kw = f"%{q.keyword.lower()}%"
        query = query.filter(
            (CaseLog.case_id.ilike(kw)) | (CaseLog.patient_name.ilike(kw))
        )
    if q.action:
        query = query.filter(CaseLog.action == q.action)
    if q.operator:
        query = query.filter(CaseLog.operator.ilike(f"%{q.operator}%"))
    if q.dateRange and len(q.dateRange) == 2:
        start, end = q.dateRange[0], q.dateRange[1]
        if start:
            query = query.filter(CaseLog.time >= start)
        if end:
            # 结束日期包含当天全部时刻
            query = query.filter(CaseLog.time <= f"{end} 23:59:59")

    total = query.count()
    rows = query.order_by(CaseLog.time.desc()).offset(
        (q.page - 1) * q.pageSize
    ).limit(q.pageSize).all()

    return ok({
        "total": total,
        "list": [
            {
                "id": r.id,
                "caseId": r.case_id,
                "patientName": r.patient_name,
                "action": r.action,
                "actionLabel": ACTION_MAP.get(r.action, {}).get("label", r.action),
                "actionColor": ACTION_MAP.get(r.action, {}).get("color", "#93A1AF"),
                "actionBg": ACTION_MAP.get(r.action, {}).get("bg", "#F2F6FA"),
                "operator": r.operator,
                "time": r.time,
                "detail": r.detail,
            }
            for r in rows
        ],
    })
