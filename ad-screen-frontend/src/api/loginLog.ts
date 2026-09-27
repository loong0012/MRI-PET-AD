/**
 * 登录日志（安全审计）API
 */
import { httpGet } from '@/utils/request'

export interface LoginLogItem {
  id: number
  username: string
  realName: string
  success: boolean
  failReason: string
  ip: string
  userAgent: string
  loginAt: string
}

export interface AdminLoginLogPage {
  list: LoginLogItem[]
  total: number
  failCount7d: number
}

/** 当前用户自己的登录记录 */
export function apiMyLoginLogs(limit = 20): Promise<LoginLogItem[]> {
  return httpGet<LoginLogItem[]>('/auth/login-logs', { limit: String(limit) })
}

/** 管理员查询全员登录日志 */
export function apiAdminLoginLogs(params: {
  page: number
  pageSize: number
  keyword?: string
  result?: string
}): Promise<AdminLoginLogPage> {
  return httpGet<AdminLoginLogPage>('/system/login-logs', {
    page: String(params.page),
    pageSize: String(params.pageSize),
    keyword: params.keyword ?? '',
    result: params.result ?? ''
  })
}
