"""
审计范围策略（声明式）
------------------------------------------------------------------
为什么用声明式策略表，而不是在每个路由里手工埋点：

项目实测有 52 个写端点、101 个读端点，而审计埋点是 0。手工埋点的模式
在 30+ 个路由文件里必然漏埋，且新增路由时开发者很容易忘记补。
把范围收敛到一处声明，新增路由默认被覆盖，漏埋在结构上就不可能发生。

范围取舍：
- 写操作（POST/PUT/PATCH/DELETE）→ 全记。改动系统状态，必须留痕。
- PHI 读操作 → 按白名单记。HIPAA 要求的是"对 ePHI 的访问"留痕，
  不是每一次仪表盘点击；读全记会让审计表在阅片场景下失控增长。
- 数据出域（导出/DICOMweb/FHIR）→ 记为 export，监管重点关照对象。
- 高频切片拉取 → 显式排除，阅片拖动能产生上百次请求，记录它们没有合规价值。
"""
from typing import Optional

# ---------- 动作常量 ----------
READ = "read"
WRITE = "write"
UPDATE = "update"
DELETE = "delete"
EXPORT = "export"
LOGIN = "login"
LOGOUT = "logout"
CONFIG = "config"
INFER = "infer"
OVERRIDE = "override"

# ---------- 结果常量 ----------
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_DENIED = "denied"

# ---------- 严重级 ----------
SEV_INFO = "info"
SEV_WARN = "warn"
SEV_CRITICAL = "critical"


# ---------- PHI 资源路由表：(路径前缀, 资源类型) ----------
# 匹配时用「最长前缀优先」，避免 /api/case 抢先匹配 /api/case/longitudinal。
# 顺序在此表中无需排序，由 resolve() 统一按长度降序匹配。
PHI_ROUTE_TABLE: list[tuple[str, str]] = [
    ("/api/case/longitudinal", "longitudinal"),
    ("/api/case", "case"),
    ("/api/patient", "patient"),
    ("/api/analysis", "analysis"),
    ("/api/report", "report"),
    ("/api/annotation", "annotation"),
    ("/api/followup", "followup"),
    ("/api/intervention", "intervention"),
    ("/api/prognosis", "prognosis"),
    ("/api/review", "review"),
    ("/api/imaging", "imaging"),
    ("/api/active-learning", "case"),
    ("/api/quality", "case"),
    ("/api/case-log", "case"),
]

# ---------- 数据出域路由：一律记为 export ----------
# 数据离开系统边界是合规审查的重点，无论请求方法是什么都按 export 记。
EXPORT_ROUTE_TABLE: list[tuple[str, str]] = [
    ("/api/system/export", "export"),
    ("/api/dicomweb", "imaging"),
    ("/api/interop/fhir", "fhir"),
    ("/api/privacy", "phi"),
    ("/api/dataset", "dataset"),
]

# ---------- 认证类路由 ----------
AUTH_ROUTE_TABLE: list[tuple[str, str]] = [
    ("/api/auth/login", LOGIN),
    ("/api/auth/logout", LOGOUT),
    ("/api/auth/register", LOGIN),
]

# ---------- 配置类路由（模型/系统设置变更，影响全局，严重级提升）----------
CONFIG_ROUTE_TABLE: list[tuple[str, str]] = [
    ("/api/model", "model"),
    ("/api/model-monitor", "model"),
    ("/api/system/backup", "backup"),
    ("/api/backup", "backup"),
    ("/api/dictionary", "dictionary"),
]

# ---------- 显式排除：不产生审计事件 ----------
# 健康检查与指标采集不涉 PHI；切片拉取高频且无合规价值。
EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/health",
    "/api/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/audit",  # 审计接口自身的读操作不再自我审计，避免递归放大
)
EXEMPT_SUFFIXES: tuple[str, ...] = (
    "/imaging/slice",
)

# 严重级判定：删除/导出/配置变更属高风险，便于审计员快速聚焦
_HIGH_RISK_ACTIONS = frozenset({DELETE, EXPORT, CONFIG, OVERRIDE, LOGOUT})

