"""
病例库管理路由
- POST /case/query：分页检索
- POST /case/upload：DICOM 影像上传
- POST /case/batch-import：CSV 批量导入
- GET /case/{caseId}：病例详情
- POST /case/{caseId}/audit：医生审核
"""
import os
import io
import csv
import json
import shutil
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, Query
from sqlalchemy import or_, func, case as sa_case
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from schemas.case import CaseQuery
from pydantic import BaseModel, Field
from models.case import CaseRecord as CaseModel
from models.analysis import AnalysisRecord
from models.log import CaseLog
from models.favorite import CaseFavorite
from services.auth import get_current_user_qs, require_role
from services.data_init import case_record_to_dict
from services.dicom_service import convert_dicom_series, extract_dicom_meta
from services.utils import format_date_time, next_seq_id

logger = logging.getLogger(__name__)


class BatchDeletePayload(BaseModel):
    # 删除是重操作（级联物理清除分析/干预/随访），单次上限 200 例
    caseIds: list[str] = Field(default_factory=list, max_length=200)

# 病例库全部端点要求登录（操作人统一从 JWT 解析）
router = APIRouter(
    prefix="/case",
    tags=["病例库"],
    dependencies=[Depends(get_current_user_qs)],
)

# 影像上传根目录（按病例归档，保存 NIfTI / DICOM）
UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads", "studies")
# 上传安全：扩展名白名单（防止任意类型文件落盘）+ 单文件体积上限（防止整文件读入内存 OOM）
UPLOAD_ALLOWED_EXT = (".nii", ".nii.gz", ".dcm", ".dicom")
UPLOAD_MAX_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
# CSV 批量导入保护：整文件读入内存解析，必须限制体积与数据行数
CSV_IMPORT_MAX_SIZE = 10 * 1024 * 1024  # 10MB
CSV_IMPORT_MAX_ROWS = 5000


def _cleanup_case_dir(case_dir: str) -> None:
    """上传/转换失败时清理已落盘的病例归档目录，避免磁盘孤儿"""
    try:
        if os.path.isdir(case_dir):
            shutil.rmtree(case_dir, ignore_errors=True)
    except Exception:
        pass


# 分块落盘块大小：上传时内存常驻仅一块（8MB），避免 2GB 整包 read 撑爆内存
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
# DICM 魔数检测只需文件头部前 256 字节
_UPLOAD_HEAD_BYTES = 256


class _UploadTooLarge(Exception):
    """分块累计字节数超过 UPLOAD_MAX_SIZE"""


async def _stream_upload_to_tmp(upload_file: UploadFile, tmp_path: str) -> tuple[int, bytes]:
    """
    将上传文件分块流式写入临时文件。
    返回 (累计字节数, 文件前 256 字节)；累计超限立即中止并抛 _UploadTooLarge，
    由调用方统一清理归档目录。客户端中断等 IO 异常向上抛出。
    """
    size = 0
    head = b""
    with open(tmp_path, "wb") as fp:
        while True:
            chunk = await upload_file.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            if len(head) < _UPLOAD_HEAD_BYTES:
                head += chunk[:_UPLOAD_HEAD_BYTES - len(head)]
            size += len(chunk)
            if size > UPLOAD_MAX_SIZE:
                raise _UploadTooLarge(size)
            fp.write(chunk)
    return size, head


def _cascade_delete_case_relations(db: Session, case_id: str) -> None:
    """
    软删病例时统一物理清理全部关联业务数据（主表 case_records 保留并打删除标记，
    子表数据清除，符合"审计留痕但 PHI 不再可读"的合规要求）。

    覆盖：AI 分析结果 / 分析历史版本 / 干预方案 / 随访计划与执行 /
          ROI 标注 / 会诊评论 / 收藏 / 报告审核 / 纵向量化 / 预警处置。
    单个删除与批量删除共用，避免两处清理口径漂移遗漏。
    """
    from models.analysis import (
        AnalysisRecord, InterventionRecord, FollowUpRecord, FollowUpVisit,
        AnalysisVersionRecord,
    )
    from models.annotation import AnnotationRecord
    from models.case_comment import CaseComment
    from models.review import ReportReview
    from models.longitudinal import LongitudinalMetric
    from models.warning import WarningHandling

    relation_models = (
        AnalysisRecord, AnalysisVersionRecord, InterventionRecord,
        FollowUpRecord, FollowUpVisit, AnnotationRecord, CaseComment,
        CaseFavorite, ReportReview, LongitudinalMetric,
    )
    for model in relation_models:
        db.query(model).filter(model.case_id == case_id).delete(synchronize_session=False)
    # 预警处置记录以 "{type}|{caseId}" 去重键关联（无独立 case_id 列），同步清除
    db.query(WarningHandling).filter(
        WarningHandling.dedup_key.like(f"%|{case_id}")
    ).delete(synchronize_session=False)

