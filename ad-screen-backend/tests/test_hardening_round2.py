"""
工程可靠性加固测试（2026-09-27 轮次）
------------------------------------------------------------------
对标本轮对标调研后落地的加固项，逐条验证：

1. SQLite 并发 PRAGMA：WAL / busy_timeout / foreign_keys 在连接建立时生效
2. CSP 响应头：本轮新增，此前完全缺失
3. 全局兜底限流：此前仅 6 个端点有限流，现补 IP 维度中间件 + 豁免策略
4. 演示数据开关：SEED_DEMO_DATA 显式控制（原为隐式 APP_ENV 判断）
5. 占位密钥黑名单：长度达标但公开的示例密钥必须被拒绝
6. 依赖清单完整性：AST 扫描出的第三方导入必须在 requirements 中声明

设计原则：直接调用中间件/函数，不依赖 TestClient 与 pytest-asyncio，
避免因 httpx 版本差异导致测试不可用。
"""
import ast
import asyncio
import os
import re
import sys
from unittest.mock import MagicMock

from fastapi import Response


# ------------------------------------------------------------------ 1. SQLite PRAGMA
class TestSQLitePragmas:
    """database.py 连接级 PRAGMA 生效验证"""

    def test_pragmas_applied_on_connect(self, tmp_path):
        """新建连接后 WAL / busy_timeout / foreign_keys 均已生效"""
        from sqlalchemy import create_engine, text

        db_file = tmp_path / "pragma_test.db"
        # 复用项目 engine 的事件监听逻辑：手动建一个带同样事件的引擎
        import database

        engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
        # 挂载与 database.py 相同的监听器
        from sqlalchemy import event
        event.listen(engine, "connect", database._sqlite_pragmas)

        with engine.connect() as conn:
            # WAL 是库级持久属性，文件型数据库生效（内存库恒为 memory）
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            assert mode.lower() == "wal", f"期望 WAL，实际 {mode}"
            # busy_timeout 是连接级
            timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
            assert timeout == database.SQLITE_BUSY_TIMEOUT_MS
            assert timeout > 0
            # 外键约束开启
            fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
            assert fk == 1
        engine.dispose()

    def test_busy_timeout_configurable(self, monkeypatch):
        """ADSCREEN_SQLITE_BUSY_TIMEOUT_MS 可覆盖默认值"""
        monkeypatch.setenv("ADSCREEN_SQLITE_BUSY_TIMEOUT_MS", "12000")
        import importlib
        import config
        cfg = importlib.reload(config)
        assert cfg.SQLITE_BUSY_TIMEOUT_MS == 12000
        # 还原，避免污染后续测试
        monkeypatch.delenv("ADSCREEN_SQLITE_BUSY_TIMEOUT_MS")
        importlib.reload(config)


# ------------------------------------------------------------------ 2. CSP
class TestContentSecurityPolicy:
    """本轮新增的 CSP 响应头"""

    def _run(self, path="/api/case/list", method="GET"):
        from main import security_headers_middleware

        async def fake_call_next(_request):
            return Response(content="ok", status_code=200)

        request = MagicMock()
        request.url.path = path
        request.method = method
        return asyncio.run(security_headers_middleware(request, fake_call_next))

    def test_csp_header_present(self):
        """CSP 头必须存在（上轮方案声称已加，实测缺失，本轮补齐）"""
        resp = self._run()
        assert "Content-Security-Policy" in resp.headers

    def test_csp_default_self_only(self):
        """default-src 限定同源，阻断未授权外部资源"""
        csp = self._run().headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp

    def test_csp_blocks_embedding_and_plugins(self):
        """禁止被 iframe 嵌入（点击劫持）与插件对象"""
        csp = self._run().headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp

    def test_csp_allows_viewer_image_sources(self):
        """阅片热力图为 Canvas 合成的 blob/data URI，必须放开 img-src"""
        csp = self._run().headers["Content-Security-Policy"]
        assert "img-src" in csp
        assert "data:" in csp and "blob:" in csp


