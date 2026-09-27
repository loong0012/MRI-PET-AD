/** 角色标识 */
export type RoleKey = 'radiologist' | 'neurologist' | 'researcher' | 'admin'

/** 可自助注册的用户类型（管理员账号禁止自助注册，仅能由既有管理员创建） */
export type RegisterableRole = Extract<RoleKey, 'radiologist' | 'neurologist' | 'researcher'>

/** 登录用户信息 */
export interface UserInfo {
  id: number
  username: string
  realName: string
  role: RoleKey
  roleName: string
  department: string
  phone?: string
  email?: string
  title?: string
  avatar?: string
  signature?: string
  createTime?: string
  lastLoginTime?: string
}

/** 登录请求体 */
export interface LoginPayload {
  username: string
  password: string
  captcha: string
  captchaKey: string
}

/** 注册请求体 */
export interface RegisterPayload {
  username: string
  password: string
  realName: string
  /** 注册申请的用户类型，默认放射科医师；管理员不可自助注册 */
  role: RegisterableRole
  department?: string
  phone?: string
  captcha: string
  captchaKey: string
}

/** 注册响应 */
export interface RegisterResult {
  id: number
  username: string
  realName: string
  role: RoleKey
  roleName: string
  department: string
  status: 0 | 1 | 2 // 1 启用 / 0 禁用 / 2 待审核
}

/** 登录响应 */
export interface LoginResult {
  token: string
  user: UserInfo
}

/** 系统用户记录（管理列表用） */
export interface UserRecord extends UserInfo {
  status: 0 | 1 | 2 // 1 启用 / 0 禁用 / 2 待审核
  phone: string
  createTime: string
  lastLoginTime: string
}

/** 用户状态枚举：1 启用 / 0 禁用 / 2 待审核 */
export type UserStatus = 0 | 1 | 2

/** 管理员审核注册账号请求体 */
export interface ReviewUserPayload {
  id: number
  approve: boolean
  operator: string
}

/** 权限节点（角色权限配置用） */
export interface PermissionNode {
  key: string
  label: string
  description: string
}

/** 角色权限配置 */
export interface RolePermission {
  role: RoleKey
  roleName: string
  permissionKeys: string[]
}

/** 演示账号（登录页提示） */
export interface DemoAccount {
  username: string
  password: string
  role: RoleKey
  roleName: string
}

// ==================== 个人中心 ====================

/** 个人完整档案 */
export interface UserProfile {
  id: number
  username: string
  realName: string
  role: RoleKey
  roleName: string
  department: string
  phone: string
  email: string
  title: string
  avatar: string
  signature: string
  createTime: string
  lastLoginTime: string
}

/** 资料更新请求体 */
export interface ProfileUpdatePayload {
  realName: string
  phone?: string
  email?: string
  department?: string
  title?: string
  signature?: string
  avatar?: string
}

/** 资料更新响应（含新 token 以便顶栏即时刷新） */
export interface ProfileUpdateResult {
  profile: UserProfile
  token: string
}

/** 修改密码请求体 */
export interface PasswordChangePayload {
  oldPassword: string
  newPassword: string
}

/** 个人临床工作统计 */
export interface ProfileStats {
  totalCases: number
  completedCases: number
  inferenceCount: number
  reportedCount: number
  loginCount: number
  lastLoginTime: string
  registerTime: string
}

/** 个人近期活动记录 */
export interface ActivityItem {
  time: string
  module: string
  action: string
  detail: string
}
