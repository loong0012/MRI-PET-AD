import type { DashboardStats, TaskItem, TrendPoint, RiskDistItem, DashboardDistribution, TodoSummary } from '@/types/dashboard'
import { httpGet } from '@/utils/request'
import { dashboardStats, trendData, riskDistribution, recentTasks, dashboardDistributions } from '@/mock/db'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

/** 工作台统计卡片 */
export function apiGetDashboardStats(): Promise<DashboardStats> {
  if (useMock) return Promise.resolve(dashboardStats())
  return httpGet<DashboardStats>('/dashboard/stats')
}

/** 近 6 个月筛查趋势 */
export function apiGetTrend(): Promise<TrendPoint[]> {
  if (useMock) return Promise.resolve(trendData())
  return httpGet<TrendPoint[]>('/dashboard/trend')
}

/** 风险等级分布 */
export function apiGetRiskDistribution(): Promise<RiskDistItem[]> {
  if (useMock) return Promise.resolve(riskDistribution())
  return httpGet<RiskDistItem[]>('/dashboard/risk-distribution')
}

/** 近期任务列表 */
export function apiGetRecentTasks(): Promise<TaskItem[]> {
  if (useMock) return Promise.resolve(recentTasks())
  return httpGet<TaskItem[]>('/dashboard/recent-tasks')
}

/** 看板增强分布统计（平均风险分 / 模态分布 / 科室分布 / 待审核数） */
export function apiGetDistributions(): Promise<DashboardDistribution> {
  if (useMock) return Promise.resolve(dashboardDistributions())
  return httpGet<DashboardDistribution>('/dashboard/distributions')
}

/** 首页"我的待办"聚合（按当前角色返回，Mock 模式无该业务概念，返回空） */
export function apiGetTodoSummary(): Promise<TodoSummary> {
  if (useMock) return Promise.resolve({ items: [], total: 0 })
  return httpGet<TodoSummary>('/dashboard/todo-summary')
}