# 项目内置 ADNI 真实影像数据集（mri_crop / pet_crop：{受试者ID}_{标签}.nii）
DATASET_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "datasets", "MRI PET图像"
)

# 演示示例配对（受试者在 mri_crop 与 pet_crop 中均有同标签影像）
SAMPLE_STUDIES = {
    "ad": {
        "subject": "137_S_0438",
        "label": "AD",
        "label_cn": "AD 阳性（阿尔茨海默病典型表现）",
        "gender": "M",
        "age": 74,
    },
    "smci": {
        "subject": "137_S_0994",
        "label": "sMCI",
        "label_cn": "sMCI（稳定性轻度认知障碍）",
        "gender": "M",
        "age": 71,
    },
    "cn": {
        "subject": "137_S_0972",
        "label": "CN",
        "label_cn": "CN（认知正常对照）",
        "gender": "F",
        "age": 68,
    },
}


def _classify_modality(filename: str, fallback_modality: str) -> str:
    """按文件名判断 MRI / PET"""
    name = (filename or "").lower()
    if "pet" in name or "fdg" in name:
        return "PET"
    if "mri" in name or "_mr" in name or "mr_" in name or "t1" in name or "mpr" in name:
        return "MRI"
    # 文件名无法判断时回退到表单模态
    if fallback_modality in ("MRI", "PET"):
        return fallback_modality
    return "MRI"


@router.post("/query")
def query_cases(q: CaseQuery, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user_qs)):
    """分页检索病例库（仅返回未软删除病例，支持多维筛选）"""
    query = db.query(CaseModel).filter(CaseModel.is_deleted == False)  # noqa: E712

    # 仅查当前用户收藏的病例（工作台"查看全部收藏"跳转用）
    if q.favorites:
        fav_ids = [
            r[0] for r in db.query(CaseFavorite.case_id)
            .filter(CaseFavorite.username == current_user["username"]).all()
        ]
        if not fav_ids:
            return ok({"list": [], "total": 0})
        query = query.filter(CaseModel.id.in_(fav_ids))

    # SQL 列可直接筛选的字段
    if q.keyword:
        # 关键词同时匹配病例ID、患者编号、患者姓名（姓名/编号存于 patient_json，用 SQLite JSON1 提取）
        # json_valid 防御空字符串/非法 JSON：非有效 JSON 时 json_extract 不执行，走 coalesce 为 ""
        kw = f"%{q.keyword.lower()}%"
        _safe_no = func.coalesce(
            sa_case((func.json_valid(CaseModel.patient_json) == 1,
                     func.json_extract(CaseModel.patient_json, "$.patientNo")),
                    else_=None),
            "",
        )
        _safe_name = func.coalesce(
            sa_case((func.json_valid(CaseModel.patient_json) == 1,
                     func.json_extract(CaseModel.patient_json, "$.name")),
                    else_=None),
            "",
        )
        query = query.filter(or_(
            CaseModel.id.ilike(kw),
            func.lower(_safe_no).like(kw),
            _safe_name.like(kw),
        ))
    if q.modality:
        query = query.filter(CaseModel.modality == q.modality)
    if q.status:
        query = query.filter(CaseModel.status == q.status)
    if q.riskLevel:
        query = query.filter(CaseModel.risk_level == q.riskLevel)
    if q.diagStatus:
        query = query.filter(CaseModel.diag_status == q.diagStatus)
    if q.department:
        query = query.filter(CaseModel.department == q.department)
    if q.dateRange and len(q.dateRange) == 2:
        s, e = q.dateRange
        if s:
            query = query.filter(CaseModel.exam_date >= s)
        if e:
            query = query.filter(CaseModel.exam_date <= e + " 23:59:59")

    # JSON 字段筛选（gender/age 在 patient_json 中，需 Python 侧过滤后分页）
    needs_json_filter = q.gender or q.ageMin is not None or q.ageMax is not None
    needs_followup_filter = q.hasFollowup is not None

    # 随访覆盖筛选：优先用 SQL EXISTS 子查询下推，避免全表加载到 Python
    if needs_followup_filter and not needs_json_filter:
        from models.analysis import FollowUpVisit
        fu_sub = db.query(FollowUpVisit.id).filter(FollowUpVisit.case_id == CaseModel.id)
        query = query.filter(fu_sub.exists() if q.hasFollowup else ~fu_sub.exists())
        needs_followup_filter = False  # 已在 SQL 完成，Python 侧无需再处理

    if needs_json_filter or needs_followup_filter:
        all_cases = query.order_by(CaseModel.create_time.desc()).all()
        # 随访覆盖筛选（仅当同时有 JSON 筛选、SQL 子查询未下推时兜底）
        if needs_followup_filter:
            from models.analysis import FollowUpVisit
            fu_case_ids = {r.case_id for r in db.query(FollowUpVisit.case_id).all()}
            if q.hasFollowup:
                all_cases = [c for c in all_cases if c.id in fu_case_ids]
            else:
                all_cases = [c for c in all_cases if c.id not in fu_case_ids]
        # JSON 字段筛选
        if needs_json_filter:
            filtered = []
            for c in all_cases:
                try:
                    p = json.loads(c.patient_json or "{}")
                except json.JSONDecodeError:
                    continue
                if q.gender and p.get("gender") != q.gender:
                    continue
                age = p.get("age")
                try:
                    age_int = int(age) if age is not None else None
                except (TypeError, ValueError):
                    age_int = None
                if q.ageMin is not None and (age_int is None or age_int < q.ageMin):
                    continue
                if q.ageMax is not None and (age_int is None or age_int > q.ageMax):
                    continue
                filtered.append(c)
            all_cases = filtered
        total = len(all_cases)
        start = (q.page - 1) * q.pageSize
        cases = all_cases[start:start + q.pageSize]
    else:
        total = query.count()
        cases = query.order_by(CaseModel.create_time.desc()).offset(
            (q.page - 1) * q.pageSize
        ).limit(q.pageSize).all()

    return ok({"list": [case_record_to_dict(c) for c in cases], "total": total})


