/**
 * 报告会签审核工作流类型定义
 */

/** 报告审核状态 */
export type ReviewStatus =
  | 'draft' // 起草（待提交）
  | 'pending_review' // 待审
  | 'in_review' // 会签中
  | 'signed' // 已签发
  | 'rejected' // 已退回
  | 'withdrawn' // 已撤回

/** 审核决策类型 */
export type ReviewDecision = 'approve' | 'reject' | 'sign' | 'submit' | 'withdraw'

/** 审核人角色 */
export type ReviewerRole = 'radiologist' | 'neurologist' | 'researcher' | 'admin'

/** 单条会签审核记录 */
export interface ReviewRecord {
  id: number
  caseId: string
  versionId: number | null
  /** 审核人账号 */
  reviewerUsername: string
  /** 审核人姓名 */
  reviewerName: string
  /** 审核人角色 */
  reviewerRole: string
  /** 决策类型：approve/reject/sign/submit/withdraw */
  decision: ReviewDecision
  /** 审核意见 */
  comment: string
  /** 签字时间（YYYY-MM-DD HH:mm:ss） */
  signedAt: string
}

/** 会签进度信息（POST 接口返回） */
export interface ReviewStatusResult {
  reviewStatus: ReviewStatus
  signedCount: number
  requiredCount: number
  hasRadiologist: boolean
}

/** 待审报告列表项 */
export interface ReviewListItem {
  caseId: string
  patientName: string
  modality: string
  examDate: string
  riskLevel: string
  riskScore: number | null
  reviewStatus: ReviewStatus
  signedCount: number
  requiredCount: number
  hasRadiologist: boolean
  versionId: number | null
  version: number | null
  /** 所有会签记录（按时间倒序） */
  reviews: ReviewRecord[]
}

/** 分页结果 */
export interface ReviewListResult {
  list: ReviewListItem[]
  total: number
  page: number
  pageSize: number
}

/** 报告提交人信息 */
export interface ReviewSubmitter {
  username: string
  name: string
  /** 提交时间 */
  at: string
}

/** 最新分析版本信息 */
export interface ReviewLatestVersion {
  id: number
  version: number
  riskLevel: string
  riskScore: number | null
  modelVersion: string
  fusionStrategy: string
  operator: string
  createdAt: string
}

/** 单病例审核详情 */
export interface ReviewDetail {
  caseId: string
  patientName: string
  modality: string
  examDate: string
  department: string
  riskLevel: string
  riskScore: number | null
  caseStatus: string
  diagStatus: string
  reviewStatus: ReviewStatus
  signedCount: number
  requiredCount: number
  hasRadiologist: boolean
  submitter: ReviewSubmitter | null
  latestVersion: ReviewLatestVersion | null
  /** 所有会签记录（按时间倒序） */
  reviews: ReviewRecord[]
  /** 当前周期内的有效审核记录（不含 submit/withdraw 边界） */
  currentCycleReviews: ReviewRecord[]
}

/** 提交审核请求体 */
export interface SubmitPayload {
  /** 提交备注 */
  comment?: string
}

/** 审核决策请求体 */
export interface DecisionPayload {
  /** 决策：approve / reject / sign */
  decision: 'approve' | 'reject' | 'sign'
  /** 审核意见 */
  comment: string
}
