"""
批量 AI 分析任务管理（数据库持久化队列）
==================================================================
对标参考架构第 ③ 层 Workflow Orchestrator。

历史实现为进程内存字典单例，存在两个硬伤：
1. uvicorn 多 worker 部署时各进程内存不共享 → 任务状态在不同请求间"漂移"；
2. 进程重启/崩溃后任务丢失 → 前端轮询 task_id 永远拿不到终态。

本版改为 **SQLite 持久化**：
- 任务状态落 `batch_task_records` 表，多 worker 与重启后均可恢复；
- 对外方法签名与返回结构完全不变（`create_task` / `get_task` / `list_tasks` /
  `start_task` / `update_progress` / `finish_task` / `cleanup_stale` / `to_dict`），
  路由层与前端零改动；
- 仍为单机队列（无优先级抢占、无跨节点调度）。
  若后续需要跨节点/定时重试，再迁 Celery+Redis——届时只需替换本模块实现。

并发说明：
- 每次状态变更独立提交事务，避免长事务占锁；
- `WAL + busy_timeout` 已在 database.py 下发，多进程写冲突由 SQLite 等待而非直接报错。
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from database import SessionLocal
from models.batch_task import BatchTaskRecord

logger = logging.getLogger(__name__)

# 保留策略：已结束任务超过 24h 清理；任务总数超 200 时优先淘汰最老的已结束任务
TASK_TTL_SECONDS = 24 * 3600
TASK_MAX_STORED = 200


@dataclass
class BatchTask:
    """任务内存视图：与数据库行一一对应，供路由层与 to_dict 使用"""
    task_id: str
    case_ids: list[str]
    operator: str
    status: str = "pending"  # pending / running / completed / failed
    total: int = 0
    done: int = 0
    current_case_id: str = ""
    results: list[dict] = field(default_factory=list)
    error: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0


def _row_to_task(row: BatchTaskRecord) -> BatchTask:
    """数据库行 → BatchTask（JSON 列容错，脏数据不抛异常）"""
    def _loads(raw, default):
        try:
            v = json.loads(raw or "")
            return v if isinstance(v, type(default)) else default
        except (json.JSONDecodeError, TypeError):
            return default

    return BatchTask(
        task_id=row.task_id,
        case_ids=_loads(row.case_ids_json, []),
        operator=row.operator or "",
        status=row.status or "pending",
        total=row.total or 0,
        done=row.done or 0,
        current_case_id=row.current_case_id or "",
        results=_loads(row.results_json, []),
        error=row.error or "",
        created_at=row.created_at or 0.0,
        started_at=row.started_at or 0.0,
        finished_at=row.finished_at or 0.0,
    )


def _task_to_row(task: BatchTask, row: BatchTaskRecord) -> None:
    """BatchTask → 数据库行（原地写入）"""
    row.case_ids_json = json.dumps(task.case_ids, ensure_ascii=False)
    row.operator = task.operator
    row.status = task.status
    row.total = task.total
    row.done = task.done
    row.current_case_id = task.current_case_id
    row.results_json = json.dumps(task.results, ensure_ascii=False)
    row.error = task.error
    row.created_at = task.created_at
    row.started_at = task.started_at
    row.finished_at = task.finished_at


class BatchTaskManager:
    """数据库持久化批量任务管理器（多 worker 安全）"""

    def create_task(self, case_ids: list[str], operator: str) -> str:
        """创建批量任务，返回 task_id（完整 uuid4 hex，避免短 ID 被枚举）"""
        task_id = uuid.uuid4().hex
        now = time.time()
        db = SessionLocal()
        try:
            db.add(BatchTaskRecord(
                task_id=task_id,
                case_ids_json=json.dumps(case_ids, ensure_ascii=False),
                operator=operator,
                status="pending",
                total=len(case_ids),
                done=0,
                current_case_id="",
                results_json="[]",
                error="",
                created_at=now,
            ))
            db.commit()
        finally:
            db.close()
        self._prune()
        return task_id

    def _prune(self) -> None:
        """清理过期/超量的已结束任务，避免表无界增长"""
        now = time.time()
        db = SessionLocal()
        try:
            expired = db.query(BatchTaskRecord).filter(
                BatchTaskRecord.status.in_(("completed", "failed")),
                BatchTaskRecord.finished_at > 0,
                BatchTaskRecord.finished_at < now - TASK_TTL_SECONDS,
            ).all()
            for row in expired:
                db.delete(row)
            db.commit()
            total = db.query(BatchTaskRecord).count()
            if total > TASK_MAX_STORED:
                overflow = db.query(BatchTaskRecord).filter(
                    BatchTaskRecord.status.in_(("completed", "failed")),
                    BatchTaskRecord.finished_at > 0,
                ).order_by(BatchTaskRecord.finished_at.asc()).limit(total - TASK_MAX_STORED).all()
                for row in overflow:
                    db.delete(row)
                db.commit()
        finally:
            db.close()

    def get_task(self, task_id: str) -> Optional[BatchTask]:
        """获取任务状态"""
        db = SessionLocal()
        try:
            row = db.query(BatchTaskRecord).filter(BatchTaskRecord.task_id == task_id).first()
            return _row_to_task(row) if row else None
        finally:
            db.close()

    def list_tasks(self, operator: str = "") -> list[BatchTask]:
        """获取任务列表（可按操作人过滤），最新在前，最多 20 条"""
        db = SessionLocal()
        try:
            q = db.query(BatchTaskRecord)
            if operator:
                q = q.filter(BatchTaskRecord.operator == operator)
            rows = q.order_by(BatchTaskRecord.created_at.desc()).limit(20).all()
            return [_row_to_task(r) for r in rows]
        finally:
            db.close()

    def list_active(self) -> list[BatchTask]:
        """获取进行中的任务（供健康检查与指标暴露）"""
        db = SessionLocal()
        try:
            rows = db.query(BatchTaskRecord).filter(
                BatchTaskRecord.status.in_(("pending", "running"))
            ).all()
            return [_row_to_task(r) for r in rows]
        finally:
            db.close()

    def start_task(self, task_id: str):
        """标记任务开始"""
        db = SessionLocal()
        try:
            row = db.query(BatchTaskRecord).filter(BatchTaskRecord.task_id == task_id).first()
            if row:
                row.status = "running"
                row.started_at = time.time()
                db.commit()
        finally:
            db.close()

    def update_progress(self, task_id: str, case_id: str, result: dict):
        """更新单个病例的处理结果（读-改-写，独立短事务）"""
        db = SessionLocal()
        try:
            row = db.query(BatchTaskRecord).filter(BatchTaskRecord.task_id == task_id).first()
            if not row:
                return
            task = _row_to_task(row)
            task.current_case_id = case_id
            task.results.append(result)
            task.done += 1
            _task_to_row(task, row)
            db.commit()
        finally:
            db.close()

    def finish_task(self, task_id: str, status: str = "completed", error: str = ""):
        """标记任务结束"""
        db = SessionLocal()
        try:
            row = db.query(BatchTaskRecord).filter(BatchTaskRecord.task_id == task_id).first()
            if row:
                row.status = status
                row.error = error
                row.finished_at = time.time()
                row.current_case_id = ""
                db.commit()
        finally:
            db.close()

    def cleanup_stale(self) -> int:
        """
        启动时调用：把上次进程中断时仍处于 running/pending 的任务标记为 failed。

        持久化后任务本身不会丢，但被强杀的后台线程不会再推进它，
        若不标记终态，前端轮询会无限等待。返回被清理的任务数。
        """
        db = SessionLocal()
        try:
            rows = db.query(BatchTaskRecord).filter(
                BatchTaskRecord.status.in_(("pending", "running"))
            ).all()
            now = time.time()
            for row in rows:
                row.status = "failed"
                row.error = "服务重启导致任务中断，请重新发起批量分析"
                row.finished_at = now
                row.current_case_id = ""
            db.commit()
            cleaned = len(rows)
        finally:
            db.close()
        if cleaned > 0:
            logger.warning("启动时清理了 %d 个中断的批量分析任务（标记为 failed）", cleaned)
        return cleaned

    def to_dict(self, task: BatchTask) -> dict:
        """任务转字典（用于 API 响应）"""
        return {
            "taskId": task.task_id,
            "status": task.status,
            "total": task.total,
            "done": task.done,
            "currentCaseId": task.current_case_id,
            "results": task.results,
            "error": task.error,
            "createdAt": task.created_at,
            "startedAt": task.started_at,
            "finishedAt": task.finished_at,
        }


# 全局管理器（无状态：状态在数据库，多进程共享）
batch_task_manager = BatchTaskManager()
