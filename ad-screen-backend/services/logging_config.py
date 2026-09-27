"""
统一日志配置
------------------------------------------------------------------
医疗系统排障依赖完整的操作与异常链路，本模块在应用启动时一次性配置：
- 控制台输出（开发直观，INFO 及以上）
- 轮转文件（logs/ad_screen.log，单文件 10MB、保留 5 份，避免磁盘占满）
- 错误日志单独归档（logs/error.log，仅 WARNING 及以上，便于审计快速定位）
- 统一格式：时间 [级别] [模块] [trace_id] 消息
- 请求追踪：每条日志自动携带当前请求 trace_id（contextvars 注入）

用法（main.py 启动时调用一次）：
    from services.logging_config import setup_logging
    setup_logging()

各业务模块统一通过 logger = logging.getLogger(__name__) 取用，
禁止再自行调用 logging.basicConfig（重复配置会导致日志重复/丢失）。
"""
import os
import logging
from logging.handlers import RotatingFileHandler

from config import LOG_DIR, LOG_LEVEL

# 日志格式（模块名占 18 字符宽；trace_id 占 8 字符宽，对齐便于阅读与 grep）
_LOG_FMT = "%(asctime)s [%(levelname)-7s] [%(name)-18s] [%(trace_id)8s] %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# 单文件大小上限与保留份数（10MB × 5 ≈ 50MB 上限）
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_configured = False


class TraceIdFilter(logging.Filter):
    """
    日志 Filter：从 contextvar 注入 trace_id 到每条 LogRecord。
    - 无请求上下文（启动期日志、后台线程）时填 "-"，保持格式对齐
    - 不抛异常：contextvars 在某些线程池可能未初始化，安全降级
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from services.request_context import get_trace_id
            tid = get_trace_id()
        except Exception:
            tid = None
        record.trace_id = tid or "-"
        return True


def setup_logging() -> logging.Logger:
    """
    初始化全局日志（幂等：重复调用只配置一次，避免 uvicorn reload 双进程重复 handler）。
    返回根 logger，便于启动处输出首条就绪日志。
    """
    global _configured
    if _configured:
        return logging.getLogger("ad_screen")

    os.makedirs(LOG_DIR, exist_ok=True)

    trace_filter = TraceIdFilter()
    formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)

    # 控制台 handler（开发即时可见）
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    console.addFilter(trace_filter)

    # 全量轮转文件（INFO 及以上）
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "ad_screen.log"),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(LOG_LEVEL)
    file_handler.addFilter(trace_filter)

    # 错误专用文件（WARNING 及以上，审计与故障定位）
    error_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "error.log"),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.WARNING)
    error_handler.addFilter(trace_filter)

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)
    # 清理既有 handler（兼容 model_inference 中遗留的 basicConfig）
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(file_handler)
    root.addHandler(error_handler)

    # 第三方库降噪：SQLAlchemy 引擎 SQL 回放在生产无意义且噪声大
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger = logging.getLogger("ad_screen")
    logger.info("日志系统初始化完成（级别=%s，目录=%s）", LOG_LEVEL, LOG_DIR)

    _configured = True
    return logger
