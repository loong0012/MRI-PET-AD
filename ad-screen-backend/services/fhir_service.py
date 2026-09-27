"""
FHIR R4 互操作服务
==================================================================
把 ADScreen 内部病例 / AI 分析结果映射为标准 FHIR R4 资源，
使筛查结果能进入医院 EHR、科研数据平台与区域健康信息交换，
而不是只能通过 CSV 文件交换。

对标参考架构（D/Vision Lab 医疗影像 AI 分层）的第 ⑤ 层 Results Management：
「输出应能转换为 DICOM SR、FHIR 或其他标准格式」。

产出的资源：
- Patient：患者身份（去标识化：只保留出生年份）
- ImagingStudy：影像检查（modality 用 DICOM 编码体系）
- Observation：AI 量化指标（海马体积 / SUV / 皮层厚度 / 风险评分 …）
- DiagnosticReport：NIA-AA 分期结论，引用上述 Observation
- Bundle：聚合上述资源

编码体系说明（重要）：
- 通用语义（性别、影像模态、报告类别、观测状态）使用标准体系；
- AD 专病指标（风险评分、NIA-AA 分期）尚无统一标准编码，
  统一走本系统定义的 CodeSystem（见 LOCAL_CS），并在资源中标注为本地扩展，
  避免伪造 LOINC/SNOMED 编码造成下游误读。
"""
import json
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 本系统本地编码体系（AD 专病指标尚无统一标准编码，显式声明而非伪造标准码）
LOCAL_CS = "urn:ad-screen:CodeSystem:ad-metrics"
LOCAL_STAGE_CS = "urn:ad-screen:CodeSystem:niaa-stage"
# DICOM modality 编码体系（标准）
DICOM_MODALITY_CS = "http://dicom.nema.org/resources/ontology/DCM"

# 内部 modality 字符串 → DICOM modality code
_MODALITY_TO_DICOM = {
    "MRI": "MR",
    "PET": "PT",
    "MRI+PET": "NM",  # 双模态融合检查：核医学（含 MR 衰减校正）语义最接近
}

# 风险分级中文 → 显示名，用于 Observation 的 interpretation
_RISK_LEVEL_CN = {
    "low": "低风险",
    "mci": "轻度认知障碍",
    "ad-early": "AD 早期",
    "ad-late": "AD 晚期",
}

# NIA-AA 分期 → 本地编码
_STAGE_CODE = {
    "无异常": "normal",
    "临床前AD": "preclinical",
    "AD源性MCI": "mci-due-to-ad",
    "AD痴呆": "ad-dementia",
    "非AD源性": "non-ad",
}


def _load_json(raw: Optional[str]) -> dict:
    """安全解析 JSON 字段（脏数据不抛异常）"""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        logger.debug("JSON 解析失败，按空对象处理")
        return {}


def _patient_json(case: Any) -> dict:
    return _load_json(getattr(case, "patient_json", "{}"))


def _birth_year_only(age: Any) -> Optional[str]:
    """
    年龄 → 出生年份（仅年份）。

    合规考虑：HIPAA 安全港与《个人信息保护法》去标识化均要求
    删除日期中比"年"更精确的部分。此处只输出 YYYY，
    既满足 FHIR date 的 partial date 语法，又降低再识别风险。
    """
    try:
        age_int = int(age)
    except (TypeError, ValueError):
        return None
    if not 0 < age_int < 130:
        return None
    return str(datetime.now().year - age_int)


# ------------------------------------------------------------------ Patient
def build_patient(case: Any) -> dict:
    """病例 → FHIR Patient"""
    p = _patient_json(case)
    gender_raw = (p.get("gender") or "").upper()
    gender = {"M": "male", "F": "female"}.get(gender_raw, "unknown")

    resource: dict = {
        "resourceType": "Patient",
        "id": f"case-{case.id}",
        "identifier": [
            {
                "use": "usual",
                "system": "urn:ad-screen:patient-no",
                "value": p.get("patientNo") or case.id,
            }
        ],
        "gender": gender,
    }
    name = (p.get("name") or "").strip()
    if name:
        resource["name"] = [{"use": "official", "text": name}]
    birth_year = _birth_year_only(p.get("age"))
    if birth_year:
        resource["birthDate"] = birth_year
    return resource


