"""
数据脱敏审计与导出合规路由
- POST /privacy/audit   导出前 PHI 扫描 + k-匿名检查 + 合规评级
- POST /privacy/mask    对选中病例应用脱敏规则，返回脱敏后的 patient_json

医疗数据导出合规：
  - PHI（Protected Health Information）字段需脱敏：姓名 / 身份证号 / 电话 / 住址 / 患者编号 / 出生日期
  - k-匿名（k-anonymity）：按准标识符（性别 / 年龄段 / 队列）分组，每组病例数 ≥ k 才满足，
    防止去标识后被重识别
"""
import json
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from models.case import CaseRecord
from models.annotation import AnnotationRecord
from services.auth import get_current_user_qs

router = APIRouter(
    prefix="/privacy",
    tags=["数据脱敏审计"],
    dependencies=[Depends(get_current_user_qs)],
)

# k-匿名阈值：每个分组（性别 × 年龄段 × 队列）病例数 ≥ k 才满足
K_ANONYMITY = 5

# PHI 字段配置：字段键候选列表 / 中文标签 / 建议动作
# 字段键按候选优先级排列，patient_json 中可能使用任一键名（驼峰 / 下划线）
PHI_FIELDS = [
    {"keys": ["name"], "label": "患者姓名", "action": "mask", "actionLabel": "掩码"},
    {"keys": ["idcard", "id_card", "idCard"], "label": "身份证号", "action": "drop", "actionLabel": "剔除"},
    {"keys": ["phone", "mobile", "telephone"], "label": "电话", "action": "mask", "actionLabel": "掩码"},
    {"keys": ["address"], "label": "住址", "action": "mask", "actionLabel": "掩码"},
    {"keys": ["patientNo", "patient_no"], "label": "患者编号", "action": "mask", "actionLabel": "部分掩码"},
    {"keys": ["birthdate", "birthday", "birth"], "label": "出生日期", "action": "mask", "actionLabel": "仅保留年份"},
]


# ---------- 请求体 ----------

class AuditBody(BaseModel):
    """脱敏审计请求体"""
    # PHI 批量操作上限 500，防止超大 IN 子句拖库
    caseIds: list[str] = Field(max_length=500)
    includeAnnotations: bool = False
    includeImaging: bool = False


class MaskBody(BaseModel):
    """应用脱敏规则请求体"""
    caseIds: list[str] = Field(max_length=500)
    # 指定要脱敏的字段键集合，空=全部 PHI 命中字段
    fields: list[str] = Field(default_factory=list, max_length=50)


# ---------- 脱敏规则 ----------

def _mask_name(name: str) -> str:
    """姓名脱敏：保留首字，其余替换为 *（张三 → 张*）"""
    if not name or len(name) <= 1:
        return "*"
    return name[0] + "*" * (len(name) - 1)


def _mask_phone(phone: str) -> str:
    """电话脱敏：保留前3后4，中间 4 位掩码（13812341234 → 138****1234）"""
    if not phone or len(phone) < 7:
        return "****"
    return phone[:3] + "****" + phone[-4:]


def _mask_idcard(idcard: str) -> str:
    """身份证脱敏：保留前6位，其余掩码（110101199001011234 → 110101************）"""
    if not idcard or len(idcard) < 6:
        return "****"
    return idcard[:6] + "*" * (len(idcard) - 6)


def _mask_address(addr: str) -> str:
    """住址脱敏：仅保留前 6 字符（行政区域），其余掩码（剔除门牌号）"""
    if not addr:
        return ""
    if len(addr) <= 6:
        return "*" * len(addr)
    return addr[:6] + "*" * (len(addr) - 6)


def _mask_patient_no(no: str) -> str:
    """患者编号脱敏：保留前2后2，中间掩码（ADNI_001 → AD*****01）"""
    if not no or len(no) <= 4:
        return "****"
    return no[:2] + "*" * (len(no) - 4) + no[-2:]


