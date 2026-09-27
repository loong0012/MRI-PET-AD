"""
科研统计 score-histogram SQL 下推分桶回归测试
==================================================================
优化前：全表 .all() 加载病例到 Python，逐行 risk_score // 10 分桶
优化后：CAST(risk_score/10 AS INT) 在 SQL 内分桶 GROUP BY，仅返回 ≤10 行聚合结果
本测试验证分桶边界（0/9.9/10/50/99.9/100）与旧逻辑语义一致。
"""
from sqlalchemy import func, Integer

from models.case import CaseRecord
from tests.conftest import make_case


def _histogram(db, modality=""):
    """复刻 analytics.py 优化后的 SQL 分桶逻辑"""
    bucket_idx = func.min(func.cast(CaseRecord.risk_score / 10.0, Integer), 9)
    q = (
        db.query(bucket_idx.label("b"), func.count().label("cnt"))
        .filter(CaseRecord.is_deleted == False, CaseRecord.risk_score.isnot(None))  # noqa: E712
    )
    if modality:
        q = q.filter(CaseRecord.modality == modality)
    rows = q.group_by("b").all()
    buckets = [0] * 10
    total = 0
    for r in rows:
        if r.b is None:
            continue
        b = int(r.b)
        if 0 <= b <= 9:
            buckets[b] += int(r.cnt)
            total += int(r.cnt)
    return buckets, total


def _old_histogram(db):
    """旧 Python 逐行分桶逻辑（基准）"""
    buckets = [0] * 10
    total = 0
    for c in db.query(CaseRecord).filter(CaseRecord.is_deleted == False).all():  # noqa: E712
        if c.risk_score is None:
            continue
        score = float(c.risk_score)
        idx = min(int(score // 10), 9)
        buckets[idx] += 1
        total += 1
    return buckets, total


def _seed(db):
    """覆盖关键分桶边界：0 / 9.9 / 10 / 25 / 50 / 99.9 / 100，外加 None 与已删除"""
    scores = [0, 9.9, 10, 25, 50, 99.9, 100]
    for i, s in enumerate(scores):
        c = make_case(f"H{i:03d}", f"PH{i:03d}", "2026-09-01")
        c.risk_score = s
        db.add(c)
    # 无评分（应排除）
    c_none = make_case("H900", "PH900", "2026-09-01")
    c_none.risk_score = None
    db.add(c_none)
    # 已软删除（应排除）
    c_del = make_case("H901", "PH901", "2026-09-01")
    c_del.risk_score = 55
    c_del.is_deleted = True
    db.add(c_del)
    db.commit()


def test_histogram_matches_old_logic(db):
    _seed(db)
    new_buckets, new_total = _histogram(db)
    old_buckets, old_total = _old_histogram(db)
    assert new_buckets == old_buckets
    assert new_total == old_total == 7


def test_histogram_bucket_boundaries(db):
    _seed(db)
    buckets, _ = _histogram(db)
    # 0 → 桶0；9.9 → 桶0；10 → 桶1
    assert buckets[0] == 2  # 0, 9.9
    assert buckets[1] == 1  # 10
    assert buckets[2] == 1  # 25
    assert buckets[5] == 1  # 50
    # 99.9 和 100 都归入桶9
    assert buckets[9] == 2


def test_histogram_excludes_none_and_deleted(db):
    _seed(db)
    _, total = _histogram(db)
    assert total == 7  # 9 条插入 - None - 已删除 = 7


def test_histogram_modality_filter(db):
    _seed(db)
    # 造 2 个 MRI 模态病例
    for i in range(2):
        c = make_case(f"HM{i}", f"PM{i}", "2026-09-01")
        c.risk_score = 30 + i * 10
        c.modality = "MRI"
        db.add(c)
    db.commit()
    buckets, total = _histogram(db, modality="MRI")
    assert total == 2
    assert buckets[3] == 1 and buckets[4] == 1
