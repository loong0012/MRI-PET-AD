"""
AD-Screen 阿尔茨海默病早期筛查系统后端
FastAPI 主入口
- CORS 中间件
- 数据库初始化 + 演示数据填充
- 路由注册
- 启动入口：uvicorn main:app --reload --port 8000
"""
import os
import sys

# 将项目根目录加入 sys.path（让后端能 import TransMF 训练代码 models.*）
# 注意：必须 append 到末尾，否则项目根的 models/ 会遮盖后端的 models/（ORM）
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.append(_PROJECT_ROOT)

import logging
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import (
    CORS_ORIGINS,
    APP_ENV,
    validate_security_config,
    GLOBAL_RATE_LIMIT,
    GLOBAL_RATE_WINDOW_SEC,
    AUDIT_ENABLED,
)
from database import init_db
from services.rate_limit import hit_rate_limit, client_ip
from services.logging_config import setup_logging
from services.data_init import init_demo_data
from services.batch_task import batch_task_manager
from services.request_context import gen_request_id, set_request_context, reset_request_context, get_trace_id
from services.observability import metrics_registry, observe_request
from services import audit_policy
from services.audit_service import record_safe
from routers import auth, dashboard, case, analysis, intervention, followup, model_config, report, report_template, imaging, analytics, notification, case_log, backup, warning, patient, quality, audit_board, cdss, dictionary, annotation, model_monitor, dataset, comment, knowledge, prognosis, review, privacy, active_learning, longitudinal, export, interop, dicomweb, audit

logger = logging.getLogger("ad_screen")

# 慢请求阈值（毫秒）：超过则 warning 级别记录，便于发现性能瓶颈
_SLOW_REQUEST_MS = 2000
# query 中需要脱敏的敏感参数（短期媒体票据也不写入日志，纵深防御）
_SENSITIVE_QS_KEYS = {"token"}
# 高频轮询/拉取类请求：阅片拖动切片时单病例可达上百次，降级 debug 避免审计日志被刷屏
_QUIET_PATH_SUFFIXES = ("/imaging/slice",)
# 长连接/长耗时但属正常交互的请求：豁免慢请求 warning（SSE 推理时长由业务进度体现）
_LONG_RUNNING_PATH_SUFFIXES = ("/run-stream",)


def _masked_target(request: Request) -> str:
    """记录用目标地址：保留 query 参数名，敏感参数值打码"""
    from urllib.parse import parse_qsl, urlencode
    path = request.url.path
    query = request.url.query
    if not query:
        return path
    pairs = parse_qsl(query, keep_blank_values=True)
    masked = [(k, "***" if k.lower() in _SENSITIVE_QS_KEYS else v) for k, v in pairs]
    return f"{path}?{urlencode(masked)}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时：日志初始化 + 安全校验 + 建表 + 填充演示数据"""
    setup_logging()
    # 生产环境安全配置校验：致命问题直接拒绝启动，避免弱密钥/裸奔上线
    security_issues = validate_security_config()
    for issue in security_issues:
        logger.error("安全配置校验失败：%s", issue)
    if APP_ENV == "production" and security_issues:
        raise RuntimeError("生产环境安全配置校验未通过，已拒绝启动：" + "；".join(security_issues))
    if APP_ENV != "production":
        logger.info("运行环境：%s（生产部署请设置 ADSCREEN_ENV=production）", APP_ENV)

    init_db()
    init_demo_data()
    # DICOM UID 根校验：生产环境必须注入本机构已申请的 OID 根，
    # 否则对外导出的 UID 可能与其它机构冲突（外部系统无法稳定引用检查）
    from services.dicomweb_service import using_placeholder_uid_root
    if using_placeholder_uid_root():
        logger.warning(
            "DICOM UID 根仍为占位值，生产部署前请通过 ADSCREEN_DICOM_UID_ROOT "
            "注入本机构已申请的 OID 根，避免 UID 冲突"
        )
    # 清理上次进程中断时遗留的批量分析任务（避免前端无限轮询已死的 task_id）
    batch_task_manager.cleanup_stale()

    # 后台预热深度学习依赖：torch/monai 首次 import 在本机可达 70s+，
    # 若不预热，首个访问 /api/model/status（模型配置页）的请求会超过前端 30s 超时。
    # daemon 线程执行，不阻塞服务启动与健康检查；仅填充 sys.modules 缓存。
    def _preload_dl_stack() -> None:
        import importlib
        for mod_name in ("torch", "monai"):
            try:
                importlib.import_module(mod_name)
                logger.info("深度学习依赖预热完成：%s", mod_name)
            except Exception:
                logger.debug("深度学习依赖预热失败（不影响模拟模式）：%s", mod_name, exc_info=True)

    threading.Thread(target=_preload_dl_stack, name="dl-preload", daemon=True).start()
    # 多 worker 部署：批量任务队列已持久化到 SQLite（batch_task_records），
    # 状态在进程间共享，不再需要限制为单 worker。此处仅记录部署形态便于排障。
    if APP_ENV == "production":
        try:
            workers = int(os.environ.get("UVICORN_WORKERS", "1"))
        except ValueError:
            workers = 1
        if workers > 1:
            logger.info(
                "多 worker 部署（workers=%d）：批量任务队列已持久化至 SQLite，任务状态跨进程共享", workers
            )
    logger.info("AD-Screen 后端启动完成")
    yield
    logger.info("AD-Screen 后端关闭")


