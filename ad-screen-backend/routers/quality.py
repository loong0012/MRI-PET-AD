"""
影像数据质控中心
- GET /quality/summary：体检得分 + 各分类问题计数 + 严重度计数
- GET /quality/issues：问题清单（category / severity 筛选），每条附修复建议

质控规则（扫描未软删病例）：
1. modality-incomplete 模态与实际图像不一致（申报 MRI+PET 但缺对应图像）
2. file-missing        影像文件丢失（数据库有路径但磁盘文件不存在）
3. info-incomplete      患者/检查关键信息不全
4. duplicate            同患者同一天重复建档
5. stalled              流转停滞（待分析/分析中超 3 天；分析完成超 7 天未出报告）
6. review-abnormal      AI 审核异常（最新版本被驳回；待审核超 2 天）
"""
import os
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok
from models.case import CaseRecord as CaseModel
from models.analysis import AnalysisVersionRecord
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/quality",
    tags=["数据质控中心"],
    dependencies=[Depends(get_current_user_qs)],
)

CATEGORY_LABELS = {
    "modality-incomplete": "模态图像缺失",
    "file-missing": "影像文件丢失",
    "info-incomplete": "信息不完整",
    "duplicate": "重复建档",
    "stalled": "流转停滞",
    "review-abnormal": "审核异常",
}


def _patient(c: CaseModel) -> dict:
    try:
        return json.loads(c.patient_json or "{}")
    except json.JSONDecodeError:
        return {}


