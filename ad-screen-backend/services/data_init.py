"""
数据初始化服务
- 首次启动时填充演示数据（复刻前端 Mock db.ts 逻辑）
- 账号体系、病例库、模型配置、日志
"""
import json
import os
import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from config import SEED_DEMO_DATA, ADMIN_PASSWORD
from database import SessionLocal
from models.user import User as UserModel, RolePermission
from models.case import CaseRecord
from models.model_config import ModelConfigRecord
from models.log import InferenceLog, SystemLog, CaseLog
from models.analysis import FollowUpRecord, FollowUpVisit
from services.auth import hash_password
from services.utils import mulberry32, format_date_time, score_to_risk_level
from dateutil.relativedelta import relativedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------- 演示账号 ----------
DEMO_ACCOUNTS = [
    {"username": "rad01", "password": "123456", "role": "radiologist", "roleName": "放射科医师", "realName": "王建国", "department": "放射科"},
    {"username": "neu01", "password": "123456", "role": "neurologist", "roleName": "神经内科医师", "realName": "李雨薇", "department": "神经内科"},
    {"username": "sci01", "password": "123456", "role": "researcher", "roleName": "科研管理员", "realName": "张明远", "department": "科研部"},
    {"username": "admin", "password": "admin123", "role": "admin", "roleName": "超级管理员", "realName": "系统管理员", "department": "信息科"},
]

ROLE_PERMISSIONS = [
    {"role": "radiologist", "roleName": "放射科医师", "permissionKeys": ["dashboard", "cases", "viewer", "analysis", "report"]},
    {"role": "neurologist", "roleName": "神经内科医师", "permissionKeys": ["dashboard", "cases", "viewer", "analysis", "report"]},
    {"role": "researcher", "roleName": "科研管理员", "permissionKeys": ["dashboard", "cases", "model"]},
    {"role": "admin", "roleName": "超级管理员", "permissionKeys": ["dashboard", "cases", "viewer", "analysis", "report", "model", "system"]},
]

ALL_PERMISSIONS = [
    {"key": "dashboard", "label": "工作台仪表盘", "description": "筛查总览与任务统计"},
    {"key": "cases", "label": "病例库管理", "description": "病例检索、影像上传与批量导入"},
    {"key": "viewer", "label": "阅片与AI分析", "description": "多模态阅片、ROI 标注、AI 推理"},
    {"key": "analysis", "label": "AI详情与干预方案", "description": "量化结果查看、干预方案编辑"},
    {"key": "report", "label": "筛查报告", "description": "报告预览、模板切换与导出"},
    {"key": "model", "label": "模型科研配置", "description": "融合策略、阈值与性能监控"},
    {"key": "system", "label": "系统权限管理", "description": "账号、角色与审计日志"},
]

# ---------- 病例生成 ----------
SURNAMES = ["王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴", "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗"]
GIVEN = ["建国", "淑兰", "桂英", "国强", "秀珍", "德华", "凤兰", "忠义", "玉梅", "福贵", "秀英", "文华", "桂香", "永强", "素芬", "长顺", "玉兰", "志明", "淑珍", "广田"]
DEPTS = ["神经内科", "放射科", "记忆门诊", "老年医学科"]

DEFAULT_MODEL_CONFIG = {
    "strategy": "feature",
    "mriWeight": 0.55,
    "riskThreshold": 0.8,
    "roiRegions": ["海马", "颞叶皮层", "内嗅皮层", "后扣带回"],
    "snapshotVersion": "TransMF-15ens-v4",
    "updatedAt": "2026-09-02 15:20:00",
    "updatedBy": "sci01",
}


