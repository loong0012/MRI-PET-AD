import type { ActionOption, CaseLogPage, CaseLogQuery } from '@/types/caseLog'
import { httpGet, httpPost } from '@/utils/request'

/**
 * 病例操作审计日志 API（admin）
 */

/** 操作类型字典（含展示名与配色） */
export function apiGetActionOptions(): Promise<ActionOption[]> {
  return httpGet<ActionOption[]>('/case-log/actions')
}

/** 分页查询操作日志 */
export function apiQueryCaseLogs(query: CaseLogQuery): Promise<CaseLogPage> {
  return httpPost<CaseLogPage>('/case-log/list', query)
}

/** 单病例操作历程时间线项 */
export interface CaseTimelineItem {
  id: string
  action: string
  actionLabel: string
  actionColor: string
  actionBg: string
  operator: string
  time: string
  detail: string
}

/** 单病例完整操作历程（时间线） */
export function apiGetCaseTimeline(caseId: string): Promise<CaseTimelineItem[]> {
  return httpGet<CaseTimelineItem[]>(`/case-log/case/${caseId}`)
}
