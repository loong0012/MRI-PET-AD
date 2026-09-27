"""
用户与认证 Pydantic 模型（对应前端 types/user.d.ts）
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from services.auth import validate_password

RoleKey = Literal["radiologist", "neurologist", "researcher", "admin"]


class UserInfo(BaseModel):
    id: int
    username: str
    realName: str
    role: RoleKey
    roleName: str
    department: str


class LoginPayload(BaseModel):
    username: str
    password: str
    captcha: str
    captchaKey: str


class LoginResult(BaseModel):
    token: str
    user: UserInfo


class UserRecord(BaseModel):
    id: Optional[int] = None
    username: str
    realName: str
    role: RoleKey
    roleName: str
    department: str
    status: int = 1  # 1 启用 / 0 禁用 / 2 待审核
    phone: str = ""
    createTime: str = ""
    lastLoginTime: str = ""

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    realName: str = Field(min_length=1, max_length=50)
    role: RoleKey
    roleName: str = ""
    department: str = Field(default="", max_length=50)
    phone: str = Field(default="", max_length=20)
    status: int = 1
    # 管理员新建账号必须显式指定初始密码（不允许服务端静默默认弱口令）
    password: str = Field(min_length=6, max_length=20)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        # 管理员建号同样强制字母+数字复杂度，弱口令在 schema 层拦截
        msg = validate_password(v)
        if msg:
            raise ValueError(msg)
        return v


class PermissionNode(BaseModel):
    key: str
    label: str
    description: str


class RolePermission(BaseModel):
    role: RoleKey
    roleName: str
    permissionKeys: list[str]

    model_config = {"from_attributes": True}


class DemoAccount(BaseModel):
    username: str
    password: str
    role: RoleKey
    roleName: str


class RegisterPayload(BaseModel):
    """用户自助注册请求体"""
    username: str
    password: str
    realName: str
    # 注册身份：仅允许三类业务角色自助注册，管理员账号只能由既有管理员在系统管理中创建
    role: Literal["radiologist", "neurologist", "researcher"] = "radiologist"
    department: str = ""
    phone: str = ""
    captcha: str
    captchaKey: str


class ToggleUserPayload(BaseModel):
    id: int
    status: Literal[0, 1, 2]  # 扩展为三态：0 禁用 / 1 启用 / 2 待审核


class ReviewUserPayload(BaseModel):
    """管理员审核注册账号：approve=True 通过（→1），False 拒绝（→0）"""
    id: int
    approve: bool
    operator: str = "admin"


class SaveRolePermissionsPayload(BaseModel):
    """角色权限批量保存"""
    pass  # 前端直接传 RolePermission[]，路由用 list[RolePermission] 接收


# ==================== 个人中心 ====================

class UserProfile(BaseModel):
    """当前登录用户的完整个人档案（个人中心展示用）"""
    id: int
    username: str
    realName: str
    role: RoleKey
    roleName: str
    department: str
    phone: str
    email: str
    title: str
    avatar: str
    signature: str
    createTime: str
    lastLoginTime: str


class ProfileUpdate(BaseModel):
    """个人资料更新（用户可自主修改的字段）"""
    realName: str = Field(min_length=1, max_length=50)
    phone: str = Field(default="", max_length=20)
    email: str = Field(default="", max_length=100)
    department: str = Field(default="", max_length=50)
    title: str = Field(default="", max_length=50)
    signature: str = Field(default="", max_length=200)
    # 200KB 图片 base64 后约 273KB，留余量限 300KB；空串表示不修改头像
    avatar: str = Field(default="", max_length=300_000)

    @field_validator("avatar")
    @classmethod
    def _validate_avatar(cls, v: str) -> str:
        if v and not v.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/jpg;base64,")):
            raise ValueError("头像仅支持 PNG/JPEG 的 base64 data URL")
        return v


class PasswordChange(BaseModel):
    """修改密码：必须校验旧密码"""
    oldPassword: str
    newPassword: str

    @field_validator("newPassword")
    @classmethod
    def _validate_new_password(cls, v: str) -> str:
        # schema 层兜底复杂度（端点内还会结合旧密码做业务校验）
        msg = validate_password(v)
        if msg:
            raise ValueError(msg)
        return v


class ProfileStats(BaseModel):
    """个人临床工作统计（展示临床工作量与活跃度）"""
    totalCases: int          # 参与/创建病例数
    completedCases: int      # 已完成筛查数
    inferenceCount: int      # AI 分析次数
    reportedCount: int       # 出具报告数
    loginCount: int          # 累计登录次数
    lastLoginTime: str
    registerTime: str


class ActivityItem(BaseModel):
    """个人近期活动记录（审计日志 + 推理日志合并）"""
    time: str
    module: str
    action: str
    detail: str