@router.post("/upload")
async def upload_study(
    patientNo: str = Form(...),
    patientName: str = Form(...),
    gender: str = Form(...),
    age: int = Form(...),
    modality: str = Form(...),
    department: str = Form(...),
    exam_indication: str = Form("diagnosis"),
    pet_tracer: str = Form("fdg"),
    # 2026 版 PET/MRI 指南：检查前禁忌证筛查清单
    # 绝对禁忌清单（JSON 字符串）：pacemaker/defibrillator/cochlear_implant/dbs/insulin_pump
    absolute_contraindications: str = Form("[]"),
    # 相对禁忌清单（JSON 字符串）：steel_nails/artificial_joint/aneurysm_clip/coronary_stent/
    # removable_dentures/claustrophobia/diabetes_uncontrolled
    relative_contraindications: str = Form("[]"),
    # 特殊人群标记：normal/down_synodrome/claustrophobia/diabetes/implanted_device
    special_population: str = Form("normal"),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """影像上传建档（支持 NIfTI .nii/.nii.gz 与 DICOM，MRI/PET 自动识别归档）"""
    # ---------- 2026 版 PET/MRI 指南：禁忌证筛查校验 ----------
    # 绝对禁忌存在则拒绝建档（指南明确 MRI 检查不可进行）
    def _parse_list_form(s: str) -> list[str]:
        try:
            v = json.loads(s or "[]")
            return [str(x) for x in v if x]
        except (json.JSONDecodeError, TypeError):
            return []
    absolute_list = _parse_list_form(absolute_contraindications)
    relative_list = _parse_list_form(relative_contraindications)
    # 校验特殊人群标记白名单
    if special_population not in ("normal", "down_synodrome", "claustrophobia", "diabetes", "implanted_device"):
        return fail(f"未知特殊人群标记：{special_population}", 400)
    # 包含 MRI 的检查：绝对禁忌直接拒绝；纯 PET 检查仅警告（PET 无强磁场风险）
    has_mri_modality = modality != "PET"
    if absolute_list and has_mri_modality:
        return fail(
            f"存在 MRI 检查绝对禁忌：{', '.join(absolute_list)}，"
            f"指南明确不可进行 MRI 检查，请评估后选择替代方案（如 PET-only 或 CT）",
            400,
        )
    contraindication_warnings = []
    if absolute_list and not has_mri_modality:
        contraindication_warnings.append(
            f"PET 检查中存在 MRI 绝对禁忌记录：{', '.join(absolute_list)}，"
            f"已建档但需在后续如需 MRI 复查时重新评估"
        )
    if relative_list:
        contraindication_warnings.append(
            f"存在 MRI 相对禁忌：{', '.join(relative_list)}，"
            f"已建档；后续 MRI 复查需确认植入物 MRI 安全等级并按情况处理"
        )
    # Down 综合征等特殊人群：需家属陪同并特别标注
    if special_population == "down_synodrome":
        contraindication_warnings.append("Down 综合征 AD 患者：检查当日需家属全程陪同，扫描时间宜适当缩短")
    contraindication_data = {
        "absolute": absolute_list,
        "relative": relative_list,
        "cleared": not absolute_list,  # 无绝对禁忌视为筛查通过
        "warnings": contraindication_warnings,
    }

    # id 基于现有最大数字后缀 +1（count() 在删除记录后会产生主键冲突）
    case_id = next_seq_id(db, CaseModel, "AD", start=260001)
    case_dir = os.path.join(UPLOAD_ROOT, case_id)
    os.makedirs(case_dir, exist_ok=True)

    mri_path = ""
    pet_path = ""
    saved = []
    # DICOM 序列按模态归集到子目录，上传后统一转换为 NIfTI
    dcm_dirs: dict = {}

    tmp_index = 0
    for f in files:
        fname = f.filename or "image.dcm"
        low = fname.lower()
        kind = _classify_modality(fname, modality)

        # 体积预判：f.size 来自 multipart Content-Length，存在时传输前直接拒绝
        if f.size is not None and f.size > UPLOAD_MAX_SIZE:
            _cleanup_case_dir(case_dir)
            return fail(f"文件 {fname} 超过 {UPLOAD_MAX_SIZE // (1024 ** 3)}GB 上限", 400)

        # 先分块流式落到临时文件（内存常驻仅 8MB/块），读完后再按类型归位，
        # 避免 await f.read() 把最大 2GB 的整文件读进内存导致并发上传 OOM
        tmp_index += 1
        tmp_path = os.path.join(case_dir, f".upload_{tmp_index:04d}.tmp")
        try:
            _, head = await _stream_upload_to_tmp(f, tmp_path)
        except _UploadTooLarge:
            _cleanup_case_dir(case_dir)
            return fail(f"文件 {fname} 超过 {UPLOAD_MAX_SIZE // (1024 ** 3)}GB 上限", 400)
        except Exception:
            logger.warning("病例上传流式落盘失败（file=%s）", fname, exc_info=True)
            _cleanup_case_dir(case_dir)
            return fail(f"文件 {fname} 上传失败，请重试", 400)

        # 判定是否 DICOM：扩展名 .dcm/.dicom 或文件头含 DICM 魔数（兼容无扩展名 DICOM 切片）
        is_dicom = low.endswith(".dcm") or low.endswith(".dicom") or (
            len(head) > 132 and head[128:132] == b"DICM"
        )
        # 扩展名白名单：非 DICOM 文件必须是 .nii/.nii.gz，拒绝任意类型落盘
        if not is_dicom and not low.endswith((".nii", ".nii.gz")):
            _cleanup_case_dir(case_dir)
            return fail(f"不支持的文件类型：{fname}（仅支持 .nii / .nii.gz / .dcm / .dicom）", 400)

        if is_dicom:
            dcm_dir = os.path.join(case_dir, f"{kind}_dcm")
            os.makedirs(dcm_dir, exist_ok=True)
            dcm_path = os.path.join(dcm_dir, f"{len(dcm_dirs.get(kind, [])):04d}_{os.path.basename(fname)}")
            os.replace(tmp_path, dcm_path)
            dcm_dirs.setdefault(kind, dcm_dir)
            saved.append(f"{kind}(DICOM):{fname}")
            continue

        # NIfTI：保留扩展名（.nii.gz / .nii）
        if low.endswith(".nii.gz"):
            ext = ".nii.gz"
        else:
            ext = os.path.splitext(fname)[1] or ".nii"

        save_name = f"{kind}{ext}"
        save_path = os.path.join(case_dir, save_name)
        os.replace(tmp_path, save_path)
        saved.append(f"{kind}:{fname}")

        if kind == "MRI" and not mri_path:
            mri_path = save_path
        elif kind == "PET" and not pet_path:
            pet_path = save_path

    # DICOM 序列 → NIfTI 转换
    converted = []
    dicom_meta_dict: dict = {}
    for kind, dcm_dir in dcm_dirs.items():
        out_path = os.path.join(case_dir, f"{kind}.nii.gz")
        result = convert_dicom_series(dcm_dir, out_path)
        if result:
            converted.append(kind)
            # 优先抽取 MRI 序列的元数据；若没有 MRI 则用 PET（任一即可，标签同源）
            if not dicom_meta_dict:
                dicom_meta_dict = extract_dicom_meta(dcm_dir)
            if kind == "MRI" and not mri_path:
                mri_path = out_path
            elif kind == "PET" and not pet_path:
                pet_path = out_path
        else:
            # 转换失败：清理已落盘文件，避免磁盘孤儿
            _cleanup_case_dir(case_dir)
            return fail(f"{kind} DICOM 序列转换失败：请确认上传的是同一序列的完整切片", 400)

    has_mri = bool(mri_path) or (modality != "PET" and not pet_path)
    has_pet = bool(pet_path) or (modality != "MRI" and not mri_path)
    # 实际模态按上传文件判定
    if mri_path and pet_path:
        real_modality = "MRI+PET"
    elif pet_path:
        real_modality = "PET"
    else:
        real_modality = "MRI"

    case = CaseModel(
        id=case_id,
        patient_json=json.dumps({"patientNo": patientNo, "name": patientName, "gender": gender, "age": age}),
        modality=real_modality,
        exam_date=format_date_time(),
        department=department,
        status="pending",
        diag_status="待AI分析",
        risk_level=None,
        risk_score=None,
        has_mri=has_mri,
        has_pet=has_pet,
        create_time=format_date_time(),
        cloud_saved=False,
        mri_path=mri_path,
        pet_path=pet_path,
        dicom_meta=json.dumps(dicom_meta_dict, ensure_ascii=False) if dicom_meta_dict else "{}",
        exam_indication=exam_indication,
        pet_tracer=pet_tracer,
        contraindications=json.dumps(contraindication_data, ensure_ascii=False),
        special_population=special_population,
    )
    db.add(case)
    db.commit()

    # 记录操作日志
    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id=case_id,
        patient_name=patientName,
        action="上传 MRI/PET 影像",
        operator=current_user["username"],
        time=format_date_time(),
        detail=f"上传 {len(files)} 个影像文件（{real_modality}）：{', '.join(saved) if saved else '无文件'}",
    ))
    db.commit()

    return ok(case_record_to_dict(case))