def _make_case(i: int) -> dict:
    """生成单个病例（与前端 Mock 逻辑一致）"""
    rand = mulberry32(20260901 + i * 7)
    gender = "M" if next(rand) > 0.48 else "F"
    age = 60 + int(next(rand) * 29)
    mr = next(rand) > 0.08
    pet = next(rand) > 0.18
    modality = "MRI+PET" if mr and pet else ("MRI" if mr else "PET")

    exam_dt = datetime.now() - timedelta(days=int(next(rand) * 180)) - timedelta(hours=int(next(rand) * 8))
    exam_date = format_date_time(exam_dt)

    # 风险分布
    r = next(rand)
    if r < 0.45:
        risk_score = 12 + int(next(rand) * 22)
    elif r < 0.73:
        risk_score = 38 + int(next(rand) * 22)
    elif r < 0.91:
        risk_score = 62 + int(next(rand) * 18)
    else:
        risk_score = 82 + int(next(rand) * 17)

    has_result = r > 0.16
    risk_level = score_to_risk_level(risk_score) if has_result else None

    status = "completed"
    diag_status = "AI已分析"
    if not has_result:
        status = "pending"
        diag_status = "待AI分析"
    elif r > 0.94:
        status = "reported"
        diag_status = "已出报告"
    elif r > 0.86:
        diag_status = "医生已审核"

    case = {
        "id": f"AD{260001 + i}",
        "patient": {
            "patientNo": f"P{20260101 + i * 3}",
            "name": SURNAMES[int(next(rand) * len(SURNAMES))] + GIVEN[int(next(rand) * len(GIVEN))],
            "gender": gender,
            "age": age,
        },
        "modality": modality,
        "examDate": exam_date,
        "department": DEPTS[int(next(rand) * len(DEPTS))],
        "status": status,
        "diagStatus": diag_status,
        "riskLevel": risk_level,
        "riskScore": float(risk_score) if has_result else None,
        "hasMRI": mr,
        "hasPET": pet,
        "createTime": format_date_time(exam_dt + timedelta(seconds=1800)),
        "cloudSaved": False,
    }
    return case


def _serialize_case(c: dict) -> dict:
    """将嵌套 dict 扁平化为 ORM 字段"""
    return {
        "id": c["id"],
        "patient_json": json.dumps(c["patient"], ensure_ascii=False),
        "modality": c["modality"],
        "exam_date": c["examDate"],
        "department": c["department"],
        "status": c["status"],
        "diag_status": c["diagStatus"],
        "risk_level": c["riskLevel"],
        "risk_score": c["riskScore"],
        "has_mri": c["hasMRI"],
        "has_pet": c["hasPET"],
        "create_time": c["createTime"],
        "cloud_saved": c["cloudSaved"],
        "mri_path": c.get("mriPath", ""),
        "pet_path": c.get("petPath", ""),
        # 2026 版 PET/MRI 指南对齐字段（演示病例默认无禁忌证 + 普通人群）
        "exam_indication": c.get("examIndication", "diagnosis"),
        "pet_tracer": c.get("petTracer", "fdg"),
        "contraindications": json.dumps(
            c.get("contraindications")
            or {"absolute": [], "relative": [], "cleared": True, "warnings": []},
            ensure_ascii=False,
        ),
        "special_population": c.get("specialPopulation", "normal"),
    }


def case_record_to_dict(rec) -> dict:
    """ORM CaseRecord → 前端 CaseRecord dict（附加 mriPath/petPath 供真实推理使用）"""
    return {
        "id": rec.id,
        "patient": json.loads(rec.patient_json),
        "modality": rec.modality,
        "examDate": rec.exam_date,
        "department": rec.department,
        "status": rec.status,
        "diagStatus": rec.diag_status,
        "riskLevel": rec.risk_level,
        "riskScore": rec.risk_score,
        "hasMRI": rec.has_mri,
        "hasPET": rec.has_pet,
        "createTime": rec.create_time,
        "cloudSaved": rec.cloud_saved,
        # 供真实 TransMF 模型推理使用
        "mriPath": rec.mri_path or "",
        "petPath": rec.pet_path or "",
        # 2026 版 PET/MRI 指南对齐字段
        "examIndication": getattr(rec, "exam_indication", "diagnosis") or "diagnosis",
        "petTracer": getattr(rec, "pet_tracer", "fdg") or "fdg",
        "contraindications": json.loads(getattr(rec, "contraindications", "{}") or "{}"),
        "specialPopulation": getattr(rec, "special_population", "normal") or "normal",
    }


