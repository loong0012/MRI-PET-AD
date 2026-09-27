"""
第 22 轮安全加固回归测试
覆盖：
- 密码复杂度统一校验（长度 / 字母+数字 / 弱口令黑名单）
- 滑动窗口限流器（窗口内拒绝、key 隔离、窗口滑出恢复）
- 已软删病例的 ROI 标注 / 会诊评论读写均返回 404，且不产生孤儿数据
- 软删病例级联清理 10 张关联表 + 预警处置记录（dedup_key 后缀精确匹配，
  不误伤同前缀病例；主表保留审计留痕；对照病例数据不受影响）
"""
import pytest

from services.auth import validate_password
from services import rate_limit as rl
from services.rate_limit import hit_rate_limit, reset_rate_limit
from routers import annotation as ann_router
from routers import comment as comment_router
from routers.case import _cascade_delete_case_relations
from models.case import CaseRecord as CaseModel
from models.analysis import (
    AnalysisRecord, AnalysisVersionRecord, InterventionRecord,
    FollowUpRecord, FollowUpVisit,
)
from models.annotation import AnnotationRecord
from models.case_comment import CaseComment
from models.favorite import CaseFavorite
from models.review import ReportReview
from models.longitudinal import LongitudinalMetric
from models.warning import WarningHandling
# conftest.py 顶层工具函数（非 fixture），可直接导入
from conftest import make_case


# ---------- 限流器：每个用例前后清空全局窗口，避免跨用例污染 ----------
@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    reset_rate_limit()
    yield
    reset_rate_limit()


# ---------- 1. 密码复杂度 ----------
class TestPasswordPolicy:
    def test_valid_passwords_pass(self):
        assert validate_password("Zhang2024") is None
        assert validate_password("a1b2c3") is None          # 恰好 6 位下限
        assert validate_password("a" * 19 + "1") is None    # 恰好 20 位上限

    def test_length_bounds(self):
        assert validate_password("a1") is not None                    # 短于 6 位
        assert validate_password("a" * 20 + "1") is not None          # 21 位超长

    def test_must_mix_letter_and_digit(self):
        assert validate_password("1234567") is not None   # 纯数字
        assert validate_password("abcdefg") is not None   # 纯字母

    def test_weak_password_blacklist(self):
        # 长度与字母数字组合均合规，但命中弱口令黑名单
        assert validate_password("abc123") is not None
        assert validate_password("ADMIN123") is not None


# ---------- 2. 滑动窗口限流 ----------
class TestRateLimiter:
    def test_window_rejects_after_limit(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])

        results = [hit_rate_limit("login:1.1.1.1", 3, 60) for _ in range(4)]
        assert results == [False, False, False, True]

    def test_keys_are_independent(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])

        for _ in range(3):
            assert hit_rate_limit("captcha:1.1.1.1", 3, 60) is False
        # 同 IP 已打满，另一 IP / 另一动作不受影响
        assert hit_rate_limit("captcha:2.2.2.2", 3, 60) is False
        assert hit_rate_limit("register:1.1.1.1", 3, 60) is False

    def test_window_slides_out(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])

        for _ in range(3):
            assert hit_rate_limit("login:1.1.1.1", 3, 60) is False
        assert hit_rate_limit("login:1.1.1.1", 3, 60) is True
        # 时间推进超过窗口，计数滑出后恢复放行
        clock["t"] += 61
        assert hit_rate_limit("login:1.1.1.1", 3, 60) is False