@router.post("/upload-sample")
def upload_sample(sample: str = "ad", db: Session = Depends(get_db), current_user: dict = Depends(get_current_user_qs)):
    """
    一键载入示例影像数据（用于演示）：
    从项目内置 ADNI 数据集复制同受试者配对的 MRI+PET NIfTI 到病例归档目录，
    走与影像上传完全一致的建档与日志流程。sample ∈ {ad, smci, cn}。
    """
    key = (sample or "ad").strip().lower()
    if key not in SAMPLE_STUDIES:
        return fail(f"未知示例类型：{sample}，可选 ad / smci / cn", 400)
    meta = SAMPLE_STUDIES[key]

    mri_src = os.path.join(DATASET_ROOT, "mri_crop", f"{meta['subject']}_{meta['label']}.nii")
    pet_src = os.path.join(DATASET_ROOT, "pet_crop", f"{meta['subject']}_{meta['label']}.nii")
    missing = [p for p in (mri_src, pet_src) if not os.path.isfile(p)]
    if missing:
        return fail(f"示例影像文件缺失：{', '.join(os.path.basename(p) for p in missing)}", 500)

    case_id = next_seq_id(db, CaseModel, "AD", start=260001)
    case_dir = os.path.join(UPLOAD_ROOT, case_id)
    os.makedirs(case_dir, exist_ok=True)

    mri_path = os.path.join(case_dir, "MRI.nii")
    pet_path = os.path.join(case_dir, "PET.nii")
    shutil.copyfile(mri_src, mri_path)
    shutil.copyfile(pet_src, pet_path)

    patient_name = f"示例患者·{meta['label_cn'].split('（')[0]}"
    case = CaseModel(
        id=case_id,
        patient_json=json.dumps({
            "patientNo": meta["subject"],
            "name": patient_name,
            "gender": meta["gender"],
            "age": meta["age"],
        }),
        modality="MRI+PET",
        exam_date=format_date_time(),
        department="放射科",
        status="pending",
        diag_status="待AI分析",
        risk_level=None,
        risk_score=None,
        has_mri=True,
        has_pet=True,
        create_time=format_date_time(),
        cloud_saved=False,
        mri_path=mri_path,
        pet_path=pet_path,
        exam_indication="diagnosis",
        pet_tracer="fdg",
        contraindications='{"absolute": [], "relative": [], "cleared": true, "warnings": []}',
        special_population="normal",
    )
    db.add(case)
    db.commit()

    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id=case_id,
        patient_name=patient_name,
        action="上传 MRI/PET 影像",
        operator=current_user["username"],
        time=format_date_time(),
        detail=f"载入内置示例数据（ADNI {meta['subject']} {meta['label']}，MRI+PET 配对）",
    ))
    db.commit()

    return ok(case_record_to_dict(case))


