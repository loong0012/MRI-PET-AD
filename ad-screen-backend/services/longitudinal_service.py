"""
纵向影像量化服务
==================================================================
按 patient_no 聚合同一患者多期影像的量化指标（派生自 AnalysisRecord.result_json），
计算相邻两期的年化变化率 Δ/年，并依据 NIA-AA Research Framework 阈值判定
纵向进展分级（CN / MCI / AD-E / AD-L）。

核心入口：
- compute_metrics(patient_no, db) -> dict  拉取多期指标 + 计算变化率 + 分级 + MCID 显著性
- compute_rates(timepoints) -> dict         对相邻两期算 Δ/年 + 百分比变化
- classify_niaaa(rates, prev_metrics) -> str  NIA-AA 纵向进展分级
- assess_significance(rates, prev_metrics) -> dict  MCID 显著性检验

设计要点：
- 不复制 build_analysis 全量输出，按需 json.loads(AnalysisRecord.result_json) 派生
- 单期患者：annual_rate_* 为 null，niaaa_stage = "baseline"
- 缺失 AnalysisRecord 的期跳过，progression_note 标注
- exam_date 支持三种格式归一化：yyyy-mm-dd / yyyymmdd / yyyy/mm/dd
- MCID 阈值依据 2026 版 PET/MRI 指南纵向随访章节：
    海马体积年萎缩率 > 3% = 显著进展
    SUVr 年降幅 > 2% = 显著进展
    皮层厚度年降幅 > 0.05 mm/年 = 显著进展
"""
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from models.case import CaseRecord
from models.analysis import AnalysisRecord
from models.longitudinal import LongitudinalMetric
from services.utils import now_str

logger = logging.getLogger(__name__)


