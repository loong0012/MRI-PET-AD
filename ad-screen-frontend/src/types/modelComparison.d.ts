/**
 * AI 模型版本对比类型定义
 * 不同模型版本在真实病例库上的表现对比
 */

/** 模型对比指标 */
export interface ModelComparisonItem {
  modelVersion: string
  sampleCount: number
  avgScore: number
  minScore: number
  maxScore: number
  highRiskCount: number
  highRiskRate: number
  approvedCount: number
  rejectedCount: number
  pendingCount: number
  approvalRate: number
  rejectionRate: number
  levelDist: {
    low: number
    mci: number
    'ad-early': number
    'ad-late': number
  }
}

/** 评分分布对比 */
export interface ModelScoreDist {
  modelVersion: string
  buckets: string[]
  counts: number[]
}

/** 模型对比结果 */
export interface ModelComparisonResult {
  models: ModelComparisonItem[]
  scoreDist: ModelScoreDist[]
  totalVersions: number
  totalCases: number
}

/** 批量随访计划预览项 */
export interface BatchFollowUpPreviewItem {
  caseId: string
  patientName: string
  gender: string
  age: number | null
  modality: string
  riskLevel: string
  riskScore: number | null
  hasPlan: boolean
  cycleMonths: number
  nextDate: string
}

/** 批量随访计划预览结果 */
export interface BatchFollowUpPreview {
  list: BatchFollowUpPreviewItem[]
  total: number
  newCount: number
  updateCount: number
  scope: string
}

/** 批量生成结果 */
export interface BatchGenerateResult {
  created: number
  updated: number
  total: number
}
