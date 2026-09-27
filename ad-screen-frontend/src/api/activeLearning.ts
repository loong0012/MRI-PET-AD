import type {
  QueueItem,
  QueueResult,
  QueueStats,
  LearningStats,
  AssignPayload,
  AssignResult,
  Recommendations,
  RecommendationItem,
  QueueParams
} from '@/types/activeLearning'
import { httpGet, httpPost } from '@/utils/request'

/**
 * 主动学习标注优先级队列 API（researcher / admin）
 * 基于模型不确定性筛选高价值待标注病例，按不确定性得分排序输出标注队列。
 * - GET  /active-learning/queue           获取标注优先级队列
 * - GET  /active-learning/stats           主动学习统计概览
 * - POST /active-learning/assign          分配标注任务
 * - GET  /active-learning/recommendations 模型迭代建议
 */

/** 获取标注优先级队列（按不确定性得分降序） */
export function apiGetQueue(params?: QueueParams): Promise<QueueResult> {
  return httpGet<QueueResult>('/active-learning/queue', {
    limit: params?.limit ?? 20,
    onlyUnannotated: params?.onlyUnannotated ?? false,
    onlyHighUncertainty: params?.onlyHighUncertainty ?? false
  })
}

/** 主动学习统计概览（不确定性分布 + 模型稳定性） */
export function apiGetStats(): Promise<LearningStats> {
  return httpGet<LearningStats>('/active-learning/stats')
}

/** 分配标注任务（内存存储，不强制建表） */
export function apiAssignTask(payload: AssignPayload): Promise<AssignResult> {
  return httpPost<AssignResult>('/active-learning/assign', payload)
}

/** 模型迭代建议 + 模型健康分 */
export function apiGetRecommendations(): Promise<Recommendations> {
  return httpGet<Recommendations>('/active-learning/recommendations')
}

// 类型重导出，便于调用方统一引用
export type {
  QueueItem,
  QueueResult,
  QueueStats,
  LearningStats,
  AssignPayload,
  AssignResult,
  Recommendations,
  RecommendationItem,
  QueueParams
}