@router.post("/batch-import")
async def batch_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """CSV 批量导入病例（10MB / 5000 行上限，防大文件整读导致内存尖峰）"""
    if not (file.filename or "").lower().endswith(".csv"):
        return fail("仅支持 .csv 文件", 400)
    content = await file.read()
    if len(content) > CSV_IMPORT_MAX_SIZE:
        return fail(
            f"CSV 文件超过 {CSV_IMPORT_MAX_SIZE // 1024 // 1024}MB 上限"
            f"（当前约 {max(1, len(content) // 1024 // 1024)}MB），请拆分后导入",
            400,
        )
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 2:
        return fail("CSV 文件为空或无数据行", 400)
    if len(rows) - 1 > CSV_IMPORT_MAX_ROWS:
        return fail(f"单次最多导入 {CSV_IMPORT_MAX_ROWS} 条病例，请拆分后导入", 400)

    count = 0
    for line in rows[1:]:  # 跳过表头
        if len(line) < 5:
            continue
        name, gender, age, modality, dept = [c.strip() for c in line[:5]]
        case_id = next_seq_id(db, CaseModel, "AD", start=260001)
        case = CaseModel(
            id=case_id,
            patient_json=json.dumps({"patientNo": f"P{int(datetime.now().timestamp())}{count}", "name": name, "gender": "M" if gender == "M" else "F", "age": int(age) if age.isdigit() else 65}),
            modality=modality if modality in ["MRI", "PET", "MRI+PET"] else "MRI+PET",
            exam_date=format_date_time(),
            department=dept or "神经内科",
            status="pending",
            diag_status="待AI分析",
            risk_level=None,
            risk_score=None,
            has_mri=modality != "PET",
            has_pet=modality != "MRI",
            create_time=format_date_time(),
            cloud_saved=False,
            exam_indication="diagnosis",
            pet_tracer="fdg",
            contraindications='{"absolute": [], "relative": [], "cleared": true, "warnings": []}',
            special_population="normal",
        )
        db.add(case)
        db.flush()  # 立即落库自增可见，保证 next_seq_id 取到唯一 id
        count += 1

    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id="BATCH",
        patient_name="—",
        action="批量导入病例",
        operator=current_user["username"],
        time=format_date_time(),
        detail=f"导入 {count} 条病例记录",
    ))
    db.commit()
    return ok({"imported": count})


