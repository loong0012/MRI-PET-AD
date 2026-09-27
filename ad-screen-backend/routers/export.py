"""
数据导出路由（admin）
==================================================================
临床研究数据导出，支持 CSV 格式（带 UTF-8 BOM，Excel 直接打开中文不乱码）：
- GET /system/export/cases      病例列表导出（患者基本信息 + 风险 + 模态 + 适应证）
- GET /system/export/analysis   AI 分析结果导出（量化指标 + 分期 + 置信度 + 模型元信息）

导出范围：未软删除病例；可按 cohort / riskLevel / modality / status 过滤。
审计留痕：每次导出记录操作人、IP、筛选条件、导出行数。
"""
import csv
import io
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models.case import CaseRecord as CaseModel
from models.analysis import AnalysisRecord
from models.log import SystemLog as SystemLogModel
from services.auth import require_role
from services.utils import format_date_time, next_seq_id
from services.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/system/export",
    tags=["数据导出"],
    dependencies=[Depends(require_role("admin"))],
)


def _audit_export(db: Session, user: dict, request: Request, action: str, detail: str):
    """导出操作审计落库（涉及全量患者 PHI，必须留痕可追溯）"""
    xff = request.headers.get("x-forwarded-for", "")
    ip = (xff.split(",")[0].strip() if xff else (request.client.host if request.client else ""))[:50]
    db.add(SystemLogModel(
        id=next_seq_id(db, SystemLogModel, "S", start=1),
        module="数据导出",
        action=action,
        operator=user.get("username", ""),
        role=user.get("roleName", ""),
        ip=ip,
        result="成功",
        time=format_date_time(),
        detail=detail,
    ))
    db.commit()


def _apply_filters(query, cohort: Optional[str], risk_level: Optional[str],
                   modality: Optional[str], status: Optional[str]):
    """复用病例列表的 SQL 列筛选逻辑"""
    if cohort:
        query = query.filter(CaseModel.cohort == cohort)
    if risk_level:
        query = query.filter(CaseModel.risk_level == risk_level)
    if modality:
        query = query.filter(CaseModel.modality == modality)
    if status:
        query = query.filter(CaseModel.status == status)
    return query


