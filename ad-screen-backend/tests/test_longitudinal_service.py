"""
longitudinal_service 单元测试
覆盖：
- 2 期数据 → 变化率计算 + NIA-AA 分级
- 3 期数据 → 取最近一对相邻期
- 单期 → available=False + reason='single_timepoint'
- 缺失 AnalysisRecord → 该期跳过 + progression_note 标注
- classify_niaaa 阈值映射（CN/MCI/AD-E 三档；AD-L 在 spec 设计下不可达）
"""
import pytest

from services.longitudinal_service import (
    compute_metrics, compute_rates, classify_niaaa, _normalize_exam_date,
)
# conftest.py 顶层工具函数（非 fixture），可直接导入
from conftest import make_case, make_analysis_record


# ---------- 工具函数 ----------
def test_normalize_exam_date_formats():
    """exam_date 三种格式归一化"""
    assert _normalize_exam_date("2024-01-15") == "2024-01-15"
    assert _normalize_exam_date("2024/01/15") == "2024-01-15"
    assert _normalize_exam_date("20240115") == "2024-01-15"
    assert _normalize_exam_date("") is None
    assert _normalize_exam_date("invalid") is None
    # 兼容带时间的 datetime 字符串（演示库 exam_date 常用 "yyyy-mm-dd HH:MM:SS"）
    assert _normalize_exam_date("2026-09-12 12:51:16") == "2026-09-12"
    assert _normalize_exam_date("2026/09/12 08:30:00") == "2026-09-12"


# ---------- compute_metrics：2 期 ----------
def test_compute_metrics_2_timepoints(db):
    """2 期数据：变化率与 NIA-AA 分级正确"""
    db.add(make_case("AD260001", "P001", "2023-01-01"))
    db.add(make_case("AD260002", "P001", "2024-01-01"))
    db.add(make_analysis_record("AD260001", hv_l=3.0, hv_r=3.0, suv=1.0, cort=3.0))
    db.add(make_analysis_record("AD260002", hv_l=2.7, hv_r=2.7, suv=0.95, cort=2.9))
    db.commit()

    result = compute_metrics("P001", db)

    assert result["available"] is True
    assert len(result["timepoints"]) == 2
    assert result["timepoints"][0]["caseId"] == "AD260001"
    assert result["timepoints"][1]["caseId"] == "AD260002"
    assert result["timepoints"][0]["timepointIdx"] == 0
    assert result["timepoints"][1]["timepointIdx"] == 1

    # 间隔约 365 天 → 年化 ~1.0
    assert result["rates"]["intervalDays"] == 365
    # hv: (2.7 - 3.0) / (365/365.25) ≈ -0.30
    assert result["rates"]["hv"] == pytest.approx(-0.30, abs=0.01)
    assert result["rates"]["suv"] == pytest.approx(-0.05, abs=0.01)
    assert result["rates"]["cort"] == pytest.approx(-0.10, abs=0.01)

    # NIA-AA：hv 萎缩 10%(+2) + suv 降 5%(+1) + cort 降 0.10(+1) → score=4 → AD-E
    assert result["niaaaStage"] == "AD-E"


# ---------- compute_metrics：3 期取最近一对 ----------
def test_compute_metrics_3_timepoints_take_latest_pair(db):
    """3 期数据：变化率取最近一对（T2→T3）"""
    db.add(make_case("AD260001", "P002", "2022-01-01"))
    db.add(make_case("AD260002", "P002", "2023-01-01"))
    db.add(make_case("AD260003", "P002", "2024-01-01"))
    # T1→T2 海马萎缩严重，T2→T3 萎缩轻微（应取 T2→T3）
    db.add(make_analysis_record("AD260001", hv_l=3.5, hv_r=3.5, suv=1.1, cort=3.2))
    db.add(make_analysis_record("AD260002", hv_l=3.0, hv_r=3.0, suv=1.0, cort=3.0))
    db.add(make_analysis_record("AD260003", hv_l=2.95, hv_r=2.95, suv=0.99, cort=2.98))
    db.commit()

    result = compute_metrics("P002", db)

    assert result["available"] is True
    assert len(result["timepoints"]) == 3
    # 取最近一对 T2→T3：hv (2.95-3.0)/1年 ≈ -0.05，不是 T1→T2 的 -0.5
    assert result["rates"]["hv"] == pytest.approx(-0.05, abs=0.01)
    assert result["rates"]["suv"] == pytest.approx(-0.01, abs=0.01)