# ---------- 3. 软删病例：标注 / 评论 404 ----------
class TestSoftDeleteGuard:
    def test_annotation_read_write_export(self, db):
        case = make_case("AD260001", "P001", "2026-01-01")
        db.add(case)
        db.commit()
        assert ann_router._active_case_exists(db, "AD260001") is True

        # 活跃病例：列表正常返回
        resp = ann_router.list_annotations("AD260001", db)
        assert resp["code"] == 200
        assert resp["data"] == []

        # 软删后：存在性校验、列表、导出均 404
        case.is_deleted = True
        db.commit()
        assert ann_router._active_case_exists(db, "AD260001") is False
        assert ann_router._active_case_exists(db, "AD999999") is False
        assert ann_router.list_annotations("AD260001", db)["code"] == 404
        assert ann_router.export_annotations("AD260001", db)["code"] == 404

        # 写入被拦截，不得产生孤儿标注
        body = ann_router.AnnotationCreate(sliceIndex=1, coords={"cx": 0, "cy": 0, "r": 5})
        resp = ann_router.create_annotation(
            "AD260001", body, db, {"username": "rad01", "realName": "张医生"}
        )
        assert resp["code"] == 404
        assert db.query(AnnotationRecord).count() == 0

    def test_comment_read_write(self, db):
        case = make_case("AD260002", "P002", "2026-01-01")
        db.add(case)
        db.commit()
        assert comment_router._active_case_exists(db, "AD260002") is True

        case.is_deleted = True
        db.commit()
        assert comment_router.list_comments("AD260002", db)["code"] == 404

        body = comment_router.CommentCreateBody(content="会诊意见")
        resp = comment_router.create_comment(
            "AD260002", body, db, {"username": "dr01", "realName": "李医生"}
        )
        assert resp["code"] == 404
        assert db.query(CaseComment).count() == 0


# ---------- 4. 级联清理 ----------
RELATION_MODELS = (
    AnalysisRecord, AnalysisVersionRecord, InterventionRecord,
    FollowUpRecord, FollowUpVisit, AnnotationRecord, CaseComment,
    CaseFavorite, ReportReview, LongitudinalMetric,
)


def _add_all_relations(db, case_id, patient_no):
    """给指定病例构造全部 10 张关联表各 1 行 + 1 条预警处置记录"""
    db.add_all([
        AnalysisRecord(case_id=case_id, result_json="{}"),
        AnalysisVersionRecord(case_id=case_id, version=1, result_json="{}"),
        InterventionRecord(case_id=case_id, sections_json="[]"),
        FollowUpRecord(case_id=case_id, plan_json="{}"),
        FollowUpVisit(case_id=case_id, visit_date="2026-01-01"),
        AnnotationRecord(case_id=case_id, coords="{}", created_at="2026-01-01 00:00:00"),
        CaseComment(case_id=case_id, user="u1", content="x",
                    created_at="2026-01-01 00:00:00"),
        CaseFavorite(username="u1", case_id=case_id),
        ReportReview(case_id=case_id, reviewer_username="r1", decision="submit"),
        LongitudinalMetric(patient_no=patient_no, case_id=case_id),
        WarningHandling(dedup_key=f"followup-overdue|{case_id}"),
    ])


class TestCascadeDelete:
    def test_cascade_clears_all_relations(self, db):
        db.add(make_case("C1", "P1", "2026-01-01"))
        db.add(make_case("C2", "P2", "2026-01-02"))
        # 同前缀病例：验证 dedup_key LIKE '%|C1' 不会误删 C10
        db.add(make_case("C10", "P10", "2026-01-03"))
        _add_all_relations(db, "C1", "P1")
        _add_all_relations(db, "C2", "P2")
        _add_all_relations(db, "C10", "P10")
        db.commit()

        _cascade_delete_case_relations(db, "C1")
        db.commit()

        # 目标病例：10 张关联表全部清空
        for model in RELATION_MODELS:
            assert db.query(model).filter(model.case_id == "C1").count() == 0
        # 预警处置按 dedup 键同步清除
        assert db.query(WarningHandling).filter(
            WarningHandling.dedup_key == "followup-overdue|C1").count() == 0

        # 对照病例数据完好
        for model in RELATION_MODELS:
            assert db.query(model).filter(model.case_id == "C2").count() == 1
        assert db.query(WarningHandling).filter(
            WarningHandling.dedup_key == "followup-overdue|C2").count() == 1

        # 后缀精确匹配：C10 不得被 '%|C1' 误伤
        for model in RELATION_MODELS:
            assert db.query(model).filter(model.case_id == "C10").count() == 1
        assert db.query(WarningHandling).filter(
            WarningHandling.dedup_key == "followup-overdue|C10").count() == 1

        # 主表保留（软删审计留痕，只清子表 PHI）
        assert db.query(CaseModel).filter(CaseModel.id == "C1").count() == 1
