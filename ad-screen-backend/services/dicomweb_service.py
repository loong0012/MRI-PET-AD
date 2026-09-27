"""
DICOMweb 只读服务（QIDO-RS / WADO-RS metadata）
==================================================================
对标参考架构第 ② 层 Integration & DICOM Gateway，
让外部系统（OHIF、Orthanc、PACS、区域平台）能用标准协议检索本平台的检查，
而不是依赖私有 REST 约定。

实现范围（只读，本轮不做写入）：
- QIDO-RS：studies / series / instances 检索，返回 DICOM JSON（PS3.18 F.2）
- WADO-RS：instance 的 metadata（不含像素数据 BulkData）

不做 STOW-RS 的原因：
写入需要完整的归档语义（SOP Class 校验、幂等、存储承诺 Storage Commitment），
属于归档服务职责，列为 P2（对标 dcm4chee 的归档语义后再实施）。

UID 说明（重要）：
DICOM UID 必须在全球唯一，生产部署前必须通过
`ADSCREEN_DICOM_UID_ROOT` 注入本机构已申请的 OID 根；
未配置时使用占位根，并在启动时告警。
UID 生成是**确定性**的（同一病例每次请求得到相同 UID），
否则外部系统无法稳定引用同一检查。
"""
import hashlib
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 占位 UID 根（生产必须覆盖）
_PLACEHOLDER_ROOT = "1.2.826.0.1.3680043.10.1333"
DICOM_UID_ROOT = os.getenv("ADSCREEN_DICOM_UID_ROOT", "").strip() or _PLACEHOLDER_ROOT


def using_placeholder_uid_root() -> bool:
    """是否仍在使用占位 UID 根（启动时据此告警）"""
    return DICOM_UID_ROOT == _PLACEHOLDER_ROOT


# MR Image Storage / PET Image Storage / Secondary Capture
_SOP_CLASS_BY_MODALITY = {
    "MR": "1.2.840.10008.5.1.4.1.1.4",
    "PT": "1.2.840.10008.5.1.4.1.1.128",
    "NM": "1.2.840.10008.5.1.4.1.1.20",
}


def _dicom_uid(*parts: str) -> str:
    """
    由病例/序列标识确定性推导 DICOM UID。

    用 SHA-1（截断）而非随机数：保证同一实体每次生成相同 UID，
    这是外部系统能稳定引用同一检查的前提。

    长度控制：DICOM UID 总长上限 64 字符。逐段拼接 8 位十六进制转十进制
    （每段 ≤10 位十进制），拼不下就停止，确保绝不超限——
    长 UID 会被 dcm4chee/Orthanc 等归档系统直接拒绝。
    """
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    uid = DICOM_UID_ROOT
    for i in range(0, 24, 8):
        component = str(int(digest[i:i + 8], 16))
        if len(uid) + 1 + len(component) > 64:
            break
        uid += "." + component
    return uid


# ------------------------------------------------------------------ DICOM JSON 构造
def _tag(vr: str, value=None) -> dict:
    """构造 DICOM JSON 元素；空值返回 {vr}（标准规定的空元素表示）"""
    if value is None or value == "" or value == []:
        return {"vr": vr}
    if not isinstance(value, list):
        value = [value]
    return {"vr": vr, "Value": value}


def _pn_tag(vr: str, name: Optional[str]) -> dict:
    """PatientName 用 PN VR，值为 {Alphabetic: ...} 结构"""
    if not name:
        return {"vr": vr}
    return {"vr": vr, "Value": [{"Alphabetic": str(name)}]}


def _meta(case: Any) -> dict:
    """安全解析 case.dicom_meta"""
    import json
    raw = getattr(case, "dicom_meta", None) or "{}"
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _patient_of(case: Any) -> dict:
    import json
    try:
        d = json.loads(getattr(case, "patient_json", "{}") or "{}")
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _modality_codes(case: Any) -> list[str]:
    """内部 modality 字符串 → DICOM modality code 列表"""
    m = (case.modality or "").upper()
    if m == "MRI+PET":
        return ["MR", "PT"]
    if m == "MRI":
        return ["MR"]
    if m == "PET":
        return ["PT"]
    return ["OT"]


