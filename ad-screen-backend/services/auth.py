"""
认证服务
- JWT 签发与校验
- 图形验证码生成与校验（内存存储，5 分钟过期）
- 密码哈希（bcrypt 直接调用，不依赖 passlib）
- FastAPI 依赖注入：get_current_user
"""
import time
import random
import secrets
import threading
import logging
from datetime import datetime, timedelta
from typing import Callable

import bcrypt
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, Request, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import (
    SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES,
    LOGIN_MAX_FAILS, LOGIN_LOCK_MINUTES, LOGIN_FAIL_WINDOW_MINUTES,
)
from database import SessionLocal
from models.user import User as UserModel

logger = logging.getLogger(__name__)

# ---------- 密码哈希 ----------


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        # hash 格式非法/损坏时按校验失败处理，记录便于发现脏数据
        logger.debug("bcrypt 密码校验异常（hash 可能损坏）：%s", e)
        return False


# ---------- 密码复杂度 ----------
PASSWORD_MIN_LEN = 6
PASSWORD_MAX_LEN = 20
# 常见弱口令黑名单（医疗内网系统不过度严苛，但必须挡住扫库常用口令）
_WEAK_PASSWORDS = {
    "123456", "1234567", "12345678", "123456789", "111111", "000000",
    "666666", "888888", "abc123", "a12345", "123456a", "aa1234",
    "password", "password1", "qwerty", "admin", "admin123", "iloveyou",
}


def validate_password(password: str) -> str | None:
    """
    统一密码复杂度校验：6-20 位、必须同时包含 ASCII 字母与数字、拒绝常见弱口令。
    返回 None 表示通过，否则返回可直接展示给用户的中文错误信息。
    注册 / 管理员建号 / 修改密码三处共用，保证口径一致。
    """
    pwd = password or ""
    if not (PASSWORD_MIN_LEN <= len(pwd) <= PASSWORD_MAX_LEN):
        return f"密码长度须为 {PASSWORD_MIN_LEN}-{PASSWORD_MAX_LEN} 位"
    has_letter = any(c.isascii() and c.isalpha() for c in pwd)
    has_digit = any(c.isdigit() for c in pwd)
    if not (has_letter and has_digit):
        return "密码须同时包含字母和数字"
    if pwd.lower() in _WEAK_PASSWORDS:
        return "密码过于简单，请更换为字母与数字混合的强密码"
    return None

# ---------- 验证码存储 ----------
# { captchaKey: { code, expire } }
_captcha_store: dict[str, dict] = {}
_CAPTCHA_TTL = 5 * 60 * 1000  # 5 分钟
# 字典硬上限：过期清理后仍超额（高频拉取）时淘汰最旧条目，防止内存无限堆积
_CAPTCHA_MAX_ENTRIES = 5000


def generate_captcha() -> tuple[str, str]:
    """
    生成 4 位图形验证码
    返回 (captchaKey, code)
    前端 canvas 绘制由前端完成，后端只负责生成与校验
    """
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 剔除易混淆字符
    # 验证码属安全凭据，使用密码学随机源（random.choice 可被预测）
    code = "".join(secrets.choice(chars) for _ in range(4))
    key = f"cap_{int(time.time() * 1000)}_{secrets.randbelow(10000)}"
    _captcha_store[key] = {"code": code.lower(), "expire": time.time() * 1000 + _CAPTCHA_TTL}
    # 清理过期项
    now = time.time() * 1000
    for k in list(_captcha_store.keys()):
        if _captcha_store[k]["expire"] < now:
            del _captcha_store[k]
    # 硬上限兜底：仍超额时淘汰最早到期的一批（极端高频刷验证码接口场景）
    if len(_captcha_store) >= _CAPTCHA_MAX_ENTRIES:
        oldest = sorted(_captcha_store, key=lambda k: _captcha_store[k]["expire"])[:100]
        for k in oldest:
            _captcha_store.pop(k, None)
    return key, code


def verify_captcha(key: str, user_input: str) -> bool:
    """校验验证码（忽略大小写，一次性使用）"""
    entry = _captcha_store.get(key)
    if not entry:
        return False
    del _captcha_store[key]
    return entry["expire"] > time.time() * 1000 and entry["code"] == user_input.strip().lower()


# ---------- 登录失败锁定（防爆破，内存存储） ----------
# 结构：{ username: {"fails": int, "lock_until": float(时间戳秒), "first_fail": float(时间戳秒), "last_ip": str} }
_login_fail_store: dict[str, dict] = {}
_login_lock = threading.Lock()


def _login_cleanup(now: float) -> None:
    """清理已过锁定期的记录（锁定期满后失败计数清零，给予重试机会）"""
    expired = [u for u, v in _login_fail_store.items() if v.get("lock_until", 0) < now and v["fails"] >= LOGIN_MAX_FAILS]
    for u in expired:
        del _login_fail_store[u]


