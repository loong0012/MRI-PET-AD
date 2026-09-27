/**
 * 科研统计分析类型定义
 */

/** 通用分布项（名称 + 数量） */
export interface DistItem {
  name: string
  value: number
}

/** 队列人群画像（年龄段 + 性别分布） */
export interface Demographics {
  /** 在库病例总数 */
  total: number
  ageGroups: DistItem[]
  genderDist: DistItem[]
}

/** 风险评分直方图桶 */
export interface ScoreBucket {
  /** 桶区间，如 "0-10" */
  bucket: string
  count: number
}

/** 风险评分直方图 */
export interface ScoreHistogram {
  /** 已出分病例总数 */
  total: number
  histogram: ScoreBucket[]
}

/** 按风险等级细分的医生审核统计 */
export interface LevelAgreement {
  /** 风险等级 key：low / mci / ad-early / ad-late */
  level: string
  levelName: string
  total: number
  approved: number
  approvalRate: number
}

/** AI 分级 vs 医生审核一致率 */
export interface AgreementStats {
  total: number
  pending: number
  approved: number
  rejected: number
  /** 已处理（approved+rejected）版本数 */
  reviewed: number
  /** 医生认可率 % */
  approvalRate: number
  byLevel: LevelAgreement[]
}

/** 随访质量统计 */
export interface FollowUpStats {
  planCount: number
  visitCount: number
  coveredCases: number
  avgMmse: number | null
  avgMoca: number | null
  cognitionDist: DistItem[]
  adherenceDist: DistItem[]
  visitTypeDist: DistItem[]
}

/** 月度筛查趋势点（按检查日期真实聚合） */
export interface MonthlyTrendPoint {
  /** YYYY-MM */
  month: string
  total: number
  highRisk: number
  /** 高风险占比 % */
  positiveRate: number
}

/** 认知功能纵向趋势点（按随访月份聚合的 MMSE / MoCA 平均） */
export interface CognitiveTrendPoint {
  /** YYYY-MM */
  month: string
  /** 当月所有随访的平均 MMSE 评分（无数据为 null） */
  avgMMSE: number | null
  /** 当月所有随访的平均 MoCA 评分（无数据为 null） */
  avgMoCA: number | null
  /** 当月随访患者数（按 case_id 去重） */
  patientCount: number
  /** 当月随访中属于 ad-early / ad-late 风险的病例数 */
  adConversionCount: number
}

// ==================== 风险因子关联分析 ====================

/** 散点 + 回归数据 */
export interface ScatterData {
  points: { x: number; y: number; caseId: string; name: string }[]
  regression: { slope: number; intercept: number; r2: number }
  xLabel: string
  yLabel: string
}

/** 箱线图分组数据 */
export interface BoxGroup {
  name: string
  values: number[]
}

/** 风险因子关联分析结果 */
export interface CorrelationResult {
  factorLabels: string[]
  /** NxN 皮尔逊相关系数矩阵 */
  matrix: number[][]
  scatter: {
    ageRisk: ScatterData
    mmseRisk: ScatterData
  }
  boxplots: {
    genderRisk: BoxGroup[]
    levelMmse: BoxGroup[]
  }
  sampleCount: number
}

// ==================== 临床路径流转桑基图 ====================

/** 桑基图节点 */
export interface PathwayNode {
  name: string
}

/** 桑基图链接 */
export interface PathwayLink {
  source: string
  target: string
  value: number
}

/** 瓶颈分析项 */
export interface PathwayBottleneck {
  stage: string
  count: number
  pct: number
}

/** 临床路径流转结果 */
export interface PathwayResult {
  nodes: PathwayNode[]
  links: PathwayLink[]
  bottlenecks: PathwayBottleneck[]
  totalCases: number
}

// ==================== 队列纵向轨迹对比 ====================

/** 队列轨迹患者 */
export interface CohortPatient {
  patientNo: string
  name: string
  gender: string
  age: number | null
  riskTrend: { date: string; score: number }[]
  mmseTrend: { date: string; mmse: number }[]
  caseCount: number
  visitCount: number
}

/** 队列轨迹响应 */
export interface CohortTrajectory {
  patients: CohortPatient[]
}