def _study_date_time(meta: dict, case: Any) -> tuple[Optional[str], Optional[str]]:
    """优先取真实 DICOM 标签，回退到病例检查日期"""
    date = meta.get("studyDate")
    time = meta.get("studyTime")
    if not date:
        raw = (case.exam_date or "").strip()
        date = raw[:10].replace("-", "") or None
        if " " in raw:
            time = time or raw[11:].replace(":", "")[:6]
    if date:
        date = str(date).replace("-", "")
    if time:
        time = str(time).split(".")[0].replace(":", "")
    return date, time


# ------------------------------------------------------------------ QIDO-RS
def study_resource(case: Any) -> dict:
    """病例 → QIDO-RS study 资源（DICOM JSON）"""
    meta = _meta(case)
    patient = _patient_of(case)
    codes = _modality_codes(case)
    date, time = _study_date_time(meta, case)

    return {
        "0020000D": _tag("UI", _dicom_uid("study", case.id)),
        "00080020": _tag("DA", date),
        "00080030": _tag("TM", time),
        "00081030": _tag("LO", f"AD 多模态筛查（{case.modality}）"),
        "00080050": _tag("SH", case.id),  # AccessionNumber 用病例号，便于院内对照
        "00100010": _pn_tag("PN", patient.get("name") or meta.get("patientName")),
        "00100020": _tag("LO", patient.get("patientNo") or meta.get("patientId") or case.id),
        "00080061": _tag("CS", codes),  # ModalitiesInStudy
        "00201206": _tag("IS", len(codes)),  # NumberOfStudyRelatedSeries
        "00201208": _tag("IS", len(codes)),  # NumberOfStudyRelatedInstances（按序列数估）
        "00080080": _tag("LO", getattr(case, "department", "") or None),  # InstitutionName
    }


def series_resource(case: Any, modality_code: str, series_no: int) -> dict:
    """病例 + 模态 → QIDO-RS series 资源"""
    meta = _meta(case)
    return {
        "0020000D": _tag("UI", _dicom_uid("study", case.id)),
        "0020000E": _tag("UI", _dicom_uid("series", case.id, modality_code)),
        "00200011": _tag("IS", series_no),
        "00080060": _tag("CS", modality_code),
        "0008103E": _tag("LO", meta.get("seriesDescription") or f"{modality_code} 序列"),
        "00201209": _tag("IS", 1),
        "00081070": _tag("PN", meta.get("operatorName")),  # Operators'Name
        "00180021": _tag("CS", meta.get("sequenceVariant")),  # SequenceVariant
    }


def instance_resource(case: Any, modality_code: str, instance_no: int = 1) -> dict:
    """病例 + 模态 → WADO-RS instance metadata（不含像素 BulkData）"""
    meta = _meta(case)
    spacing = meta.get("pixelSpacing")
    sop_class = _SOP_CLASS_BY_MODALITY.get(modality_code, "1.2.840.10008.5.1.4.1.1.7")

    resource = {
        "00080018": _tag("UI", _dicom_uid("instance", case.id, modality_code, str(instance_no))),
        "00080016": _tag("UI", sop_class),
        "0020000D": _tag("UI", _dicom_uid("study", case.id)),
        "0020000E": _tag("UI", _dicom_uid("series", case.id, modality_code)),
        "00200013": _tag("IS", instance_no),
        "00080060": _tag("CS", modality_code),
        "00180050": _tag("DS", meta.get("sliceThickness")),
    }
    if isinstance(spacing, list) and len(spacing) >= 2:
        resource["00280030"] = _tag("DS", [spacing[0], spacing[1]])  # PixelSpacing
    if meta.get("windowCenter") is not None:
        resource["00281050"] = _tag("DS", meta.get("windowCenter"))  # WindowCenter
    if meta.get("windowWidth") is not None:
        resource["00281051"] = _tag("DS", meta.get("windowWidth"))  # WindowWidth
    if meta.get("fieldStrength") is not None:
        resource["00180087"] = _tag("DS", meta.get("fieldStrength"))  # MagneticFieldStrength
    if meta.get("manufacturer"):
        resource["00080070"] = _tag("LO", meta.get("manufacturer"))  # Manufacturer
    return resource


def flatten_for_qido(resource: dict) -> dict:
    """
    QIDO-RS 响应按惯例展开为 {Tag: {vr, Value}} 形式；
    本实现已按该结构构造，此处保留为显式出口，便于后续加字段过滤（includefield）。
    """
    return dict(resource)
