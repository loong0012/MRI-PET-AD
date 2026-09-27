"""
后端全局配置
- SQLite 数据库路径
- JWT 密钥与过期时间
- 跨域白名单

配置优先级：环境变量 > 默认值（开发便利）。
生产部署必须通过环境变量注入敏感配置：
  ADSCREEN_ENV=production
  ADSCREEN_SECRET_KEY=<高强度随机密钥>
  ADSCREEN_CORS_ORIGINS=https://your-hospital-domain
生产环境若检测到仍在使用内置默认密钥，启动时直接报错终止，避免弱密钥签发 JWT。
"""
import os

# ---------- 运行环境 ----------
# development（默认，宽松校验）/ production（强制安全校验）
APP_ENV = os.getenv("ADSCREEN_ENV", "development").strip().lower()

# ---------- 数据库 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("ADSCREEN_DB_PATH") or os.path.join(BASE_DIR, "ad_screen.db")
DB_URL = f"sqlite:///{DB_PATH}"

# ---------- JWT ----------
# 内置开发密钥仅用于本地开发；生产环境必须用 ADSCREEN_SECRET_KEY 覆盖
_DEV_SECRET_KEY = "ad-screen-medical-secret-2026-change-in-production"
SECRET_KEY = os.getenv("ADSCREEN_SECRET_KEY", _DEV_SECRET_KEY).strip()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ADSCREEN_TOKEN_EXPIRE_MINUTES", "720"))  # 默认 12 小时

# ---------- 合规审计 ----------
# 审计日志保留天数。HIPAA 要求至少 6 年（2190 天），多数机构留存 10 年。
# 设为 0 表示永久保留。清理为显式触发（默认 dry-run），系统不会自动删除。
AUDIT_RETENTION_DAYS = int(os.getenv("ADSCREEN_AUDIT_RETENTION_DAYS", "2190"))
# 是否启用自动审计中间件（默认开）。关掉后仅保留手工埋点。
AUDIT_ENABLED = os.getenv("ADSCREEN_AUDIT_ENABLED", "1").strip().lower() not in ("0", "false", "no")

# ---------- 登录防爆破 ----------
LOGIN_MAX_FAILS = int(os.getenv("ADSCREEN_LOGIN_MAX_FAILS", "5"))        # 连续失败 N 次锁定
LOGIN_LOCK_MINUTES = int(os.getenv("ADSCREEN_LOGIN_LOCK_MINUTES", "15"))  # 锁定时长（分钟）
# 连续失败计数滑动窗口（分钟）：窗口外的历史失败自动清零，避免"数月前错 4 次 + 今天错 1 次"被锁定
LOGIN_FAIL_WINDOW_MINUTES = int(os.getenv("ADSCREEN_LOGIN_FAIL_WINDOW_MINUTES", "15"))

# ---------- 跨域（开发环境允许前端 Vite 端口） ----------
# 生产环境用 ADSCREEN_CORS_ORIGINS 传入逗号分隔的白名单域名
_env_origins = os.getenv("ADSCREEN_CORS_ORIGINS", "").strip()
if _env_origins:
    CORS_ORIGINS = [o.strip() for o in _env_origins.split(",") if o.strip()]
else:
    CORS_ORIGINS = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
    ]

# ---------- 日志 ----------
LOG_DIR = os.getenv("ADSCREEN_LOG_DIR") or os.path.join(BASE_DIR, "logs")
LOG_LEVEL = os.getenv("ADSCREEN_LOG_LEVEL", "INFO").strip().upper()

# ---------- SQLite 并发 ----------
# 争锁等待毫秒：批量推理写库与 Web 读并发时，等待而非立即抛 database is locked
SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("ADSCREEN_SQLITE_BUSY_TIMEOUT_MS", "5000"))
# 同步策略：WAL 下 NORMAL 兼顾吞吐与崩溃安全；需要极致持久化改为 FULL
SQLITE_SYNCHRONOUS = os.getenv("ADSCREEN_SQLITE_SYNCHRONOUS", "NORMAL").strip().upper()
if SQLITE_SYNCHRONOUS not in ("OFF", "NORMAL", "FULL", "EXTRA"):
    SQLITE_SYNCHRONOUS = "NORMAL"

# ---------- 演示数据开关 ----------
# 生产环境默认关闭：避免向医院环境写入 admin/admin123 一类公开弱口令
_seed_raw = os.getenv("ADSCREEN_SEED_DEMO_DATA", "").strip().lower()
if _seed_raw in ("1", "true", "yes", "on"):
    SEED_DEMO_DATA = True
elif _seed_raw in ("0", "false", "no", "off"):
    SEED_DEMO_DATA = False
else:
    SEED_DEMO_DATA = APP_ENV != "production"

# 关闭演示数据时管理员初始密码；未设置则由系统随机生成并打印一次性日志
ADMIN_PASSWORD = os.getenv("ADSCREEN_ADMIN_PASSWORD", "").strip()

# ---------- 全局兜底限流 ----------
# 每 IP 每分钟请求上限，0 表示关闭全局限流（仅保留端点级限流）
GLOBAL_RATE_LIMIT = int(os.getenv("ADSCREEN_GLOBAL_RATE_LIMIT", "600"))
# 滑动窗口长度（秒）
GLOBAL_RATE_WINDOW_SEC = int(os.getenv("ADSCREEN_GLOBAL_RATE_WINDOW_SEC", "60"))


# 公开渠道出现过的占位密钥：长度达标但实质是弱密钥，必须显式拒绝
# （否则 compose 示例串这类 48 字符明文可绕过"长度 ≥32"检查）
_KNOWN_PLACEHOLDER_KEYS = {
    _DEV_SECRET_KEY,
    "please-change-this-secret-key-in-production-env",
    "please-change-this-secret-key-in-production",
    "change-me",
    "changeme",
    "your-secret-key",
    "your_secret_key",
    "secret",
    "ad-screen-secret",
}


def validate_security_config() -> list[str]:
    """
    启动时安全校验，返回警告/错误信息列表。
    生产环境出现致命配置错误时由 main.py 决定是否终止启动。
    """
    issues: list[str] = []
    if APP_ENV == "production":
        if not SECRET_KEY or SECRET_KEY == _DEV_SECRET_KEY:
            issues.append(
                "生产环境（ADSCREEN_ENV=production）必须通过环境变量 ADSCREEN_SECRET_KEY 设置自定义 JWT 密钥，"
                "禁止使用内置开发默认密钥"
            )
        if len(SECRET_KEY) < 32:
            issues.append("ADSCREEN_SECRET_KEY 长度不足，建议至少 32 个字符的高强度随机串")
        # 长度达标但属于公开占位串：这类密钥与弱口令等效，必须拦截
        if SECRET_KEY.lower() in {k.lower() for k in _KNOWN_PLACEHOLDER_KEYS}:
            issues.append(
                "ADSCREEN_SECRET_KEY 使用了公开已知的占位密钥（如 docker-compose 示例串），"
                "请用 `python -c \"import secrets;print(secrets.token_urlsafe(48))\"` 生成后重新注入"
            )
        if not _env_origins:
            issues.append("生产环境建议通过 ADSCREEN_CORS_ORIGINS 明确配置 CORS 白名单域名")
    return issues