@router.get("/search")
def global_search_cases(
    q: str = Query(default="", max_length=50),
    limit: int = Query(default=8, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """
    全局快捷搜索（病例ID / 患者姓名），顶栏搜索框下拉数据源。
    返回精简字段供前端跳转病例详情/阅片/分析页。
    """
    kw = q.strip().lower()
    if not kw:
        return ok([])
    # patient_name/patientNo 存于 patient_json：用 SQLite JSON1 下推过滤到 SQL 层，
    # 仅取 limit 条候选，避免拉取全量病例在 Python 端逐条解析
    kw_like = f"%{kw}%"
    _safe_no = func.coalesce(
        sa_case((func.json_valid(CaseModel.patient_json) == 1,
                 func.json_extract(CaseModel.patient_json, "$.patientNo")),
                else_=None),
        "",
    )
    _safe_name = func.coalesce(
        sa_case((func.json_valid(CaseModel.patient_json) == 1,
                 func.json_extract(CaseModel.patient_json, "$.name")),
                else_=None),
        "",
    )
    rows = (
        db.query(CaseModel)
        .filter(
            CaseModel.is_deleted == False,  # noqa: E712
            or_(
                CaseModel.id.ilike(kw_like),
                func.lower(_safe_no).like(kw_like),
                func.lower(_safe_name).like(kw_like),
            ),
        )
        .order_by(CaseModel.create_time.desc())
        .limit(limit)
        .all()
    )
    import json as _json

    def _name(c) -> str:
        try:
            return _json.loads(c.patient_json or "{}").get("name", "")
        except _json.JSONDecodeError:
            return ""

    return ok([
        {
            "id": c.id,
            "patientName": _name(c),
            "modality": c.modality,
            "riskLevel": c.risk_level,
            "riskScore": c.risk_score,
            "examDate": c.exam_date,
            "hasAnalysis": c.risk_score is not None,
        }
        for c in rows
    ])


# ==================== 病例收藏（按用户独立） ====================

@router.get("/favorites/list")
def list_favorites(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user_qs)):
    """当前用户的收藏病例列表（返回完整病例字典）"""
    username = current_user["username"]
    favs = (
        db.query(CaseFavorite)
        .filter(CaseFavorite.username == username)
        .order_by(CaseFavorite.created_at.desc())
        .all()
    )
    case_ids = [f.case_id for f in favs]
    if not case_ids:
        return ok([])
    rows = db.query(CaseModel).filter(CaseModel.id.in_(case_ids), CaseModel.is_deleted == False).all()  # noqa: E712
    case_map = {c.id: case_record_to_dict(c) for c in rows}
    # 保持收藏顺序
    return ok([case_map[cid] for cid in case_ids if cid in case_map])


@router.post("/favorites/{case_id}")
def toggle_favorite(case_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user_qs)):
    """切换收藏状态：已收藏则取消，未收藏则添加；返回 {favorited: bool}"""
    username = current_user["username"]
    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted == False).first()  # noqa: E712
    if not case:
        return fail("病例不存在")
    fav = db.query(CaseFavorite).filter(CaseFavorite.username == username, CaseFavorite.case_id == case_id).first()
    if fav:
        db.delete(fav)
        db.commit()
        return ok({"favorited": False})
    db.add(CaseFavorite(username=username, case_id=case_id))
    db.commit()
    return ok({"favorited": True})


