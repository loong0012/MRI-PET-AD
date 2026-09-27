/**
 * 高危病例预警中心 API
 */
import { httpGet, httpPost } from '@/utils/request'

export type WarningHandleStatus = 'open' | 'handled' | 'ignored'

export interface WarningSummary {
  total: number
  high: number
  medium: number
  low: number
  overdue: number
  cognition: number
  riskRise: number
  handled: number
  ignored: number
}

export interface WarningItem {
  dedupKey: string
  type: 'followup-overdue' | 'cognition-decline' | 'risk-rise'
  typeLabel: string
  caseId: string
  patientName: string
  riskLevel: string | null
  riskScore: number | null
  level: 'high' | 'medium' | 'low'
  levelText: string
  metric: string
  detail: string
  sortValue: number
  handleStatus: WarningHandleStatus
  handleNote: string
  handler: string
  handledAt: string
}

export type WarningType = 'all' | 'overdue' | 'cognition' | 'risk'
export type WarningLevel = '' | 'high' | 'medium' | 'low'

export function apiWarningSummary(): Promise<WarningSummary> {
  return httpGet<WarningSummary>('/warning/summary')
}

export function apiWarningList(
  type: WarningType,
  level: WarningLevel,
  status: WarningHandleStatus | 'all' = 'open'
): Promise<{ list: WarningItem[]; total: number }> {
  return httpGet('/warning/list', { type, level, status })
}

/** 预警处置：handled 已处理 / ignored 忽略 / reopen 重新打开 */
export function apiHandleWarning(dedupKey: string, action: 'handled' | 'ignored' | 'reopen', note = ''): Promise<void> {
  return httpPost('/warning/handle', { dedupKey, action, note })
}