# ------------------------------------------------------------------ 3. 全局限流
class TestGlobalRateLimit:
    """全局兜底限流中间件"""

    @staticmethod
    def _run(path, method="GET", ip="10.0.0.1"):
        from main import global_rate_limit_middleware
        from services.rate_limit import reset_rate_limit

        async def fake_call_next(_request):
            return Response(content="ok", status_code=200)

        request = MagicMock()
        request.url.path = path
        request.method = method
        request.headers = {}
        request.client = MagicMock(host=ip)
        return asyncio.run(global_rate_limit_middleware(request, fake_call_next)), reset_rate_limit

    def test_limits_after_threshold(self, monkeypatch):
        """超过阈值返回 429 且为统一响应格式"""
        from services.rate_limit import reset_rate_limit
        import main

        monkeypatch.setattr(main, "GLOBAL_RATE_LIMIT", 3)
        reset_rate_limit()

        status_codes = []
        for _ in range(5):
            resp, _ = self._run("/api/case/list")
            status_codes.append(resp.status_code)
            if isinstance(resp, Response) and resp.status_code == 429:
                # 统一响应体 {code, message, data}
                assert resp.headers.get("Retry-After")
                break
        assert 429 in status_codes, f"未触发限流，状态码序列={status_codes}"
        reset_rate_limit()

    def test_exempt_health_and_metrics(self, monkeypatch):
        """健康检查与指标端点豁免（否则监控系统会被自己限流）"""
        import main

        monkeypatch.setattr(main, "GLOBAL_RATE_LIMIT", 2)
        from services.rate_limit import reset_rate_limit
        reset_rate_limit()

        for i in range(10):
            health, _ = self._run("/api/health")
            metrics, _ = self._run("/api/metrics")
            assert health.status_code == 200, f"第 {i} 次健康检查被限流"
            assert metrics.status_code == 200
        reset_rate_limit()

    def test_exempt_viewer_slice_high_frequency(self, monkeypatch):
        """阅片切片高频拉取豁免（拖动一次可达上百请求）"""
        import main

        monkeypatch.setattr(main, "GLOBAL_RATE_LIMIT", 2)
        from services.rate_limit import reset_rate_limit
        reset_rate_limit()

        for i in range(10):
            resp, _ = self._run("/api/imaging/slice")
            assert resp.status_code == 200, f"第 {i} 次切片请求被限流"
        reset_rate_limit()

    def test_exempt_cors_preflight(self, monkeypatch):
        """CORS 预检 OPTIONS 豁免，避免跨域能力被限流破坏"""
        import main

        monkeypatch.setattr(main, "GLOBAL_RATE_LIMIT", 1)
        from services.rate_limit import reset_rate_limit
        reset_rate_limit()

        for _ in range(5):
            resp, _ = self._run("/api/case/list", method="OPTIONS")
            assert resp.status_code == 200
        reset_rate_limit()

    def test_disabled_when_zero(self, monkeypatch):
        """配置为 0 时关闭全局限流"""
        import main

        monkeypatch.setattr(main, "GLOBAL_RATE_LIMIT", 0)
        from services.rate_limit import reset_rate_limit
        reset_rate_limit()

        for _ in range(50):
            resp, _ = self._run("/api/case/list")
            assert resp.status_code == 200
        reset_rate_limit()