app = FastAPI(
    title="AD-Screen 阿尔茨海默病筛查系统 API",
    version="1.0.0",
    description=(
        "## 脑影·明衰 — 阿尔茨海默病一体化 MRI/PET 脑成像智能诊断系统\n\n"
        "多模态 MRI+PET 影像融合 AI 筛查后端，基于 TransMF 双流 3D-CNN + 跨模态 Transformer + 对抗域适应模型。\n\n"
        "### 核心模块\n"
        "- **病例库**：DICOM/NIfTI 影像上传、病例检索、多中心队列管理\n"
        "- **AI 分析**：75 模型快照集成推理（5 seeds×5 folds×3 snapshots，fp16 + 温度校准 + 置信度量化）\n"
        "- **影像阅片**：三平面双模态联动、MIP 重建、WW/WL、量测工具\n"
        "- **纵向对比**：MCID 显著性检验、ATN 分期随访频率推荐\n"
        "- **临床决策**：NIA-AA A/T/N 框架、ARIA 风险、2026 指南建议\n"
        "- **数据导出**：病例列表 / 分析结果 CSV 导出\n"
        "- **运行审计**：登录趋势、病例操作分布、推理统计、Prometheus 指标\n\n"
        "### 鉴权\n"
        "- `Authorization: Bearer <token>` 常规会话票\n"
        "- `?token=<media_token>` 短期媒体票（EventSource / img 等场景）"
    ),
    contact={
        "name": "AD-Screen 研发团队",
        "email": "ad-screen@research.org",
    },
    license_info={
        "name": "Research Use Only",
        "url": "https://www.apache.org/licenses/LICENSE-2.0",
    },
    openapi_tags=[
        {"name": "认证", "description": "登录登出、媒体票据、用户管理"},
        {"name": "病例库", "description": "影像上传、病例检索、多中心队列"},
        {"name": "AI 分析", "description": "TransMF 集成推理、结果保存、历史版本"},
        {"name": "影像阅片", "description": "切片拉取、三平面、MIP、Grad-CAM"},
        {"name": "纵向对比", "description": "多期指标变化率、MCID 检验、随访推荐"},
        {"name": "临床决策", "description": "CDSS 规则引擎、干预方案、随访管理"},
        {"name": "报告", "description": "结构化报告生成与模板管理"},
        {"name": "数据导出", "description": "病例列表 / 分析结果 CSV 导出（admin）"},
        {"name": "数据备份", "description": "SQLite 一致性备份与恢复（admin）"},
        {"name": "运行审计看板", "description": "登录/操作/推理统计聚合（admin）"},
        {"name": "系统", "description": "健康检查、Prometheus 指标、模型状态"},
    ],
    lifespan=lifespan,
)

