"""
合规审计追踪接口（仅管理员）
------------------------------------------------------------------
与 audit_board 的区别（两者常被混淆）：
- `audit_board`：对 login_records / case_logs / system_logs 的**统计聚合看板**，
  回答"系统运行得怎么样"。
- 本模块：对 `audit_events` 事实表的**逐条检索与完整性校验**，
  回答"谁在什么时候对谁做了什么"——这是合规审查与事件调查要的东西。

端点：
  GET  /audit/list            多维过滤分页查询
  GET  /audit/patient/{ref}   某患者的完整访问史（合规调查核心场景）
  GET  /audit/verify          哈希链完整性校验（证明日志未被篡改）
  GET  /audit/stats           汇总统计
  GET  /audit/export          导出（CSV/JSON），自身也留下一条 export 审计
  POST /audit/purge           超期清理（默认 dry-run，需显式确认）
"""
import csv
import io
import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from config import AUDIT_RETENTION_DAYS
from database import get_db
from schemas.common import ok, fail
from services.auth import get_current_user, require_role
from services import audit_service

router = APIRouter(
    prefix="/audit",
    tags=["合规审计追踪"],
    dependencies=[Depends(require_role("admin"))],
)


def _row_to_dict(r) -> dict:
    return {
        "id": r.id,
        "ts": r.ts,
        "tsEpoch": r.ts_epoch,
        "actor": r.actor or "匿名",
        "actorRole": r.actor_role,
        "actorIp": r.actor_ip,
        "action": r.action,
        "resourceType": r.resource_type,
        "resourceId": r.resource_id,
        "patientRef": r.patient_ref,
        "outcome": r.outcome,
        "severity": r.severity,
        "detail": r.detail,
        "requestId": r.request_id,
        "modelVersion": r.model_version,
        "hash": (r.hash or "")[:12],  # 前端只做展示，截断即可，完整值用于校验
    }


@router.get("/list")
def audit_list(
    actor: str = Query(default=""),
    action: str = Query(default=""),
    resourceType: str = Query(default=""),
    resourceId: str = Query(default=""),
    patientRef: str = Query(default=""),
    outcome: str = Query(default=""),
    severity: str = Query(default=""),
    since: float = Query(default=0),
    until: float = Query(default=0),
    keyword: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """审计事件查询（时间倒序）"""
    rows, total = audit_service.query_events(
        db,
        actor=actor, action=action, resource_type=resourceType,
        resource_id=resourceId, patient_ref=patientRef,
        outcome=outcome, severity=severity,
        since=since, until=until, keyword=keyword,
        page=page, page_size=pageSize,
    )
    return ok({
        "list": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": pageSize,
    })


@router.get("/patient/{patient_ref}")
def audit_by_patient(
    patient_ref: str,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    某患者的完整访问史。

    合规调查最常见的问题：怀疑某份病历被越权访问时，需要一次性拉出
    "所有人对该患者的所有操作"。没有 patient_ref 维度时，只能靠 resource_id
    在不同表里分别拼凑，实操中几乎无法完成。
    """
    rows, total = audit_service.query_events(
        db, patient_ref=patient_ref, page=page, page_size=pageSize
    )
    return ok({
        "patientRef": patient_ref,
        "list": [_row_to_dict(r) for r in rows],
        "total": total,
    })


@router.get("/verify")
def audit_verify(
    limit: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """
    哈希链完整性校验。

    返回 valid=False 时 broken_at 指出第一条不匹配记录，reason 说明是
    "记录被篡改"还是"前驱链断裂"（后者意味着有记录被插入或前一条被改）。
    """
    result = audit_service.verify_chain(db, limit=limit)
    return ok(result)


@router.get("/stats")
def audit_stats(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    """审计汇总：按动作与结果分布，denied 数量是越权尝试的关键指标"""
    since = time.time() - days * 86400.0
    data = audit_service.stats(db, since_epoch=since)
    data["rangeDays"] = days
    data["retentionDays"] = AUDIT_RETENTION_DAYS
    return ok(data)


@router.get("/export")
def audit_export(
    format: str = Query(default="json"),
    actor: str = Query(default=""),
    action: str = Query(default=""),
    patientRef: str = Query(default=""),
    outcome: str = Query(default=""),
    days: int = Query(default=30, ge=1, le=3650),
    limit: int = Query(default=5000, ge=1, le=50000),
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    """
    导出审计记录（供监管报送 / 外部 SIEM）。

    导出本身也要留痕——否则"谁导出了审计日志"这件事就成了盲区。
    """
    fmt = (format or "json").strip().lower()
    if fmt not in ("json", "csv"):
        return fail("format 仅支持 json / csv", 400)

    since = time.time() - days * 86400.0
    rows, total = audit_service.query_events(
        db, actor=actor, action=action, patient_ref=patientRef,
        outcome=outcome, since=since, page=1, page_size=limit,
    )

    audit_service.record(
        db,
        action="export",
        resource_type="audit",
        outcome="success",
        severity="critical",
        detail={"format": fmt, "rows": len(rows), "days": days, "matched": total},
        actor=current.get("username", ""),
        actor_role=current.get("roleName", ""),
        request_id="",
    )

    data = [_row_to_dict(r) for r in rows]

    if fmt == "json":
        return ok({"total": total, "exported": len(rows), "list": data})

    # CSV：BOM 前缀保证 Excel 打开中文不乱码（国内医院环境普遍需要）
    buf = io.StringIO()
    buf.write("\ufeff")
    if data:
        writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    buf.seek(0)
    filename = f"audit_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/purge")
def audit_purge(
    confirm: bool = Query(default=False),
    retentionDays: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    """
    清理超期审计记录。

    默认 dry-run（只统计不删除）。必须显式 confirm=true 才真正删除。
    **自动静默删除本身就是可被用来掩盖痕迹的机制**，所以这里不做定时任务，
    只提供人工触发，且删除动作自身会再写一条审计记录。
    """
    result = audit_service.purge_expired(
        db,
        retention_days=retentionDays,
        dry_run=not confirm,
    )
    if confirm and result.get("purged", 0) > 0:
        audit_service.record(
            db,
            action="delete",
            resource_type="audit",
            outcome="success",
            severity="critical",
            detail={
                "retentionDays": result.get("retentionDays"),
                "purged": result.get("purged"),
                "operator": current.get("username", ""),
            },
            actor=current.get("username", ""),
            actor_role=current.get("roleName", ""),
        )
    result["confirmed"] = confirm
    return ok(result)
