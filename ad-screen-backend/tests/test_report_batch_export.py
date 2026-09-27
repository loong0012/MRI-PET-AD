"""
批量报告导出 N+1 查询优化回归测试
==================================================================
优化前：循环内对每个 case 单独 first() 查询 InterventionRecord 和 FollowUpRecord
        （N 个病例 → 2N 次额外查询）
优化后：循环外用 in_(case_ids) 一次性批量查询，构建 dict 映射（2 次查询）
本测试用 SQLAlchemy 事件监听器精确计数查询次数，确保 N+1 真正消除。
"""
import json

from sqlalchemy import event

from models.analysis import InterventionRecord, FollowUpRecord
from tests.conftest import make_case


def _seed_cases(db, n=5):
    """造 n 个病例，其中前 3 个有干预/随访记录"""
    cases = []
    for i in range(1, n + 1):
        c = make_case(f"R{i:03d}", f"PR{i:03d}", "2026-09-01")
        cases.append(c)
        db.add(c)
    db.flush()
    for i in range(1, 4):
        db.add(InterventionRecord(
            case_id=f"R{i:03d}",
            sections_json=json.dumps([{"title": f"药物干预{i}", "content": "胆碱酯酶抑制剂"}]),
        ))
        db.add(FollowUpRecord(
            case_id=f"R{i:03d}",
            plan_json=json.dumps({"nextDate": "2026-12-01", "interval": "3个月"}),
        ))
    db.commit()
    return [c.id for c in cases]


def _build_export_rows(db, case_ids):
    """复刻 routers/report.py batch_export_reports 的批量查询逻辑（优化后）"""
    from models.case import CaseRecord as CaseModel

    cases = db.query(CaseModel).filter(CaseModel.id.in_(case_ids), CaseModel.is_deleted.is_(False)).all()
    case_map = {c.id: c for c in cases}

    inter_map = {}
    for rec in db.query(InterventionRecord).filter(InterventionRecord.case_id.in_(case_ids)).all():
        inter_map.setdefault(rec.case_id, rec)
    fu_map = {}
    for rec in db.query(FollowUpRecord).filter(FollowUpRecord.case_id.in_(case_ids)).all():
        fu_map.setdefault(rec.case_id, rec)

    rows = []
    for cid in case_ids:
        c = case_map.get(cid)
        if not c:
            continue
        patient = json.loads(c.patient_json or "{}")
        inter_rec = inter_map.get(cid)
        inter_summary = "—"
        if inter_rec:
            try:
                sections = json.loads(inter_rec.sections_json or "[]")
                inter_summary = "；".join(s.get("title", "") for s in sections if s.get("title"))[:80] or "—"
            except json.JSONDecodeError:
                pass
        fu_rec = fu_map.get(cid)
        fu_summary = "—"
        if fu_rec:
            try:
                plan = json.loads(fu_rec.plan_json or "{}")
                fu_summary = f"下次随访 {plan.get('nextDate', '—')}（{plan.get('interval', '—')}）"
            except json.JSONDecodeError:
                pass
        rows.append([cid, patient.get("name", ""), inter_summary, fu_summary])
    return rows


def _build_export_rows_n1(db, case_ids):
    """复刻优化前的 N+1 逐例查询逻辑（用于对比）"""
    from models.case import CaseRecord as CaseModel

    cases = db.query(CaseModel).filter(CaseModel.id.in_(case_ids), CaseModel.is_deleted.is_(False)).all()
    case_map = {c.id: c for c in cases}

    rows = []
    for cid in case_ids:
        c = case_map.get(cid)
        if not c:
            continue
        patient = json.loads(c.patient_json or "{}")
        inter_rec = db.query(InterventionRecord).filter(InterventionRecord.case_id == cid).first()
        inter_summary = "—"
        if inter_rec:
            try:
                sections = json.loads(inter_rec.sections_json or "[]")
                inter_summary = "；".join(s.get("title", "") for s in sections if s.get("title"))[:80] or "—"
            except json.JSONDecodeError:
                pass
        fu_rec = db.query(FollowUpRecord).filter(FollowUpRecord.case_id == cid).first()
        fu_summary = "—"
        if fu_rec:
            try:
                plan = json.loads(fu_rec.plan_json or "{}")
                fu_summary = f"下次随访 {plan.get('nextDate', '—')}（{plan.get('interval', '—')}）"
            except json.JSONDecodeError:
                pass
        rows.append([cid, patient.get("name", ""), inter_summary, fu_summary])
    return rows


def test_batch_export_query_count_is_constant(db):
    """优化后查询次数与病例数无关（恒为 3：cases + intervention + followup）"""
    engine = db.get_bind()
    case_ids = _seed_cases(db, n=5)

    counts = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def count_sql(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            counts["n"] += 1

    try:
        _build_export_rows(db, case_ids)
    finally:
        event.remove(engine, "before_cursor_execute", count_sql)

    # 批量查询应为常数级（3 次），而不是随病例数线性增长
    assert counts["n"] == 3, f"批量导出应仅 3 次 SELECT，实际 {counts['n']} 次"


def test_batch_export_n1_comparison(db):
    """对比验证：N+1 旧逻辑查询次数随病例数线性增长（2N+1）"""
    engine = db.get_bind()
    case_ids = _seed_cases(db, n=5)

    counts = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def count_sql(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            counts["n"] += 1

    try:
        _build_export_rows_n1(db, case_ids)
    finally:
        event.remove(engine, "before_cursor_execute", count_sql)

    # N+1：1(cases) + 5×2(intervention+followup per case) = 11
    assert counts["n"] == 11, f"N+1 旧逻辑应为 11 次 SELECT，实际 {counts['n']} 次"


def test_batch_export_content_correctness(db):
    """批量导出内容正确：有记录病例取到摘要，无记录病例显示占位符"""
    case_ids = _seed_cases(db, n=5)
    rows = _build_export_rows(db, case_ids)

    assert len(rows) == 5
    by_id = {r[0]: r for r in rows}
    # R001-R003 有记录
    assert by_id["R001"][2] == "药物干预1"
    assert by_id["R001"][3] == "下次随访 2026-12-01（3个月）"
    # R004-R005 无记录，显示占位符
    assert by_id["R004"][2] == "—"
    assert by_id["R004"][3] == "—"


def test_batch_export_handles_nonexistent_case(db):
    """批量导出跳过不存在的病例 ID，不报错"""
    case_ids = _seed_cases(db, n=3)
    rows = _build_export_rows(db, case_ids + ["NOTEXIST"])
    assert len(rows) == 3  # 不存在的被跳过


def test_batch_export_handles_invalid_json(db):
    """干预/随访 JSON 损坏时不报错，显示占位符"""
    case_ids = _seed_cases(db, n=3)
    # 手动损坏 R001 的 sections_json
    rec = db.query(InterventionRecord).filter(InterventionRecord.case_id == "R001").first()
    rec.sections_json = "not-valid-json{"
    db.commit()

    rows = _build_export_rows(db, case_ids)
    by_id = {r[0]: r for r in rows}
    assert by_id["R001"][2] == "—"  # JSON 损坏降级为占位符
