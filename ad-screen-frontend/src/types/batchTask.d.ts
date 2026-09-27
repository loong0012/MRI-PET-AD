/** 批量任务中的单病例分析结果 */
export interface BatchTaskResultItem {
  caseId: string
  status: string
  riskScore?: number
  riskLevel?: string
  error?: string
}

/** 批量任务实时状态（轮询返回） */
export interface BatchTaskStatus {
  taskId: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  total: number
  done: number
  currentCaseId: string
  results: BatchTaskResultItem[]
  error: string
  createdAt: number
  startedAt: number
  finishedAt: number
}

/** 批量任务列表条目（历史/在跑任务概览） */
export interface BatchTaskItem {
  taskId: string
  status: string
  total: number
  done: number
  createdAt: number
}