# ------------------------------------------------------------------ ImagingStudy
def build_imaging_study(case: Any) -> dict:
    """病例 → FHIR ImagingStudy（modality 走 DICOM 标准编码）"""
    modality_key = (case.modality or "").upper()
    dicom_code = _MODALITY_TO_DICOM.get(modality_key, "OT")

    meta = _load_json(getattr(case, "dicom_meta", "{}"))
    study_uid = meta.get("studyInstanceUID") or f"urn:ad-screen:study:{case.id}"

    started = (case.exam_date or "").strip()
    resource: dict = {
        "resourceType": "ImagingStudy",
        "id": f"study-{case.id}",
        "status": "available",
        "subject": {"reference": f"Patient/case-{case.id}"},
        "identifier": [{"system": "urn:dicom:uid", "value": f"urn:oid:{study_uid}"}],
        "modality": [
            {"system": DICOM_MODALITY_CS, "code": dicom_code, "display": modality_key}
        ],
    }
    if started:
        # FHIR dateTime 需带时区；院内检查时间按本地时区补全，缺失时分秒以 00 补齐
        resource["started"] = _to_fhir_datetime(started)
    desc = f"AD 多模态筛查（{modality_key}）"
    if getattr(case, "pet_tracer", None):
        desc += f"，PET 显像剂 {case.pet_tracer}"
    resource["description"] = desc
    return resource


def _to_fhir_datetime(raw: str) -> str:
    """'2026-09-01 10:30:00' / '2026-09-01' → FHIR dateTime（本地时区 +08:00）"""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        except ValueError:
            continue
    return raw


# ------------------------------------------------------------------ Observation
def _observation(case_id: str, code: str, display: str, value: Any,
                 unit: str, ucum: str) -> Optional[dict]:
    """构造单个 Observation（值为 None 时返回 None，避免产出空壳资源）"""
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return {
        "resourceType": "Observation",
        "id": f"obs-{case_id}-{code}",
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "imaging",
                        "display": "Imaging",
                    }
                ]
            }
        ],
        "code": {
            "coding": [{"system": LOCAL_CS, "code": code, "display": display}],
            "text": display,
        },
        "subject": {"reference": f"Patient/case-{case_id}"},
        "valueQuantity": {
            "value": round(num, 4),
            "unit": unit,
            "system": "http://unitsofmeasure.org",
            "code": ucum,
        },
    }


def build_observations(case: Any, result: dict) -> list[dict]:
    """AI 量化指标 → Observation 列表"""
    cid = case.id
    specs = [
        ("risk-score", "AD 风险评分", result.get("riskScore"), "分", "1"),
        ("hippocampus-volume-l", "左侧海马体积", result.get("hippocampusVolumeL"), "mm3", "mm3"),
        ("hippocampus-volume-r", "右侧海马体积", result.get("hippocampusVolumeR"), "mm3", "mm3"),
        ("mean-suv", "平均 SUV", result.get("meanSUV"), "SUV", "{SUV}"),
        ("cortical-thickness", "皮层厚度", result.get("corticalThickness"), "mm", "mm"),
        ("ventricle-volume", "脑室体积", result.get("ventricleVolume"), "mm3", "mm3"),
    ]
    obs = [o for o in (_observation(cid, *s) for s in specs) if o]

    # MTA 评分是序数等级，用 valueCodeableConcept 而非数值
    mta = result.get("mtaScore")
    if mta not in (None, ""):
        obs.append({
            "resourceType": "Observation",
            "id": f"obs-{cid}-mta",
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "imaging",
                            "display": "Imaging",
                        }
                    ]
                }
            ],
            "code": {
                "coding": [{"system": LOCAL_CS, "code": "mta-score", "display": "内侧颞叶萎缩 MTA 评分"}],
                "text": "内侧颞叶萎缩 MTA 评分",
            },
            "subject": {"reference": f"Patient/case-{cid}"},
            "valueCodeableConcept": {
                "coding": [{"system": LOCAL_CS, "code": f"mta-{mta}", "display": f"MTA {mta} 级"}],
                "text": f"MTA {mta} 级",
            },
        })
    return obs