@router.get("/favorites/check")
def check_favorites(
    caseIds: str = Query(default=""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """批量查询收藏状态：?caseIds=AD260001,AD260002 → {caseId: bool}"""
    username = current_user["username"]
    ids = [i.strip() for i in caseIds.split(",") if i.strip()]
    if not ids:
        return ok({})
    favs = db.query(CaseFavorite).filter(CaseFavorite.username == username, CaseFavorite.case_id.in_(ids)).all()
    fav_set = {f.case_id for f in favs}
    return ok({cid: cid in fav_set for cid in ids})


@router.get("/{case_id}/similar")
def get_similar_cases(
    case_id: str,
    limit: int = Query(default=3, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """相似病例 top-k 推荐
    基于风险等级匹配 + 异常脑区集合 Jaccard + 年龄/性别/日期接近度综合打分。
    批量预取候选分析记录（避免 N+1 查询），并过滤零相似度结果。
    """
    def _safe_json(raw: Optional[str]) -> dict:
        """容错解析 JSON 文本（损坏数据返回空 dict，不阻断整批推荐）"""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _region_set(data: dict) -> set:
        """从分析结果提取 '脑区|侧别' 集合，用于 Jaccard 计算"""
        out = set()
        for r in (data.get('abnormalRegions') or []):
            region = (r.get('region') or '').strip()
            side = (r.get('side') or '').strip()
            if region:
                out.add(f"{region}|{side}")
        return out

    # 1. 拉取目标病例的最新分析
    target_analysis = db.query(AnalysisRecord).filter(
        AnalysisRecord.case_id == case_id
    ).order_by(AnalysisRecord.id.desc()).first()
    if not target_analysis:
        return ok([], "目标病例暂无分析记录")

    target_data = _safe_json(target_analysis.result_json)
    target_risk = target_data.get('riskLevel', '')
    target_regions = _region_set(target_data)

    # 拉取目标病例基本信息
    target_case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not target_case:
        return ok([], "目标病例不存在")
    target_patient = _safe_json(target_case.patient_json)
    target_age = target_patient.get('age', 0) or 0
    target_gender = target_patient.get('gender', '')

    # 2. 候选集：所有有分析记录且非软删且非自身的病例
    candidate_case_ids = db.query(AnalysisRecord.case_id).distinct().all()
    candidate_ids_set = {row[0] for row in candidate_case_ids} - {case_id}
    if not candidate_ids_set:
        return ok([], "无候选相似病例")

    candidates = db.query(CaseModel).filter(
        CaseModel.id.in_(candidate_ids_set),
        CaseModel.is_deleted == False  # noqa: E712 软删除过滤
    ).all()

    # 3. 批量预取候选集全部分析记录，按 id desc 排序后在 Python 端取每个病例最新一条
    #    （替代逐候选查询，消除 N+1）
    all_records = (
        db.query(AnalysisRecord)
        .filter(AnalysisRecord.case_id.in_(candidate_ids_set))
        .order_by(AnalysisRecord.id.desc())
        .all()
    )
    latest_analysis_by_case: dict[str, AnalysisRecord] = {}
    for rec in all_records:
        if rec.case_id not in latest_analysis_by_case:
            latest_analysis_by_case[rec.case_id] = rec  # 已按 id desc，首条即最新

    # 风险等级邻接表
    risk_adjacent = {
        'low': ['mci'],
        'mci': ['low', 'ad-early'],
        'ad-early': ['mci', 'ad-late'],
        'ad-late': ['ad-early']
    }

    scored = []
    for cand in candidates:
        cand_analysis = latest_analysis_by_case.get(cand.id)
        if not cand_analysis:
            continue
        cand_data = _safe_json(cand_analysis.result_json)
        cand_risk = cand_data.get('riskLevel', '')
        if not cand_risk:
            continue

        score = 0.0
        # 风险等级匹配
        if cand_risk == target_risk:
            score += 3
        elif cand_risk in (risk_adjacent.get(target_risk) or []):
            score += 1

        # 异常脑区 Jaccard（双方都空 → 不加分，避免 NaN）
        cand_regions = _region_set(cand_data)
        if target_regions or cand_regions:
            union = target_regions | cand_regions
            inter = target_regions & cand_regions
            score += (len(inter) / len(union)) * 3 if union else 0

        # 年龄接近度
        cand_patient = _safe_json(cand.patient_json)
        cand_age = cand_patient.get('age', 0) or 0
        if abs(cand_age - target_age) <= 5:
            score += 1

        # 性别匹配
        if cand_patient.get('gender', '') == target_gender:
            score += 1

        # 检查日期接近度（≤6 个月 +0.5）
        try:
            t_date = datetime.strptime(target_case.exam_date, '%Y-%m-%d') if target_case.exam_date else None
            c_date = datetime.strptime(cand.exam_date, '%Y-%m-%d') if cand.exam_date else None
            if t_date and c_date and abs((t_date - c_date).days) <= 180:
                score += 0.5
        except (ValueError, TypeError):
            pass

        # 零相似度（风险等级既不匹配也不相邻、脑区无交集、人口学全不匹配）不推荐，避免误导
        if score <= 0:
            continue

        scored.append({
            'caseId': cand.id,
            'patientName': cand_patient.get('name', ''),
            'age': cand_age,
            'gender': cand_patient.get('gender', 'M'),
            'riskScore': cand_data.get('riskScore', 0) or 0,
            'riskLevel': cand_risk,
            'stageCode': cand_data.get('stageCode', 'CN'),
            'abnormalRegionCount': len(cand_data.get('abnormalRegions') or []),
            'examDate': cand.exam_date or '',
            '_score': score
        })

    # 排序取 top limit
    scored.sort(key=lambda x: x['_score'], reverse=True)
    top = scored[:limit]
    # 移除内部字段 _score
    for item in top:
        item.pop('_score', None)

    return ok(top, f"找到 {len(top)} 个相似病例")


@router.get("/{case_id}")
def get_case(case_id: str, db: Session = Depends(get_db)):
    """病例详情"""
    case = db.query(CaseModel).filter(
        CaseModel.id == case_id,
        CaseModel.is_deleted == False,  # noqa: E712
    ).first()
    if not case:
        return fail("病例不存在或已被删除", 404)
    return ok(case_record_to_dict(case))


@router.post("/{case_id}/audit")
def audit_case(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """医生审核"""
    case = db.query(CaseModel).filter(CaseModel.id == case_id, CaseModel.is_deleted.is_(False)).first()
    if not case:
        return fail("病例不存在", 404)
    case.diag_status = "医生已审核"
    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id=case_id,
        patient_name=json.loads(case.patient_json).get("name", ""),
        action="医生审核",
        operator=current_user["username"],
        time=format_date_time(),
        detail="医师完成结果审核",
    ))
    db.commit()
    return ok(None, "审核成功")


# 删除会级联物理清除病例的 AI/干预/随访数据，仅限临床医生与管理员（与报告/审核角色一致）
_CASE_DELETER = Depends(require_role("radiologist", "neurologist", "admin"))


@router.delete("/batch")
def delete_cases_batch(
    payload: BatchDeletePayload,
    db: Session = Depends(get_db),
    current_user: dict = _CASE_DELETER,
):
    """批量软删除病例。请求体：{"caseIds": ["AD260001", ...]}"""

    case_ids = payload.caseIds
    if not case_ids:
        return fail("请选择要删除的病例")
    operator = current_user.get("username", "unknown")
    deleted = 0
    for case_id in case_ids:
        case = db.query(CaseModel).filter(
            CaseModel.id == case_id,
            CaseModel.is_deleted == False,  # noqa: E712
        ).first()
        if not case:
            continue
        patient_name = json.loads(case.patient_json or "{}").get("name", "")
        case.is_deleted = True
        case.diag_status = "已删除"
        # 级联物理清除该病例全部子表 PHI 数据（与单个删除同一口径）
        _cascade_delete_case_relations(db, case_id)
        db.add(CaseLog(
            id=next_seq_id(db, CaseLog, "CL"),
            case_id=case_id,
            patient_name=patient_name,
            action="删除病例",
            operator=operator,
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            detail=f"病例 {case_id}（{patient_name}）由 {operator} 批量软删除",
        ))
        db.flush()
        deleted += 1
    db.commit()
    return ok({"deleted": deleted}, f"已删除 {deleted} 例")


@router.delete("/{case_id}")
def delete_case(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: dict = _CASE_DELETER,
):
    """
    删除病例（软删除，医疗合规保留审计痕迹）。
    级联标记该病例的 AI 分析 / 干预 / 随访计划 / 随访记录为失效，并记录操作日志。
    """

    case = db.query(CaseModel).filter(
        CaseModel.id == case_id,
        CaseModel.is_deleted == False,  # noqa: E712
    ).first()
    if not case:
        return fail("病例不存在或已被删除", 404)

    patient_name = json.loads(case.patient_json or "{}").get("name", "")

    # 1. 软删除病例主记录
    case.is_deleted = True
    case.diag_status = "已删除"

    # 2. 级联清理关联业务数据（主表软删留痕，全部子表 PHI 物理清除）
    _cascade_delete_case_relations(db, case_id)

    # 3. 审计日志
    operator = current_user.get("username", "unknown")
    db.add(CaseLog(
        id=next_seq_id(db, CaseLog, "CL"),
        case_id=case_id,
        patient_name=patient_name,
        action="删除病例",
        operator=operator,
        time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        detail=f"病例 {case_id}（{patient_name}）由 {operator} 软删除，AI 分析/干预/随访数据已级联清理",
    ))

    db.commit()
    return ok(None, "病例已删除")
