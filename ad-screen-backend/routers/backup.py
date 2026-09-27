"""
数据备份与恢复路由（admin）
------------------------------------------------------------------
SQLite 在线一致性备份，满足运维灾备需求：
- GET  /system/backup/info      数据库大小与各表行数
- POST /system/backup/download  生成一致性备份并下载（同时存档到 backups/）
- GET  /system/backup/files     服务器存档备份列表
- POST /system/backup/restore   从存档恢复（恢复前自动再备份一次当前库）
"""
import os
import shutil
import sqlite3
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import DB_PATH, BASE_DIR
from database import engine, get_db
from schemas.common import ok, fail
from models.log import SystemLog as SystemLogModel
from services.auth import get_current_user_qs
from services.utils import format_date_time, next_seq_id

router = APIRouter(
    prefix="/system/backup",
    tags=["数据备份"],
    dependencies=[Depends(get_current_user_qs)],
)

BACKUP_DIR = os.path.join(BASE_DIR, "backups")
# 允许恢复的表（用于备份完整性校验）
EXPECTED_TABLES = {
    "users", "case_records", "analysis_versions", "intervention_records",
    "follow_up_records", "follow_up_visits", "case_logs", "system_logs",
    "notification_records", "announcement_records",
}
# 恢复互斥锁：恢复是破坏性全库替换，必须串行化，防止两个恢复并发互相覆盖
_restore_lock = threading.Lock()


def _require_admin(user: dict):
    return user.get("role") == "admin"


def _audit(db: Session, user: dict, request: Request, action: str, detail: str, result: str = "成功"):
    """敏感操作审计落库（备份下载/恢复涉及全量患者数据，必须留痕可追溯）"""
    xff = request.headers.get("x-forwarded-for", "")
    ip = (xff.split(",")[0].strip() if xff else (request.client.host if request.client else ""))[:50]
    db.add(SystemLogModel(
        id=next_seq_id(db, SystemLogModel, "S", start=1),
        module="数据备份",
        action=action,
        operator=user.get("username", ""),
        role=user.get("roleName", ""),
        ip=ip,
        result=result,
        time=format_date_time(),
        detail=detail,
    ))
    db.commit()


def _ensure_backup_dir() -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _make_consistent_backup(dest_path: str) -> None:
    """用 SQLite 在线 backup API 生成一致性快照（不阻塞业务连接）"""
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _table_row_counts(db_path: str) -> dict:
    """读取各表行数（备份完整性校验/信息展示）"""
    conn = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        counts = {}
        for t in EXPECTED_TABLES:
            if t in tables:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return counts
    finally:
        conn.close()


@router.get("/info")
def backup_info(current_user: dict = Depends(get_current_user_qs)):
    """数据库文件大小与各表现有行数"""
    if not _require_admin(current_user):
        return fail("无权限", 403)
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    counts = _table_row_counts(DB_PATH)
    backups = []
    if os.path.isdir(BACKUP_DIR):
        backups = sorted(
            (f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")),
            reverse=True,
        )
    return ok({
        "dbSize": size,
        "tableCounts": counts,
        "backupCount": len(backups),
        "backups": backups,
    })


@router.post("/download")
def backup_download(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """生成一致性备份 → 存档到 backups/ → 作为文件下载"""
    if not _require_admin(current_user):
        return fail("无权限", 403)
    _ensure_backup_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ad_screen_backup_{stamp}.db"
    dest = os.path.join(BACKUP_DIR, filename)
    _make_consistent_backup(dest)
    size_kb = os.path.getsize(dest) // 1024
    _audit(db, current_user, request, "下载数据库备份",
           f"备份文件 {filename}（{size_kb} KB）已存档并下载")
    return FileResponse(
        path=dest,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.get("/files")
def backup_files(current_user: dict = Depends(get_current_user_qs)):
    """服务器存档备份列表（文件名/大小/时间）"""
    if not _require_admin(current_user):
        return fail("无权限", 403)
    items = []
    if os.path.isdir(BACKUP_DIR):
        for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if not f.endswith(".db"):
                continue
            fp = os.path.join(BACKUP_DIR, f)
            stat = os.stat(fp)
            items.append({
                "filename": f,
                "size": stat.st_size,
                "createdAt": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    return ok(items)


class RestorePayload(BaseModel):
    filename: str


@router.post("/restore")
def backup_restore(
    payload: RestorePayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    从存档恢复：
    1. 校验备份文件完整性（含核心表）
    2. 恢复前自动再备份一次当前库（pre_restore_ 前缀，可回退）
    3. engine.dispose() 关闭连接池 → 替换数据库文件 → 新请求自动重连
    """
    if not _require_admin(current_user):
        return fail("无权限", 403)

    # 文件名白名单校验（防路径穿越）
    safe_name = os.path.basename(payload.filename)
    if safe_name != payload.filename or not safe_name.endswith(".db"):
        _audit(db, current_user, request, "恢复数据库备份",
               f"非法文件名被拦截：{payload.filename}", result="失败")
        return fail("非法的备份文件名", 400)
    src_backup = os.path.join(BACKUP_DIR, safe_name)
    if not os.path.isfile(src_backup):
        _audit(db, current_user, request, "恢复数据库备份",
               f"备份不存在：{safe_name}", result="失败")
        return fail("备份文件不存在", 404)

    # 1. 完整性校验：备份必须包含核心业务表
    try:
        counts = _table_row_counts(src_backup)
    except sqlite3.DatabaseError:
        _audit(db, current_user, request, "恢复数据库备份",
               f"备份已损坏：{safe_name}", result="失败")
        return fail("备份文件已损坏，无法恢复", 400)
    if "users" not in counts or "case_records" not in counts:
        _audit(db, current_user, request, "恢复数据库备份",
               f"备份缺少核心表：{safe_name}", result="失败")
        return fail("备份文件缺少核心数据表，已拒绝恢复", 400)

    _ensure_backup_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pre_restore = os.path.join(BACKUP_DIR, f"pre_restore_{stamp}.db")

    # 恢复是破坏性全库替换：全局互斥，防止两个恢复并发互相覆盖
    if not _restore_lock.acquire(blocking=False):
        _audit(db, current_user, request, "恢复数据库备份",
               f"另一个恢复任务正在进行，拒绝并发：{safe_name}", result="失败")
        return fail("另一个恢复任务正在进行，请稍后重试", 409)
    try:
        # 审计必须在 dispose/换库之前落库（之后当前 ORM 会话指向的库将被替换）
        _audit(db, current_user, request, "恢复数据库备份",
               f"从 {safe_name} 恢复；恢复前快照 pre_restore_{stamp}.db")

        # 2. 关闭连接池（释放 Windows 文件锁）后替换文件
        engine.dispose()
        # 清理残留的 journal/wal/shm，防止旧事务回滚日志跨恢复覆盖新库
        for suffix in ("-journal", "-wal", "-shm"):
            side = DB_PATH + suffix
            if os.path.isfile(side):
                try:
                    os.remove(side)
                except OSError:
                    pass
        try:
            shutil.copy2(DB_PATH, pre_restore)
            shutil.copy2(src_backup, DB_PATH)
        except OSError as e:
            return fail(f"恢复失败：{e}（恢复前的备份已存为 pre_restore_{stamp}.db）", 500)
    finally:
        _restore_lock.release()

    return ok(
        {"preRestoreFile": f"pre_restore_{stamp}.db"},
        "数据库已恢复，即将自动重连；如异常可用 pre_restore 备份回退",
    )
