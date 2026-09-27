"""
认证路由
- POST /auth/login：登录（验证码 + JWT）
- POST /auth/register：用户注册（验证码 + 唯一性校验，默认 status=2 待审核）
- POST /system/users/review：管理员审核注册账号（approve=true 通过/拒绝，写审计日志）
- GET /auth/captcha：获取验证码（返回 captchaKey + code，前端 canvas 绘制）
- GET /auth/demo-accounts：演示账号列表
- GET /system/users：用户列表
- POST /system/users：新增用户
- POST /system/users/status：启用/禁用
- GET /system/role-permissions：角色权限配置
- POST /system/role-permissions：保存角色权限
- GET /system/permissions：全部权限节点
"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas.common import ok, fail
from schemas.user import (
    LoginPayload, LoginResult, UserCreate,
    RolePermission, PermissionNode, DemoAccount, ToggleUserPayload,
    RegisterPayload, ReviewUserPayload,
    UserProfile, ProfileUpdate, PasswordChange, ProfileStats, ActivityItem,
)
from models.user import User as UserModel, RolePermission as RolePermissionModel
from models.log import SystemLog as SystemLogModel, InferenceLog as InferenceLogModel
from models.case import CaseRecord as CaseRecordModel
from models.login_log import LoginRecord
from services.auth import (
    verify_captcha, create_access_token, create_media_token,
    MEDIA_TOKEN_EXPIRE_MINUTES, hash_password, verify_password,
    get_current_user_qs, require_role,
    check_login_locked, record_login_fail, clear_login_fails, bump_token_version,
    validate_password,
)
from services.rate_limit import rate_limit
from config import LOGIN_MAX_FAILS, APP_ENV
from services.data_init import DEMO_ACCOUNTS, ROLE_PERMISSIONS, ALL_PERMISSIONS
from services.utils import format_date_time, next_seq_id

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/auth", tags=["认证"])
# 系统管理路由统一要求超级管理员角色（router 级依赖对所有端点生效）
sys_router = APIRouter(
    prefix="/system",
    tags=["系统管理"],
    dependencies=[Depends(require_role("admin"))],
)


def _client_ip(request: Request) -> str:
    """获取客户端真实 IP（兼容反向代理 X-Forwarded-For）"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:50]
    return (request.client.host if request.client else "")[:50]


def _write_login_log(db: Session, username: str, real_name: str, success: int, reason: str, request: Request):
    """落库一条登录日志（失败不阻断登录流程）"""
    try:
        db.add(LoginRecord(
            username=username[:64],
            real_name=(real_name or "")[:64],
            success=success,
            fail_reason=(reason or "")[:100],
            ip=_client_ip(request),
            user_agent=(request.headers.get("user-agent", "") or "")[:300],
            login_at=datetime.now(),
        ))
        db.commit()
    except Exception as e:
        # 审计日志落库失败不应阻断登录，但必须告警（医疗合规要求登录痕迹可追溯）
        logger.warning("登录审计日志写入失败（username=%s）：%s", username, e)
        db.rollback()