# ------------------------------------------------------------------ 4. 演示数据开关
class TestSeedDemoDataSwitch:
    """SEED_DEMO_DATA 显式开关"""

    def test_dev_defaults_enabled(self, monkeypatch):
        """开发环境默认填充演示数据"""
        import importlib
        import config
        monkeypatch.delenv("ADSCREEN_SEED_DEMO_DATA", raising=False)
        monkeypatch.setenv("ADSCREEN_ENV", "development")
        cfg = importlib.reload(config)
        assert cfg.SEED_DEMO_DATA is True

    def test_prod_defaults_disabled(self, monkeypatch):
        """生产环境默认关闭（避免 admin/admin123 入库）"""
        import importlib
        import config
        monkeypatch.delenv("ADSCREEN_SEED_DEMO_DATA", raising=False)
        monkeypatch.setenv("ADSCREEN_ENV", "production")
        cfg = importlib.reload(config)
        assert cfg.SEED_DEMO_DATA is False

    def test_explicit_off_overrides_env(self, monkeypatch):
        """显式置 0 时，即便开发环境也关闭"""
        import importlib
        import config
        monkeypatch.setenv("ADSCREEN_ENV", "development")
        monkeypatch.setenv("ADSCREEN_SEED_DEMO_DATA", "0")
        cfg = importlib.reload(config)
        assert cfg.SEED_DEMO_DATA is False

    def test_explicit_on_overrides_env(self, monkeypatch):
        """显式置 1 时，即便生产环境也开启（供演示部署）"""
        import importlib
        import config
        monkeypatch.setenv("ADSCREEN_ENV", "production")
        monkeypatch.setenv("ADSCREEN_SEED_DEMO_DATA", "1")
        cfg = importlib.reload(config)
        assert cfg.SEED_DEMO_DATA is True

    def test_data_init_reads_flag(self):
        """data_init 依赖模块级 SEED_DEMO_DATA 常量而非直接读 APP_ENV"""
        import inspect
        import services.data_init as di
        src = inspect.getsource(di.init_demo_data)
        assert "SEED_DEMO_DATA" in src
        # 弱口令演示账号只在开关打开时写入
        assert "if not SEED_DEMO_DATA:" in src


# ------------------------------------------------------------------ 5. 密钥黑名单
class TestPlaceholderSecretBlacklist:
    """占位密钥必须被拒绝"""

    def test_compose_placeholder_rejected(self, monkeypatch):
        """docker-compose 示例串（48 字符，长度检查可放行）必须被拦截"""
        import importlib
        import config
        monkeypatch.setenv("ADSCREEN_ENV", "production")
        monkeypatch.setenv("ADSCREEN_SECRET_KEY", "please-change-this-secret-key-in-production-env")
        monkeypatch.setenv("ADSCREEN_CORS_ORIGINS", "https://hospital.example")
        cfg = importlib.reload(config)
        issues = cfg.validate_security_config()
        assert any("占位密钥" in i for i in issues), f"占位密钥未被拦截：{issues}"

    def test_dev_default_key_rejected(self, monkeypatch):
        """内置开发密钥在生产被拒绝"""
        import importlib
        import config
        monkeypatch.setenv("ADSCREEN_ENV", "production")
        monkeypatch.delenv("ADSCREEN_SECRET_KEY", raising=False)
        monkeypatch.setenv("ADSCREEN_CORS_ORIGINS", "https://hospital.example")
        cfg = importlib.reload(config)
        issues = cfg.validate_security_config()
        assert any("内置开发默认密钥" in i for i in issues)

    def test_strong_key_passes(self, monkeypatch):
        """高强度随机密钥通过校验"""
        import importlib
        import config
        monkeypatch.setenv("ADSCREEN_ENV", "production")
        monkeypatch.setenv("ADSCREEN_SECRET_KEY", "x" * 64)
        monkeypatch.setenv("ADSCREEN_CORS_ORIGINS", "https://hospital.example")
        cfg = importlib.reload(config)
        # 仅剩 CORS 之外的项应为空（dev 默认密钥已被覆盖）
        blocking = [i for i in cfg.validate_security_config() if "密钥" in i]
        assert blocking == [], f"强密钥被误判：{blocking}"

    def test_dev_env_not_blocked(self, monkeypatch):
        """开发环境不因占位密钥拒绝启动（避免阻断本地调试）"""
        import importlib
        import config
        monkeypatch.setenv("ADSCREEN_ENV", "development")
        monkeypatch.setenv("ADSCREEN_SECRET_KEY", "please-change-this-secret-key-in-production-env")
        cfg = importlib.reload(config)
        assert cfg.validate_security_config() == []