def _parse_dt(s: str):
    try:
        return datetime.strptime((s or "")[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        try:
            return datetime.strptime((s or "")[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None


def _scan(db: Session) -> list[dict]:
    issues: list[dict] = []
    now = datetime.now()
    cases = (
        db.query(CaseModel)
        .filter(CaseModel.is_deleted == False)  # noqa: E712
        .order_by(CaseModel.create_time.desc())
        .all()
    )

    def add(cat: str, severity: str, c: CaseModel, message: str, suggestion: str):
        issues.append({
            "id": f"{cat}|{c.id}",
            "category": cat,
            "categoryLabel": CATEGORY_LABELS[cat],
            "severity": severity,
            "caseId": c.id,
            "patientName": _patient(c).get("name", ""),
            "examDate": (c.exam_date or "")[:10],
            "message": message,
            "suggestion": suggestion,
        })

    # ---------- 规则 1/2：模态完整性 + 磁盘文件 ----------
    for c in cases:
        need_mri = "MRI" in (c.modality or "")
        need_pet = "PET" in (c.modality or "")
        if need_mri and not c.has_mri:
            add("modality-incomplete", "high", c,
                f"申报模态含 MRI，但病例缺少 MRI 图像",
                "补传 MRI 影像或更正影像模态，避免单模态误判")
        if need_pet and not c.has_pet:
            add("modality-incomplete", "high", c,
                "申报模态含 PET，但病例缺少 PET 图像",
                "补传 PET 影像或更正影像模态，确保多模态融合输入完整")
        for kind, path in (("MRI", c.mri_path), ("PET", c.pet_path)):
            if path and not os.path.exists(path):
                add("file-missing", "high", c,
                    f"{kind} 影像文件在存储路径中不存在（可能被移动或清理）",
                    f"检查服务器存储：{path}；必要时重新归档原始 DICOM")

    # ---------- 规则 3：关键信息完整性 ----------
    for c in cases:
        p = _patient(c)
        missing = []
        if not (p.get("patientNo") or "").strip():
            missing.append("患者编号")
        if not (p.get("name") or "").strip():
            missing.append("姓名")
        if not (p.get("gender") or "").strip():
            missing.append("性别")
        if p.get("age") in (None, ""):
            missing.append("年龄")
        if not (c.exam_date or "").strip():
            missing.append("检查日期")
        if not (c.department or "").strip():
            missing.append("申请科室")
        if missing:
            add("info-incomplete", "medium", c,
                f"缺少关键字段：{'、'.join(missing)}",
                "补全患者人口学与检查信息，保证科研统计与报告要素完整")

    # ---------- 规则 4：同患者同日重复建档 ----------
    groups: dict[tuple[str, str], list[CaseModel]] = {}
    for c in cases:
        p = _patient(c)
        key = (p.get("patientNo", ""), (c.exam_date or "")[:10])
        groups.setdefault(key, []).append(c)
    for (pno, exam_day), grp in groups.items():
        if pno and exam_day and len(grp) > 1:
            for c in grp:
                add("duplicate", "high", c,
                    f"患者 {pno} 在 {exam_day} 存在 {len(grp)} 份病例，疑似重复建档",
                    "核对是否为重复上传；确认重复后在病例库软删除多余记录")

    # ---------- 规则 5：流转停滞 ----------
    for c in cases:
        created = _parse_dt(c.create_time)
        age_days = (now - created).days if created else None
        if c.status in ("pending", "analyzing") and age_days is not None and age_days >= 3:
            label = "待分析" if c.status == "pending" else "分析中"
            add("stalled", "medium", c,
                f"病例处于「{label}」状态已 {age_days} 天，长期未流转",
                "尽快执行 AI 分析；如影像有问题可退回补传")
        if c.status == "completed" and age_days is not None and age_days >= 7:
            add("stalled", "low", c,
                f"AI 分析完成已 {age_days} 天，仍未出具正式报告",
                "提醒主管医生完成版本审核并出具报告")

    # ---------- 规则 6：审核异常 ----------
    for c in cases:
        latest = (
            db.query(AnalysisVersionRecord)
            .filter(AnalysisVersionRecord.case_id == c.id)
            .order_by(AnalysisVersionRecord.version.desc())
            .first()
        )
        if not latest:
            continue
        if latest.review_status == "rejected":
            add("review-abnormal", "medium", c,
                f"最新分析版本 v{latest.version} 审核被驳回"
                + (f"：{latest.review_comment[:40]}" if latest.review_comment else ""),
                "根据驳回意见复核影像或重新分析后再次提交审核")
        elif latest.review_status == "pending":
            created = _parse_dt(latest.created_at)
            age_days = (now - created).days if created else 0
            if age_days >= 2:
                add("review-abnormal", "low", c,
                    f"v{latest.version} 待审核已 {age_days} 天，审核队列积压",
                    "请具有审核权限的医生尽快处理审核队列")

    # 同病例同分类去重（按插入顺序）
    seen = set()
    uniq = []
    for it in issues:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        uniq.append(it)
    return uniq, len(cases)


@router.get("/summary")
def quality_summary(db: Session = Depends(get_db)):
    """质控体检汇总"""
    issues, total_cases = _scan(db)
    sev = {"high": 0, "medium": 0, "low": 0}
    cat_counts = {k: 0 for k in CATEGORY_LABELS}
    for it in issues:
        sev[it["severity"]] += 1
        cat_counts[it["category"]] += 1
    healthy_cases = total_cases - len({it["caseId"] for it in issues})
    return ok({
        "totalCases": total_cases,
        "issueTotal": len(issues),
        "affectedCases": total_cases - healthy_cases,
        "healthyCases": healthy_cases,
        "high": sev["high"],
        "medium": sev["medium"],
        "low": sev["low"],
        "categories": [
            {"key": k, "label": CATEGORY_LABELS[k], "count": cat_counts[k]}
            for k in CATEGORY_LABELS
        ],
    })


@router.get("/issues")
def quality_issues(
    category: str = Query(default=""),
    severity: str = Query(default="", pattern="^$|^(high|medium|low)$"),
    db: Session = Depends(get_db),
):
    """问题清单（按严重度排序：高→中→低）"""
    issues, _ = _scan(db)
    if category:
        issues = [it for it in issues if it["category"] == category]
    if severity:
        issues = [it for it in issues if it["severity"] == severity]
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda x: (order[x["severity"]], x["category"], x["caseId"]))
    return ok({"list": issues, "total": len(issues)})