def _seed_followups(db: Session, cases: list[dict]):
    """
    为已分析病例填充随访管理演示数据：
    - 风险越高随访周期越短（AD 3 月 / MCI 6 月 / 低风险 12 月）
    - 下次随访日按确定性序列分布在逾期 / 7 天内 / 30 天内 / 未来
    - 约 55% 病例带 1-2 次历史随访（含 MMSE 变化趋势）
    """
    today = datetime.now().date()
    # 距今天数序列：覆盖逾期、临期、正常未来，确定性轮转
    offset_seq = [-38, -15, -6, -2, 0, 4, 12, 26, 45, 78, 110, 160]
    cycle_map = {"ad-late": 3, "ad-early": 3, "mci": 6, "low": 12}
    change_for_level = {
        "ad-late": ["轻度下降", "明显下降", "稳定"],
        "ad-early": ["轻度下降", "稳定", "轻度下降"],
        "mci": ["稳定", "轻度下降", "稳定"],
        "low": ["稳定", "改善", "稳定"],
    }
    rand_global = mulberry32(77701)
    seq_idx = 0

    for c in cases:
        if c["riskScore"] is None:
            continue
        level = c["riskLevel"]
        cycle = cycle_map.get(level, 6)
        offset_days = offset_seq[seq_idx % len(offset_seq)]
        seq_idx += 1
        next_d = today + timedelta(days=offset_days)
        reminder_d = next_d - timedelta(days=6)
        next_str = next_d.strftime("%Y-%m-%d")
        plan = {
            "cycleMonths": cycle,
            "nextDate": next_str,
            "reminders": [next_str, reminder_d.strftime("%Y-%m-%d")],
            "note": "随访内容：认知量表复评 + 头颅 MRI 海马定量" + (" + PET 代谢复查" if level in ("ad-late", "ad-early") else ""),
        }
        db.add(FollowUpRecord(
            case_id=c["id"],
            plan_json=json.dumps(plan, ensure_ascii=False),
        ))

        # 历史随访记录（约 55% 病例）
        if next(rand_global) > 0.45:
            changes = change_for_level.get(level, ["稳定"])
            visit_n = 1 if next(rand_global) > 0.35 else 2
            base_mmse = max(14, min(28, round(32 - float(c["riskScore"]) * 0.16, 1)))
            decline_map = {"明显下降": 4.0, "轻度下降": 2.0, "稳定": 0.0, "改善": -2.0}
            prev_d = next_d
            for k in range(visit_n):
                visit_d = prev_d - relativedelta(months=cycle)
                change = changes[(k + seq_idx) % len(changes)]
                # k=0 为最近一次历史随访；越早的随访分数按变化方向回调，形成趋势
                mmse = round(min(30.0, max(10.0, base_mmse + k * decline_map.get(change, 0.0))), 1)
                db.add(FollowUpVisit(
                    case_id=c["id"],
                    patient_name=c["patient"]["name"],
                    visit_date=visit_d.strftime("%Y-%m-%d"),
                    visit_type=["门诊复诊", "电话随访", "影像复查"][(seq_idx + k) % 3],
                    mmse=mmse,
                    moca=min(30.0, max(9.0, mmse - 2.0)),
                    cognition_change=change,
                    medication_adherence=["规律", "偶有漏服", "规律"][(seq_idx + k) % 3],
                    adverse_event="无" if (seq_idx + k) % 4 else "轻微",
                    notes="规律服药，家属反馈认知训练配合良好" if change in ("稳定", "改善") else "家属诉近期记忆力下降，调整用药方案",
                    next_date=prev_d.strftime("%Y-%m-%d"),
                    operator=["王建国", "李雨薇"][(seq_idx + k) % 2],
                    created_at=f"{visit_d.strftime('%Y-%m-%d')} 10:{15 + k * 7:02d}:00",
                ))
                prev_d = visit_d