# 以"病例/患者"为主键的资源类型：其 resource_id 可直接作为 patient_ref，
# 使合规调查能按患者一次性拉出全部访问史（"谁看过这个病人"）。
PATIENT_SCOPED_TYPES = frozenset({
    "case", "patient", "analysis", "report", "annotation",
    "followup", "intervention", "prognosis", "review", "longitudinal",
})


def _longest_match(path: str, table: list[tuple[str, str]]) -> Optional[tuple[str, str]]:
    """最长前缀匹配（表本身无需有序）"""
    best: Optional[tuple[str, str]] = None
    best_len = -1
    for prefix, value in table:
        if path.startswith(prefix) and len(prefix) > best_len:
            best = (prefix, value)
            best_len = len(prefix)
    return best


def is_exempt(path: str) -> bool:
    """该路径是否免于审计"""
    if any(path.startswith(p) for p in EXEMPT_PREFIXES):
        return True
    return any(path.endswith(s) for s in EXEMPT_SUFFIXES)


def resolve(path: str, method: str) -> Optional[dict]:
    """
    判定一个请求是否应审计，以及审计成什么。

    返回 None 表示不审计。否则返回：
        {"action": str, "resource_type": str, "severity": str}

    判定优先级（先特殊后一般）：
        export > auth > config > PHI读/任意写 > 不审计
    """
    if not path.startswith("/api/"):
        return None
    if is_exempt(path):
        return None

    m = method.upper()

    # 1) 数据出域：无论什么方法都算 export
    hit = _longest_match(path, EXPORT_ROUTE_TABLE)
    if hit:
        return {"action": EXPORT, "resource_type": hit[1], "severity": SEV_CRITICAL}

    # 2) 认证事件
    hit = _longest_match(path, AUTH_ROUTE_TABLE)
    if hit:
        return {"action": hit[1], "resource_type": "auth", "severity": SEV_INFO}

    # 3) 配置变更
    hit = _longest_match(path, CONFIG_ROUTE_TABLE)
    if hit:
        action = CONFIG if m != "GET" else READ
        return {
            "action": action,
            "resource_type": hit[1],
            "severity": SEV_CRITICAL if action == CONFIG else SEV_INFO,
        }

    # 4) PHI 资源的读操作
    hit = _longest_match(path, PHI_ROUTE_TABLE)
    if hit and m == "GET":
        return {"action": READ, "resource_type": hit[1], "severity": SEV_INFO}

    # 5) 任何写操作（含非 PHI 资源）：状态变更一律留痕
    if m in ("POST", "PUT", "PATCH", "DELETE"):
        rtype = hit[1] if hit else "system"
        action = {"POST": WRITE, "PUT": UPDATE, "PATCH": UPDATE, "DELETE": DELETE}[m]
        return {
            "action": action,
            "resource_type": rtype,
            "severity": SEV_CRITICAL if action in _HIGH_RISK_ACTIONS else SEV_WARN,
        }

    return None


def extract_resource_id(path: str, resource_type: str) -> str:
    """
    从路径中抽取资源标识。

    只取最后一段，且要求它看起来像标识（非空、非纯动作词）。
    取不到就留空——审计宁可少一个字段，也不要拿路径片段冒充主键误导调查。
    """
    seg = path.rstrip("/").split("/")[-1] if path else ""
    if not seg:
        return ""
    # 明显是动作而非标识的尾段（如 /case/list、/patient/cohort-trajectory）
    if seg in {"list", "detail", "overview", "stats", "create", "update", "delete"}:
        return ""
    if len(seg) > 80:
        return ""
    return seg


def outcome_from_status(status: int) -> str:
    """HTTP 状态码 → 审计结果"""
    if 200 <= status < 300:
        return OUTCOME_SUCCESS
    if status in (401, 403):
        # 鉴权拒绝是入侵检测的关键信号，单独归类，便于告警
        return OUTCOME_DENIED
    return OUTCOME_FAILURE


def severity_for_outcome(base_severity: str, outcome: str, status: int) -> str:
    """失败/被拒/服务端错误时提升严重级"""
    if status >= 500 or outcome in (OUTCOME_FAILURE, OUTCOME_DENIED):
        return SEV_WARN
    return base_severity
