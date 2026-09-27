"""
报告模板管理路由
- GET    /report-template/list            模板列表（首次访问自动初始化 4 套默认模板）
- GET    /report-template/{template_id}   模板详情
- POST   /report-template/                新增模板
- PUT    /report-template/{template_id}    编辑模板
- DELETE /report-template/{template_id}    删除模板（默认模板不可删）
- POST   /report-template/{template_id}/set-default  设为默认
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.report_template import ReportTemplate
from services.auth import get_current_user_qs, require_role

router = APIRouter(
    prefix="/report-template",
    tags=["报告模板"],
    dependencies=[Depends(get_current_user_qs)],
)


# ---------- 默认字段配置 ----------
# contentStructure 字段显隐字典，13 个可勾选项
# 10 个基础字段 + 3 个新增字段（NIA-AA 框架对齐 / 矛盾证据 / 患者通俗版）
DEFAULT_FIELDS = {
    "patientInfo": True,        # 患者基本信息
    "examInfo": True,           # 检查信息
    "aiResult": True,           # AI 分析结果
    "imagingDesc": True,        # 影像描述
    "riskStratification": True,  # 风险分层
    "interventionAdvice": True,  # 干预建议
    "followupPlan": True,      # 随访计划
    "gradcamImage": False,     # GradCAM 热图（详版/科研版才显示）
    "brainMetrics": False,     # 脑区结构指标（科研版才显示）
    "disclaimer": True,        # 免责声明
    "niaaAlignment": False,    # NIA-AA A/T/N 框架对齐（详版/科研版显示）
    "conflictEvidence": False,  # 矛盾证据：影像-认知/模态间不一致（详版/科研版显示）
    "patientFriendly": False,  # 患者通俗科普版（患者版模板专用）
}

# 默认医院抬头
DEFAULT_HEADER = {
    "hospitalName": "神经影像智能筛查中心",
    "department": "放射科 · 核医学科",
    "logoUrl": "",
    "title": "脑影·明衰  阿尔茨海默病多模态影像筛查报告",
    "subtitle": "BrainImaging · Dementia Insight — MRI/PET 融合智能诊断",
    "systemName": "脑影·明衰",
    "systemVersion": "v1.0.0",
    "modelVersion": "TransMF-15ens-v4",
    "guideline": "2026 版 PET/MRI 脑成像临床应用指南",
}


class TemplateSaveBody(BaseModel):
    # 模板名限 50 字；两个配置字典限键数量（正常约 13 个字段开关 + 少量抬头项），防超大 JSON 入库
    name: str = Field(min_length=1, max_length=50)
    templateType: str = "detailed"     # brief / detailed / research
    contentStructure: dict = Field(default_factory=dict, max_length=50)
    headerConfig: dict = Field(default_factory=dict, max_length=50)
    isDefault: bool = False


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_dict(rec: ReportTemplate) -> dict:
    """ORM 记录 → 前端响应字典"""
    try:
        content = json.loads(rec.content_structure or "{}")
    except json.JSONDecodeError:
        content = {}
    try:
        header = json.loads(rec.header_config or "{}")
    except json.JSONDecodeError:
        header = {}
    return {
        "id": rec.id,
        "name": rec.name,
        "templateType": rec.template_type,
        "contentStructure": content,
        "headerConfig": header,
        "isDefault": bool(rec.is_default),
        "createdBy": rec.created_by or "",
        "createdAt": rec.created_at or "",
        "updatedAt": rec.updated_at or "",
    }


def _build_default_templates() -> list[ReportTemplate]:
    """构造 4 套默认模板：简版 / 详版（默认）/ 科研版 / 患者科普版"""
    now = _now_str()
    # 简版：仅核心字段（保持不变，3 个新字段均 False）
    brief_fields = {
        "patientInfo": True, "examInfo": True, "aiResult": True,
        "imagingDesc": False, "riskStratification": True,
        "interventionAdvice": False, "followupPlan": False,
        "gradcamImage": False, "brainMetrics": False, "disclaimer": True,
        "niaaAlignment": False, "conflictEvidence": False, "patientFriendly": False,
    }
    # 详版：含全部基本字段 + gradcamImage + NIA-AA + 矛盾证据
    detailed_fields = dict(DEFAULT_FIELDS)
    detailed_fields["gradcamImage"] = True
    detailed_fields["niaaAlignment"] = True
    detailed_fields["conflictEvidence"] = True
    # 科研版：含全部字段 + gradcamImage + brainMetrics + 3 个新字段全部启用
    research_fields = dict(DEFAULT_FIELDS)
    research_fields["gradcamImage"] = True
    research_fields["brainMetrics"] = True
    research_fields["niaaAlignment"] = True
    research_fields["conflictEvidence"] = True
    research_fields["patientFriendly"] = True
    # 患者科普版：仅启用患者通俗版字段，其他字段全部关闭
    patient_fields = {
        "patientInfo": False, "examInfo": False, "aiResult": False,
        "imagingDesc": False, "riskStratification": False,
        "interventionAdvice": False, "followupPlan": False,
        "gradcamImage": False, "brainMetrics": False, "disclaimer": False,
        "niaaAlignment": False, "conflictEvidence": False, "patientFriendly": True,
    }

    return [
        ReportTemplate(
            name="简版筛查报告",
            template_type="brief",
            content_structure=json.dumps(brief_fields, ensure_ascii=False),
            header_config=json.dumps(DEFAULT_HEADER, ensure_ascii=False),
            is_default=False,
            created_by="system",
            created_at=now,
            updated_at=now,
        ),
        ReportTemplate(
            name="详版筛查报告",
            template_type="detailed",
            content_structure=json.dumps(detailed_fields, ensure_ascii=False),
            header_config=json.dumps(DEFAULT_HEADER, ensure_ascii=False),
            is_default=True,
            created_by="system",
            created_at=now,
            updated_at=now,
        ),
        ReportTemplate(
            name="科研版筛查报告",
            template_type="research",
            content_structure=json.dumps(research_fields, ensure_ascii=False),
            header_config=json.dumps(DEFAULT_HEADER, ensure_ascii=False),
            is_default=False,
            created_by="system",
            created_at=now,
            updated_at=now,
        ),
        ReportTemplate(
            name="患者科普版",
            template_type="patient",
            content_structure=json.dumps(patient_fields, ensure_ascii=False),
            header_config=json.dumps(DEFAULT_HEADER, ensure_ascii=False),
            is_default=False,
            created_by="system",
            created_at=now,
            updated_at=now,
        ),
    ]


def _build_default_fields_for_type(template_type: str) -> dict:
    """按模板类型构造 13 字段默认配置（用于既有模板迁移补齐）"""
    base = dict(DEFAULT_FIELDS)
    if template_type == "brief":
        # 简版保持 3 个新字段全部 False
        base["niaaAlignment"] = False
        base["conflictEvidence"] = False
        base["patientFriendly"] = False
    elif template_type == "detailed":
        # 详版启用 NIA-AA 与矛盾证据
        base["gradcamImage"] = True
        base["niaaAlignment"] = True
        base["conflictEvidence"] = True
    elif template_type == "research":
        # 科研版 3 个新字段全部启用
        base["gradcamImage"] = True
        base["brainMetrics"] = True
        base["niaaAlignment"] = True
        base["conflictEvidence"] = True
        base["patientFriendly"] = True
    elif template_type == "patient":
        # 患者科普版仅启用 patientFriendly，其他字段全部关闭
        base = {k: False for k in DEFAULT_FIELDS}
        base["patientFriendly"] = True
    return base


def _migrate_legacy_templates(db: Session) -> None:
    """向前迁移：补齐既有模板缺失的 3 个新字段（NIA-AA / 矛盾证据 / 患者通俗版）；
    若缺少 patient 类型模板，自动插入。

    幂等：已包含全部字段的模板不会被修改，避免覆盖用户自定义。
    """
    all_templates = db.query(ReportTemplate).all()
    if not all_templates:
        return
    migrated = False
    expected_fields = set(DEFAULT_FIELDS.keys())
    for rec in all_templates:
        try:
            content = json.loads(rec.content_structure or "{}")
        except json.JSONDecodeError:
            content = {}
        existing = set(content.keys())
        missing = expected_fields - existing
        if not missing:
            continue
        # 按模板类型补齐缺失字段
        type_defaults = _build_default_fields_for_type(rec.template_type or "detailed")
        for k in missing:
            content[k] = type_defaults.get(k, False)
        rec.content_structure = json.dumps(content, ensure_ascii=False)
        rec.updated_at = _now_str()
        migrated = True
    # 若缺失 patient 类型模板，插入新模板
    if not any(t.template_type == "patient" for t in all_templates):
        patient_template = [t for t in _build_default_templates() if t.template_type == "patient"]
        if patient_template:
            db.add(patient_template[0])
            migrated = True
    if migrated:
        db.commit()


def _ensure_seed(db: Session) -> None:
    """首次访问若表为空，自动初始化 4 套默认模板；
    若已有模板但缺少新字段（NIA-AA/矛盾证据/患者版），自动迁移补齐。"""
    if db.query(ReportTemplate).count() == 0:
        for rec in _build_default_templates():
            db.add(rec)
        db.commit()
    else:
        _migrate_legacy_templates(db)


def _unset_other_defaults(db: Session, exclude_id: int | None = None) -> None:
    """将其他模板 is_default 置 False（保证全局唯一默认）"""
    q = db.query(ReportTemplate).filter(ReportTemplate.is_default == True)  # noqa: E712
    if exclude_id is not None:
        q = q.filter(ReportTemplate.id != exclude_id)
    for r in q.all():
        r.is_default = False


# ---------- 端点 ----------

@router.get("/list")
def list_templates(db: Session = Depends(get_db)):
    """模板列表（按 is_default DESC, created_at DESC 排序）；首次访问自动初始化"""
    _ensure_seed(db)
    items = (
        db.query(ReportTemplate)
        .order_by(ReportTemplate.is_default.desc(), ReportTemplate.created_at.desc())
        .all()
    )
    return ok({"list": [_to_dict(r) for r in items], "total": len(items)})


@router.get("/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db)):
    """模板详情"""
    rec = db.query(ReportTemplate).filter(ReportTemplate.id == template_id).first()
    if not rec:
        return fail("模板不存在", 404)
    return ok(_to_dict(rec))


# 模板是全院报告的统一规范，写操作仅限临床医生与管理员（与报告页路由角色一致）；科研角色只读
_TEMPLATE_WRITER = Depends(require_role("radiologist", "neurologist", "admin"))


@router.post("/")
def create_template(
    body: TemplateSaveBody,
    db: Session = Depends(get_db),
    current_user: dict = _TEMPLATE_WRITER,
):
    """新增模板；若 isDefault=True，先把其他模板的 isDefault 设为 False"""
    _ensure_seed(db)
    if body.isDefault:
        _unset_other_defaults(db)
    now = _now_str()
    rec = ReportTemplate(
        name=body.name.strip(),
        template_type=body.templateType,
        content_structure=json.dumps(body.contentStructure or {}, ensure_ascii=False),
        header_config=json.dumps(body.headerConfig or {}, ensure_ascii=False),
        is_default=body.isDefault,
        created_by=current_user.get("realName") or current_user.get("username", ""),
        created_at=now,
        updated_at=now,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return ok({"id": rec.id}, "模板已创建")


@router.put("/{template_id}")
def update_template(
    template_id: int,
    body: TemplateSaveBody,
    db: Session = Depends(get_db),
    current_user: dict = _TEMPLATE_WRITER,
):
    """编辑模板：更新名称 / 字段配置 / 抬头配置；若 isDefault=True，先把其他设为 False"""
    rec = db.query(ReportTemplate).filter(ReportTemplate.id == template_id).first()
    if not rec:
        return fail("模板不存在", 404)
    rec.name = body.name.strip()
    rec.template_type = body.templateType
    rec.content_structure = json.dumps(body.contentStructure or {}, ensure_ascii=False)
    rec.header_config = json.dumps(body.headerConfig or {}, ensure_ascii=False)
    rec.updated_at = _now_str()
    if body.isDefault and not rec.is_default:
        _unset_other_defaults(db, exclude_id=template_id)
        rec.is_default = True
    elif not body.isDefault and rec.is_default:
        rec.is_default = False
    db.commit()
    return ok(None, "模板已更新")


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: dict = _TEMPLATE_WRITER,
):
    """删除模板；默认模板不允许删除"""
    rec = db.query(ReportTemplate).filter(ReportTemplate.id == template_id).first()
    if not rec:
        return fail("模板不存在", 404)
    if rec.is_default:
        return fail("默认模板不允许删除，请先切换其他模板为默认")
    db.delete(rec)
    db.commit()
    return ok(None, "模板已删除")


@router.post("/{template_id}/set-default")
def set_default(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: dict = _TEMPLATE_WRITER,
):
    """设为默认：先把其他模板 isDefault 设为 False，再把当前设为 True"""
    rec = db.query(ReportTemplate).filter(ReportTemplate.id == template_id).first()
    if not rec:
        return fail("模板不存在", 404)
    _unset_other_defaults(db, exclude_id=template_id)
    rec.is_default = True
    rec.updated_at = _now_str()
    db.commit()
    return ok(None, "已设为默认模板")