@router.post("/login", dependencies=[Depends(rate_limit("login", 30, 60))])
def login(payload: LoginPayload, request: Request, db: Session = Depends(get_db)):
    """登录（验证码 + 失败锁定防爆破：连续失败 N 次锁定 15 分钟）"""
    client_ip = _client_ip(request)

    # 1. 锁定检查优先（锁定期间直接拒绝，不再消耗验证码与查询数据库）
    locked, remain_min = check_login_locked(payload.username)
    if locked:
        _write_login_log(db, payload.username, "", 0, f"账号锁定中（剩余{remain_min}分钟）", request)
        logger.warning("登录被拒：账号 %s 处于锁定状态（IP=%s，剩余 %d 分钟）", payload.username, client_ip, remain_min)
        return fail(f"账号已锁定，请 {remain_min} 分钟后再试（连续失败 {LOGIN_MAX_FAILS} 次触发）", 429)

    # 2. 验证码校验（失败不触发锁定计数，避免与爆破策略混淆）
    if not verify_captcha(payload.captchaKey, payload.captcha):
        _write_login_log(db, payload.username, "", 0, "验证码错误", request)
        return fail("验证码错误或已过期", 400)

    # 3. 账号与密码校验
    user = db.query(UserModel).filter(UserModel.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        _write_login_log(db, payload.username, "", 0, "用户名或密码错误", request)
        fails, just_locked, lock_min = record_login_fail(payload.username, client_ip)
        if just_locked:
            return fail(f"连续登录失败 {fails} 次，账号已锁定 {lock_min} 分钟", 429)
        remain_tries = max(0, LOGIN_MAX_FAILS - fails)
        return fail(f"用户名或密码错误，还可尝试 {remain_tries} 次", 400)
    if user.status == 2:
        _write_login_log(db, payload.username, user.real_name, 0, "账号待审核", request)
        return fail("账号待管理员审核，请耐心等待", 403)
    if user.status == 0:
        _write_login_log(db, payload.username, user.real_name, 0, "账号已禁用", request)
        return fail("账号已被禁用，请联系管理员", 403)

    # 4. 登录成功：清除失败计数，更新最后登录时间 + 记录日志
    clear_login_fails(payload.username)
    user.last_login_time = format_date_time()
    db.commit()
    _write_login_log(db, user.username, user.real_name, 1, "", request)

    token = create_access_token({
        "id": user.id,
        "username": user.username,
        "realName": user.real_name,
        "role": user.role,
        "roleName": user.role_name,
        "department": user.department,
        "ver": user.token_version or 0,
    })
    return ok(LoginResult(
        token=token,
        user={
            "id": user.id,
            "username": user.username,
            "realName": user.real_name,
            "role": user.role,
            "roleName": user.role_name,
            "department": user.department,
        },
    ).model_dump())


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    主动登出：令牌版本号自增，当前 JWT 立即失效（其它已登录设备同步退出）。
    无状态 JWT 无法真正"删除"令牌，版本号校验是等价的服务端失效方案。
    """
    bump_token_version(db, current_user["id"])
    db.commit()
    _write_login_log(db, current_user.get("username", ""), current_user.get("realName", ""),
                     1, "主动登出", request)
    logger.info("用户 %s 主动登出，历史令牌已失效", current_user.get("username"))
    return ok(None, "已退出登录")


@router.post("/register", dependencies=[Depends(rate_limit("register", 10, 3600))])
def register(payload: RegisterPayload, db: Session = Depends(get_db)):
    """
    用户自助注册
    - 校验验证码
    - 用户名唯一性校验
    - 用户类型：放射科医师 / 神经内科医师 / 科研人员（管理员不允许自助注册，双重拦截）
    - status=2（待管理员审核），审核通过后方可登录
    """
    if not verify_captcha(payload.captchaKey, payload.captcha):
        return fail("验证码错误或已过期", 400)

    # 用户名唯一性校验
    existing = db.query(UserModel).filter(UserModel.username == payload.username).first()
    if existing:
        return fail("用户名已被注册", 400)

    # 用户名长度与字符校验（3-20 位字母数字下划线）
    username = payload.username.strip()
    if not (3 <= len(username) <= 20) or not username.replace("_", "").isalnum():
        return fail("用户名须为 3-20 位字母、数字或下划线", 400)

    # 密码强度校验（6-20 位 + 字母数字混合 + 弱口令黑名单，与改密/建号同口径）
    pwd_msg = validate_password(payload.password)
    if pwd_msg:
        return fail(pwd_msg, 400)

    # 真实姓名必填
    real_name = payload.realName.strip()
    if not real_name:
        return fail("请填写真实姓名", 400)

    # 注册角色白名单：仅三类业务角色可自助注册（Schema Literal 已拦一层，此处防御性兜底）
    role_name_map = {
        "radiologist": "放射科医师",
        "neurologist": "神经内科医师",
        "researcher": "科研管理员",
    }
    role = payload.role if payload.role in role_name_map else "radiologist"
    role_name = role_name_map[role]

    # 按所选身份创建账号，status=2（待审核：管理员审核通过后 →1 方可登录）
    user = UserModel(
        username=username,
        password_hash=hash_password(payload.password),
        real_name=real_name,
        role=role,
        role_name=role_name,
        department=payload.department.strip() or "待分配",
        phone=payload.phone.strip(),
        status=2,  # 待审核：管理员在系统权限管理中审核通过后（→1）方可登录
        create_time=format_date_time(),
        last_login_time="—",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return ok({
        "id": user.id,
        "username": user.username,
        "realName": user.real_name,
        "role": user.role,
        "roleName": user.role_name,
        "department": user.department,
        "status": user.status,
    }, f"注册成功，身份为「{role_name}」，待管理员审核启用")


@router.get("/captcha", dependencies=[Depends(rate_limit("captcha", 30, 60))])
def get_captcha():
    """
    获取图形验证码：服务端 PIL 渲染 PNG（data URL）。
    明文 code 仅保存在服务端内存，不下发响应体，防止脚本读取验证码爆破登录。
    """
    from services.auth import generate_captcha_image
    key, image = generate_captcha_image()
    return ok({"captchaKey": key, "image": image})


@router.get("/demo-accounts")
def get_demo_accounts():
    """演示账号列表（登录页提示用）：响应体含明文密码，生产环境必须关闭，避免匿名获取超管口令"""
    if APP_ENV == "production":
        return fail("演示账号仅开发/演示环境可用", 404)
    return ok([DemoAccount(**a) for a in DEMO_ACCOUNTS])


# ==================== 系统管理 ====================

@sys_router.get("/users")
def get_users(db: Session = Depends(get_db)):
    """用户列表"""
    users = db.query(UserModel).order_by(UserModel.id).all()
    result = []
    for u in users:
        result.append({
            "id": u.id,
            "username": u.username,
            "realName": u.real_name,
            "role": u.role,
            "roleName": u.role_name,
            "department": u.department,
            "status": u.status,
            "phone": u.phone,
            "createTime": u.create_time,
            "lastLoginTime": u.last_login_time,
        })
    return ok(result)


@sys_router.post("/users")
def add_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """新增用户"""
    existing = db.query(UserModel).filter(UserModel.username == payload.username).first()
    if existing:
        return fail("账号已存在", 400)

    role_name = payload.roleName
    if not role_name:
        for rp in ROLE_PERMISSIONS:
            if rp["role"] == payload.role:
                role_name = rp["roleName"]
                break

    user = UserModel(
        username=payload.username,
        # 初始密码由管理员在表单显式指定（UserCreate 已强制 6-20 位），不再静默默认弱口令
        password_hash=hash_password(payload.password),
        real_name=payload.realName,
        role=payload.role,
        role_name=role_name,
        department=payload.department,
        phone=payload.phone,
        status=1,
        create_time=format_date_time(),
        last_login_time="—",
    )
    db.add(user)

    # 审计日志
    db.add(SystemLogModel(
        id=next_seq_id(db, SystemLogModel, "S", start=1),
        module="系统管理",
        action="新增用户",
        operator=current_user.get("username", "admin"),
        role="超级管理员",
        ip="—",
        result="成功",
        time=format_date_time(),
        detail=f"新增账号 {user.username}（{user.real_name} / {user.role_name}）",
    ))
    db.commit()
    db.refresh(user)
    return ok({
        "id": user.id,
        "username": user.username,
        "realName": user.real_name,
        "role": user.role,
        "roleName": user.role_name,
        "department": user.department,
        "status": user.status,
        "phone": user.phone,
        "createTime": user.create_time,
        "lastLoginTime": user.last_login_time,
    })


@sys_router.post("/users/status")
def toggle_user(
    payload: ToggleUserPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """启用/禁用用户（三态：0 禁用 / 1 启用 / 2 待审核）"""
    user = db.query(UserModel).filter(UserModel.id == payload.id).first()
    if not user:
        return fail("用户不存在", 404)
    user.status = payload.status
    # 禁用时自增令牌版本：被禁用账号即使持有未过期 JWT 也会被立即拦下
    if payload.status == 0:
        bump_token_version(db, user.id)

    # 审计日志（与前端 mock 行为保持一致）
    action = "启用用户" if payload.status == 1 else "禁用用户"
    db.add(SystemLogModel(
        id=next_seq_id(db, SystemLogModel, "S", start=1),
        module="系统管理",
        action=action,
        operator=current_user.get("username", "admin"),
        role="超级管理员",
        ip="—",
        result="成功",
        time=format_date_time(),
        detail=f"{action}：账号 {user.username}（{user.real_name}）",
    ))
    db.commit()
    return ok(None, "操作成功")


@sys_router.post("/users/review")
def review_user(
    payload: ReviewUserPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    管理员审核注册账号
    - approve=True：状态置为 1（启用），账号即可登录
    - approve=False：状态置为 0（拒绝/禁用），账号无法登录
    - 操作人取自 JWT，不信任前端传值；同时写入系统操作日志（审计留痕）
    """
    user = db.query(UserModel).filter(UserModel.id == payload.id).first()
    if not user:
        return fail("用户不存在", 404)
    if user.status != 2:
        return fail("该账号非待审核状态，无需审核", 400)

    next_status = 1 if payload.approve else 0
    action = "审核通过用户" if payload.approve else "拒绝注册申请"
    user.status = next_status

    # 审计日志留痕（id 全表最大数字后缀 +1，避免主键冲突）
    db.add(SystemLogModel(
        id=next_seq_id(db, SystemLogModel, "S", start=1),
        module="系统管理",
        action=action,
        operator=current_user.get("username", "admin"),
        role="超级管理员",
        ip="—",
        result="成功",
        time=format_date_time(),
        detail=f"{action}：账号 {user.username}（{user.real_name} / {user.role_name}）"
    ))
    db.commit()
    return ok(None, f"已{action}：{user.username}")


@sys_router.get("/role-permissions")
def get_role_permissions(db: Session = Depends(get_db)):
    """角色权限配置"""
    records = db.query(RolePermissionModel).all()
    result = []
    for rp in records:
        result.append({
            "role": rp.role,
            "roleName": rp.role_name,
            "permissionKeys": json.loads(rp.permission_keys),
        })
    return ok(result)


@sys_router.post("/role-permissions")
def save_role_permissions(payload: list[RolePermission], db: Session = Depends(get_db)):
    """保存角色权限配置"""
    for item in payload:
        rp = db.query(RolePermissionModel).filter(RolePermissionModel.role == item.role).first()
        if rp:
            rp.permission_keys = json.dumps(item.permissionKeys, ensure_ascii=False)
            rp.role_name = item.roleName
    db.commit()
    return ok(None, "权限配置已保存")


@sys_router.get("/permissions")
def get_all_permissions():
    """全部权限节点"""
    return ok([PermissionNode(**p) for p in ALL_PERMISSIONS])


# ==================== 个人中心 ====================

@router.get("/media-token")
def get_media_token(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """
    换取短期媒体票据（10 分钟，typ=media）。
    仅供 <img src>/EventSource 等无法自定义请求头的场景拼在 URL query 上，
    避免把长效会话 JWT 暴露到访问日志、浏览器历史与 Referer 中。
    """
    user = db.query(UserModel).filter(UserModel.id == current_user["id"]).first()
    if not user:
        return fail("用户不存在", 404)
    token = create_media_token({
        "id": user.id,
        "username": user.username,
        "realName": user.real_name,
        "role": current_user.get("role", ""),
        "roleName": current_user.get("roleName", ""),
        "department": user.department,
        "ver": user.token_version or 0,
    })
    return ok({"token": token, "expiresIn": MEDIA_TOKEN_EXPIRE_MINUTES * 60})


@router.get("/profile")
def get_profile(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """获取当前登录用户的完整个人档案"""
    user = db.query(UserModel).filter(UserModel.id == current_user["id"]).first()
    if not user:
        return fail("用户不存在", 404)
    return ok(UserProfile(
        id=user.id,
        username=user.username,
        realName=user.real_name,
        role=user.role,
        roleName=user.role_name,
        department=user.department,
        phone=user.phone,
        email=user.email,
        title=user.title,
        avatar=user.avatar,
        signature=user.signature,
        createTime=user.create_time,
        lastLoginTime=user.last_login_time,
    ).model_dump())


@router.put("/profile")
def update_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """更新个人资料（姓名/手机/邮箱/科室/职称/简介/头像）"""
    user = db.query(UserModel).filter(UserModel.id == current_user["id"]).first()
    if not user:
        return fail("用户不存在", 404)

    real_name = payload.realName.strip()
    if not real_name:
        return fail("真实姓名不能为空", 400)

    user.real_name = real_name
    user.phone = payload.phone.strip()
    user.email = payload.email.strip()
    user.department = payload.department.strip()
    user.title = payload.title.strip()
    user.signature = payload.signature.strip()
    # 头像仅接收 base64 data URL（前端裁剪后上传，限制体积由前端控制）
    if payload.avatar and payload.avatar.startswith("data:image"):
        user.avatar = payload.avatar
    db.commit()

    # 同步更新 JWT 中的用户信息（前端拿到新 token 后顶栏即时刷新）
    # 仅更新资料，令牌版本号不变，故新旧令牌在过期前都有效
    token = create_access_token({
        "id": user.id,
        "username": user.username,
        "realName": user.real_name,
        "role": user.role,
        "roleName": user.role_name,
        "department": user.department,
        "ver": user.token_version or 0,
    })
    return ok({
        "profile": UserProfile(
            id=user.id, username=user.username, realName=user.real_name,
            role=user.role, roleName=user.role_name, department=user.department,
            phone=user.phone, email=user.email, title=user.title,
            avatar=user.avatar, signature=user.signature,
            createTime=user.create_time, lastLoginTime=user.last_login_time,
        ).model_dump(),
        "token": token,
    }, "资料已更新")


@router.put("/password")
def change_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """修改密码（校验旧密码 + 新密码强度）"""
    user = db.query(UserModel).filter(UserModel.id == current_user["id"]).first()
    if not user:
        return fail("用户不存在", 404)
    if not verify_password(payload.oldPassword, user.password_hash):
        return fail("原密码错误", 400)
    pwd_msg = validate_password(payload.newPassword)
    if pwd_msg:
        return fail(pwd_msg, 400)
    if payload.newPassword == payload.oldPassword:
        return fail("新密码不能与原密码相同", 400)

    user.password_hash = hash_password(payload.newPassword)
    # 自增令牌版本：本机与其它设备上的所有旧会话立即失效，强制以新密码重新登录
    new_ver = bump_token_version(db, user.id)
    db.commit()
    logger.info("用户 %s 修改密码，全部历史令牌已失效（token_version=%d）", user.username, new_ver)
    return ok(None, "密码修改成功，请重新登录")


@router.get("/profile/stats")
def get_profile_stats(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """个人临床工作统计：病例数、AI 分析次数、报告数、登录次数等"""
    username = current_user["username"]
    # 以操作人维度聚合审计日志与推理日志（演示数据 operator 多为模拟名，含本人的计入）
    inference_count = db.query(InferenceLogModel).filter(
        InferenceLogModel.operator == username
    ).count()
    reported_count = db.query(SystemLogModel).filter(
        SystemLogModel.operator == username,
        SystemLogModel.module == "报告管理",
    ).count()
    # 个人参与的病例：以创建/操作痕迹匹配（演示数据较粗，用全院在库病例近似）
    # 必须排除软删病例，否则统计口径偏大且间接泄露已删除数据
    total_cases = db.query(CaseRecordModel).filter(
        CaseRecordModel.is_deleted.is_(False)
    ).count()
    completed_cases = db.query(CaseRecordModel).filter(
        CaseRecordModel.is_deleted.is_(False),
        CaseRecordModel.status.in_(["completed", "reported"])
    ).count()
    # 登录次数：审计日志中"用户登录"操作
    login_count = db.query(SystemLogModel).filter(
        SystemLogModel.operator == username,
        SystemLogModel.action.like("%登录%"),
    ).count()
    # 至少记 1 次（本次登录）
    login_count = max(login_count, 1)

    user = db.query(UserModel).filter(UserModel.id == current_user["id"]).first()
    return ok(ProfileStats(
        totalCases=total_cases,
        completedCases=completed_cases,
        inferenceCount=inference_count,
        reportedCount=reported_count,
        loginCount=login_count,
        lastLoginTime=user.last_login_time if user else "—",
        registerTime=user.create_time if user else "—",
    ).model_dump())


@router.get("/profile/activities")
def get_profile_activities(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """个人近期活动记录（系统操作日志 + AI 推理日志合并，按时间倒序）"""
    username = current_user["username"]
    items: list[ActivityItem] = []

    sys_logs = db.query(SystemLogModel).filter(
        SystemLogModel.operator == username
    ).order_by(SystemLogModel.time.desc()).limit(8).all()
    for s in sys_logs:
        items.append(ActivityItem(
            time=s.time, module=s.module, action=s.action, detail=s.detail or "",
        ))

    infer_logs = db.query(InferenceLogModel).filter(
        InferenceLogModel.operator == username
    ).order_by(InferenceLogModel.time.desc()).limit(8).all()
    for i in infer_logs:
        items.append(ActivityItem(
            time=i.time, module="AI 分析",
            action=f"{i.patient_name} 风险评分 {i.risk_score:.2f}",
            detail=f"病例 {i.case_id} · 模型 {i.model_version} · 耗时 {i.duration_sec:.1f}s",
        ))

    items.sort(key=lambda x: x.time, reverse=True)
    return ok([i.model_dump() for i in items[:12]])


# ==================== 登录日志（安全审计） ====================

def _login_log_to_dict(r: LoginRecord) -> dict:
    return {
        "id": r.id,
        "username": r.username,
        "realName": r.real_name,
        "success": r.success == 1,
        "failReason": r.fail_reason,
        "ip": r.ip,
        "userAgent": r.user_agent,
        "loginAt": r.login_at.strftime("%Y-%m-%d %H:%M:%S") if r.login_at else "",
    }


@router.get("/login-logs")
def my_login_logs(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_qs),
):
    """当前用户自己的登录记录（个人中心安全审计）"""
    rows = (
        db.query(LoginRecord)
        .filter(LoginRecord.username == current_user["username"])
        .order_by(LoginRecord.login_at.desc())
        .limit(limit)
        .all()
    )
    return ok([_login_log_to_dict(r) for r in rows])


@sys_router.get("/login-logs")
def admin_login_logs(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    keyword: str = Query(default=""),
    result: str = Query(default=""),  # success / fail
    db: Session = Depends(get_db),
):
    """管理员查询全员登录日志（分页 + 用户名/姓名筛选 + 结果筛选）"""
    query = db.query(LoginRecord)
    if keyword.strip():
        kw = f"%{keyword.strip()}%"
        query = query.filter(
            (LoginRecord.username.ilike(kw)) | (LoginRecord.real_name.ilike(kw))
        )
    if result == "success":
        query = query.filter(LoginRecord.success == 1)
    elif result == "fail":
        query = query.filter(LoginRecord.success == 0)

    total = query.count()
    rows = (
        query.order_by(LoginRecord.login_at.desc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
        .all()
    )
    # 失败统计（异常登录快速感知）
    from datetime import timedelta
    since = datetime.now() - timedelta(days=7)
    fail7 = db.query(LoginRecord).filter(LoginRecord.success == 0, LoginRecord.login_at >= since).count()
    return ok({
        "list": [_login_log_to_dict(r) for r in rows],
        "total": total,
        "failCount7d": fail7,
    })
