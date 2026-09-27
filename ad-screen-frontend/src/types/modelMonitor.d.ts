/**
 * 模型性能监控类型定义
 * 跟踪生产环境中 AI 模型的实际表现：分数分布、混淆矩阵、漂移检测、性能告警
 */

/** 漂移等级 */
export type DriftLevel = 'green' | 'yellow' | 'red'

/** 告警等级 */
export type AlertLevel = 'high' | 'medium' | 'low'

/** 告警类型 */
export type AlertType = 'drift' | 'performance' | 'sampleVolume'

/** 风险分数分布响应 */
export interface ScoreDistribution {
  /** 桶标签，如 "0-10" */
  buckets: string[]
  /** 每桶计数 */
  counts: number[]
  /** 累积分布占比 % */
  cumulative: number[]
  /** 已出分病例总数 */
  totalCases: number
  /** 平均分数 */
  avgScore: number
  /** 标准差 */
  stdScore: number
  /** 时间窗口天数 */
  days: number
}

/** 性能指标 */
export interface PerformanceMetrics {
  /** 准确率 % */
  accuracy: number
  /** 灵敏度（召回率）% */
  sensitivity: number
  /** 特异度 % */
  specificity: number
  /** F1 分数 % */
  f1: number
  /** 精确率 % */
  precision: number
}

/** 混淆矩阵响应 */
export interface ConfusionMatrixResult {
  /**
   * 2x2 矩阵：[[TP, FN], [FP, TN]]
   * - TP: 正确识别 AD
   * - FN: 漏诊
   * - FP: 误诊
   * - TN: 正确识别非 AD
   */
  matrix: [[number, number], [number, number]]
  metrics: PerformanceMetrics
  /** 已审核样本总数 */
  totalReviewed: number
  /** 待审核样本数 */
  pendingCount: number
}

/** 数据漂移检测结果 */
export interface DriftDetectionResult {
  /** PSI（Population Stability Index） */
  psi: number
  /** KS 统计量 */
  ksStatistic: number
  /** 漂移等级 */
  driftLevel: DriftLevel
  /** 当前窗口分布占比 */
  currentDist: number[]
  /** 基线窗口分布占比 */
  baselineDist: number[]
  /** 当前窗口计数 */
  currentCounts: number[]
  /** 基线窗口计数 */
  baselineCounts: number[]
  /** 桶标签 */
  buckets: string[]
  /** 当前窗口样本量 */
  currentTotal: number
  /** 基线窗口样本量 */
  baselineTotal: number
  /** 时间窗口天数 */
  days: number
}

/** 单条告警 */
export interface ModelAlert {
  /** 告警类型 */
  type: AlertType
  /** 告警等级 */
  level: AlertLevel
  /** 告警消息 */
  message: string
  /** 触发值 */
  value: number
  /** 阈值 */
  threshold: number
  /** 检测时间 */
  detectedAt: string
}

/** 告警汇总 */
export interface AlertSummary {
  /** 总数 */
  total: number
  /** 高等级数量 */
  high: number
  /** 中等级数量 */
  medium: number
  /** 低等级数量 */
  low: number
}

/** 告警响应 */
export interface AlertsResult {
  alerts: ModelAlert[]
  summary: AlertSummary
}
