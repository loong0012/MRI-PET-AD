"""
第四轮：合规审计追踪测试
==================================================================
覆盖三件事：
1. 审计能真的记下来（自动埋点，而非靠开发者自觉）
2. 记录不可篡改（哈希链 + 数据库触发器）
3. 被篡改能被检测出来（这是审计系统的价值所在）

设计原则：沿用项目既有约定——直接调用服务层与中间件函数，
不依赖 TestClient / pytest-asyncio（异步部分用 asyncio.run 包一层）。

注意：篡改检测测试必须真实走"攻击者绕过应用直接改库"的路径，
即先摘掉触发器再改——只测应用层"没有 delete 方法"是没有意义的。
"""
import asyncio
import time

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

import database as database_module
from models.audit import AuditEvent

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _make_session(tmp_path, name="audit.db"):
    """构造带 append-only 触发器的独立库"""
    from services.audit_service import install_append_only_guard

    engine = create_engine(
        f"sqlite:///{tmp_path}/{name}", connect_args={"check_same_thread": False}
    )
    event.listen(engine, "connect", database_module._sqlite_pragmas)
    database_module.Base.metadata.create_all(bind=engine)
    install_append_only_guard(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ======================================================================
# 1. 审计范围策略
# ======================================================================

class TestAuditPolicy:
    """声明式策略表：决定什么该记、记成什么"""

    def test_write_operations_always_audited(self):
        """任何写操作都必须留痕（POST→write, DELETE→delete）"""
        from services import audit_policy as ap

        r = ap.resolve("/api/case/create", "POST")
        assert r is not None and r["action"] == ap.WRITE

        r = ap.resolve("/api/case/C001", "DELETE")
        assert r is not None and r["action"] == ap.DELETE
        assert r["severity"] == ap.SEV_CRITICAL  # 删除属高风险

    def test_phi_read_audited(self):
        """PHI 资源读操作要记"""
        from services import audit_policy as ap

        r = ap.resolve("/api/case/list", "GET")
        assert r is not None and r["action"] == ap.READ and r["resource_type"] == "case"

        r = ap.resolve("/api/patient/P001", "GET")
        assert r is not None and r["resource_type"] == "patient"

    def test_export_audited_regardless_of_method(self):
        """数据出域：无论 GET/POST 都记为 export，且严重级最高"""
        from services import audit_policy as ap

        for m in ("GET", "POST"):
            r = ap.resolve("/api/system/export/cases", m)
            assert r is not None and r["action"] == ap.EXPORT
            assert r["severity"] == ap.SEV_CRITICAL

        r = ap.resolve("/api/dicomweb/studies", "GET")
        assert r is not None and r["action"] == ap.EXPORT

    def test_longest_prefix_wins(self):
        """/api/case/longitudinal 不能被 /api/case 抢先匹配"""
        from services import audit_policy as ap

        r = ap.resolve("/api/case/longitudinal/list", "GET")
        assert r is not None and r["resource_type"] == "longitudinal"

    def test_exempt_paths_not_audited(self):
        """健康检查与高频切片不记，避免刷爆审计表"""
        from services import audit_policy as ap

        assert ap.resolve("/api/health", "GET") is None
        assert ap.resolve("/api/metrics", "GET") is None
        assert ap.resolve("/api/case/C001/imaging/slice", "GET") is None
        # 非 API 路径（静态资源）不记
        assert ap.resolve("/assets/logo.png", "GET") is None

    def test_non_phi_read_not_audited(self):
        """仪表盘等不含 PHI 的读操作不记（读全记会让审计表失控增长）"""
        from services import audit_policy as ap

        assert ap.resolve("/api/knowledge/list", "GET") is None
        assert ap.resolve("/api/dashboard/stats", "GET") is None

    def test_resource_id_extraction(self):
        """路径尾段是标识才抽取；是动作词则留空，不拿路径片段冒充主键"""
        from services import audit_policy as ap

        assert ap.extract_resource_id("/api/case/C001", "case") == "C001"
        assert ap.extract_resource_id("/api/case/list", "case") == ""

    def test_outcome_from_status(self):
        """401/403 归为 denied——这是入侵检测的关键信号"""
        from services import audit_policy as ap

        assert ap.outcome_from_status(200) == ap.OUTCOME_SUCCESS
        assert ap.outcome_from_status(401) == ap.OUTCOME_DENIED
        assert ap.outcome_from_status(403) == ap.OUTCOME_DENIED
        assert ap.outcome_from_status(500) == ap.OUTCOME_FAILURE


# ======================================================================
# 2. 写入与查询
# ======================================================================

class TestAuditRecord:

    def test_record_persists_all_compliance_fields(self, tmp_path):
        """合规要求的核心字段一个都不能少"""
        engine, Session = _make_session(tmp_path)
        db = Session()
        row = None
        from services.audit_service import record, GENESIS_HASH

        row = record(
            db, action="read", resource_type="case", resource_id="C001",
            patient_ref="C001", actor="dr_wang", actor_role="医师",
            actor_ip="10.0.0.7", request_id="abc123",
            detail={"method": "GET", "path": "/api/case/C001"},
        )
        assert row is not None
        assert row.prev_hash == GENESIS_HASH  # 链首
        assert row.hash and len(row.hash) == 64
        assert row.ts and row.ts_epoch > 0
        assert row.actor == "dr_wang"
        assert row.patient_ref == "C001"
        db.close()

    def test_chain_links_sequentially(self, tmp_path):
        """第二条记录的前驱哈希必须等于第一条的哈希"""
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record

        a = record(db, action="write", resource_type="case", actor="u1")
        b = record(db, action="read", resource_type="case", actor="u1")
        c = record(db, action="export", resource_type="export", actor="u1")
        assert b.prev_hash == a.hash
        assert c.prev_hash == b.hash
        db.close()

    def test_detail_dict_is_json_serialized(self, tmp_path):
        """detail 支持 dict 且序列化稳定（sort_keys 保证哈希可复现）"""
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record

        row = record(db, action="read", detail={"z": 1, "a": 2})
        import json

        assert json.loads(row.detail) == {"a": 2, "z": 1}
        db.close()

    def test_query_by_patient(self, tmp_path):
        """按患者检索全量访问史——合规调查的核心场景"""
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record, query_events

        record(db, action="read", resource_type="case", resource_id="C001", patient_ref="C001", actor="a")
        record(db, action="read", resource_type="case", resource_id="C002", patient_ref="C002", actor="b")
        record(db, action="write", resource_type="case", resource_id="C001", patient_ref="C001", actor="c")

        rows, total = query_events(db, patient_ref="C001")
        assert total == 2
        assert all(r.patient_ref == "C001" for r in rows)
        assert rows[0].action == "write"  # 时间倒序，最新在前
        db.close()

    def test_query_time_range_filter(self, tmp_path):
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record, query_events

        old = time.time() - 10 * 86400
        record(db, action="read", ts_epoch=old)
        record(db, action="read")
        rows, total = query_events(db, since=time.time() - 86400)
        assert total == 1
        db.close()


# ======================================================================
# 3. 防篡改（本轮核心）
# ======================================================================

class TestTamperEvidence:

    def test_clean_chain_verifies(self, tmp_path):
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record, verify_chain

        for i in range(5):
            record(db, action="read", resource_type="case", actor=f"u{i}")
        res = verify_chain(db)
        assert res["valid"] is True
        assert res["checked"] == 5
        assert res["broken_at"] == ""
        db.close()

    def test_empty_chain_is_valid(self, tmp_path):
        """空库不应误报为被篡改"""
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import verify_chain

        assert verify_chain(db)["valid"] is True
        db.close()

    def test_tampered_content_detected(self, tmp_path):
        """
        攻击者绕过应用直接改库：摘掉触发器 → 改一条记录 → 链必须断裂。
        这是"审计系统有用"的证明——只测"应用层没有 delete 方法"没意义。
        """
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record, verify_chain

        for i in range(4):
            record(db, action="read", resource_type="case", actor=f"u{i}")
        assert verify_chain(db)["valid"] is True

        # 模拟攻击者：连上库、摘掉保护、抹掉一条访问记录
        db.execute(text("DROP TRIGGER IF EXISTS trg_audit_events_no_update"))
        db.commit()
        db.execute(text("UPDATE audit_events SET actor='someone_else' WHERE id='AU00000002'"))
        db.commit()

        res = verify_chain(db)
        assert res["valid"] is False
        assert res["broken_at"] == "AU00000002"
        assert "篡改" in res["reason"]
        db.close()

    def test_deleted_record_breaks_chain(self, tmp_path):
        """删除中间一条记录：后续记录的前驱哈希对不上，同样能被检出"""
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record, verify_chain

        for i in range(4):
            record(db, action="read", resource_type="case", actor=f"u{i}")

        db.execute(text("DROP TRIGGER IF EXISTS trg_audit_events_no_delete"))
        db.commit()
        db.execute(text("DELETE FROM audit_events WHERE id='AU00000002'"))
        db.commit()

        res = verify_chain(db)
        assert res["valid"] is False
        assert res["broken_at"] == "AU00000003"  # AU3 的 prev_hash 还指向已消失的 AU2
        assert "前驱" in res["reason"]
        db.close()

    def test_append_only_trigger_blocks_update(self, tmp_path):
        """数据库级保护：即使绕过应用，UPDATE 也被拒绝"""
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record
        from sqlalchemy.exc import IntegrityError, OperationalError

        record(db, action="read", resource_type="case", actor="u1")
        with pytest.raises((IntegrityError, OperationalError)):
            db.execute(text("UPDATE audit_events SET actor='hacker' WHERE id='AU00000001'"))
            db.commit()
        db.rollback()
        db.close()

    def test_append_only_trigger_blocks_delete(self, tmp_path):
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record
        from sqlalchemy.exc import IntegrityError, OperationalError

        record(db, action="read", resource_type="case", actor="u1")
        with pytest.raises((IntegrityError, OperationalError)):
            db.execute(text("DELETE FROM audit_events"))
            db.commit()
        db.rollback()
        db.close()


# ======================================================================
# 4. 保留策略
# ======================================================================

class TestRetention:

    def test_purge_dry_run_by_default(self, tmp_path):
        """默认只统计不删除——自动静默删除本身可被用来掩盖痕迹"""
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record, purge_expired

        record(db, action="read", ts_epoch=time.time() - 4000 * 86400)  # 超 6 年
        res = purge_expired(db, dry_run=True)
        assert res["expired"] == 1
        assert res["purged"] == 0
        assert db.query(AuditEvent).count() == 1
        db.close()

    def test_purge_requires_explicit_confirm(self, tmp_path):
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record, purge_expired, verify_chain

        record(db, action="read", ts_epoch=time.time() - 4000 * 86400)
        record(db, action="read")
        res = purge_expired(db, retention_days=2190, dry_run=False)
        assert res["purged"] == 1
        assert db.query(AuditEvent).count() == 1  # 保留未超期的
        # 删掉链首后必须重封整条链，否则校验会误报"被篡改"
        assert res["resealed"] == 1
        assert verify_chain(db)["valid"] is True
        db.close()

    def test_zero_retention_means_keep_forever(self, tmp_path):
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record, purge_expired

        record(db, action="read", ts_epoch=time.time() - 99999 * 86400)
        res = purge_expired(db, retention_days=0, dry_run=False)
        assert res["purged"] == 0
        assert res["retentionDays"] == 0
        db.close()


# ======================================================================
# 5. 自动审计中间件
# ======================================================================

class TestAuditMiddleware:
    """中间件自动埋点：这是杜绝"新增路由忘记补埋点"的结构性保障"""

    @staticmethod
    def _request(method, path, headers=None, client=("10.0.0.1", 1234)):
        from starlette.requests import Request

        raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": raw,
            "client": client,
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
        return Request(scope)

    def _run(self, tmp_path, monkeypatch, method, path, headers=None, status=200):
        """驱动一次中间件调用，返回库中的审计记录"""
        engine, Session = _make_session(tmp_path, "mw.db")
        monkeypatch.setattr("services.audit_service.SessionLocal", Session)
        import main

        async def scenario():
            req = self._request(method, path, headers)
            captured = {}

            async def call_next(_req):
                from fastapi.responses import JSONResponse

                captured["path"] = _req.url.path
                return JSONResponse({"code": status, "message": "x", "data": None},
                                    status_code=status)

            return await main.audit_middleware(req, call_next)

        asyncio.run(scenario())
        db = Session()
        rows = db.query(AuditEvent).all()
        db.close()
        return rows

    def test_write_request_is_audited(self, tmp_path, monkeypatch):
        rows = self._run(tmp_path, monkeypatch, "POST", "/api/case/create")
        assert len(rows) == 1
        assert rows[0].action == "write"
        assert rows[0].resource_type == "case"

    def test_unauthenticated_access_recorded_as_denied(self, tmp_path, monkeypatch):
        """401 也要记，而且是 denied——被拒的访问尝试才是调查重点"""
        rows = self._run(tmp_path, monkeypatch, "GET", "/api/case/list", status=401)
        assert len(rows) == 1
        assert rows[0].outcome == "denied"
        assert rows[0].actor == ""  # 匿名

    def test_actor_extracted_from_jwt(self, tmp_path, monkeypatch):
        """从 JWT 解析操作者，不查库（避免每请求多一次查询）"""
        from services.auth import create_access_token

        tok = create_access_token({"sub": "dr_li", "username": "dr_li", "role": "doctor",
                                   "roleName": "医师", "id": 1, "ver": 0})
        rows = self._run(tmp_path, monkeypatch, "GET", "/api/case/C009",
                         headers={"Authorization": f"Bearer {tok}"})
        assert rows[0].actor == "dr_li"
        assert rows[0].actor_role == "医师"
        assert rows[0].actor_ip == "10.0.0.1"
        assert rows[0].patient_ref == "C009"

    def test_invalid_token_still_audited_as_anonymous(self, tmp_path, monkeypatch):
        rows = self._run(tmp_path, monkeypatch, "GET", "/api/case/C009",
                         headers={"Authorization": "Bearer garbage.token.here"})
        assert len(rows) == 1
        assert rows[0].actor == ""  # 无效 token → 匿名，但照样留痕

    def test_exempt_path_not_audited(self, tmp_path, monkeypatch):
        assert self._run(tmp_path, monkeypatch, "GET", "/api/health") == []

    def test_audit_failure_does_not_break_response(self, tmp_path, monkeypatch):
        """旁路原则：审计写失败绝不能导致业务请求失败"""
        engine, Session = _make_session(tmp_path, "fail.db")
        monkeypatch.setattr("services.audit_service.SessionLocal", lambda: (_ for _ in ()).throw(
            RuntimeError("db down")))
        import main
        from fastapi.responses import JSONResponse

        async def scenario():
            async def call_next(_r):
                return JSONResponse({"code": 200, "message": "ok", "data": None})

            return await main.audit_middleware(
                self._request("POST", "/api/case/create"), call_next)

        resp = asyncio.run(scenario())
        assert resp.status_code == 200  # 业务不受影响


# ======================================================================
# 6. FHIR AuditEvent 映射
# ======================================================================

class TestFhirAuditEvent:

    def test_maps_to_standard_resource(self, tmp_path):
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record
        from routers.interop import build_audit_event

        row = record(db, action="export", resource_type="case", resource_id="C001",
                     patient_ref="C001", actor="dr_wang", actor_role="医师",
                     actor_ip="10.0.0.5", outcome="success", request_id="r1")
        ae = build_audit_event(row)

        assert ae["resourceType"] == "AuditEvent"
        assert ae["type"]["code"] == "110100"  # DCM Application Activity
        assert ae["action"] == "E"             # Export
        assert ae["outcome"] == "0"            # RFC 3881 成功
        assert ae["agent"][0]["who"]["display"] == "dr_wang"
        # 患者维度单独挂 entity，SIEM 可按患者聚合告警
        assert any(e["type"].get("code") == "1" for e in ae["entity"])
        db.close()

    def test_denied_maps_to_major_failure(self, tmp_path):
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record
        from routers.interop import build_audit_event

        row = record(db, action="read", resource_type="case", outcome="denied")
        assert build_audit_event(row)["outcome"] == "12"
        db.close()

    def test_unknown_action_does_not_fake_a_code(self, tmp_path):
        """
        本地动作无标准编码时留空，绝不套用语义不符的标准码。
        伪造标准码会让下游系统误判事件性质——比不填更糟。
        """
        engine, Session = _make_session(tmp_path)
        db = Session()
        from services.audit_service import record
        from routers.interop import build_audit_event

        row = record(db, action="some_local_action", resource_type="case")
        ae = build_audit_event(row)
        assert ae["action"] == ""
        assert ae["subtype"][0]["code"] == "some_local_action"
        db.close()
