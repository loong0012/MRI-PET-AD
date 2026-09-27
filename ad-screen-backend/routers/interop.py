"""
互操作路由（FHIR R4）
==================================================================
把 ADScreen 的筛查结果以标准 FHIR R4 资源对外输出，
使结果能进入 EHR / 科研平台 / 区域健康信息交换，而非只能导出 CSV。

- GET /interop/fhir/patient/{case_id}            单病例 Patient
- GET /interop/fhir/diagnostic-report/{case_id}  单病例报告 + 量化观测
- GET /interop/fhir/bundle                       批量 Bundle（可按队列/风险筛选）
- GET /interop/fhir/metadata                     能力声明（CapabilityStatement 精简版）

权限：科研人员及以上（含 PHI，不当作公开接口）。
审计：每次导出落 SystemLog，记录操作人、IP、筛选条件与资源条数。
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from database import get_db
from models.case import CaseRecord as CaseModel
from models.analysis import AnalysisRecord
from models.log import SystemLog as SystemLogModel
from services.auth import require_role
from services.utils import format_date_time, next_seq_id
from services.fhir_service import (
    build_patient,
    build_bundle,
)
from services.audit_service import query_events as audit_query

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/interop/fhir",
    tags=["互操作(FHIR)"],
    dependencies=[Depends(require_role("researcher", "admin"))],
)


def _audit_export(db: Session, user: dict, request: Request, action: str, detail: str):
    """导出审计（FHIR 输出含 PHI 与 AI 结论，必须留痕）"""
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


def _result_map(db: Session, case_ids: list[str]) -> dict:
    """批量取分析结果，避免 N+1 查询"""
    out = {}
    for a in db.query(AnalysisRecord).filter(AnalysisRecord.case_id.in_(case_ids)).all():
        try:
            out[a.case_id] = json.loads(a.result_json or "{}")
        except json.JSONDecodeError:
            out[a.case_id] = {}
    return out


@router.get("/metadata")
def capability_statement():
    """
    FHIR 能力声明（精简版）：告知集成方可获取哪些资源与编码体系。
    对应 FHIR 标准的 `CapabilityStatement` 资源。
    """
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": format_date_time(),
        "kind": "instance",
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {"type": "Patient", "interaction": [{"code": "search-type"}, {"code": "read"}]},
                    {"type": "ImagingStudy", "interaction": [{"code": "search-type"}, {"code": "read"}]},
                    {"type": "Observation", "interaction": [{"code": "search-type"}]},
                    {"type": "DiagnosticReport", "interaction": [{"code": "search-type"}, {"code": "read"}]},
                    {"type": "AuditEvent", "interaction": [{"code": "search-type"}]},
                ],
            }
        ],
    }


# ---------- FHIR AuditEvent ----------

# ADScreen 内部动作 → FHIR AuditEvent.action 编码
# 取值来自 DICOM Audit 事件类型（DCM）与 IETF RFC 3881 的常用子集，
# 未纳入标准的本地动作留空 action 码，仅保留 subtype 文本，
# 避免为了"看起来标准"而套用语义不符的编码（那会让下游误判）。
_FHIR_ACTION_CODE = {
    "read": "R",      # DCM 110106 Read
    "write": "C",     # DCM 110107 Create
    "update": "U",    # DCM 110105 Update
    "delete": "D",    # DCM 110108 Delete
    "export": "E",    # DCM 110110 Export
    "login": "L",     # DCM 110122 Login
    "logout": "L",    # 复用 Login，subtype 文本区分
    "config": "U",
    "infer": "E",     # 执行并输出结论
    "override": "U",
}
_FHIR_ACTION_DISPLAY = {
    "read": "Read", "write": "Create", "update": "Update", "delete": "Delete",
    "export": "Export", "login": "Login", "logout": "Logout",
    "config": "Update", "infer": "Execute", "override": "Update",
}
# outcome → FHIR AuditEvent.outcome 编码（RFC 3881 定义：0 成功 / 4 小错 / 8 严重错 / 12 大错）
_FHIR_OUTCOME_CODE = {"success": "0", "failure": "8", "denied": "12"}


def build_audit_event(row) -> dict:
    """
    把一条内部审计记录转换为 FHIR R4 AuditEvent。

    编码体系说明：
    - type 用 DICOM DCM 110100（Application Activity）
    - subtype 用本地 CodeSystem（ADScreen 自有动作集无标准编码）
    - outcome 用 RFC 3881 的 0/8/12，这是标准值，可安全使用
    - entity 挂 patient_ref，让 SIEM 能按患者聚合告警
    """
    from services.fhir_service import LOCAL_CS

    action = row.action or ""
    res: dict = {
        "resourceType": "AuditEvent",
        "id": row.id,
        "type": {
            "system": "http://dicom.nema.org/resources/ontology/DCM",
            "code": "110100",
            "display": "Application Activity",
        },
        "subtype": [
            {
                "system": LOCAL_CS,
                "code": action or "unknown",
                "display": _FHIR_ACTION_DISPLAY.get(action, action),
            }
        ],
        "action": _FHIR_ACTION_CODE.get(action, ""),
        "recorded": row.ts.replace(" ", "T") if row.ts else "",
        "outcome": _FHIR_OUTCOME_CODE.get(row.outcome or "success", "0"),
        "outcomeDesc": row.outcome or "success",
        "agent": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/extra-security-role-type",
                            "code": "humanuser",
                            "display": "human user",
                        }
                    ]
                },
                "who": {"display": row.actor or "匿名"},
                "requestor": True,
            }
        ],
        "source": {"observer": {"display": "ADScreen"}, "site": row.actor_ip or ""},
    }
    if row.actor_role:
        res["agent"][0]["policy"] = [row.actor_role]

    entity = {
        "what": {
            "display": row.resource_id or "",
            "identifier": {"value": row.resource_id or ""} if row.resource_id else None,
        },
        "type": {
            "system": LOCAL_CS,
            "code": row.resource_type or "unknown",
        },
    }
    # 患者维度单独挂一个 entity：合规告警按患者聚合时可直接用
    if row.patient_ref:
        entity["what"]["identifier"] = {"value": row.patient_ref}
        res["entity"] = [
            entity,
            {
                "what": {"identifier": {"value": row.patient_ref}},
                "type": {
                    "system": "http://terminology.hl7.org/CodeSystem/audit-entity-type",
                    "code": "1",
                    "display": "Person",
                },
            },
        ]
    else:
        res["entity"] = [entity]

    if row.request_id:
        res["id"] = f"{row.id}"
        res["extension"] = [
            {"url": f"{LOCAL_CS}/request-id", "valueString": row.request_id}
        ]
    if row.model_version:
        res.setdefault("extension", []).append(
            {"url": f"{LOCAL_CS}/model-version", "valueString": row.model_version}
        )
    if row.severity and row.severity != "info":
        res.setdefault("extension", []).append(
            {"url": f"{LOCAL_CS}/severity", "valueString": row.severity}
        )
    return res


@router.get("/audit-event")
def get_audit_events(
    actor: str = Query(default=""),
    action: str = Query(default=""),
    outcome: str = Query(default=""),
    patientRef: str = Query(default=""),
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """
    以 FHIR AuditEvent 形式输出审计日志，供院内 SIEM / 日志平台采集。

    为什么需要它：自建审计表只有本系统能读。安全运营中心需要统一采集
    全院系统的审计事件做关联分析，FHIR AuditEvent 是医疗领域的事实标准格式。
    """
    import time as _time

    since = _time.time() - days * 86400.0
    rows, total = audit_query(
        db, actor=actor, action=action, patient_ref=patientRef,
        outcome=outcome, since=since, page=1, page_size=limit,
    )
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "entry": [{"resource": build_audit_event(r)} for r in rows],
    }


@router.get("/patient/{case_id}")
def get_patient(
    case_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("researcher", "admin")),
):
    """单病例 → FHIR Patient"""
    case = db.query(CaseModel).filter(
        CaseModel.id == case_id, CaseModel.is_deleted == False  # noqa: E712
    ).first()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="病例不存在")
    return build_patient(case)


@router.get("/diagnostic-report/{case_id}")
def get_diagnostic_report(
    case_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("researcher", "admin")),
):
    """
    单病例 → DiagnosticReport + ImagingStudy + Observation 组成的 Bundle。

    未做 AI 分析的病例仅返回 ImagingStudy（不产出结论，避免误导临床）。
    """
    case = db.query(CaseModel).filter(
        CaseModel.id == case_id, CaseModel.is_deleted == False  # noqa: E712
    ).first()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="病例不存在")

    result = _result_map(db, [case.id]).get(case.id) or {}
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="该病例尚无 AI 分析结果，无法生成诊断报告资源",
        )
    return build_bundle([case], {case.id: result}, bundle_type="collection")


@router.get("/bundle")
def get_bundle(
    request: Request,
    db: Session = Depends(get_db),
    cohort: Optional[str] = Query(default=None, description="队列筛选 ADNI1/ADNI2/ADNI3/UNKNOWN"),
    riskLevel: Optional[str] = Query(default=None, description="风险分级 low/mci/ad-early/ad-late"),
    modality: Optional[str] = Query(default=None, description="模态 MRI/PET/MRI+PET"),
    limit: int = Query(default=100, ge=1, le=1000, description="返回病例数上限"),
    user: dict = Depends(require_role("researcher", "admin")),
):
    """
    批量导出 FHIR Bundle（searchset）。

    一个病例贡献：Patient + ImagingStudy + N×Observation + DiagnosticReport。
    无 AI 结果的病例仍输出 Patient/ImagingStudy，但不产出结论资源。
    """
    query = db.query(CaseModel).filter(CaseModel.is_deleted == False)  # noqa: E712
    if cohort:
        query = query.filter(CaseModel.cohort == cohort)
    if riskLevel:
        query = query.filter(CaseModel.risk_level == riskLevel)
    if modality:
        query = query.filter(CaseModel.modality == modality)
    cases = query.order_by(CaseModel.create_time.desc()).limit(limit).all()

    analysis_map = _result_map(db, [c.id for c in cases])
    bundle = build_bundle(cases, analysis_map, bundle_type="searchset")

    _audit_export(
        db, user, request, "导出 FHIR Bundle",
        f"筛选：cohort={cohort or '全部'}, riskLevel={riskLevel or '全部'}, "
        f"modality={modality or '全部'}；病例 {len(cases)} 例，资源 {bundle['total']} 条",
    )
    return bundle
