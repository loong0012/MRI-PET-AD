/**
 * 系统运行审计看板 API（超级管理员）
 */
import { httpGet } from '@/utils/request'

export interface AuditTotals {
  loginCount: number
  loginFail: number
  failRate: number
  activeUsers: number
  caseOps: number
  systemOps: number
  inferenceCount: number
  inferenceFail: number
  avgDuration: number
}

export interface NameValue {
  name: string
  value: number
}

export interface AuditOverview {
  rangeDays: number
  totals: AuditTotals
  loginTrend: Array<{ date: string; success: number; fail: number }>
  activeUsers: Array<{ username: string; realName: string; success: number; fail: number }>
  caseActions: NameValue[]
  moduleUsage: NameValue[]
  failHours: Array<{ hour: string; count: number }>
}

export function apiAuditOverview(days = 30): Promise<AuditOverview> {
  return httpGet<AuditOverview>('/audit-board/overview', { days: String(days) })
}
