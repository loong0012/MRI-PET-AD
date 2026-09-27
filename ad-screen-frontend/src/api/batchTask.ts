/**
 * 批量 AI 分析任务相关接口
 * ------------------------------------------------------------------
 * 与后端 services/batch_task.py 中的 BatchTaskManager 对应，
 * 提供任务状态轮询与历史任务列表查询能力。
 */
import type { BatchTaskStatus, BatchTaskItem } from '@/types/batchTask'
import { httpGet } from '@/utils/request'

/**
 * 查询批量任务实时状态（供前端轮询进度）
 * 后端路由：GET /analysis/batch/{taskId}/status（注意 /status 后缀，缺省会 404）
 */
export function apiGetBatchTaskStatus(taskId: string): Promise<BatchTaskStatus> {
  return httpGet<BatchTaskStatus>(`/analysis/batch/${taskId}/status`)
}

/**
 * 获取批量任务列表（最近 20 条，用于历史/在跑任务概览）
 * 后端路由建议：GET /analysis/batch/tasks
 */
export function apiListBatchTasks(): Promise<BatchTaskItem[]> {
  return httpGet<BatchTaskItem[]>('/analysis/batch/tasks')
}
