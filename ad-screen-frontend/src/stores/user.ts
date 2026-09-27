import { defineStore } from 'pinia'
import type { UserInfo, RoleKey } from '@/types/user'
import { apiLogin, apiLogout } from '@/api/auth'
import { apiGetProfile } from '@/api/profile'
import type { LoginPayload } from '@/types/user'
import { getToken, setToken, clearToken, getStoredUser, setStoredUser, clearStoredUser } from '@/utils/auth'
import { clearMediaToken } from '@/utils/mediaToken'

/**
 * 用户状态：Token + 身份 + 权限判断
 * 路由守卫与侧边栏均依赖本 store
 */
export const useUserStore = defineStore('user', {
  state: () => ({
    token: getToken(),
    userInfo: getStoredUser<UserInfo>()
  }),
  getters: {
    isLoggedIn: (s): boolean => !!s.token,
    role: (s): RoleKey => s.userInfo?.role ?? 'radiologist',
    roleName: (s): string => s.userInfo?.roleName ?? '',
    realName: (s): string => s.userInfo?.realName ?? '',
    avatar: (s): string => s.userInfo?.avatar ?? '',
    username: (s): string => s.userInfo?.username ?? '',
  },
  actions: {
    /** 登录：保存 token 与用户信息 */
    async login(payload: LoginPayload): Promise<void> {
      const res = await apiLogin(payload)
      this.token = res.token
      this.userInfo = res.user
      setToken(res.token)
      setStoredUser(res.user)
      // 清掉可能残留的上一账号媒体票据，由布局层重新预取
      clearMediaToken()
    },
    /**
     * 退出登录：先通知后端使当前 JWT 立即失效（服务端会话失效），
     * 无论网络成功与否都清理本地凭证（避免断网时无法退出）。
     */
    async logout(): Promise<void> {
      try {
        await apiLogout()
      } catch {
        /* 静默：后端不可达/令牌已失效都不阻断本地退出 */
      }
      this.token = ''
      this.userInfo = null
      clearToken()
      clearStoredUser()
      clearMediaToken()
    },
    /** 是否具备指定角色访问权（管理员全通过） */
    hasRole(roles?: RoleKey[]): boolean {
      if (!roles || roles.length === 0) return true
      if (this.role === 'admin') return true
      return roles.includes(this.role)
    },
    /**
     * 局部更新用户信息（个人中心保存资料后调用）。
     * 同步顶栏头像/姓名/科室展示，并持久化到 localStorage。
     */
    patchUser(partial: Partial<UserInfo>): void {
      if (!this.userInfo) return
      this.userInfo = { ...this.userInfo, ...partial }
      setStoredUser(this.userInfo)
    },
    /**
     * 刷新完整档案（从后端拉取最新个人中心数据并合并到全局状态）。
     * 用于进入个人中心时同步后端字段（头像/职称/签名等登录时未下发的字段）。
     */
    async fetchProfile(): Promise<void> {
      try {
        const p = await apiGetProfile()
        this.patchUser(p)
      } catch {
        /* 静默：网络异常时不阻塞页面 */
      }
    }
  }
})
