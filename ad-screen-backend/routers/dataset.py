"""
科研数据集导出路由
- POST /dataset/export  导出病例元数据 + AI结果 + ROI标注为 JSON/CSV

用于科研场景下批量导出脱敏后的病例结构化数据：
  - 基础字段：病例ID / 患者信息 / 影像模态 / 检查日期 / 风险评分 / 风险分级 / 有无MRI / 有无PET
  - 可选字段：ROI 标注列表（includeAnnotations=True）、影像文件路径（includeImaging=True）
返回格式支持 JSON（便于后续程序处理）与 CSV（便于 Excel 直接打开，含 BOM 头）。
"""
import csv
import io
import json
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import fail
from models.case import CaseRecord
from models.annotation import AnnotationRecord
from models.log import SystemLog as SystemLogModel
from services.auth import get_current_user_qs, require_role
from services.utils import next_seq_id, format_date_time, csv_sanitize

router = APIRouter(
    prefix="/dataset",
    tags=["科研数据集"],
    dependencies=[Depends(get_current_user_qs)],
)

# 单次导出病例数上限（批量 PHI 导出，与报告批量导出 500 上限一致）
DATASET_EXPORT_LIMIT = 500


class DatasetExportBody(BaseModel):
    """科研数据集导出请求体"""
    # 上限 500：批量 PHI 导出限量，降低批量泄露风险
    caseIds: list[str] = Field(default_factory=list, max_length=DATASET_EXPORT_LIMIT)
    includeAnnotations: bool = True
    includeImaging: bool = False
    format: str = "json"  # json / csv


# ---------- 字段组装 ----------

