"""
轻量内存限流器（滑动窗口）
------------------------------------------------------------------
按「IP + 动作」维度限制请求频率，用于验证码拉取、注册、登录等敏感端点，
缓解三类威胁：
1. 验证码接口高频拉取（每次同步 PIL 渲染）造成的 CPU 耗尽；
2. 注册接口刷库；
3. 登录爆破 / 故意输错他人密码触发账号锁定（恶意锁号）。

说明：
- 纯进程内存 + threading.Lock，单实例部署足够；多实例水平扩展时需替换为 Redis；
- 滑动窗口用 deque 记录窗口内时间戳，超限本次请求不计入；
- 定期清扫过期 key，避免限流字典随 IP 数量无界增长。
"""
import time
import threading
from collections import deque

from fastapi import Request, HTTPException, status

# {限流键: 窗口内最近请求的单调时间戳队列}
_windows: dict[str, deque[float]] = {}
_guard = threading.Lock()

# 每 N 次调用顺带做一次过期 key 清扫（摊销 O(1)，避免单独后台线程）
_SWEEP_EVERY = 1000
_sweep_calls = 0


def hit_rate_limit(key: str, limit: int, window_sec: int) -> bool:
    """
    记录一次请求并判定是否超限。
    返回 True 表示已超限（本次不计入窗口），False 表示放行。
    """
    global _sweep_calls
    now = time.monotonic()
    bound = now - window_sec
    with _guard:
        _sweep_calls += 1
        if _sweep_calls >= _SWEEP_EVERY:
            _sweep_calls = 0
            # 空队列或最新一次请求也已滑出窗口的 key 直接回收
            for k in [k for k, dq in _windows.items() if not dq or dq[-1] < bound]:
                del _windows[k]
        dq = _windows.get(key)
        if dq is None:
            dq = deque()
            _windows[key] = dq
        while dq and dq[0] < bound:
            dq.popleft()
        if len(dq) >= limit:
            return True
        dq.append(now)
        return False


def reset_rate_limit(key: str | None = None) -> None:
    """清空限流计数（测试用；key=None 清空全部）"""
    with _guard:
        if key is None:
            _windows.clear()
        else:
            _windows.pop(key, None)


def client_ip(request: Request) -> str:
    """取客户端真实 IP（兼容反向代理 X-Forwarded-For，与 routers.auth._client_ip 同口径）"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:50]
    return (request.client.host if request.client else "")[:50]


def rate_limit(name: str, limit: int, window_sec: int):
    """
    FastAPI 依赖工厂：按 IP 维度对指定动作做滑动窗口限流。
    用法：dependencies=[Depends(rate_limit("captcha", 30, 60))]
    """
    def _dep(request: Request) -> None:
        ip = client_ip(request)
        if hit_rate_limit(f"{name}:{ip}", limit, window_sec):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="请求过于频繁，请稍后再试",
            )
    return _dep