# ---------- 日期归一化 ----------
def _normalize_exam_date(raw: str) -> Optional[str]:
    """归一化 exam_date 为 yyyy-mm-dd，无法解析返回 None。
    支持 yyyy-mm-dd / yyyy/mm/dd / yyyymmdd，以及带时间的
    "yyyy-mm-dd HH:MM:SS" / "yyyy/mm/dd HH:MM:SS"（截取前 10 位）。
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    # 兼容 datetime 字符串：前 10 位为合法 yyyy-mm-dd / yyyy/mm/dd 时截取
    if len(s) > 10 and s[4:5] in ("-", "/") and s[7:8] in ("-", "/"):
        head = s[:10]
        try:
            datetime.strptime(head, "%Y-%m-%d")
            s = head
        except ValueError:
            try:
                datetime.strptime(head, "%Y/%m/%d")
                s = head
            except ValueError:
                pass
    # yyyy-mm-dd / yyyy/mm/dd
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # yyyymmdd
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def _parse_date(normalized: str) -> Optional[datetime]:
    """已归一化的 yyyy-mm-dd 字符串 → datetime"""
    try:
        return datetime.strptime(normalized, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ---------- 从 AnalysisRecord.result_json 派生指标 ----------
def _derive_metrics_from_result(result_json_str: str) -> Optional[dict]:
    """解析 AnalysisRecord.result_json 取量化指标，失败返回 None"""
    if not result_json_str:
        return None
    try:
        data = json.loads(result_json_str)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("[longitudinal] result_json 解析失败：%s", e)
        return None
    return {
        "hippocampus_vol_l": data.get("hippocampusVolumeL"),
        "hippocampus_vol_r": data.get("hippocampusVolumeR"),
        "mean_suv": data.get("meanSUV"),
        "cortical_thickness": data.get("corticalThickness"),
        "ventricle_vol": data.get("ventricleVolume"),
        "mta_score": str(data.get("mtaScore", "")) if data.get("mtaScore") is not None else None,
    }


def _avg_hippocampus(m: dict) -> Optional[float]:
    """左右海马体积均值，缺一返 None"""
    l, r = m.get("hippocampus_vol_l"), m.get("hippocampus_vol_r")
    if l is None or r is None:
        return None
    try:
        return (float(l) + float(r)) / 2.0
    except (TypeError, ValueError):
        return None


# ---------- 变化率计算 ----------
# MCID 阈值（Minimum Clinically Important Difference）
# 依据 2026 版 PET/MRI 脑成像指南纵向随访章节
MCID_HV_ATROPHY_PCT = 3.0   # 海马体积年萎缩率% 阈值
MCID_SUV_DECLINE_PCT = 2.0  # SUVr 年降幅% 阈值
MCID_CORT_DECLINE = 0.05    # 皮层厚度年降幅 mm/年 阈值


def compute_rates(timepoints: list) -> dict:
    """
    对相邻两期算年化变化率 Δ/年，同时计算相对百分比变化（基于前一期基线）。
    timepoints: list[dict]，每项含 normalized_exam_date + 派生指标
    返回 {hv, suv, cort, interval_days, note, hv_pct, suv_pct, cort_pct}
    多期取最近一对。
    """
    result = {"hv": None, "suv": None, "cort": None,
              "interval_days": 0, "note": "",
              "hv_pct": None, "suv_pct": None, "cort_pct": None}
    if len(timepoints) < 2:
        return result

    prev = timepoints[-2]
    curr = timepoints[-1]
    d_prev = _parse_date(prev["normalized_exam_date"])
    d_curr = _parse_date(curr["normalized_exam_date"])
    if not d_prev or not d_curr:
        result["note"] = "日期解析失败，无法计算变化率"
        return result

    interval_days = (d_curr - d_prev).days
    result["interval_days"] = interval_days
    if interval_days <= 0:
        result["note"] = "检查日期顺序异常"
        return result
    interval_years = interval_days / 365.25
    if interval_days < 30:
        result["note"] = "间隔过短，变化率仅供参考"

    m_prev = prev["metrics"]
    m_curr = curr["metrics"]
    # 海马体积 Δ cm³/年（用左右均值）+ 相对百分比
    hv_prev = _avg_hippocampus(m_prev)
    hv_curr = _avg_hippocampus(m_curr)
    if hv_prev is not None and hv_curr is not None and hv_prev > 0:
        result["hv"] = round((hv_curr - hv_prev) / interval_years, 4)
        # 相对前期的百分比变化（正值=增大，负值=萎缩）
        result["hv_pct"] = round((hv_curr - hv_prev) / hv_prev * 100, 2)

    # SUV Δ/年 + 相对百分比
    suv_prev, suv_curr = m_prev.get("mean_suv"), m_curr.get("mean_suv")
    if suv_prev is not None and suv_curr is not None and float(suv_prev) > 0:
        result["suv"] = round((float(suv_curr) - float(suv_prev)) / interval_years, 4)
        result["suv_pct"] = round((float(suv_curr) - float(suv_prev)) / float(suv_prev) * 100, 2)

    # 皮层厚度 Δ mm/年 + 相对百分比
    cort_prev, cort_curr = m_prev.get("cortical_thickness"), m_curr.get("cortical_thickness")
    if cort_prev is not None and cort_curr is not None and float(cort_prev) > 0:
        result["cort"] = round((float(cort_curr) - float(cort_prev)) / interval_years, 4)
        result["cort_pct"] = round((float(cort_curr) - float(cort_prev)) / float(cort_prev) * 100, 2)

    return result


# ---------- MCID 显著性检验 ----------
def assess_significance(rates: dict, prev_metrics: dict) -> dict:
    """
    MCID 最小临床重要差异检验：判断各指标变化是否超过临床显著阈值。
    返回 {
        hv: {exceeded: bool, direction: 'atrophy'/'growth'/'stable',
             changePct: float|null, mcidPct: 3.0, note: str},
        suv: {...},
        cort: {...},
        anyExceeded: bool,  # 任一指标超 MCID 即为显著进展
        progression: 'significant'/'stable'/'insufficient_data'
    }
    """
    result = {
        "hv": {"exceeded": False, "direction": "stable",
               "changePct": None, "mcidPct": MCID_HV_ATROPHY_PCT, "note": ""},
        "suv": {"exceeded": False, "direction": "stable",
                "changePct": None, "mcidPct": MCID_SUV_DECLINE_PCT, "note": ""},
        "cort": {"exceeded": False, "direction": "stable",
                 "changePct": None, "mcidRate": MCID_CORT_DECLINE, "note": ""},
        "anyExceeded": False,
        "progression": "insufficient_data",
    }

    # 海马体积萎缩率%（负变化率表示萎缩，取绝对值与 MCID 比较）
    hv_pct = rates.get("hv_pct")
    if hv_pct is not None:
        result["hv"]["changePct"] = hv_pct
        atrophy_pct = -hv_pct  # 正数表示萎缩
        if atrophy_pct > MCID_HV_ATROPHY_PCT:
            result["hv"]["exceeded"] = True
            result["hv"]["direction"] = "atrophy"
            result["hv"]["note"] = f"海马体积年萎缩 {atrophy_pct:.2f}% 超过 MCID 阈值 {MCID_HV_ATROPHY_PCT}%，临床显著进展"
        else:
            result["hv"]["direction"] = "atrophy" if atrophy_pct > 0 else ("growth" if atrophy_pct < 0 else "stable")
            result["hv"]["note"] = f"海马体积年萎缩 {atrophy_pct:.2f}% 未超 MCID 阈值，属稳定"

    # SUVr 降幅%（负变化率表示下降，取绝对值与 MCID 比较）
    suv_pct = rates.get("suv_pct")
    if suv_pct is not None:
        result["suv"]["changePct"] = suv_pct
        decline_pct = -suv_pct  # 正数表示下降
        if decline_pct > MCID_SUV_DECLINE_PCT:
            result["suv"]["exceeded"] = True
            result["suv"]["direction"] = "decline"
            result["suv"]["note"] = f"SUVR 年降幅 {decline_pct:.2f}% 超过 MCID 阈值 {MCID_SUV_DECLINE_PCT}%，代谢显著下降"
        else:
            result["suv"]["direction"] = "decline" if decline_pct > 0 else ("increase" if decline_pct < 0 else "stable")
            result["suv"]["note"] = f"SUVR 年降幅 {decline_pct:.2f}% 未超 MCID 阈值，属稳定"

    # 皮层厚度年降幅 mm/年（负变化率表示变薄）
    cort_rate = rates.get("cort")
    if cort_rate is not None:
        decline_mm = -cort_rate  # 正数表示变薄
        if decline_mm > MCID_CORT_DECLINE:
            result["cort"]["exceeded"] = True
            result["cort"]["direction"] = "thinning"
            result["cort"]["changePct"] = rates.get("cort_pct")
            result["cort"]["note"] = f"皮层厚度年降幅 {decline_mm:.3f}mm 超过 MCID 阈值 {MCID_CORT_DECLINE}mm，结构显著萎缩"
        else:
            result["cort"]["direction"] = "thinning" if decline_mm > 0 else ("thickening" if decline_mm < 0 else "stable")
            result["cort"]["changePct"] = rates.get("cort_pct")
            result["cort"]["note"] = f"皮层厚度年降幅 {decline_mm:.3f}mm 未超 MCID 阈值，属稳定"

    # 综合判定
    any_exceeded = (result["hv"]["exceeded"] or result["suv"]["exceeded"]
                    or result["cort"]["exceeded"])
    result["anyExceeded"] = any_exceeded
    if any_exceeded:
        result["progression"] = "significant"
    elif (rates.get("hv_pct") is not None or rates.get("suv_pct") is not None
          or rates.get("cort_pct") is not None):
        result["progression"] = "stable"
    else:
        result["progression"] = "insufficient_data"

    return result


# ---------- NIA-AA 纵向进展分级 ----------
def classify_niaaa(rates: dict, prev_metrics: dict) -> str:
    """
    NIA-AA Research Framework 纵向进展分级：
    - 海马体积年萎缩率 > 3%/年（相对前值）→ +2
    - SUVr 年降幅 > 2%/年（相对前值）→ +1
    - 皮层厚度年降幅 > 0.05 mm/年 → +1
    累加分数：0=CN, 1-2=MCI, 3-4=AD-E, ≥5=AD-L
    rates: compute_rates 输出；prev_metrics: 前一期派生指标 dict
    返回分级 code（'CN' / 'MCI' / 'AD-E' / 'AD-L' / 'baseline'）
    """
    if rates.get("hv") is None and rates.get("suv") is None and rates.get("cort") is None:
        return "baseline"

    score = 0
    # 海马体积年萎缩率%（负变化率表示萎缩，-rate/prev * 100 得正数%）
    hv_rate = rates.get("hv")
    hv_prev = _avg_hippocampus(prev_metrics)
    if hv_rate is not None and hv_prev and hv_prev > 0:
        hv_atrophy_pct = (-hv_rate / hv_prev) * 100
        if hv_atrophy_pct > 3.0:
            score += 2

    # SUVr 年降幅%
    suv_rate = rates.get("suv")
    suv_prev = prev_metrics.get("mean_suv")
    if suv_rate is not None and suv_prev and suv_prev > 0:
        suv_decline_pct = (-suv_rate / float(suv_prev)) * 100
        if suv_decline_pct > 2.0:
            score += 1

    # 皮层厚度年降幅 mm/年
    cort_rate = rates.get("cort")
    if cort_rate is not None and cort_rate < -0.05:
        score += 1

    if score == 0:
        return "CN"
    if score <= 2:
        return "MCI"
    if score <= 4:
        return "AD-E"
    return "AD-L"


# ---------- 主入口：compute_metrics ----------
def compute_metrics(patient_no: str, db: Session) -> dict:
    """
    拉取 patient_no 全部多期 CaseRecord + 派生 AnalysisRecord 指标，
    计算年化变化率 + NIA-AA 分级，结果缓存到 LongitudinalMetric 表（upsert）。

    返回结构：
    {
      available: bool,
      reason: str (available=False 时填),
      timepoints: [{ caseId, examDate, timepointIdx, hippocampusVolL, ... }],
      rates: { hv, suv, cort, intervalDays, note },
      niaaaStage: 'CN'|'MCI'|'AD-E'|'AD-L'|'baseline',
      progressionNote: str
    }
    """
    if not patient_no:
        return {"available": False, "reason": "missing_patient_no",
                "timepoints": [], "rates": {}, "niaaaStage": "baseline",
                "progressionNote": "无患者编号"}

    # 拉取该 patient_no 所有未删除病例
    cases = db.query(CaseRecord).filter(
        CaseRecord.is_deleted.is_(False)
    ).all()
    # patient_no 存在 case.patient_json (JSON: { patientNo, name, gender, age })
    target_cases = []
    for c in cases:
        try:
            pj = json.loads(c.patient_json or "{}")
            if pj.get("patientNo") == patient_no:
                target_cases.append(c)
        except (json.JSONDecodeError, TypeError):
            continue

    if not target_cases:
        return {"available": False, "reason": "no_cases",
                "timepoints": [], "rates": {}, "niaaaStage": "baseline",
                "progressionNote": "未找到该患者病例"}

    # 按 exam_date 升序
    enriched = []
    for c in target_cases:
        nd = _normalize_exam_date(c.exam_date)
        if not nd:
            logger.warning("[longitudinal] 病例 %s exam_date 解析失败：%s", c.id, c.exam_date)
            continue
        enriched.append((c, nd))
    enriched.sort(key=lambda x: x[1])
    if not enriched:
        return {"available": False, "reason": "invalid_dates",
                "timepoints": [], "rates": {}, "niaaaStage": "baseline",
                "progressionNote": "检查日期均无法解析"}

    # 单期：写快照但不计算变化率
    if len(enriched) < 2:
        c, nd = enriched[0]
        _upsert_metric(db, patient_no, c, nd, 0, None, None, None, "baseline",
                       "基线期，待随访")
        db.commit()
        return {"available": False, "reason": "single_timepoint",
                "timepoints": [_serialize_timepoint(c, nd, 0)],
                "rates": {"hv": None, "suv": None, "cort": None,
                          "intervalDays": 0, "note": "基线期，待随访"},
                "niaaaStage": "baseline",
                "progressionNote": "暂无纵向对比数据，需 ≥2 期影像"}

    # 多期：派生指标 + 计算变化率
    timepoints = []
    for idx, (c, nd) in enumerate(enriched):
        ar = db.query(AnalysisRecord).filter(AnalysisRecord.case_id == c.id).first()
        metrics = _derive_metrics_from_result(ar.result_json) if ar else None
        note_item = ""
        if metrics is None:
            metrics = {"hippocampus_vol_l": None, "hippocampus_vol_r": None,
                       "mean_suv": None, "cortical_thickness": None,
                       "ventricle_vol": None, "mta_score": None}
            note_item = "该期 AI 分析未完成"
        timepoints.append({
            "case_id": c.id, "normalized_exam_date": nd,
            "exam_date": nd, "metrics": metrics, "note": note_item,
        })

    # 计算变化率（取最近一对）
    rates = compute_rates(timepoints)
    prev_metrics = timepoints[-2]["metrics"]
    niaaa = classify_niaaa(rates, prev_metrics)
    # MCID 显著性检验（最小临床重要差异）
    significance = assess_significance(rates, prev_metrics)

    # 组装 progression_note
    notes = [tp["note"] for tp in timepoints if tp["note"]]
    if rates.get("note"):
        notes.append(rates["note"])
    # 将显著性结论也加入 progression_note（前端可直接展示）
    if significance["progression"] == "significant":
        exceeded_items = []
        for k in ("hv", "suv", "cort"):
            if significance[k]["exceeded"]:
                exceeded_items.append(significance[k]["note"])
        if exceeded_items:
            notes.append("MCID 显著：" + "；".join(exceeded_items))
    elif significance["progression"] == "stable":
        notes.append("MCID 检验：各项指标变化均未超阈值，属稳定")
    progression_note = "；".join(notes) if notes else "无"

    # upsert 每期 LongitudinalMetric（最近一对的变化率写入末期）
    for idx, (c, nd) in enumerate(enriched):
        tp = timepoints[idx]
        m = tp["metrics"]
        # 仅末期写入变化率；其他期 annual_rate_* 置 null
        if idx == len(enriched) - 1:
            ar_hv, ar_suv, ar_cort = rates.get("hv"), rates.get("suv"), rates.get("cort")
        else:
            ar_hv = ar_suv = ar_cort = None
        _upsert_metric(db, patient_no, c, nd, idx, ar_hv, ar_suv, ar_cort,
                       niaaa, progression_note)
    db.commit()

    # 返回结构化结果
    return {
        "available": True,
        "timepoints": [_serialize_timepoint(c, nd, idx, timepoints[idx]["metrics"],
                                             timepoints[idx]["note"])
                       for idx, (c, nd) in enumerate(enriched)],
        "rates": {
            "hv": rates.get("hv"), "suv": rates.get("suv"), "cort": rates.get("cort"),
            "hvPct": rates.get("hv_pct"), "suvPct": rates.get("suv_pct"),
            "cortPct": rates.get("cort_pct"),
            "intervalDays": rates.get("interval_days", 0), "note": rates.get("note", ""),
        },
        "niaaaStage": niaaa,
        "progressionNote": progression_note,
        # MCID 显著性检验结果（前端可据此高亮显著变化指标）
        "significance": significance,
        # ATN 分期对应的随访频率与临床路径推荐（见 clinical_pathway_service）
        "followupRecommendation": _build_followup_rec(niaaa, significance),
    }


def _build_followup_rec(niaaa: str, significance: dict) -> dict:
    """
    基于 NIA-AA 纵向分期与 MCID 显著性，给出随访频率与下一步临床路径建议。
    依据 2026 版 PET/MRI 脑成像临床应用指南纵向随访章节。
    """
    rec = {
        "intervalMonths": 12,        # 默认 12 个月随访
        "priority": "routine",       # routine / enhanced / urgent
        "actions": [],
        "rationale": "",
    }
    if significance["progression"] == "significant":
        rec["priority"] = "enhanced"
        rec["intervalMonths"] = 6
        rec["actions"].append("建议补充 tau PET 复查以确认病理进展")
        rec["actions"].append("如未启动抗 Aβ 治疗，建议评估 DMT 适应证")
        rec["rationale"] = "MCID 显著进展，需缩短随访间隔并评估干预窗口"
    elif niaaa == "AD-E":
        rec["priority"] = "enhanced"
        rec["intervalMonths"] = 6
        rec["actions"].append("建议 6 个月后复查 MRI + FDG PET")
        rec["rationale"] = "AD 早期阶段，6 个月随访可及时发现病理进展"
    elif niaaa == "AD-L":
        rec["priority"] = "urgent"
        rec["intervalMonths"] = 3
        rec["actions"].append("3 个月临床+影像复查，启动/调整 DMT 治疗")
        rec["actions"].append("评估照护需求与认知康复介入")
        rec["rationale"] = "AD 中晚期，需 3 个月密集随访与治疗干预"
    elif niaaa == "MCI":
        rec["priority"] = "routine"
        rec["intervalMonths"] = 12
        rec["actions"].append("12 个月后复查 MRI，关注海马体积变化率")
        rec["rationale"] = "MCI 阶段，年度影像随访监测转化风险"
    else:  # CN / baseline
        rec["actions"].append("建议 12-24 个月后基线复查")
        rec["rationale"] = "稳定期，常规随访"
    return rec


# ---------- upsert 工具 ----------
def _upsert_metric(db: Session, patient_no: str, case: CaseRecord,
                   exam_date: str, timepoint_idx: int,
                   ar_hv: Optional[float], ar_suv: Optional[float],
                   ar_cort: Optional[float], niaaa_stage: str,
                   progression_note: str) -> None:
    """按 patient_no + case_id upsert LongitudinalMetric"""
    existing = db.query(LongitudinalMetric).filter(
        LongitudinalMetric.patient_no == patient_no,
        LongitudinalMetric.case_id == case.id,
    ).first()

    # 从 AnalysisRecord 派生快照（若可用）
    ar = db.query(AnalysisRecord).filter(AnalysisRecord.case_id == case.id).first()
    m = _derive_metrics_from_result(ar.result_json) if ar else None
    if m is None:
        m = {"hippocampus_vol_l": None, "hippocampus_vol_r": None,
             "mean_suv": None, "cortical_thickness": None,
             "ventricle_vol": None, "mta_score": None}

    if existing:
        existing.exam_date = exam_date
        existing.timepoint_idx = timepoint_idx
        existing.hippocampus_vol_l = m["hippocampus_vol_l"]
        existing.hippocampus_vol_r = m["hippocampus_vol_r"]
        existing.mean_suv = m["mean_suv"]
        existing.cortical_thickness = m["cortical_thickness"]
        existing.ventricle_vol = m["ventricle_vol"]
        existing.mta_score = m["mta_score"]
        existing.annual_rate_hv = ar_hv
        existing.annual_rate_suv = ar_suv
        existing.annual_rate_cort = ar_cort
        existing.niaaa_stage = niaaa_stage
        existing.progression_note = progression_note
        existing.compute_time = now_str()
    else:
        record = LongitudinalMetric(
            patient_no=patient_no, case_id=case.id, exam_date=exam_date,
            timepoint_idx=timepoint_idx,
            hippocampus_vol_l=m["hippocampus_vol_l"],
            hippocampus_vol_r=m["hippocampus_vol_r"],
            mean_suv=m["mean_suv"],
            cortical_thickness=m["cortical_thickness"],
            ventricle_vol=m["ventricle_vol"],
            mta_score=m["mta_score"],
            annual_rate_hv=ar_hv, annual_rate_suv=ar_suv, annual_rate_cort=ar_cort,
            niaaa_stage=niaaa_stage, progression_note=progression_note,
            compute_time=now_str(),
        )
        db.add(record)


def _serialize_timepoint(case: CaseRecord, exam_date: str, timepoint_idx: int,
                         metrics: dict = None, note: str = "") -> dict:
    """序列化单期为 API 响应格式（camelCase 字段名，与前端 TS 接口对齐）"""
    m = metrics or {}
    return {
        "caseId": case.id,
        "examDate": exam_date,
        "timepointIdx": timepoint_idx,
        "hippocampusVolL": m.get("hippocampus_vol_l"),
        "hippocampusVolR": m.get("hippocampus_vol_r"),
        "meanSuv": m.get("mean_suv"),
        "corticalThickness": m.get("cortical_thickness"),
        "ventricleVol": m.get("ventricle_vol"),
        "mtaScore": m.get("mta_score"),
        "note": note or "",
    }