def _mask_birthdate(bd: str) -> str:
    """出生日期脱敏：仅保留年份（1990-01-01 → 1990）"""
    if not bd:
        return ""
    year = bd.strip()[:4]
    return year if (year.isdigit() and len(year) == 4) else "****"


# 字段键 → 脱敏函数 映射
_MASKERS = {
    "name": _mask_name,
    "idcard": _mask_idcard,
    "id_card": _mask_idcard,
    "idCard": _mask_idcard,
    "phone": _mask_phone,
    "mobile": _mask_phone,
    "telephone": _mask_phone,
    "address": _mask_address,
    "patientNo": _mask_patient_no,
    "patient_no": _mask_patient_no,
    "birthdate": _mask_birthdate,
    "birthday": _mask_birthdate,
    "birth": _mask_birthdate,
}


# ---------- 工具函数 ----------

def _parse_patient(patient_json: str) -> dict:
    """安全解析 patient_json，异常返回空 dict 防止单条脏数据拖垮整批"""
    try:
        return json.loads(patient_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _first_present_key(patient: dict, keys: list[str]) -> Optional[str]:
    """返回 patient_json 中首个非空命中的字段键，否则 None"""
    for k in keys:
        v = patient.get(k)
        if v not in (None, "", [], {}):
            return k
    return None


def _age_to_group(age: Any) -> str:
    """年龄转年龄段：<60 / 60-69 / 70-79 / 80+ / 未知"""
    if age is None or age == "":
        return "未知"
    try:
        a = int(age)
    except (TypeError, ValueError):
        return "未知"
    if a < 60:
        return "<60"
    if a < 70:
        return "60-69"
    if a < 80:
        return "70-79"
    return "80+"


def _gender_label(gender: Any) -> str:
    """性别归一化为 男/女/未知"""
    g = str(gender or "").strip().upper()
    if g in ("M", "MALE", "男"):
        return "男"
    if g in ("F", "FEMALE", "女"):
        return "女"
    return "未知"


def _cohort_label(cohort: Any) -> str:
    """队列标签归一化，空值统一为 UNKNOWN"""
    return str(cohort or "UNKNOWN").strip() or "UNKNOWN"


def _apply_mask(field_key: str, value: Any) -> str:
    """对单个字段值应用脱敏规则"""
    masker = _MASKERS.get(field_key)
    if masker is None:
        return "*" * (len(str(value)) if value else 1)
    return masker(str(value))


def _mask_patient_json(patient: dict, fields: Optional[list[str]] = None) -> dict:
    """
    对 patient_json 应用脱敏规则。
    fields 指定要脱敏的字段键集合；为 None 则脱敏所有 PHI 字段。
    action=drop 的字段置空（剔除值）；action=mask 的字段按规则掩码。
    返回新的 dict（不修改原数据）。
    """
    masked = dict(patient)  # 浅拷贝，避免污染原数据
    field_set = set(fields) if fields else None
    for phi in PHI_FIELDS:
        for k in phi["keys"]:
            if k in masked and masked[k] not in (None, "", [], {}):
                if field_set is not None and k not in field_set:
                    continue
                if phi["action"] == "drop":
                    masked[k] = ""
                else:
                    masked[k] = _apply_mask(k, masked[k])
    return masked


def _build_sample(patient: dict) -> tuple[dict, dict]:
    """
    生成脱敏样例对比：仅保留 PHI 字段 + gender/age 上下文字段，
    返回 (原始子集, 脱敏后子集)。age 在样例中泛化为年龄段以体现 k-匿名辅助效果。
    """
    keys_flat = [k for phi in PHI_FIELDS for k in phi["keys"]]
    original = {k: patient.get(k) for k in keys_flat if k in patient}
    # 无任何 PHI 字段时回退到通用展示字段，便于对比仍可见
    if not original:
        for k in ("patientNo", "name", "gender", "age", "phone"):
            if k in patient:
                original[k] = patient.get(k)
    masked = _mask_patient_json(original, None)
    # 年龄做泛化（k-匿名辅助）：原值替换为年龄段
    if "age" in original:
        masked["age"] = _age_to_group(original.get("age"))
    return original, masked


# ---------- 端点 ----------

@router.post("/audit")
def audit_export(body: AuditBody, db: Session = Depends(get_db)):
    """
    导出前脱敏审计：
      1. 查询选中病例（未软删除）
      2. 扫描 patient_json 的 PHI 字段命中情况
      3. k-匿名检查（按性别 × 年龄段 × 队列 分组）
      4. 综合合规评级（green / yellow / red）
      5. 给出原始 vs 脱敏后的样例对比
    """
    case_ids = body.caseIds or []
    if not case_ids:
        return fail("请选择至少 1 例病例", 400)

    cases = (
        db.query(CaseRecord)
        .filter(CaseRecord.id.in_(case_ids), CaseRecord.is_deleted.is_(False))
        .all()
    )
    if not cases:
        return fail("所选病例不存在或已删除", 404)

    # ---------- PHI 字段命中统计 ----------
    by_field: list[dict] = []
    total_hits = 0

    for phi in PHI_FIELDS:
        hits = 0
        sample_value = ""
        for c in cases:
            p = _parse_patient(c.patient_json)
            key = _first_present_key(p, phi["keys"])
            if key:
                hits += 1
                total_hits += 1
                if not sample_value:
                    sample_value = str(p.get(key, ""))
        # 样例脱敏值：drop 动作置空，mask 动作应用脱敏规则
        if phi["action"] == "drop":
            sample_masked = ""
        else:
            sample_masked = _apply_mask(phi["keys"][0], sample_value) if sample_value else ""
        by_field.append({
            "field": phi["keys"][0],
            "label": phi["label"],
            "hits": hits,
            "action": phi["action"],
            "actionLabel": phi["actionLabel"],
            "sample": sample_masked,
        })

    # ---------- 标注自由文本 PHI 扫描（可选） ----------
    # 标注 notes / user 可能包含自由文本 PHI，启用时按正则扫描电话/身份证模式
    ann_hits = 0
    if body.includeAnnotations:
        anns = (
            db.query(AnnotationRecord)
            .filter(AnnotationRecord.case_id.in_([c.id for c in cases]))
            .all()
        )
        phone_re = re.compile(r"1[3-9]\d{9}")
        idcard_re = re.compile(r"\d{17}[\dXx]")
        for a in anns:
            text = f"{a.notes or ''} {a.user or ''}"
            if phone_re.search(text) or idcard_re.search(text):
                ann_hits += 1

    # ---------- k-匿名检查 ----------
    # 按 (性别, 年龄段, 队列) 分组统计病例数
    group_map: dict[str, dict] = {}
    for c in cases:
        p = _parse_patient(c.patient_json)
        gender = _gender_label(p.get("gender"))
        age_group = _age_to_group(p.get("age"))
        cohort = _cohort_label(c.cohort)
        key = f"{gender} / {age_group} / {cohort}"
        g = group_map.setdefault(key, {"group": key, "count": 0})
        g["count"] += 1

    groups = []
    violations = []
    for key, g in sorted(group_map.items(), key=lambda x: x[0]):
        satisfied = g["count"] >= K_ANONYMITY
        groups.append({
            "group": g["group"],
            "count": g["count"],
            "satisfied": satisfied,
        })
        if not satisfied:
            violations.append({
                "group": g["group"],
                "count": g["count"],
                "deficit": K_ANONYMITY - g["count"],
            })

    k_satisfied = len(violations) == 0

    # ---------- 综合合规评级 ----------
    # red:   k-匿名违反（去标识后仍可能被重识别）
    # green: 无 PHI 命中 + k-匿名满足
    # yellow: 有 PHI 命中但可脱敏 + k-匿名满足
    if not k_satisfied:
        level = "red"
        level_text = "k-匿名违反，存在重识别风险"
        summary = (
            f"命中 {total_hits} 处 PHI 字段，k-匿名检查未通过"
            f"（{len(violations)} 个分组病例数 < {K_ANONYMITY}），"
            f"去标识后仍可能被重识别，建议从少数分组中移除病例后再导出"
        )
        can_export = False
    elif total_hits == 0:
        level = "green"
        level_text = "可直接导出"
        summary = "未命中 PHI 字段，k-匿名检查通过，可直接导出"
        can_export = True
    else:
        level = "yellow"
        level_text = "需脱敏后可导出"
        summary = (
            f"命中 {total_hits} 处 PHI 字段，建议脱敏后导出；k-匿名检查通过"
        )
        can_export = True

    # ---------- 警告列表 ----------
    warnings: list[str] = []
    for f in by_field:
        if f["hits"] > 0:
            verb = "剔除" if f["action"] == "drop" else "脱敏"
            warnings.append(f"{f['label']}命中 {f['hits']} 例，导出前请确认已{verb}")
    if ann_hits > 0:
        warnings.append(
            f"标注自由文本中检测到 {ann_hits} 条疑似 PHI（电话/身份证），请人工复核"
        )
    if body.includeImaging:
        warnings.append(
            "已开启影像路径导出，文件名可能包含患者标识，请复核 mri_path / pet_path"
        )
    if not k_satisfied:
        for v in violations:
            warnings.append(
                f"k-匿名违反：分组「{v['group']}」仅 {v['count']} 例"
                f"（差 {v['deficit']} 例），建议移除该分组病例后再导出"
            )

    # ---------- 脱敏样例对比 ----------
    # 选首个含 PHI 命中的病例做对比；全部无命中则取第一例展示"无需脱敏"
    sample_case_obj = None
    sample_patient = {}
    for c in cases:
        p = _parse_patient(c.patient_json)
        if any(_first_present_key(p, phi["keys"]) for phi in PHI_FIELDS):
            sample_case_obj = c
            sample_patient = p
            break
    if sample_case_obj is None:
        sample_case_obj = cases[0]
        sample_patient = _parse_patient(cases[0].patient_json)

    original_sample, masked_sample = _build_sample(sample_patient)

    return ok({
        "totalCases": len(cases),
        "phiScan": {
            "totalHits": total_hits,
            "byField": by_field,
            "annotationHits": ann_hits,
        },
        "kAnonymity": {
            "k": K_ANONYMITY,
            "satisfied": k_satisfied,
            "quasiIdentifiers": ["性别", "年龄段", "队列"],
            "groups": groups,
            "violations": violations,
        },
        "compliance": {
            "level": level,
            "levelText": level_text,
            "summary": summary,
            "canExport": can_export,
            "warnings": warnings,
        },
        "sampleCase": {
            "caseId": sample_case_obj.id,
            "original": original_sample,
            "masked": masked_sample,
        },
        "options": {
            "includeAnnotations": body.includeAnnotations,
            "includeImaging": body.includeImaging,
        },
    })


@router.post("/mask")
def mask_cases(body: MaskBody, db: Session = Depends(get_db)):
    """
    对选中病例应用脱敏规则，返回脱敏后的 patient_json 列表。
    用于实际导出时应用脱敏：fields 指定要脱敏的字段键集合，空=全部 PHI 命中字段。
    """
    case_ids = body.caseIds or []
    if not case_ids:
        return fail("请选择至少 1 例病例", 400)

    cases = (
        db.query(CaseRecord)
        .filter(CaseRecord.id.in_(case_ids), CaseRecord.is_deleted.is_(False))
        .all()
    )
    if not cases:
        return fail("所选病例不存在或已删除", 404)

    masked_list = []
    for c in cases:
        p = _parse_patient(c.patient_json)
        masked_p = _mask_patient_json(p, body.fields or None)
        masked_list.append({
            "caseId": c.id,
            "patientJson": masked_p,
        })

    return ok({
        "masked": masked_list,
        "totalMasked": len(masked_list),
    })