def _parse_patient(patient_json: str) -> dict:
    """安全解析 patient_json（异常时返回空 dict，避免单条脏数据拖垮整批导出）"""
    try:
        return json.loads(patient_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _annotation_to_dict(ann: AnnotationRecord) -> dict:
    """AnnotationRecord → 可序列化 dict（coords 字段解析为 JSON 对象）"""
    try:
        coords = json.loads(ann.coords or "{}")
    except (json.JSONDecodeError, TypeError):
        coords = {}
    return {
        "id": ann.id,
        "caseId": ann.case_id,
        "user": ann.user,
        "sliceIndex": ann.slice_index,
        "modality": ann.modality,
        "roiType": ann.roi_type,
        "coords": coords,
        "label": ann.label,
        "area": ann.area,
        "notes": ann.notes,
        "createdAt": ann.created_at,
    }


def _build_case_row(
    case: CaseRecord,
    annotations: list[AnnotationRecord],
    include_annotations: bool,
    include_imaging: bool,
) -> dict:
    """
    组装单病例导出行。
    patient_json 解析后以 patient 子对象输出（patientNo / name / gender / age）。
    """
    patient = _parse_patient(case.patient_json)
    row: dict = {
        "id": case.id,
        "patient": patient,
        "modality": case.modality,
        "examDate": case.exam_date,
        "riskLevel": case.risk_level,
        "riskScore": case.risk_score,
        "hasMRI": case.has_mri,
        "hasPET": case.has_pet,
    }
    if include_imaging:
        row["mriPath"] = case.mri_path or ""
        row["petPath"] = case.pet_path or ""
    if include_annotations:
        row["annotations"] = [_annotation_to_dict(a) for a in annotations]
    return row


# ---------- 端点 ----------

@router.post("/export")
def export_dataset(
    body: DatasetExportBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("researcher", "admin")),
):
    """
    导出科研数据集（病例元数据 + AI结果 + ROI标注；仅科研/管理员）。
    仅导出未软删除（is_deleted=False）的病例；请求体中 caseIds 不存在或已删除的 ID 会被静默跳过。
    批量 PHI 导出必须写 SystemLog 留痕（操作人/IP/范围/格式）。
    """
    case_ids = body.caseIds or []
    if not case_ids:
        return fail("请选择至少 1 例病例", 400)

    fmt = (body.format or "json").lower()
    if fmt not in ("json", "csv"):
        return fail("format 仅支持 json 或 csv", 400)

    # 查询未软删除的病例
    cases = (
        db.query(CaseRecord)
        .filter(CaseRecord.id.in_(case_ids), CaseRecord.is_deleted.is_(False))
        .all()
    )
    case_map = {c.id: c for c in cases}

    # 按请求顺序遍历（保留用户选择顺序），缺失/已删除的 ID 跳过
    ordered_cases = [case_map[cid] for cid in case_ids if cid in case_map]
    if not ordered_cases:
        return fail("所选病例不存在或已删除", 404)

    # 如需标注，一次性查出全部相关标注再按 case_id 分组，避免 N+1 查询
    ann_map: dict[str, list[AnnotationRecord]] = {}
    if body.includeAnnotations:
        anns = (
            db.query(AnnotationRecord)
            .filter(AnnotationRecord.case_id.in_([c.id for c in ordered_cases]))
            .all()
        )
        for a in anns:
            ann_map.setdefault(a.case_id, []).append(a)

    rows = [
        _build_case_row(
            case=c,
            annotations=ann_map.get(c.id, []),
            include_annotations=body.includeAnnotations,
            include_imaging=body.includeImaging,
        )
        for c in ordered_cases
    ]

    # 敏感操作审计：批量 PHI 导出留痕（操作人/IP/范围/格式/是否含标注影像）
    xff = request.headers.get("x-forwarded-for", "")
    ip = (xff.split(",")[0].strip() if xff else (request.client.host if request.client else ""))[:50]
    db.add(SystemLogModel(
        id=next_seq_id(db, SystemLogModel, "S", start=1),
        module="科研数据集",
        action="导出科研数据集",
        operator=current_user.get("username", ""),
        role=current_user.get("roleName", ""),
        ip=ip,
        result="成功",
        time=format_date_time(),
        detail=(
            f"格式 {fmt.upper()}，{len(rows)} 例；"
            f"含标注：{'是' if body.includeAnnotations else '否'}；"
            f"含影像路径：{'是' if body.includeImaging else '否'}"
        ),
    ))
    db.commit()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "json":
        # JSON 数组：ensure_ascii=False 保证中文原样输出
        payload = json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8")
        filename = f"科研数据集_{timestamp}.json"
        encoded = quote(filename)
        return StreamingResponse(
            iter([payload]),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
        )

    # ---------- CSV ----------
    # 表头：基础字段 + 可选影像路径 + 可选标注列
    headers = [
        "病例ID", "患者编号", "患者姓名", "性别", "年龄",
        "影像模态", "检查日期", "风险评分", "风险分级", "有MRI", "有PET",
    ]
    if body.includeImaging:
        headers.extend(["MRI路径", "PET路径"])
    if body.includeAnnotations:
        headers.extend(["ROI标注数", "ROI标注明细(JSON)"])

    buf = io.StringIO()
    # BOM 头保证 Excel 打开中文不乱码
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(headers)

    for row in rows:
        patient = row["patient"] or {}
        csv_row = [
            row["id"],
            patient.get("patientNo", ""),
            patient.get("name", ""),
            patient.get("gender", ""),
            patient.get("age", ""),
            row["modality"],
            row["examDate"],
            row["riskScore"] if row["riskScore"] is not None else "",
            row["riskLevel"] or "",
            "是" if row["hasMRI"] else "否",
            "是" if row["hasPET"] else "否",
        ]
        if body.includeImaging:
            csv_row.extend([row.get("mriPath", ""), row.get("petPath", "")])
        if body.includeAnnotations:
            anns = row.get("annotations", [])
            # 标注列表序列化为 JSON 字符串列（Excel 中可看到完整结构）
            csv_row.extend([
                len(anns),
                json.dumps(anns, ensure_ascii=False, default=str),
            ])
        # 公式注入消毒：患者姓名/备注等用户可控字段以 = + - @ 开头时补单引号
        writer.writerow([csv_sanitize(x) for x in csv_row])

    csv_bytes = buf.getvalue().encode("utf-8")
    filename = f"科研数据集_{timestamp}.csv"
    encoded = quote(filename)
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )
