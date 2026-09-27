"""
安全与性能加固测试
------------------------------------------------------------------
覆盖本轮新增的三项优化：
1. _migrate_indexes：核心表索引幂等创建（重复执行不报错、索引真实存在）
2. 安全响应头中间件：X-Content-Type-Options / X-Frame-Options 等注入
3. 限流依赖：rate_limit 工厂返回可调用依赖
"""
from sqlalchemy import create_engine, inspect, text


class TestMigrateIndexes:
    """case_records 核心表索引迁移测试"""

    def test_indexes_created_on_real_engine(self):
        """在独立内存引擎上执行 _migrate_indexes，验证索引真实创建"""
        import database
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        # 建最小表结构（只需被索引的列存在）
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE case_records (id VARCHAR(30) PRIMARY KEY, "
                "status VARCHAR(20), risk_level VARCHAR(20), cohort VARCHAR(60), "
                "modality VARCHAR(20), is_deleted BOOLEAN, create_time VARCHAR(30))"
            ))
        # 临时替换全局 engine 执行迁移
        original = database.engine
        database.engine = engine
        try:
            database._migrate_indexes()
        finally:
            database.engine = original
        # 校验索引存在
        idx_names = {i["name"] for i in inspect(engine).get_indexes("case_records")}
        assert "idx_case_status" in idx_names
        assert "idx_case_risk_level" in idx_names
        assert "idx_case_cohort" in idx_names
        assert "idx_case_modality" in idx_names
        assert "idx_case_deleted_time" in idx_names

    def test_idempotent_double_run(self):
        """索引迁移幂等：连续执行两次不报错（IF NOT EXISTS）"""
        import database
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE case_records (id VARCHAR(30) PRIMARY KEY, "
                "status VARCHAR(20), risk_level VARCHAR(20), cohort VARCHAR(60), "
                "modality VARCHAR(20), is_deleted BOOLEAN, create_time VARCHAR(30))"
            ))
        original = database.engine
        database.engine = engine
        try:
            database._migrate_indexes()
            database._migrate_indexes()  # 第二次执行不应抛错
        finally:
            database.engine = original
        # 仍只有 5 个索引（未重复创建）
        assert len(inspect(engine).get_indexes("case_records")) == 5

    def test_composite_index_columns(self):
        """复合索引 (is_deleted, create_time) 列序正确（列表查询主力路径）"""
        import database
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE case_records (id VARCHAR(30) PRIMARY KEY, "
                "status VARCHAR(20), risk_level VARCHAR(20), cohort VARCHAR(60), "
                "modality VARCHAR(20), is_deleted BOOLEAN, create_time VARCHAR(30))"
            ))
        original = database.engine
        database.engine = engine
        try:
            database._migrate_indexes()
        finally:
            database.engine = original
        composite = [i for i in inspect(engine).get_indexes("case_records")
                     if i["name"] == "idx_case_deleted_time"]
        assert len(composite) == 1
        assert composite[0]["column_names"] == ["is_deleted", "create_time"]


class TestSecurityHeaders:
    """安全响应头中间件单元测试（直接调用中间件函数，不依赖 httpx2/TestClient/pytest-asyncio）"""

    def test_baseline_headers_injected(self):
        """基础安全头（XSS/点击劫持/MIME 嗅探）注入到响应"""
        import asyncio
        from main import security_headers_middleware
        from fastapi import Response
        from unittest.mock import MagicMock

        async def fake_call_next(_request):
            return Response(content="ok", status_code=200)

        request = MagicMock()
        request.url.path = "/api/case/list"
        request.method = "GET"
        response = asyncio.run(security_headers_middleware(request, fake_call_next))

        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-XSS-Protection"] == "1; mode=block"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in response.headers["Permissions-Policy"]

    def test_cache_control_on_sensitive_get(self):
        """敏感 GET API 禁缓存（PHI 防代理/浏览器缓存泄漏）"""
        import asyncio
        from main import security_headers_middleware
        from fastapi import Response
        from unittest.mock import MagicMock

        async def fake_call_next(_request):
            return Response(content="{}", status_code=200)

        request = MagicMock()
        request.url.path = "/api/case/AD260001"
        request.method = "GET"
        response = asyncio.run(security_headers_middleware(request, fake_call_next))
        assert "no-store" in response.headers["Cache-Control"]
        assert response.headers["Pragma"] == "no-cache"

    def test_cache_control_exempt_on_health(self):
        """健康检查/指标等公开端点豁免禁缓存（供监控系统轮询）"""
        import asyncio
        from main import security_headers_middleware
        from fastapi import Response
        from unittest.mock import MagicMock

        async def fake_call_next(_request):
            return Response(content="{}", status_code=200)

        request = MagicMock()
        request.url.path = "/api/health"
        request.method = "GET"
        response = asyncio.run(security_headers_middleware(request, fake_call_next))
        assert "Cache-Control" not in response.headers


class TestRateLimitDependency:
    """限流依赖工厂测试"""

    def test_rate_limit_returns_callable(self):
        """rate_limit 工厂返回 FastAPI 依赖可调用对象"""
        from services.rate_limit import rate_limit
        dep = rate_limit("test_action", 5, 60)
        assert callable(dep)

    def test_hit_rate_limit_window(self):
        """滑动窗口限流：超限返回 True，重置后放行"""
        from services.rate_limit import hit_rate_limit, reset_rate_limit
        reset_rate_limit()
        # 前 3 次放行
        for _ in range(3):
            assert hit_rate_limit("t:1.1.1.1", 3, 60) is False
        # 第 4 次超限
        assert hit_rate_limit("t:1.1.1.1", 3, 60) is True
        # 重置后放行
        reset_rate_limit("t:1.1.1.1")
        assert hit_rate_limit("t:1.1.1.1", 3, 60) is False
        reset_rate_limit()