def init_demo_data():
    """首次启动时填充演示数据"""
    db = SessionLocal()
    try:
        # 检查是否已初始化
        if db.query(UserModel).count() > 0:
            return

        # ---------- 账号 ----------
        # 开关优先：SEED_DEMO_DATA=0 时不播种固定口令演示账号（admin/admin123 等同匿名后门）
        # 生产环境默认关闭（见 config.SEED_DEMO_DATA），由 flag 而非隐式 APP_ENV 判断，便于测试与显式配置
        if not SEED_DEMO_DATA:
            # 仅引导一个超级管理员，账号/口令取环境变量；未给口令则随机生成并在启动日志输出一次。
            admin_username = os.getenv("ADSCREEN_ADMIN_USERNAME", "admin").strip() or "admin"
            admin_password = ADMIN_PASSWORD or os.getenv("ADSCREEN_ADMIN_PASSWORD", "").strip()
            generated_password = False
            if not admin_password:
                admin_password = secrets.token_urlsafe(12)
                generated_password = True
            db.add(UserModel(
                username=admin_username,
                password_hash=hash_password(admin_password),
                real_name="系统管理员",
                role="admin",
                role_name="超级管理员",
                department="信息科",
                phone="",
                status=1,
                create_time=format_date_time(datetime.now()),
                last_login_time="—",
            ))
            if generated_password:
                print(
                    f"[数据初始化][安全] 未检测到 ADSCREEN_ADMIN_PASSWORD，"
                    f"已为超管 {admin_username} 生成随机初始密码：{admin_password}"
                    "（仅显示一次，请立即登录并修改密码）"
                )
            else:
                print(f"[数据初始化] 引导超管账号：{admin_username}（口令来自 ADSCREEN_ADMIN_PASSWORD）")
        else:
            for i, acc in enumerate(DEMO_ACCOUNTS):
                db.add(UserModel(
                    username=acc["username"],
                    password_hash=hash_password(acc["password"]),
                    real_name=acc["realName"],
                    role=acc["role"],
                    role_name=acc["roleName"],
                    department=acc["department"],
                    phone=f"138{str(10000000 + i * 111111)[:8]}",
                    status=1,
                    create_time="2025-06-01 09:00:00",
                    last_login_time=format_date_time(datetime.now() - timedelta(hours=i + 1)),
                ))

        # ---------- 角色权限 ----------
        for rp in ROLE_PERMISSIONS:
            db.add(RolePermission(
                role=rp["role"],
                role_name=rp["roleName"],
                permission_keys=json.dumps(rp["permissionKeys"], ensure_ascii=False),
            ))

        # ---------- 模型配置 ----------
        db.add(ModelConfigRecord(
            id=1,
            strategy=DEFAULT_MODEL_CONFIG["strategy"],
            mri_weight=DEFAULT_MODEL_CONFIG["mriWeight"],
            risk_threshold=DEFAULT_MODEL_CONFIG["riskThreshold"],
            roi_regions=json.dumps(DEFAULT_MODEL_CONFIG["roiRegions"], ensure_ascii=False),
            snapshot_version=DEFAULT_MODEL_CONFIG["snapshotVersion"],
            updated_at=DEFAULT_MODEL_CONFIG["updatedAt"],
            updated_by=DEFAULT_MODEL_CONFIG["updatedBy"],
        ))

        # 不播种演示病例/推理日志/审计日志/随访（假 PHI 与假审计记录不得进入正式库）
        if not SEED_DEMO_DATA:
            db.commit()
            print("[数据初始化] 演示数据已关闭：仅初始化超管账号 / 角色权限 / 默认模型配置")
            return

        # ---------- 病例库（47 例） ----------
        # 前 5 例关联真实 ADNI NIfTI 影像路径（支持真实 TransMF 推理）
        _nifti_root = os.path.join(_PROJECT_ROOT, "datasets", "MRI PET图像")
        _real_niftis = [
            "sub-ADNI002S4171",
            "sub-ADNI002S4213",
            "sub-ADNI002S4225",
            "sub-ADNI002S4262",
            "sub-ADNI002S4270",
        ]
        cases = []
        for i in range(47):
            c = _make_case(i)
            # 建档时直接挂载真实影像路径（autoflush=False，无法在 commit 前回查，故在此写入）
            if i < len(_real_niftis):
                subj = _real_niftis[i]
                mri_p = os.path.join(_nifti_root, "MRI", f"{subj}.nii")
                pet_p = os.path.join(_nifti_root, "PET", f"{subj}.nii")
                if os.path.isfile(mri_p) and os.path.isfile(pet_p):
                    c["mriPath"] = mri_p
                    c["petPath"] = pet_p
                    c["hasMRI"] = True
                    c["hasPET"] = True
                    c["modality"] = "MRI+PET"
                    print(f"[数据初始化] 病例 {c['id']} 关联真实 NIfTI: {subj}")
            cases.append(c)
            db.add(CaseRecord(**_serialize_case(c)))

        # ---------- 推理日志 ----------
        case_idx = 0
        for i, c in enumerate(cases):
            if c["riskScore"] is None:
                continue
            if i >= 26:
                break
            db.add(InferenceLog(
                id=f"INF{90210 + case_idx}",
                case_id=c["id"],
                patient_name=c["patient"]["name"],
                model_version=DEFAULT_MODEL_CONFIG["snapshotVersion"],
                strategy=DEFAULT_MODEL_CONFIG["strategy"],
                duration_sec=round(2.4 + next(mulberry32(i)) * 1.8, 1),
                risk_score=float(c["riskScore"]),
                status="失败" if case_idx == 17 else "成功",
                operator=["rad01", "neu01", "sci01"][case_idx % 3],
                time=c["examDate"],
            ))
            case_idx += 1

        # ---------- 系统日志 ----------
        sys_logs = [
            {"id": "S1", "module": "系统管理", "action": "新增用户", "operator": "admin", "role": "超级管理员", "ip": "192.168.3.21", "result": "成功", "time": "2026-09-06 10:12:33", "detail": "新增账号 neu02（神经内科医师）"},
            {"id": "S2", "module": "模型配置", "action": "修改风险阈值", "operator": "sci01", "role": "科研管理员", "ip": "192.168.3.45", "result": "成功", "time": "2026-09-06 09:02:11", "detail": "高风险阈值 0.75 → 0.80"},
            {"id": "S3", "module": "报告管理", "action": "导出报告 PDF", "operator": "rad01", "role": "放射科医师", "ip": "192.168.3.88", "result": "成功", "time": "2026-09-05 16:44:02", "detail": "病例 AD260008 报告导出"},
            {"id": "S4", "module": "系统管理", "action": "禁用用户", "operator": "admin", "role": "超级管理员", "ip": "192.168.3.21", "result": "成功", "time": "2026-09-04 11:20:47", "detail": "禁用账号 rad03"},
            {"id": "S5", "module": "认证登录", "action": "用户登录", "operator": "neu01", "role": "神经内科医师", "ip": "192.168.3.102", "result": "失败", "time": "2026-09-04 08:55:19", "detail": "密码错误连续 2 次"},
        ]
        for sl in sys_logs:
            db.add(SystemLog(**sl))

        # ---------- 病例操作日志 ----------
        actions = ["上传 MRI/PET 影像", "启动 AI 分析", "保存 AI 分析结果", "生成筛查报告", "编辑干预方案"]
        operators = ["rad01", "neu01", "rad01", "rad01", "neu01"]
        for i, c in enumerate(cases[:14]):
            action = actions[i % 5]
            db.add(CaseLog(
                id=f"CL{51001 + i}",
                case_id=c["id"],
                patient_name=c["patient"]["name"],
                action=action,
                operator=operators[i % 5],
                time=c["examDate"],
                detail=f"病例 {c['id']}（{c['patient']['name']}）由 {operators[i % 5]} 完成【{action}】",
            ))

        # ---------- 随访管理演示数据（计划 + 历史随访） ----------
        _seed_followups(db, cases)

        db.commit()
        print("[数据初始化] 演示数据填充完成：4 账号 / 47 病例 / 随访数据 / 日志")
    except Exception as e:
        db.rollback()
        print(f"[数据初始化] 失败: {e}")
        raise
    finally:
        db.close()
