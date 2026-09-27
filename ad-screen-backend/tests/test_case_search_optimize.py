"""
病例库检索优化回归测试
==================================================================
验证两项 SQL 下推优化：
1. 关键词搜索同时匹配 病例ID / 患者编号(patientNo) / 患者姓名(name)
   —— 姓名/编号存于 patient_json，用 SQLite JSON1 (json_extract) 在 SQL 层过滤
2. 随访覆盖筛选（hasFollowup）在无 JSON 字段筛选时用 EXISTS 子查询下推，
   不再全表加载 FollowUpVisit 到 Python
"""
import json

from sqlalchemy import func, or_, case as sa_case

from models.case import CaseRecord as CaseModel
from models.analysis import FollowUpVisit
from tests.conftest import make_case


def _seed(db):
    """造 3 条病例：不同 患者编号/姓名/性别，2 条有随访记录"""
    c1 = make_case("C001", "P0001", "2026-09-01")
    c2 = make_case("C002", "P0002", "2026-09-02")
    c3 = make_case("C003", "P0003", "2026-09-03")
    # 覆盖姓名以便按姓名检索
    c1.patient_json = json.dumps({"patientNo": "P0001", "name": "张三", "gender": "M", "age": 70})
    c2.patient_json = json.dumps({"patientNo": "P0002", "name": "李四", "gender": "F", "age": 65})
    c3.patient_json = json.dumps({"patientNo": "P0003", "name": "王五", "gender": "M", "age": 80})
    db.add_all([c1, c2, c3])
    # c1 / c2 有随访，c3 无（visit_date 非空即可，字段以真实模型为准）
    db.add_all([
        FollowUpVisit(case_id="C001", visit_date="2026-09-10"),
        FollowUpVisit(case_id="C002", visit_date="2026-09-11"),
    ])
    db.commit()


def _safe_extract(json_field):
    """与路由一致的 json_valid 防御：非法 JSON 返回 NULL，由 coalesce 兜底为空串"""
    return func.coalesce(
        sa_case((func.json_valid(CaseModel.patient_json) == 1,
                 func.json_extract(CaseModel.patient_json, json_field)),
                else_=None),
        "",
    )


def _keyword_filter(query, keyword: str):
    """与 routers.case.query_cases 相同的 SQL 下推逻辑（提取以便单测）"""
    kw = f"%{keyword.lower()}%"
    return query.filter(or_(
        CaseModel.id.ilike(kw),
        func.lower(_safe_extract("$.patientNo")).like(kw),
        _safe_extract("$.name").like(kw),
    ))


def test_keyword_matches_case_id(db):
    _seed(db)
    rows = _keyword_filter(db.query(CaseModel), "c001").all()
    assert {r.id for r in rows} == {"C001"}


def test_keyword_matches_patient_no(db):
    _seed(db)
    rows = _keyword_filter(db.query(CaseModel), "p0002").all()
    assert {r.id for r in rows} == {"C002"}


def test_keyword_matches_patient_name(db):
    """患者姓名（中文）存在 patient_json，此前关键词无法匹配，本次修复"""
    _seed(db)
    rows = _keyword_filter(db.query(CaseModel), "王五").all()
    assert {r.id for r in rows} == {"C003"}


def test_keyword_partial_match(db):
    _seed(db)
    rows = _keyword_filter(db.query(CaseModel), "p000").all()
    assert {r.id for r in rows} == {"C001", "C002", "C003"}


def test_followup_exists_subquery_true(db):
    """hasFollowup=True：EXISTS 子查询只返回有随访的病例"""
    _seed(db)
    fu_sub = db.query(FollowUpVisit.id).filter(FollowUpVisit.case_id == CaseModel.id)
    rows = db.query(CaseModel).filter(fu_sub.exists()).all()
    assert {r.id for r in rows} == {"C001", "C002"}


def test_followup_exists_subquery_false(db):
    """hasFollowup=False：NOT EXISTS 子查询只返回无随访的病例"""
    _seed(db)
    fu_sub = db.query(FollowUpVisit.id).filter(FollowUpVisit.case_id == CaseModel.id)
    rows = db.query(CaseModel).filter(~fu_sub.exists()).all()
    assert {r.id for r in rows} == {"C003"}


def test_keyword_json_extract_handles_empty_json(db):
    """patient_json 为空/非法 JSON 时 json_extract 返回 NULL，不应报错"""
    c = make_case("C009", "P0009", "2026-09-09")
    c.patient_json = ""
    db.add(c)
    db.commit()
    rows = _keyword_filter(db.query(CaseModel), "张三").all()
    assert rows == []