# ---------- compute_metrics：单期 ----------
def test_compute_metrics_single_timepoint(db):
    """单期：available=False + reason=single_timepoint"""
    db.add(make_case("AD260001", "P003", "2024-01-01"))
    db.add(make_analysis_record("AD260001", hv_l=3.0, hv_r=3.0, suv=1.0, cort=3.0))
    db.commit()

    result = compute_metrics("P003", db)

    assert result["available"] is False
    assert result["reason"] == "single_timepoint"
    assert result["niaaaStage"] == "baseline"
    assert "暂无纵向对比" in result["progressionNote"]


# ---------- compute_metrics：缺失 AnalysisRecord ----------
def test_compute_metrics_missing_analysis(db):
    """某期无 AnalysisRecord：跳过该期指标 + note 标注"""
    db.add(make_case("AD260001", "P004", "2024-01-01"))
    db.add(make_case("AD260002", "P004", "2025-01-01"))
    # 仅 T1 有 AnalysisRecord，T2 缺失
    db.add(make_analysis_record("AD260001", hv_l=3.0, hv_r=3.0, suv=1.0, cort=3.0))
    db.commit()

    result = compute_metrics("P004", db)

    # 仍 available=True（有 2 期 case），但 rates 因 T2 缺指标全部 None
    assert result["available"] is True
    assert result["rates"]["hv"] is None
    assert result["rates"]["suv"] is None
    assert result["rates"]["cort"] is None
    # niaaa 因 rates 全 None → baseline
    assert result["niaaaStage"] == "baseline"
    # progression_note 标注 T2 缺分析
    assert "未完成" in result["progressionNote"]


# ---------- compute_metrics：未找到病例 ----------
def test_compute_metrics_no_cases(db):
    """patient_no 不存在：available=False + reason=no_cases"""
    result = compute_metrics("NONEXISTENT", db)
    assert result["available"] is False
    assert result["reason"] == "no_cases"


# ---------- compute_metrics：空 patient_no ----------
def test_compute_metrics_empty_patient_no(db):
    """空 patient_no：available=False + reason=missing_patient_no"""
    result = compute_metrics("", db)
    assert result["available"] is False
    assert result["reason"] == "missing_patient_no"


# ---------- classify_niaaa：阈值映射 ----------
def test_classify_niaaa_cn():
    """所有指标均不达阈值 → CN"""
    rates = {"hv": 0.1, "suv": 0.0, "cort": 0.0}  # 轻微增加，无萎缩
    prev = {"hippocampus_vol_l": 3.0, "hippocampus_vol_r": 3.0, "mean_suv": 1.0}
    assert classify_niaaa(rates, prev) == "CN"


def test_classify_niaaa_mci():
    """仅海马萎缩 >3% → score=2 → MCI"""
    # hv_rate = -0.15 (相对 3.0 平均值，萎缩 5%) → +2
    rates = {"hv": -0.15, "suv": 0.0, "cort": 0.0}
    prev = {"hippocampus_vol_l": 3.0, "hippocampus_vol_r": 3.0, "mean_suv": 1.0}
    assert classify_niaaa(rates, prev) == "MCI"


def test_classify_niaaa_ad_e():
    """海马萎缩(+2) + SUV 降(+1) + 皮层降(+1) → score=4 → AD-E"""
    rates = {"hv": -0.15, "suv": -0.05, "cort": -0.08}
    prev = {"hippocampus_vol_l": 3.0, "hippocampus_vol_r": 3.0, "mean_suv": 1.0}
    assert classify_niaaa(rates, prev) == "AD-E"


def test_classify_niaaa_baseline_when_no_rates():
    """所有 rates 均为 None → baseline"""
    rates = {"hv": None, "suv": None, "cort": None}
    prev = {"hippocampus_vol_l": None, "hippocampus_vol_r": None, "mean_suv": None}
    assert classify_niaaa(rates, prev) == "baseline"


# ---------- compute_rates：间隔过短提示 ----------
def test_compute_rates_short_interval_note(db=None):
    """间隔 <30 天：变化率仍计算但 note 标注间隔过短"""
    timepoints = [
        {"normalized_exam_date": "2024-01-01",
         "metrics": {"hippocampus_vol_l": 3.0, "hippocampus_vol_r": 3.0,
                     "mean_suv": 1.0, "cortical_thickness": 3.0}},
        {"normalized_exam_date": "2024-01-15",
         "metrics": {"hippocampus_vol_l": 2.9, "hippocampus_vol_r": 2.9,
                     "mean_suv": 0.98, "cortical_thickness": 2.95}},
    ]
    rates = compute_rates(timepoints)
    assert rates["interval_days"] == 14
    assert "间隔过短" in rates["note"]
    # 仍计算了 hv
    assert rates["hv"] is not None
