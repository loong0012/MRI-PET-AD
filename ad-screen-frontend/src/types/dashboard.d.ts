/** 工作台统计卡片数据 */
export interface DashboardStats {
  pendingCases: number
  completedScreening: number
  highRiskCases: number
  todayInference: number
  pendingDelta: number // 较昨日变化
  completedDelta: number
  highRiskDelta: number
  todayDelta: number
}

/** 趋势数据点 */
export interface TrendPoint {
  month: string
  total: number
  highRisk: number
}

/** 风险分布（环形图） */
export interface RiskDistItem {
  name: string
  value: number
  key: string
}

/** 近期任务 */
export interface TaskItem {
  id: string
  caseId: string
  patientName: string
  type: '初筛分析' | '复筛分析' | '随访复筛' | '报告生成'
  status: '排队中' | '执行中' | '已完成' | '失败'
  operator: string
  time: string
}

/** 分布项（模态/科室通用） */
export interface DistItem {
  name: string
  value: number
  key?: string
}

/** 看板增强分布统计 */
export interface DashboardDistribution {
  avgRiskScore: number
  scoredCount: number
  modalityDistribution: DistItem[]
  departmentDistribution: DistItem[]
  reviewPending: number
}

/** 首页"我的待办"单张卡片 */
export interface TodoItem {
  key: 'review' | 'followup' | 'warning' | 'annotation'
  title: string
  count: number
  unit: string
  /** 点击卡片跳转的业务页路径 */
  route: string
  /** 配色基调：蓝/琥珀/红/绿 */
  tone: 'blue' | 'amber' | 'red' | 'green'
  desc: string
}

/** 待办聚合响应 */
export interface TodoSummary {
  items: TodoItem[]
  total: number
}
