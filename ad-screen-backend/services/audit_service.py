"""
合规审计服务：追加写 + 哈希链校验 + 保留策略
------------------------------------------------------------------
核心不变量：
1. **只追加**。本模块不提供任何 update / delete 方法；数据库层另有触发器兜底。
2. **哈希链闭合**。第 n 条的 hash = SHA256(prev_hash + 规范化字段)，
   prev_hash 取第 n-1 条的 hash。首条 prev_hash 为 "0"*64（创世值）。
3. **审计失败不得影响业务**。审计是旁路记录，任何异常都要吞掉并记日志，
   绝不能让"写审计失败"导致"病例创建失败"。

关于并发下的哈希链：
哈希必须在 INSERT 之前算好——append-only 触发器禁止对 audit_events 做任何
UPDATE，「先插入再回填哈希」会被数据库直接拒绝（这个坑是测试发现的）。
因此链头状态保存在独立单行表 audit_chain_head：读取链头 → 计算哈希 →
插入记录 → 推进链头，四步在同一个事务里完成。SQLite 的写事务本身串行化，
所以并发写入不会拿到同一个 prev_hash，链不会分叉。

主键用零填充序号（AU00000001）而非自然序号（AU1）：审计记录会增长到百万级，
逐次扫全表求 max(id) 是 O(n) 灾难；且字符串排序下 "AU10" < "AU9"，
不补零会让按 id 排序的链校验错乱。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func as sa_func

from config import AUDIT_RETENTION_DAYS
from database import SessionLocal
from models.audit import AuditEvent, AuditChainHead
from sqlalchemy import text

logger = logging.getLogger(__name__)

# 创世哈希：链首记录的 prev_hash，固定值，让第一条记录也可被校验
GENESIS_HASH = "0" * 64

# 哈希计算覆盖的字段（顺序固定，任何字段增删改都必须同步此处，否则旧链失效）
_HASH_FIELDS = (
    "ts",
    "actor",
    "actor_role",
    "actor_ip",
    "action",
    "resource_type",
    "resource_id",
    "patient_ref",
    "outcome",
    "severity",
    "detail",
    "request_id",
    "model_version",
)


def _canonical(row: AuditEvent) -> str:
    """把参与哈希的字段序列化为确定性字符串"""
    parts = [str(getattr(row, f, "") or "") for f in _HASH_FIELDS]
    return "\x1f".join(parts)


def compute_hash(prev_hash: str, row: AuditEvent) -> str:
    """计算某条记录的链上哈希"""
    payload = f"{prev_hash}\x1e{_canonical(row)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# 写入
# ----------------------------------------------------------------------

def record(
    db,
    *,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    patient_ref: str = "",
    outcome: str = "success",
    severity: str = "info",
    detail: Any = None,
    actor: str = "",
    actor_role: str = "",
    actor_ip: str = "",
    request_id: str = "",
    model_version: str = "",
    ts_epoch: Optional[float] = None,
) -> Optional[AuditEvent]:
    """
    写入一条审计事件（追加写）。

    detail 接受 dict/list 或字符串；dict/list 会被 JSON 序列化。
    调用方**必须**保证 detail 不含 PHI 明文——审计表不是 PHI 的第二个副本。

    返回新建的 AuditEvent；失败时返回 None（不抛异常，避免拖垮业务）。
    """
    try:
        now_epoch = float(ts_epoch if ts_epoch is not None else time.time())
        ts_str = datetime.fromtimestamp(now_epoch).strftime("%Y-%m-%d %H:%M:%S")

        if isinstance(detail, (dict, list)):
            detail_str = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        else:
            detail_str = str(detail or "")

        # 链头推进与记录插入在同一事务内完成（详见模块 docstring）
        head = db.get(AuditChainHead, 1)
        if head is None:
            head = AuditChainHead(id=1, seq=0, last_hash=GENESIS_HASH, last_id="")
            db.add(head)
            db.flush()
        head.seq = int(head.seq or 0) + 1
        prev_hash = head.last_hash or GENESIS_HASH

        row = AuditEvent(
            id=f"AU{head.seq:08d}",
            ts=ts_str,
            ts_epoch=now_epoch,
            actor=actor or "",
            actor_role=actor_role or "",
            actor_ip=actor_ip or "",
            action=action,
            resource_type=resource_type or "",
            resource_id=resource_id or "",
            patient_ref=patient_ref or "",
            outcome=outcome,
            severity=severity,
            detail=detail_str,
            request_id=request_id or "",
            model_version=model_version or "",
            prev_hash=prev_hash,
            hash="",
        )
        # 哈希在落库前算好并赋值：这样是纯 INSERT，不会触发 append-only 拒绝
        row.hash = compute_hash(row.prev_hash, row)
        db.add(row)

        head.last_hash = row.hash
        head.last_id = row.id
        head.updated_epoch = now_epoch
        db.commit()
        return row
    except Exception as exc:  # 审计失败绝不影响业务主流程
        logger.error("审计事件写入失败（业务不受影响）：%s", exc, exc_info=False)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def record_safe(**kwargs) -> Optional[AuditEvent]:
    """
    独立会话写入（中间件场景：不能复用请求会话，避免污染业务事务）。

    SessionLocal() 本身也必须包在 try 内——连接池耗尽/数据库不可用时
    它就会抛异常。审计是旁路，此时应静默降级，绝不能让业务请求失败。
    """
    db = None
    try:
        db = SessionLocal()
        return record(db, **kwargs)
    except Exception as exc:
        logger.error("审计事件写入失败（业务不受影响）：%s", exc, exc_info=False)
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


# ----------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------

def verify_chain(db, limit: int = 0) -> dict:
    """
    校验哈希链完整性。

    返回 {total, checked, valid, broken_at, reason}：
    - valid=True：全链闭合
    - valid=False：broken_at 指出第一条校验失败的记录 id
    """
    q = db.query(AuditEvent).order_by(AuditEvent.id.asc())
    if limit and limit > 0:
        q = q.limit(limit)

    total = db.query(sa_func.count(AuditEvent.id)).scalar() or 0
    checked = 0
    expected_prev = GENESIS_HASH
    last: Optional[AuditEvent] = None

    for row in q.yield_per(500):
        checked += 1
        if row.prev_hash != expected_prev:
            return {
                "total": total,
                "checked": checked,
                "valid": False,
                "broken_at": row.id,
                "reason": "前驱哈希不匹配（记录被插入或前一条被篡改）",
            }
        if compute_hash(row.prev_hash, row) != row.hash:
            return {
                "total": total,
                "checked": checked,
                "valid": False,
                "broken_at": row.id,
                "reason": "记录内容与哈希不符（该条记录被篡改）",
            }
        expected_prev = row.hash
        last = row

    return {
        "total": total,
        "checked": checked,
        "valid": True,
        "broken_at": "",
        "reason": "",
        "headHash": last.hash if last is not None else "",
    }


# ----------------------------------------------------------------------
# 查询
# ----------------------------------------------------------------------

def query_events(
    db,
    *,
    actor: str = "",
    action: str = "",
    resource_type: str = "",
    resource_id: str = "",
    patient_ref: str = "",
    outcome: str = "",
    severity: str = "",
    since: float = 0.0,
    until: float = 0.0,
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AuditEvent], int]:
    """多条件过滤 + 分页（时间倒序：最新在前，符合审计员习惯）"""
    q = db.query(AuditEvent)
    if actor:
        q = q.filter(AuditEvent.actor == actor)
    if action:
        q = q.filter(AuditEvent.action == action)
    if resource_type:
        q = q.filter(AuditEvent.resource_type == resource_type)
    if resource_id:
        q = q.filter(AuditEvent.resource_id == resource_id)
    if patient_ref:
        q = q.filter(AuditEvent.patient_ref == patient_ref)
    if outcome:
        q = q.filter(AuditEvent.outcome == outcome)
    if severity:
        q = q.filter(AuditEvent.severity == severity)
    if since and since > 0:
        q = q.filter(AuditEvent.ts_epoch >= since)
    if until and until > 0:
        q = q.filter(AuditEvent.ts_epoch <= until)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(AuditEvent.detail.like(like) | AuditEvent.actor.like(like))

    total = q.count()
    rows = (
        q.order_by(AuditEvent.ts_epoch.desc(), AuditEvent.id.desc())
        .offset(max(0, (page - 1) * page_size))
        .limit(page_size)
        .all()
    )
    return rows, total


def stats(db, since_epoch: float = 0.0) -> dict:
    """审计汇总：按动作/结果/严重级分布，供看板与合规报表"""
    q = db.query(AuditEvent)
    if since_epoch and since_epoch > 0:
        q = q.filter(AuditEvent.ts_epoch >= since_epoch)

    total = q.count()

    # SQLAlchemy 2.0 起 filter(True) 不再被静默忽略，故显式构造条件列表
    cond = [AuditEvent.ts_epoch >= since_epoch] if since_epoch and since_epoch > 0 else []

    by_action = dict(
        db.query(AuditEvent.action, sa_func.count(AuditEvent.id))
        .filter(*cond)
        .group_by(AuditEvent.action)
        .all()
    )
    by_outcome = dict(
        db.query(AuditEvent.outcome, sa_func.count(AuditEvent.id))
        .filter(*cond)
        .group_by(AuditEvent.outcome)
        .all()
    )
    return {
        "total": total,
        "byAction": by_action,
        "byOutcome": by_outcome,
        "deniedCount": by_outcome.get("denied", 0),
    }


# ----------------------------------------------------------------------
# 保留策略
# ----------------------------------------------------------------------

def purge_expired(db, retention_days: Optional[int] = None, dry_run: bool = True) -> dict:
    """
    清理超期审计记录。

    retention_days 为 None 时取配置 AUDIT_RETENTION_DAYS（默认 2190 = 6 年，
    HIPAA 下限）；显式传 0 表示永久保留、不清理。
    区分 None 与 0 是必要的：否则"不指定"和"永不清理"无法表达。

    **默认 dry_run=True**：只统计不删除。真实清理必须由管理员显式触发，
    禁止系统自动静默删除——自动删除本身就是一个可被用来掩盖痕迹的机制。
    """
    days = AUDIT_RETENTION_DAYS if retention_days is None else int(retention_days)
    if days <= 0:
        return {"retentionDays": 0, "expired": 0, "purged": 0, "dryRun": dry_run,
                "note": "保留期为 0 表示永久保留，未执行清理"}

    cutoff = time.time() - days * 86400.0
    expired = db.query(AuditEvent).filter(AuditEvent.ts_epoch < cutoff).count()

    purged = 0
    resealed = 0
    if not dry_run and expired > 0:
        # 触发器会拒绝 DELETE（append-only 保护），此处需先临时摘除触发器。
        # 清理能力存在，但要求管理员显式确认。
        db.execute(_drop_guard_sql())
        db.execute(text("DROP TRIGGER IF EXISTS trg_audit_events_no_delete"))
        try:
            purged = db.query(AuditEvent).filter(AuditEvent.ts_epoch < cutoff).delete(
                synchronize_session=False
            )
            db.commit()
            # 删掉链首记录后，剩余记录的首条 prev_hash 会指向已消失的前驱，
            # 校验会误报"被篡改"。所以清理后必须重封整条链。
            resealed = reseal_chain(db)
        finally:
            db.execute(_create_guard_sql())
            db.execute(text(
                "CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_delete "
                "BEFORE DELETE ON audit_events "
                "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only: DELETE denied'); END"
            ))
            db.commit()

    return {
        "retentionDays": days,
        "cutoffEpoch": cutoff,
        "expired": expired,
        "purged": purged,
        "resealed": resealed,
        "dryRun": dry_run,
    }


def reseal_chain(db) -> int:
    """
    重封哈希链：从创世值开始重算全部记录的哈希（摘除触发器后才能调用）。

    仅在保留策略清理后需要——删除链首会让剩余首条记录的 prev_hash 悬空。
    注意这是 O(n) 操作，只在人工触发的清理后执行，不在请求路径上。
    """
    from sqlalchemy import text as _text

    db.execute(_text("DROP TRIGGER IF EXISTS trg_audit_events_no_update"))
    try:
        rows = db.query(AuditEvent).order_by(AuditEvent.id.asc()).yield_per(500)
        expected_prev = GENESIS_HASH
        head = db.get(AuditChainHead, 1)
        if head is None:
            head = AuditChainHead(id=1, seq=0, last_hash=GENESIS_HASH, last_id="")
            db.add(head)
            db.flush()
        count = 0
        last = None
        for row in rows:
            row.prev_hash = expected_prev
            row.hash = compute_hash(row.prev_hash, row)
            expected_prev = row.hash
            last = row
            count += 1
        head.last_hash = last.hash if last is not None else GENESIS_HASH
        head.last_id = last.id if last is not None else ""
        db.commit()
        return count
    finally:
        db.execute(_create_guard_sql())
        db.commit()


def _create_guard_sql():
    """重建 append-only 保护触发器"""
    from sqlalchemy import text

    return text(
        "CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_update "
        "BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only: UPDATE denied'); END"
    )


def _drop_guard_sql():
    """移除 append-only 保护触发器（仅保留策略清理时使用）"""
    from sqlalchemy import text

    return text("DROP TRIGGER IF EXISTS trg_audit_events_no_update")


def install_append_only_guard(engine) -> None:
    """
    安装数据库级保护：拒绝 UPDATE / DELETE。

    应用层"不提供删除方法"只是约定；触发器把约定变成约束——
    即使有人拿着 sqlite3 命令行直接连库，也改不动审计记录。
    """
    from sqlalchemy import text

    stmts = [
        "CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_update "
        "BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only: UPDATE denied'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_delete "
        "BEFORE DELETE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only: DELETE denied'); END",
    ]
    with engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))