# ---------- CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 安全响应头中间件 ----------
# 医疗系统硬需求：防 XSS / 点击劫持 / MIME 嗅探 / Referer 泄漏
_CSP_POLICY = (
    "default-src 'self'; "
    # 图片：阅片 PNG 走 blob/data URI（Canvas 合成热力图），必须放开
    "img-src 'self' data: blob:; "
    # 样式：Element Plus 与 Vue 运行时会注入内联 <style>
    "style-src 'self' 'unsafe-inline'; "
    # 脚本：ECharts/Three.js 动态求值需要 eval；其余限定同源
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    # 字体与网络请求限定同源（WebSocket 用于 SSE 之外的实时通道）
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    # 禁止插件与任意嵌入（医疗系统无需 Flash/Java/嵌入对象）
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """为所有响应注入安全响应头（OWASP 医疗 Web 应用基线）"""
    response = await call_next(request)
    # 内容安全策略：白名单制加载，阻断未授权脚本与外链数据渗漏
    # 说明：Vue 运行时样式注入 + ECharts/Three.js 的表达式求值需要 inline/eval 豁免，
    # 已按最小可用集放开，其余（object-src / frame-ancestors）保持最严。
    response.headers["Content-Security-Policy"] = _CSP_POLICY
    # 防 MIME 嗅探（阻止浏览器把非声明类型当 HTML 执行）
    response.headers["X-Content-Type-Options"] = "nosniff"
    # 防点击劫持（本系统无需被 iframe 嵌入）
    response.headers["X-Frame-Options"] = "DENY"
    # 旧浏览器 XSS 过滤器（现代浏览器已内置，防御深度）
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # 控制 Referer 泄漏（跨域只带 origin，不带完整路径——避免病例 ID 泄漏到第三方）
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # 禁用敏感浏览器 API（本系统不需要摄像头/麦克风/定位）
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # 敏感 API 响应禁缓存（病例/分析数据含 PHI，防止代理或浏览器缓存泄漏）
    if request.url.path.startswith("/api/") and request.method == "GET":
        # 排除公开只读端点（健康检查/文档）
        if request.url.path not in ("/api/health", "/api/metrics", "/docs", "/openapi.json"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
    return response


# ---------- 请求日志与耗时中间件 ----------
@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """
    记录每个请求的方法/路径/状态码/耗时；慢请求与 5xx 升级为 warning/error。
    并通过 contextvars 注入 trace_id，使下游业务日志自动携带请求追踪 ID，
    便于跨函数/跨线程排障。响应头返回 X-Request-Id 供前端报障定位。
    """
    # 优先透传上游 X-Request-Id（如网关注入），否则生成 8 位短 ID
    req_id = request.headers.get("X-Request-Id") or gen_request_id()
    route_label = f"{request.method} {request.url.path}"
    token = set_request_context(req_id, route_label)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        status_code = response.status_code
        # 健康检查/静态资源/高频切片拉取降级为 debug，避免刷屏污染审计日志
        path = request.url.path
        target = _masked_target(request)
        is_quiet = (
            path in ("/", "/api", "/api/health", "/api/metrics")
            or path.startswith("/assets")
            or path.endswith(_QUIET_PATH_SUFFIXES)
        )
        is_long_running = path.endswith(_LONG_RUNNING_PATH_SUFFIXES)
        if is_quiet:
            logger.debug("%s %s -> %s %.0fms", request.method, target, status_code, elapsed_ms)
        elif status_code >= 500:
            logger.error("%s %s -> %s %.0fms", request.method, target, status_code, elapsed_ms)
        elif elapsed_ms > _SLOW_REQUEST_MS and not is_long_running:
            logger.warning("慢请求 %s %s -> %s %.0fms（阈值 %dms）",
                           request.method, target, status_code, elapsed_ms, _SLOW_REQUEST_MS)
        else:
            logger.info("%s %s -> %s %.0fms", request.method, target, status_code, elapsed_ms)
        # 暴露 trace_id 给前端，方便报障时一键 grep 全链路日志
        response.headers["X-Request-Id"] = req_id
        # Prometheus 指标采集（路径、方法、状态码、耗时）
        observe_request(path, request.method, status_code, elapsed_ms)
        return response
    except Exception:
        # 未被全局异常处理器兜住的异常（如中间件链内部错误）也记录耗时后抛出
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.exception("请求处理异常 %s %s（%.0fms）", request.method, _masked_target(request), elapsed_ms)
        # 异常路径也记录一次指标（5xx）
        observe_request(request.url.path, request.method, 500, elapsed_ms)
        raise
    finally:
        # 恢复上下文，避免协程上下文泄漏到下一个请求
        reset_request_context(token)


# ---------- 全局兜底限流中间件 ----------
# 端点级限流（captcha/register/login/export）只覆盖 6 个敏感端点，
# 其余 31 个业务路由此前无任何频率约束。此处做 IP 维度兜底：
# - 抵御扫描器/脚本对病例接口的批量抓取（PHI 渗漏风险）
# - 抵抗朴素 DoS（慢查询接口被打满拖垮整个实例）
# 注册在本节最后 → 处于中间件栈最内层，其 429 响应仍会被外层
# 安全头/CORS/访问日志中间件加工，保持响应格式一致。
_RATE_LIMIT_EXEMPT_PATHS = ("/", "/api", "/api/health", "/api/metrics", "/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def global_rate_limit_middleware(request: Request, call_next):
    """按 IP 做滑动窗口兜底限流；豁免健康检查、静态资源与阅片高频切片。"""
    if GLOBAL_RATE_LIMIT > 0:
        path = request.url.path
        exempt = (
            path in _RATE_LIMIT_EXEMPT_PATHS
            or path.startswith("/assets")
            # 阅片拖动切片时单病例可达上百次请求，端点语义为只读取图，豁免
            or path.endswith(_QUIET_PATH_SUFFIXES)
            # CORS 预检不携带业务数据，放行避免破坏跨域
            or request.method == "OPTIONS"
        )
        if not exempt:
            if hit_rate_limit(f"global:{client_ip(request)}", GLOBAL_RATE_LIMIT, GLOBAL_RATE_WINDOW_SEC):
                return JSONResponse(
                    status_code=429,
                    content={"code": 429, "message": "请求过于频繁，请稍后再试", "data": None},
                    headers={"Retry-After": str(GLOBAL_RATE_WINDOW_SEC)},
                )
    return await call_next(request)


# ---------- 合规审计中间件（注册在最后 → 处于中间件栈最外层）----------
# 放在最外层是为了连"被限流 429 / 被鉴权 401 拒绝"的尝试也记录下来——
# 恰恰是这些被拒请求，才是入侵检测与合规调查最关心的信号。
#
# 覆盖范围由 services/audit_policy.py 声明：写操作全记、PHI 读按白名单记、
# 数据出域记为 export、高频切片排除。新增路由自动被覆盖，杜绝手工埋点漏埋。
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """自动记录合规审计事件（旁路：任何失败都不得影响业务响应）"""
    if not AUDIT_ENABLED:
        return await call_next(request)

    path = request.url.path
    policy = audit_policy.resolve(path, request.method)
    if policy is None:
        return await call_next(request)

    # 操作者识别：仅解析 JWT 载荷，不查库（避免给每个请求增加一次查询开销）
    actor = ""
    actor_role = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        try:
            from services.auth import decode_token

            payload = decode_token(auth_header.split(" ", 1)[1].strip()) or {}
            actor = payload.get("username") or payload.get("sub") or ""
            actor_role = payload.get("roleName") or payload.get("role") or ""
        except Exception:
            # token 无效时保持匿名记录：被拒的访问尝试同样需要留痕
            actor = ""

    ip = request.client.host if request.client else ""
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        # 未捕获异常：同样记一条 failure，再交给上层异常处理器
        status_code = 500
        raise
    finally:
        outcome = audit_policy.outcome_from_status(status_code)
        severity = audit_policy.severity_for_outcome(policy["severity"], outcome, status_code)
        resource_id = audit_policy.extract_resource_id(path, policy["resource_type"])
        record_safe(
            action=policy["action"],
            resource_type=policy["resource_type"],
            resource_id=resource_id,
            patient_ref=resource_id
            if policy["resource_type"] in audit_policy.PATIENT_SCOPED_TYPES
            else "",
            outcome=outcome,
            severity=severity,
            detail={"method": request.method, "path": path, "status": status_code},
            actor=actor,
            actor_role=actor_role,
            actor_ip=ip,
            request_id=get_trace_id() or "",
        )


# ---------- 全局异常处理（统一响应格式 + 不向前端泄露堆栈） ----------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """401/403/404 等已知 HTTP 异常：透传状态码，统一为 {code, message, data} 结构"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """请求参数校验失败：422 + 首条错误信息（不回显完整 pydantic 结构，避免泄露内部模型）"""
    first_msg = "请求参数错误"
    if exc.errors():
        err = exc.errors()[0]
        loc = ".".join(str(x) for x in err.get("loc", []) if x not in ("body", "query", "path"))
        first_msg = f"参数 {loc} {err.get('msg', '校验失败')}".strip()
    logger.warning("参数校验失败 %s %s：%s", request.method, request.url.path, first_msg)
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": first_msg, "data": None},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    兜底 500：完整堆栈只写日志（含 error.log），对前端返回友好提示，
    避免 FastAPI 默认 debug 页向前端泄露文件路径/堆栈/依赖版本。
    """
    logger.exception("未处理异常 %s %s：%s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误，请稍后重试或联系管理员", "data": None},
    )

# ---------- 路由注册（统一 /api 前缀） ----------
api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(auth.sys_router)
api_router.include_router(dashboard.router)
api_router.include_router(case.router)
api_router.include_router(imaging.router)
api_router.include_router(annotation.router)
api_router.include_router(analysis.router)
api_router.include_router(intervention.router)
api_router.include_router(followup.router)
api_router.include_router(analytics.router)
api_router.include_router(notification.router)
api_router.include_router(case_log.router)
api_router.include_router(backup.router)
api_router.include_router(model_config.router)
api_router.include_router(model_config.sys_router)
api_router.include_router(model_monitor.router)
api_router.include_router(report.router)
api_router.include_router(report_template.router)
api_router.include_router(warning.router)
api_router.include_router(patient.router)
api_router.include_router(quality.router)
api_router.include_router(audit_board.router)
api_router.include_router(cdss.router)
api_router.include_router(dictionary.router)
api_router.include_router(dataset.router)
api_router.include_router(comment.router)
api_router.include_router(knowledge.router)
api_router.include_router(prognosis.router)
api_router.include_router(review.router)
api_router.include_router(privacy.router)
api_router.include_router(longitudinal.router)
api_router.include_router(active_learning.router)
api_router.include_router(export.router)
api_router.include_router(audit.router)
# 互操作层：FHIR R4 导出 + DICOMweb 只读检索
api_router.include_router(interop.router)
api_router.include_router(dicomweb.router)
app.include_router(api_router)


@app.get("/")
def health_check():
    """健康检查"""
    return {"status": "ok", "service": "AD-Screen API", "version": "1.0.0"}


@app.get("/api")
def api_root():
    """API 根路径（前端 Vite proxy 指向 /api）"""
    return {"code": 200, "message": "AD-Screen API is running", "data": None}


@app.get("/api/health")
def api_health():
    """
    深度健康检查：报告数据库、模型、磁盘等关键依赖状态。
    供 K8s/Docker liveness 与 readiness probe 使用。
    - liveness：进程存活即返回 200（与 / 同）
    - readiness：依赖检查全部 PASS 才返回 200，否则 503
    """
    import os
    import time
    checks = {}
    overall = True

    # 1. 数据库连通性
    t0 = time.perf_counter()
    try:
        from sqlalchemy import text as _sql_text
        from database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(_sql_text("SELECT 1"))
            checks["database"] = {
                "status": "pass", "latency_ms": round((time.perf_counter() - t0) * 1000, 1)
            }
        finally:
            db.close()
    except Exception as e:
        overall = False
        checks["database"] = {"status": "fail", "error": str(e)[:200]}

    # 2. 模型推理服务可用性（不强制加载，仅检查 checkpoint 存在）
    try:
        from services.model_inference import is_real_model_available, get_model_status
        status = get_model_status()
        checks["model"] = {
            "status": "pass" if is_real_model_available() else "degraded",
            "realAvailable": status.get("realModelAvailable", False),
            "loaded": status.get("ensembleLoaded", False),
            "device": status.get("device", "未初始化"),
            "modelCount": status.get("modelCount", 0),
        }
        # degraded 不影响整体健康（仍可用模拟推理）
    except Exception as e:
        checks["model"] = {"status": "degraded", "error": str(e)[:200]}

    # 3. 磁盘空间（日志/数据库可写）
    try:
        from config import LOG_DIR, DB_PATH
        log_free = _disk_free_mb(LOG_DIR)
        db_free = _disk_free_mb(os.path.dirname(DB_PATH))
        checks["disk"] = {
            "status": "pass" if log_free > 100 and db_free > 100 else "warn",
            "logFreeMB": log_free, "dbFreeMB": db_free,
        }
    except Exception as e:
        checks["disk"] = {"status": "warn", "error": str(e)[:200]}

    # 4. 批量任务队列状态
    try:
        from services.batch_task import batch_task_manager
        active = batch_task_manager.list_active() if hasattr(batch_task_manager, "list_active") else []
        checks["batchQueue"] = {
            "status": "pass", "activeTasks": len(active) if isinstance(active, (list, dict)) else 0,
        }
    except Exception:
        checks["batchQueue"] = {"status": "pass", "activeTasks": 0}

    return JSONResponse(
        status_code=200 if overall else 503,
        content={
            "status": "healthy" if overall else "unhealthy",
            "service": "AD-Screen API",
            "version": "1.0.0",
            "env": APP_ENV,
            "checks": checks,
        },
    )


@app.get("/api/metrics", response_class=PlainTextResponse)
def api_metrics():
    """
    Prometheus 文本格式指标端点（不依赖 prometheus_client）。
    暴露：
    - http_requests_total{method,path,status} 累计请求数
    - http_request_duration_ms_bucket{le} 直方图分桶
    - model_inference_total 累计推理次数
    - model_inference_duration_seconds 推理耗时
    - process_start_time_seconds 进程启动时间
    供 Prometheus 抓取 / Grafana 可视化。
    """
    return PlainTextResponse(
        metrics_registry.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


def _disk_free_mb(path: str) -> float:
    """返回指定路径所在磁盘剩余可用空间（MB）"""
    import shutil
    usage = shutil.disk_usage(path)
    return round(usage.free / (1024 * 1024), 1)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