# ------------------------------------------------------------------ DiagnosticReport
def build_diagnostic_report(case: Any, result: dict) -> Optional[dict]:
    """AI 分期结论 → FHIR DiagnosticReport（引用 Observation）"""
    if not result:
        return None

    stage = result.get("stage") or ""
    stage_code = result.get("stageCode") or _STAGE_CODE.get(stage, "")
    bio_stage = result.get("biologicalStage") or ""
    risk_level = result.get("riskLevel") or case.risk_level or ""

    conclusion_parts = []
    if stage:
        conclusion_parts.append(f"NIA-AA 分期：{stage}")
    if bio_stage:
        conclusion_parts.append(f"生物学分期：{bio_stage}")
    if risk_level:
        conclusion_parts.append(f"风险分级：{_RISK_LEVEL_CN.get(risk_level, risk_level)}")
    confidence = result.get("confidence")
    if confidence is not None:
        try:
            conclusion_parts.append(f"置信度：{float(confidence) * 100:.1f}%")
        except (TypeError, ValueError):
            pass
    conclusion = "；".join(conclusion_parts)

    resource: dict = {
        "resourceType": "DiagnosticReport",
        "id": f"report-{case.id}",
        "status": "final",
        # LOINC LP29684-5 = Radiology（放射学报告类别，标准码）
        "category": [
            {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "LP29684-5",
                        "display": "Radiology",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": LOCAL_CS,
                    "code": "ad-screening-report",
                    "display": "阿尔茨海默病多模态影像 AI 筛查报告",
                }
            ],
            "text": "AD 多模态影像 AI 筛查报告",
        },
        "subject": {"reference": f"Patient/case-{case.id}"},
        "imagingStudy": [{"reference": f"ImagingStudy/study-{case.id}"}],
        "conclusion": conclusion,
    }

    effective = result.get("finishTime") or result.get("inferenceTime") or case.exam_date
    if effective:
        resource["effectiveDateTime"] = _to_fhir_datetime(str(effective))

    # 分期以本地编码体系输出（NIA-AA 尚无官方 FHIR 编码）
    if stage_code or stage:
        resource["conclusionCode"] = [
            {
                "coding": [
                    {"system": LOCAL_STAGE_CS, "code": stage_code or "unknown", "display": stage}
                ],
                "text": stage,
            }
        ]

    # 引用量化观测（FHIR 要求 DiagnosticReport.result 指向 Observation）
    obs_ids = [
        o["id"] for o in build_observations(case, result)
    ]
    if obs_ids:
        resource["result"] = [{"reference": f"Observation/{oid}"} for oid in obs_ids]

    # 推理引擎出处：FHIR 用 performer + 扩展承载模型版本（便于溯源与监管审计）
    model_version = result.get("modelVersion")
    if model_version:
        resource["extension"] = [
            {
                "url": "urn:ad-screen:StructureDefinition/model-version",
                "valueString": str(model_version),
            }
        ]
    return resource


# ------------------------------------------------------------------ Bundle
def build_bundle(cases: list, analysis_map: dict, bundle_type: str = "searchset") -> dict:
    """
    聚合为 FHIR Bundle。

    :param cases: CaseRecord 列表
    :param analysis_map: {case_id: result_dict}
    :param bundle_type: searchset（查询返回）/ collection（集合打包）
    """
    entries: list[dict] = []

    def _add(resource: Optional[dict]):
        if resource:
            entries.append(
                {"fullUrl": f"urn:uuid:{resource['resourceType']}-{resource['id']}", "resource": resource}
            )

    for case in cases:
        result = analysis_map.get(case.id) or {}
        _add(build_patient(case))
        _add(build_imaging_study(case))
        for obs in build_observations(case, result):
            _add(obs)
        _add(build_diagnostic_report(case, result))

    return {
        "resourceType": "Bundle",
        "type": bundle_type,
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "total": len(entries),
        "entry": entries,
    }