def check_login_locked(username: str) -> tuple[bool, int]:
    """
    检查账号是否处于锁定状态。
    返回 (是否锁定, 剩余锁定分钟数，向上取整至少1分钟)。
    """
    if not username:
        return False, 0
    now = time.time()
    with _login_lock:
        _login_cleanup(now)
        rec = _login_fail_store.get(username)
        if not rec or rec.get("lock_until", 0) < now:
            return False, 0
        remain_sec = rec["lock_until"] - now
        return True, max(1, int(remain_sec // 60) + (1 if remain_sec % 60 else 0))


def record_login_fail(username: str, ip: str = "") -> tuple[int, bool, int]:
    """
    记录一次登录失败。
    返回 (当前连续失败次数, 是否触发锁定, 锁定分钟数)。
    锁定策略：LOGIN_FAIL_WINDOW_MINUTES 分钟滑动窗口内连续失败达到 LOGIN_MAX_FAILS 次后
    锁定 LOGIN_LOCK_MINUTES 分钟；窗口外的历史失败自动清零重新计数；
    锁定期间继续失败不延长也不重置（固定时长，到期自动清零）。
    """
    now = time.time()
    with _login_lock:
        _login_cleanup(now)
        rec = _login_fail_store.get(username)
        # 锁定期间直接返回现状，不重复计数
        if rec and rec.get("lock_until", 0) >= now:
            remain_sec = rec["lock_until"] - now
            return rec["fails"], True, max(1, int(remain_sec // 60) + (1 if remain_sec % 60 else 0))
        if not rec:
            rec = {"fails": 0, "lock_until": 0.0, "first_fail": now, "last_ip": ip}
            _login_fail_store[username] = rec
        elif now - rec.get("first_fail", now) > LOGIN_FAIL_WINDOW_MINUTES * 60:
            # 距首次失败已超过滑动窗口：历史失败不再计入"连续失败"，窗口重新起算
            rec["fails"] = 0
            rec["first_fail"] = now
        rec["fails"] += 1
        rec["last_ip"] = ip
        locked = rec["fails"] >= LOGIN_MAX_FAILS
        if locked:
            rec["lock_until"] = now + LOGIN_LOCK_MINUTES * 60
            logger.warning(
                "账号 %s 连续登录失败 %d 次，已锁定 %d 分钟（IP=%s）",
                username, rec["fails"], LOGIN_LOCK_MINUTES, ip,
            )
        return rec["fails"], locked, LOGIN_LOCK_MINUTES if locked else 0


def clear_login_fails(username: str) -> None:
    """登录成功后清除失败计数"""
    with _login_lock:
        _login_fail_store.pop(username, None)


# ---------- 验证码图片渲染（服务端绘图，明文 code 不下发） ----------

def generate_captcha_image() -> tuple[str, str]:
    """
    生成图形验证码并在服务端渲染为 PNG data URL。
    返回 (captchaKey, data_url)；code 仅保存在服务端内存，响应体不下发，
    防止脚本直接读取验证码明文实施登录爆破。
    绘图风格与前端 canvas 版本一致：浅蓝灰底 + 干扰线/噪点 + 旋转字符。
    """
    import io
    import base64
    import os
    from PIL import Image, ImageDraw, ImageFont

    key, code = generate_captcha()

    # 画布需为旋转后的字符预留边距（旋转 expand 后单字符块约 34×40）
    w, h = 124, 46
    img = Image.new("RGB", (w, h), (238, 243, 248))
    draw = ImageDraw.Draw(img)

    # 字体：优先系统 TrueType（Windows arial / Linux DejaVu），回退 PIL 默认字体
    font = None
    for fp in (
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.isfile(fp):
            try:
                font = ImageFont.truetype(fp, 24)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    # 干扰线（浅色，避免压过字符）
    for _ in range(3):
        draw.line(
            [(random.randint(0, w), random.randint(0, h)),
             (random.randint(0, w), random.randint(0, h))],
            fill=(158, 184, 210),
            width=1,
        )

    # 逐字符绘制（随机颜色 + 小角度旋转）
    colors = [(47, 109, 163), (60, 85, 107), (81, 116, 154), (40, 66, 92)]
    char_imgs = []
    for i, ch in enumerate(code):
        # 每个字符单独成图后旋转粘贴，便于控制角度
        ci = Image.new("RGBA", (26, 34), (255, 255, 255, 0))
        cd = ImageDraw.Draw(ci)
        cd.text((3, 4), ch, font=font, fill=colors[i % len(colors)])
        angle = random.uniform(-20, 20)
        ci = ci.rotate(angle, expand=1, resample=Image.BICUBIC)
        char_imgs.append(ci)

    # 起始 x=4，步进 29；4 字符右缘 4+3×29+26=117，旋转余量充足，不被裁切
    x = 4
    for ci in char_imgs:
        img.paste(ci, (x, random.randint(3, 7)), ci)
        x += 29

    # 噪点（稀疏点缀）
    for _ in range(46):
        x1 = random.randint(0, w - 1)
        y1 = random.randint(0, h - 1)
        img.putpixel((x1, y1), (
            random.randint(40, 110), random.randint(70, 130), random.randint(90, 160)
        ))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return key, data_url


# ---------- JWT ----------
# 媒体票据有效期（分钟）：仅供 <img>/EventSource 等只能走 URL query 的场景使用，
# 短时有效，即使完整 URL 落入访问日志/浏览器历史/Referer，泄露窗口也极小。
MEDIA_TOKEN_EXPIRE_MINUTES = 10


def create_access_token(data: dict) -> str:
    """签发常规会话 JWT（typ=access，仅允许经 Authorization 头使用）"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "typ": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_media_token(data: dict, expires_minutes: int = MEDIA_TOKEN_EXPIRE_MINUTES) -> str:
    """
    签发短期媒体 JWT（typ=media），仅允许经 URL query 使用，
    用于影像切片/Grad-CAM 的 <img src> 与 SSE EventSource。
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire, "typ": "media"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """解码 JWT，失败抛 401"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")


# ---------- 令牌版本（会话失效方案，无需 Redis） ----------
def bump_token_version(db, user_id: int) -> int:
    """
    令牌版本号自增，使该用户此前签发的所有 JWT 在下次请求时立即失效。
    触发场景：主动登出、修改密码、管理员禁用账号。
    返回自增后的版本号（用户不存在时返回 0）。
    """
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if user is None:
        return 0
    user.token_version = (user.token_version or 0) + 1
    db.flush()
    return user.token_version


# ---------- FastAPI 依赖注入 ----------
security = HTTPBearer(auto_error=False)


def _payload_to_user(payload: dict) -> dict:
    """JWT payload → 用户信息 dict"""
    return {
        "id": payload.get("id", 0),
        "username": payload.get("username", ""),
        "realName": payload.get("realName", ""),
        "role": payload.get("role", ""),
        "roleName": payload.get("roleName", ""),
        "department": payload.get("department", ""),
    }


def _verify_token_session(payload: dict, expected_typ: str = "access") -> dict:
    """
    JWT 解码后的会话有效性校验（查库，每请求一次主键查询，SQLite 开销可忽略）：
    - 令牌用途必须与通道匹配：access 票仅允许 Authorization 头；media 票仅允许 URL query
      （防止落入日志的短期媒体票被拿去调用完整业务 API，反之亦然）
    - 用户必须存在且处于启用状态（管理员禁用后旧令牌立即失效）
    - payload.ver 必须等于 users.token_version（登出/改密后旧令牌立即失效）
    注：历史令牌无 typ 字段按 access 处理，与升级前签发的会话兼容。
    """
    token_typ = payload.get("typ") or "access"
    if token_typ != expected_typ:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌用途与认证通道不匹配",
        )
    user_id = payload.get("id", 0)
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.id == user_id).first()
    finally:
        db.close()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在或已失效，请重新登录")
    if user.status != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号已被禁用，请联系管理员")
    token_ver = payload.get("ver", 0) or 0
    if token_ver != (user.token_version or 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效，请重新登录")
    return _payload_to_user(payload)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    从 Authorization: Bearer 头解析当前用户（仅接受常规会话令牌）
    返回 { id, username, realName, role, roleName, department }
    """
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    payload = decode_token(credentials.credentials)
    return _verify_token_session(payload, expected_typ="access")


def _is_media_allowed_path(request: Request) -> bool:
    """媒体票据的 URL 通道仅允许访问只读影像资源与 SSE 推理流"""
    if request.method != "GET":
        return False
    path = request.url.path
    # 影像切片 / Grad-CAM / 体数据 / 元信息；AI 推理 SSE 流
    return "/imaging/" in path or path.endswith("/run-stream")


def get_current_user_qs(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    token: str = Query(default="", description="短期媒体 JWT（EventSource / <img> 等无法自定义请求头的场景通过 query 传递）"),
) -> dict:
    """
    双模式认证依赖，按凭证来源强制匹配令牌用途：
      - Authorization: Bearer 头 → 仅接受 typ=access 常规会话票
      - ?token= 查询参数          → 仅接受 typ=media 短期媒体票（GET /auth/media-token 换取），
                                    且只能访问只读影像资源与 SSE 推理流
    覆盖三类无法自定义请求头的前端调用：
      - EventSource（SSE 流式推理）
      - <img src>（影像切片 / Grad-CAM PNG）
      - fetch(application/octet-stream)（3D 体数据）
    """
    if credentials:
        payload = decode_token(credentials.credentials)
        return _verify_token_session(payload, expected_typ="access")
    raw = token or ""
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    if not _is_media_allowed_path(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="媒体票据仅允许访问影像资源与推理流",
        )
    try:
        payload = jwt.decode(raw, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
    return _verify_token_session(payload, expected_typ="media")


def require_role(*allowed_roles: str) -> Callable:
    """
    角色校验依赖工厂：require_role('admin') / require_role('researcher', 'admin')
    - 未登录 / token 失效 → 401
    - 角色不在白名单 → 403
    """
    def dep(user: dict = Depends(get_current_user_qs)) -> dict:
        if allowed_roles and user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前角色无权执行此操作",
            )
        return user
    return dep
