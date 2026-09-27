"""
请求上下文追踪（trace_id / request_id）
------------------------------------------------------------------
通过 contextvars 在异步中间件中注入唯一请求 ID，
所有日志自动携带同一 trace_id，便于跨函数/跨线程排障。

工作原理：
1. 中间件在请求进入时生成（或透传 X-Request-Id 头）唯一 ID
2. 通过 contextvars.set 写入当前异步上下文
3. 日志 Filter 读取 contextvar，注入到每条日志的 trace_id 字段
4. 响应头 X-Request-Id 返回给前端，前端报障时附此 ID 即可定位全链路日志

使用：
    from services.request_context import request_ctx, get_trace_id
    logger.info("处理中...")  # 自动携带 trace_id
"""
import contextvars
import uuid
from typing import Optional

# 请求级上下文变量（异步安全：每个请求独立上下文，不串号）
_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)
# 方法-路径（便于日志直接展示调用入口，无需从 record.args 反推）
_request_route: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_route", default=None
)


def get_trace_id() -> Optional[str]:
    """获取当前请求的 trace_id（无请求上下文时返回 None）"""
    return _request_id.get()


def get_request_route() -> Optional[str]:
    """获取当前请求路由（method + path）"""
    return _request_route.get()


class _ContextTokens:
    """
    一次 set 产生两个 ContextVar 的 Token，用轻量容器打包返回。
    不能往 contextvars.Token 对象上 setattr（Token 是 C 实现，无 __dict__，
    Python 3.13 会抛 AttributeError），所以单独建一个普通类持有两个 token。
    """
    __slots__ = ("id_token", "route_token")

    def __init__(self, id_token: contextvars.Token, route_token: contextvars.Token):
        self.id_token = id_token
        self.route_token = route_token


def set_request_context(request_id: str, route: str = "") -> _ContextTokens:
    """
    在中间件中注入请求上下文。
    返回 _ContextTokens 供请求结束时 reset_request_context 恢复上下文，避免上下文泄漏。
    """
    t1 = _request_id.set(request_id)
    t2 = _request_route.set(route)
    return _ContextTokens(t1, t2)


def reset_request_context(token: _ContextTokens) -> None:
    """恢复请求上下文（请求结束调用）"""
    try:
        _request_route.reset(token.route_token)
    except (ValueError, LookupError):
        # Token 已被重复 reset 或上下文已变，忽略以避免污染响应
        pass
    try:
        _request_id.reset(token.id_token)
    except (ValueError, LookupError):
        pass


def gen_request_id() -> str:
    """生成短请求 ID（8 位 hex，便于日志与报障沟通）"""
    return uuid.uuid4().hex[:8]


# 兼容别名（部分模块可能用 request_ctx 命名）
request_ctx = _request_id
