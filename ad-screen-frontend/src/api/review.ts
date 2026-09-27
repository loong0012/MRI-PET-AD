import type {
  ReviewListItem,
  ReviewListResult,
  ReviewDetail,
  ReviewStatusResult,
  SubmitPayload,
  DecisionPayload,
  ReviewStatus
} from '@/types/review'
import { httpGet, httpPost } from '@/utils/request'

/**
 * 报告会签审核工作流 API
 * - GET    /review/pending              待审报告列表（工作台）
 * - GET    /review/{case_id}            单病例审核详情
 * - POST   /review/{case_id}/submit     提交审核
 * - POST   /review/{case_id}/decision   审核决策（approve/reject/sign）
 * - POST   /review/{case_id}/withdraw   撤回审核
 */

/** 待审报告列表查询参数 */
export interface ReviewQuery {
  page: number
  pageSize: number
  /** 筛选状态：draft/pending_review/in_review/signed/rejected/withdrawn/all */
  status: ReviewStatus | 'all'
}

/** 获取待审报告列表（工作台） */
export function apiGetPendingReviews(params: ReviewQuery): Promise<ReviewListResult> {
  return httpGet<ReviewListResult>('/review/pending', {
    page: String(params.page),
    pageSize: String(params.pageSize),
    status: params.status
  })
}

/** 获取单病例审核详情 */
export function apiGetReviewDetail(caseId: string): Promise<ReviewDetail> {
  return httpGet<ReviewDetail>(`/review/${caseId}`)
}

/** 提交审核（医生提交报告进入待审） */
export function apiSubmitReview(caseId: string, payload: SubmitPayload): Promise<ReviewStatusResult> {
  return httpPost<ReviewStatusResult>(`/review/${caseId}/submit`, payload)
}

/** 审核决策（approve / reject / sign） */
export function apiReviewDecision(caseId: string, payload: DecisionPayload): Promise<ReviewStatusResult> {
  return httpPost<ReviewStatusResult>(`/review/${caseId}/decision`, payload)
}

/** 撤回审核（提交人可撤回回到 draft） */
export function apiWithdrawReview(caseId: string): Promise<ReviewStatusResult> {
  return httpPost<ReviewStatusResult>(`/review/${caseId}/withdraw`, {})
}

// 类型重导出，便于调用方统一引用
export type {
  ReviewListItem,
  ReviewDetail,
  ReviewStatusResult,
  ReviewStatus,
  SubmitPayload,
  DecisionPayload
}
