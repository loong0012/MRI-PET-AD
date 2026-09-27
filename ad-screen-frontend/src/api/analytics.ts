import type {
  Demographics,
  ScoreHistogram,
  AgreementStats,
  FollowUpStats,
  MonthlyTrendPoint,
  CognitiveTrendPoint,
  CorrelationResult,
  PathwayResult
} from '@/types/analytics'
import type { ModelComparisonResult } from '@/types/modelComparison.d'
import type { CohortComparisonResult } from '@/types/cohortComparison.d'
import { httpGet } from '@/utils/request'

/**
 * 科研统计分析 API（researcher / admin）
 * 全部基于真实病例库聚合（后端过滤软删除），无 mock 数据。
 * modality: MRI / PET / MRI+PET，空串 = 全部模态。
 */

/** 通用查询参数 */
export interface AnalyticsParams {
  modality?: string
  months?: number
}

/** 队列人群画像（年龄段 / 性别分布） */
export function apiGetDemographics(params?: AnalyticsParams): Promise<Demographics> {
  return httpGet<Demographics>('/analytics/demographics', { modality: params?.modality ?? '' })
}

/** AI 风险评分直方图 */
export function apiGetScoreHistogram(params?: AnalyticsParams): Promise<ScoreHistogram> {
  return httpGet<ScoreHistogram>('/analytics/score-histogram', { modality: params?.modality ?? '' })
}

/** AI 分级 vs 医生审核一致率（每病例最新版本口径） */
export function apiGetAgreement(params?: AnalyticsParams): Promise<AgreementStats> {
  return httpGet<AgreementStats>('/analytics/agreement', { modality: params?.modality ?? '' })
}

/** 随访质量统计 */
export function apiGetFollowUpStats(params?: AnalyticsParams): Promise<FollowUpStats> {
  return httpGet<FollowUpStats>('/analytics/followup-stats', { modality: params?.modality ?? '' })
}

/** 近 N 个月筛查量与高风险占比（months: 3/6/12） */
export function apiGetMonthlyTrend(params?: AnalyticsParams): Promise<MonthlyTrendPoint[]> {
  return httpGet<MonthlyTrendPoint[]>('/analytics/monthly-trend', {
    modality: params?.modality ?? '',
    months: params?.months ?? 6
  })
}

/** 认知功能纵向趋势（按随访月份聚合的 MMSE / MoCA 平均，默认近 12 个月） */
export function apiGetCognitiveTrend(months = 12): Promise<CognitiveTrendPoint[]> {
  return httpGet<CognitiveTrendPoint[]>('/analytics/cognitive-trend', { months })
}

/** 风险因子关联分析（皮尔逊矩阵 + 散点回归 + 分组箱线图） */
export function apiGetCorrelation(params?: AnalyticsParams): Promise<CorrelationResult> {
  return httpGet<CorrelationResult>('/analytics/correlation', {
    modality: params?.modality ?? ''
  })
}

/** 临床路径流转桑基图（阶段分布 + 瓶颈分析） */
export function apiGetPathway(params?: AnalyticsParams): Promise<PathwayResult> {
  return httpGet<PathwayResult>('/analytics/pathway', {
    modality: params?.modality ?? ''
  })
}

/** AI 模型版本对比（A/B 测试视角） */
export function apiGetModelComparison(params?: AnalyticsParams): Promise<ModelComparisonResult> {
  return httpGet<ModelComparisonResult>('/analytics/model-comparison', {
    modality: params?.modality ?? ''
  })
}

/** 多中心队列对比分析 */
export function apiGetCohortComparison(params?: AnalyticsParams): Promise<CohortComparisonResult> {
  return httpGet<CohortComparisonResult>('/analytics/cohort-comparison', {
    modality: params?.modality ?? ''
  })
}
