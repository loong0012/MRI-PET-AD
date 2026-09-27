/** 多中心队列对比分析结果 */

/** 单个队列指标 */
export interface CohortGroup {
  cohort: string
  sampleCount: number
  avgScore: number
  minScore: number
  maxScore: number
  highRiskCount: number
  highRiskRate: number
  adRate: number
  levelDist: { low: number; mci: number; adEarly: number; adLate: number }
}

/** 各队列风险等级堆叠柱状图项 */
export interface CohortLevelStack {
  cohort: string
  low: number
  mci: number
  adEarly: number
  adLate: number
}

/** 各队列评分箱线图项（boxStats: [min, Q1, median, Q3, max]） */
export interface CohortScoreBox {
  cohort: string
  boxStats: [number, number, number, number, number]
}

/** Fisher 精确检验显著性结果 */
export interface CohortSignificance {
  cohortA: string
  cohortB: string
  adA: number
  totalA: number
  adB: number
  totalB: number
  pValue: number | null
}

/** 多中心队列对比结果 */
export interface CohortComparisonResult {
  groups: CohortGroup[]
  levelStack: CohortLevelStack[]
  scoreBoxplot: CohortScoreBox[]
  significance: CohortSignificance[]
  totalCases: number
}
