import type { LoginPayload, LoginResult, UserRecord, RolePermission, PermissionNode, DemoAccount, RegisterPayload, RegisterResult, ReviewUserPayload } from '@/types/user'
import { httpPost, httpGet } from '@/utils/request'
import { verifyCaptcha } from '@/utils/captcha'
import { DEMO_ACCOUNTS, ROLE_NAME, users, rolePermissions, ALL_PERMISSIONS, systemLogs } from '@/mock/db'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

/** 登录（Mock 模式校验演示账号 + 本地验证码） */
export function apiLogin(payload: LoginPayload): Promise<LoginResult> {
  if (useMock) {
    return new Promise((resolve, reject) => {
      setTimeout(() => {
        if (!verifyCaptcha(payload.captchaKey, payload.captcha)) {
          reject(new Error('验证码错误或已过期'))
          return
        }
        const acc = DEMO_ACCOUNTS.find((a) => a.username === payload.username && a.password === payload.password)
        if (!acc) {
          reject(new Error('用户名或密码错误'))
          return
        }
        const u = users.find((x) => x.username === acc.username)
        resolve({
          token: `mock-token-${acc.username}-${Date.now()}`,
          user: {
            id: u?.id ?? 0,
            username: acc.username,
            realName: u?.realName ?? acc.roleName,
            role: acc.role,
            roleName: acc.roleName,
            department: u?.department ?? '—'
          }
        })
      }, 600)
    })
  }
  return httpPost<LoginResult>('/auth/login', payload)
}

/** 获取演示账号列表（登录页提示用，仅 Mock） */
export function apiDemoAccounts(): DemoAccount[] {
  return DEMO_ACCOUNTS
}

/**
 * 登出：通知后端自增令牌版本号，使当前 JWT 立即失效。
 * Mock 模式无服务端状态，直接返回成功。
 */
export function apiLogout(): Promise<void> {
  if (useMock) return Promise.resolve()
  return httpPost<void>('/auth/logout')
}

/**
 * 用户注册
 * - Mock 模式：本地校验用户名唯一性 + 验证码与注册角色，新增到 users 数组（status=2 待审核）
 * - 真实后端模式：POST /auth/register
 */
export function apiRegister(payload: RegisterPayload): Promise<RegisterResult> {
  if (useMock) {
    return new Promise((resolve, reject) => {
      setTimeout(() => {
        if (!verifyCaptcha(payload.captchaKey, payload.captcha)) {
          reject(new Error('验证码错误或已过期'))
          return
        }
        if (users.find((u) => u.username === payload.username) || DEMO_ACCOUNTS.find((a) => a.username === payload.username)) {
          reject(new Error('用户名已被注册'))
          return
        }
        // 白名单：自助注册仅允许三类业务角色，管理员拒绝
        const registerable = ['radiologist', 'neurologist', 'researcher'] as const
        const role = registerable.includes(payload.role as (typeof registerable)[number]) ? payload.role : 'radiologist'
        const rec: RegisterResult = {
          id: users.length + 1,
          username: payload.username,
          realName: payload.realName,
          role,
          roleName: ROLE_NAME[role],
          department: payload.department || '待分配',
          status: 2 // 待管理员审核
        }
        users.push({
          ...rec,
          phone: payload.phone || '',
          createTime: new Date().toLocaleString(),
          lastLoginTime: '—'
        })
        resolve(rec)
      }, 600)
    })
  }
  return httpPost<RegisterResult>('/auth/register', payload)
}

/** 真实后端模式：获取图形验证码（服务端渲染的 PNG data URL，明文 code 不下发） */
export function apiGetCaptcha(): Promise<{ captchaKey: string; image: string }> {
  return httpGet<{ captchaKey: string; image: string }>('/auth/captcha')
}

/** 角色名称映射 */
export function apiRoleName(role: string): string {
  return ROLE_NAME[role as keyof typeof ROLE_NAME] ?? role
}

/** 用户列表（系统管理） */
export function apiGetUsers(): Promise<UserRecord[]> {
  if (useMock) return Promise.resolve([...users]) // 浅拷贝触发响应式
  return httpGet<UserRecord[]>('/system/users')
}

/** 管理员新建用户入参：用户资料 + 仅写入不回显的初始密码（后端强制 6-20 位） */
export type AddUserPayload = Omit<UserRecord, 'id' | 'createTime' | 'lastLoginTime'> & { password: string }

/** 新增用户 */
export function apiAddUser(u: AddUserPayload): Promise<UserRecord> {
  if (useMock) {
    const rec: UserRecord = { ...u, id: users.length + 1, createTime: new Date().toLocaleString(), lastLoginTime: '—' }
    users.unshift(rec)
    return Promise.resolve(rec)
  }
  return httpPost<UserRecord>('/system/users', u)
}

/** 修改用户状态（三态：1 启用 / 0 禁用 / 2 待审核） */
export function apiToggleUser(id: number, status: 0 | 1 | 2): Promise<void> {
  if (useMock) {
    const u = users.find((x) => x.id === id)
    if (u) {
      const oldStatus = u.status
      u.status = status
      // 写入审计日志（启用/禁用操作）
      const action = status === 1 ? '启用用户' : '禁用用户'
      systemLogs.unshift({
        id: `S${Date.now()}`,
        module: '系统管理',
        action,
        operator: 'admin',
        role: '超级管理员',
        ip: '—',
        result: '成功',
        time: new Date().toLocaleString(),
        detail: `${action}：账号 ${u.username}（${u.realName}）`
      })
    }
    return Promise.resolve()
  }
  return httpPost<void>('/system/users/status', { id, status })
}

/**
 * 管理员审核注册账号
 * - approve=true 通过（→1 启用）；approve=false 拒绝（→0 禁用）
 * - Mock 模式同步将操作写入系统日志
 */
export function apiReviewUser(payload: ReviewUserPayload): Promise<void> {
  if (useMock) {
    const u = users.find((x) => x.id === payload.id)
    if (!u) return Promise.reject(new Error('用户不存在'))
    if (u.status !== 2) return Promise.reject(new Error('该账号非待审核状态'))
    u.status = payload.approve ? 1 : 0
    // Mock 写审计日志
    const action = payload.approve ? '审核通过用户' : '拒绝注册申请'
    systemLogs.unshift({
      id: `S${Date.now()}`,
      module: '系统管理',
      action,
      operator: payload.operator,
      role: '超级管理员',
      ip: '—',
      result: '成功',
      time: new Date().toLocaleString(),
      detail: `${action}：账号 ${u.username}（${u.realName} / ${u.roleName}）`
    })
    return Promise.resolve()
  }
  return httpPost<void>('/system/users/review', payload)
}

/** 保存角色权限配置 */
export function apiSaveRolePermissions(list: RolePermission[]): Promise<void> {
  if (useMock) {
    for (const next of list) {
      const cur = rolePermissions.find((r) => r.role === next.role)
      if (cur) cur.permissionKeys = [...next.permissionKeys]
    }
    return Promise.resolve()
  }
  return httpPost<void>('/system/role-permissions', list)
}

/** 获取角色权限配置 */
export function apiGetRolePermissions(): Promise<RolePermission[]> {
  if (useMock) return Promise.resolve(rolePermissions)
  return httpGet<RolePermission[]>('/system/role-permissions')
}

/** 获取全部权限节点 */
export function apiGetAllPermissions(): Promise<PermissionNode[]> {
  if (useMock) return Promise.resolve(ALL_PERMISSIONS)
  return httpGet<PermissionNode[]>('/system/permissions')
}
