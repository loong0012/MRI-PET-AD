/**
 * 随访管理 API（工作台汇总 / 列表 / 记录执行 / 历史 / 批量生成）
 */
import { httpGet, httpPost } from '@/utils/request'
import type { FollowUpSummary, FollowUpWorkItem, FollowUpVisit, FollowUpVisitPayload } from '@/types/followup'
import type { BatchFollowUpPreview, BatchGenerateResult } from '@/types/modelComparison.d'

export type FollowUpScope = 'overdue' | 'week' | 'month' | 'all'

/** 工作台顶部汇总 */
export function apiFollowUpSummary(): Promise<FollowUpSummary> {
  return httpGet<FollowUpSummary>('/followup/worklist/summary')
}

/** 工作台列表 */
export function apiFollowUpList(scope: FollowUpScope, keyword = ''): Promise<{ list: FollowUpWorkItem[]; total: number }> {
  return httpGet<{ list: FollowUpWorkItem[]; total: number }>('/followup/worklist/list', { scope, keyword })
}

/** 记录一次随访执行（后端同步滚动计划下次随访日） */
export function apiRecordVisit(caseId: string, payload: FollowUpVisitPayload): Promise<void> {
  return httpPost<void>(`/followup/visit/${caseId}`, payload)
}

/** 某病例随访执行历史 */
export function apiListVisits(caseId: string): Promise<FollowUpVisit[]> {
  return httpGet<FollowUpVisit[]>(`/followup/visits/${caseId}`)
}

// ==================== 批量随访计划生成 ====================

export type BatchScope = 'all' | 'missing' | 'urgent' | 'ad' | 'mci' | 'low'

/** 批量生成预览（不落库） */
export function apiBatchPreview(scope: BatchScope = 'all'): Promise<BatchFollowUpPreview> {
  return httpGet<BatchFollowUpPreview>('/followup/batch/preview', { scope })
}

/** 批量生成/更新随访计划（确认后落库） */
export function apiBatchGenerate(payload: { caseIds?: string[]; scope?: BatchScope }): Promise<BatchGenerateResult> {
  return httpPost<BatchGenerateResult>('/followup/batch/generate', payload)
}