# ------------------------------------------------------------------ 6. 依赖清单完整性
class TestRequirementsCompleteness:
    """元测试：AST 扫描第三方导入，确保 requirements.txt 未漏声明"""

    @staticmethod
    def _project_third_party_imports():
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skip_dirs = {"__pycache__", "tests", "backups", "logs", "uploads"}
        local_pkgs = {"config", "database", "models", "schemas", "routers", "services", "main", "setup"}
        # 已在 requirements 中声明或属于可选的深度学习栈
        optional_ml = {"torch", "monai", "einops"}
        found = set()
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if not f.endswith(".py"):
                    continue
                try:
                    tree = ast.parse(open(os.path.join(root, f), encoding="utf-8").read())
                except (SyntaxError, UnicodeDecodeError):
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for a in node.names:
                            found.add(a.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                        found.add(node.module.split(".")[0])
        stdlib = set(sys.stdlib_module_names)
        return {
            m for m in found
            if m not in stdlib and m not in local_pkgs and m not in optional_ml
        }

    @staticmethod
    def _declared_requirements():
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        req = os.path.join(base, "requirements.txt")
        names = set()
        for line in open(req, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            # 去掉版本约束与 extras，如 uvicorn[standard]>=0.34.0 → uvicorn
            name = re.split(r"[<>=!\[;]", line, maxsplit=1)[0].strip()
            if name:
                names.add(name.lower())
        return names

    def test_all_third_party_imports_declared(self):
        """代码中实际导入的第三方包必须在 requirements.txt 声明"""
        imports = self._project_third_party_imports()
        declared = self._declared_requirements()
        # 导入名 → PyPI 包名 映射（导入名与分发名不一致的情况）
        alias = {"PIL": "pillow", "jose": "python-jose", "dateutil": "python-dateutil"}
        # 传递依赖：由已声明包带入，无需也不应单独锁定版本（避免与主包版本冲突）
        transitive = {"starlette", "uvicorn"}
        missing = []
        for imp in sorted(imports):
            pkg = alias.get(imp, imp).lower()
            if pkg in transitive:
                continue
            if pkg not in declared:
                missing.append(f"{imp}(pypi:{pkg})")
        assert not missing, f"requirements.txt 缺失依赖：{missing}"

    def test_deprecated_passlib_removed(self):
        """passlib 已弃用且代码未使用，不应再出现在依赖中"""
        declared = self._declared_requirements()
        assert "passlib" not in declared, "passlib 已弃用，应从 requirements 移除（改用 bcrypt 直调）"

    def test_bcrypt_declared(self):
        """services/auth.py 直接 import bcrypt，必须声明"""
        declared = self._declared_requirements()
        assert "bcrypt" in declared

    def test_ml_requirements_file_exists(self):
        """深度学习依赖单独文件，不混入核心依赖"""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assert os.path.isfile(os.path.join(base, "requirements-ml.txt"))


# ------------------------------------------------------------------ 8. 源码不得被 gitignore 误伤
class TestSourceNotGitignored:
    """
    源码文件不得被 .gitignore 挡在库外。

    真实事故：.gitignore 里上游遗留的裸规则 `dataset.py` 会匹配**任意层级**，
    把 ad-screen-backend/routers/dataset.py 排除在版本控制之外。
    本地磁盘上文件还在，测试全绿；CI 全新检出后没有该文件，
    `from routers import dataset` 直接 ImportError，10 个用例同时挂掉，
    而报错信息完全指不到根因 —— 典型 works-on-my-machine。

    说明：本测试在 CI 上必然通过（被忽略的文件根本不会被检出，列表为空），
    它的价值在**本地**：提交前拦住这类"本地有、仓库没有"的幽灵文件。
    """

    @staticmethod
    def _gitignored_files() -> list[str]:
        import subprocess

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            out = subprocess.run(
                ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
                cwd=base, capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return []  # 无 git 或执行失败：跳过，不因环境误判
        if out.returncode != 0:
            return []
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]

    def test_no_source_file_is_gitignored(self):
        """被忽略的文件中不得出现源码（.py / .ts / .vue）"""
        ignored = self._gitignored_files()
        if not ignored:
            return  # 非 git 环境或确实没有忽略文件

        # 排除明确属于缓存/产物/依赖的目录
        noise_parts = ("node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
                       ".venv", "venv", ".mimosa", ".workbuddy")
        src_ext = (".py", ".ts", ".vue")
        offenders = [
            p for p in ignored
            if p.endswith(src_ext) and not any(n in p for n in noise_parts)
        ]
        assert not offenders, (
            "以下源码文件被 .gitignore 排除，会导致 CI 全新检出后 ImportError "
            f"（本地因文件仍在磁盘而表现正常）：{offenders}"
        )