def _csv_stream(header: list[str], rows: list[list]) -> io.BytesIO:
    """
    生成带 UTF-8 BOM 的 CSV 字节流（Excel 直接打开中文不乱码）。
    BOM = b'\xef\xbb\xbf'，Excel 据此识别 UTF-8 编码。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    out = io.BytesIO()
    out.write(b"\xef\xbb\xbf")  # UTF-8 BOM
    out.write(buf.getvalue().encode("utf-8"))
    out.seek(0)
    return out


# 病例列表 CSV 表头与字段映射（camelCase 对齐前端字段名）
_CASE_HEADER = [
    "病例ID", "患者编号", "姓名", "性别", "年龄", "模态", "检查日期",
    "科室", "病例状态", "诊断状态", "风险分级", "风险评分",
    "有MRI", "有PET", "检查适应证", "PET显像剂", "队列", "创建时间",
]

# 分析结果 CSV 表头
_ANALYSIS_HEADER = [
    "病例ID", "患者姓名", "风险分级", "风险评分", "临床分期", "分期编码",
    "置信度", "海马体积L(cm³)", "海马体积R(cm³)", "平均SUV", "皮层厚度(mm)",
    "脑室体积(cm³)", "MTA评分", "生物分期", "ARIA风险", "PET显像剂",
    "模型版本", "推理来源", "推理耗时(s)", "完成时间",
]

# 风险分级中文映射
_RISK_LEVEL_CN = {
    "low": "低风险", "mci": "轻度认知障碍",
    "ad-early": "AD早期", "ad-late": "AD晚期",
}
# 检查适应证中文映射
_EXAM_INDICATION_CN = {
    "diagnosis": "诊断", "staging": "分期", "pre_dmt": "DMT前评估", "dmt_monitoring": "DMT监测",
}


@router.get("/cases")
def export_cases(
    request: Request,
    db: Session = Depends(get_db),
    cohort: Optional[str] = Query(default=None, description="队列筛选 ADNI1/ADNI2/ADNI3/UNKNOWN"),
    riskLevel: Optional[str] = Query(default=None, description="风险分级 low/mci/ad-early/ad-late"),
    modality: Optional[str] = Query(default=None, description="模态 MRI/PET/MRI+PET"),
    status: Optional[str] = Query(default=None, description="病例状态 pending/completed/reported"),
    user: dict = Depends(require_role("admin")),
    # 全表扫描+CSV 拼接资源密集，每 IP 每分钟最多 10 次防刷（含被攻陷 admin 账号场景）
    _rl: None = Depends(rate_limit("export", 10, 60)),
):
    """导出病例列表 CSV（含患者基本信息 + 风险 + 模态 + 适应证）"""
    query = db.query(CaseModel).filter(CaseModel.is_deleted == False)  # noqa: E712
    query = _apply_filters(query, cohort, riskLevel, modality, status)
    cases = query.order_by(CaseModel.create_time.desc()).all()

    rows = []
    for c in cases:
        try:
            p = json.loads(c.patient_json or "{}")
        except json.JSONDecodeError:
            p = {}
        rows.append([
            c.id,
            p.get("patientNo", ""),
            p.get("name", ""),
            p.get("gender", ""),
            p.get("age", ""),
            c.modality,
            c.exam_date,
            c.department,
            c.status,
            c.diag_status,
            _RISK_LEVEL_CN.get(c.risk_level, c.risk_level or ""),
            c.risk_score if c.risk_score is not None else "",
            "是" if c.has_mri else "否",
            "是" if c.has_pet else "否",
            _EXAM_INDICATION_CN.get(getattr(c, "exam_indication", "diagnosis"), getattr(c, "exam_indication", "diagnosis")),
            getattr(c, "pet_tracer", "fdg") or "fdg",
            c.cohort or "UNKNOWN",
            c.create_time,
        ])

    buf = _csv_stream(_CASE_HEADER, rows)
    filter_desc = f"cohort={cohort or '全部'}, riskLevel={riskLevel or '全部'}, modality={modality or '全部'}, status={status or '全部'}"
    _audit_export(db, user, request, "导出病例列表CSV",
                  f"筛选条件：{filter_desc}；导出 {len(rows)} 条病例")

    filename = f"cases_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analysis")
def export_analysis(
    request: Request,
    db: Session = Depends(get_db),
    cohort: Optional[str] = Query(default=None, description="队列筛选"),
    riskLevel: Optional[str] = Query(default=None, description="风险分级"),
    modality: Optional[str] = Query(default=None, description="模态"),
    status: Optional[str] = Query(default=None, description="病例状态"),
    user: dict = Depends(require_role("admin")),
    _rl: None = Depends(rate_limit("export", 10, 60)),
):
    """导出 AI 分析结果 CSV（含量化指标 + 分期 + 置信度 + 模型元信息）"""
    query = db.query(CaseModel).filter(CaseModel.is_deleted == False)  # noqa: E712
    query = _apply_filters(query, cohort, riskLevel, modality, status)
    cases = query.order_by(CaseModel.create_time.desc()).all()
    case_ids = [c.id for c in cases]
    # 批量取分析结果，避免 N+1 查询
    analysis_map = {a.case_id: a for a in db.query(AnalysisRecord).filter(AnalysisRecord.case_id.in_(case_ids)).all()}

    rows = []
    for c in cases:
        rec = analysis_map.get(c.id)
        if not rec:
            continue
        try:
            r = json.loads(rec.result_json or "{}")
        except json.JSONDecodeError:
            continue
        try:
            p = json.loads(c.patient_json or "{}")
        except json.JSONDecodeError:
            p = {}

        # ARIA 风险摘要：取 summary 文本，避免嵌套对象写入 CSV
        aria = r.get("ariaRisk") or {}
        aria_text = aria.get("summary") if isinstance(aria, dict) else str(aria)

        rows.append([
            c.id,
            p.get("name", ""),
            _RISK_LEVEL_CN.get(r.get("riskLevel", ""), r.get("riskLevel", "")),
            r.get("riskScore", ""),
            r.get("stage", ""),
            r.get("stageCode", ""),
            f"{r.get('confidence', 0) * 100:.1f}%" if r.get("confidence") is not None else "",
            r.get("hippocampusVolumeL", ""),
            r.get("hippocampusVolumeR", ""),
            r.get("meanSUV", ""),
            r.get("corticalThickness", ""),
            r.get("ventricleVolume", ""),
            r.get("mtaScore", ""),
            r.get("biologicalStage", ""),
            aria_text,
            r.get("petTracer", "fdg"),
            r.get("modelVersion", ""),
            r.get("inferenceSource", ""),
            r.get("inferenceTime", ""),
            r.get("finishTime", ""),
        ])

    buf = _csv_stream(_ANALYSIS_HEADER, rows)
    filter_desc = f"cohort={cohort or '全部'}, riskLevel={riskLevel or '全部'}, modality={modality or '全部'}, status={status or '全部'}"
    _audit_export(db, user, request, "导出分析结果CSV",
                  f"筛选条件：{filter_desc}；导出 {len(rows)} 条分析结果")

    filename = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
