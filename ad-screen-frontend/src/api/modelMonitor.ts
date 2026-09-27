import type {
  ScoreDistribution,
  ConfusionMatrixResult,
  DriftDetectionResult,
  AlertsResult
} from '@/types/modelMonitor'
import { httpGet } from '@/utils/request'

/**
 * 模型性能监控 API（researcher / admin）
 * 跟踪生产环境 AI 模型实际表现：分数分布、混淆矩阵、漂移检测、性能告警
 */

/** 查询参数（时间窗口天数：7 / 30 / 90） */
export interface ModelMonitorParams {
  days?: number
}

/** 风险分数分布直方图 + 累积分布（10 桶 0-100） */
export function apiGetScoreDistribution(params?: ModelMonitorParams): Promise<ScoreDistribution> {
  return httpGet<ScoreDistribution>('/model-monitor/score-distribution', {
    days: params?.days ?? 30
  })
}

/** 混淆矩阵与性能指标（用 review_status 作为代理真实标签） */
export function apiGetConfusionMatrix(): Promise<ConfusionMatrixResult> {
  return httpGet<ConfusionMatrixResult>('/model-monitor/confusion-matrix')
}

/** 数据漂移检测（PSI + KS 统计量，当前窗口 vs 历史基线） */
export function apiGetDriftDetection(params?: ModelMonitorParams): Promise<DriftDetectionResult> {
  return httpGet<DriftDetectionResult>('/model-monitor/drift-detection', {
    days: params?.days ?? 30
  })
}

/** 模型性能告警（漂移 / 性能下降 / 样本量） */
export function apiGetAlerts(): Promise<AlertsResult> {
  return httpGet<AlertsResult>('/model-monitor/alerts')
}
