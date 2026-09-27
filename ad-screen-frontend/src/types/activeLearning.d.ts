/**
 * 主动学习标注优先级队列类型定义
 * 基于模型不确定性筛选高价值待标注病例，按不确定性得分排序输出标注队列
 */

/** 审核状态 */
export type ALReviewStatus = 'pending' | 'approved' | 'rejected'

/** 建议优先级 */
export type RecommendationPriority = 'high' | 'medium' | 'low'

/** 标注优先级队列项 */
export interface QueueItem {
  /** 病例 ID（AD260001 格式） */
  caseId: string
  /** 患者姓名 */
  patientName: string
  /** 模态（MRI+PET 等） */
  modality: string
  /** 检查日期 YYYY-MM-DD */
  examDate: string
  /** AI 风险评分 0-100 */
  riskScore: number
  /** 风险等级 low/mci/ad-early/ad-late */
  riskLevel: string
  /** 审核状态 pending/approved/rejected */
  reviewStatus: ALReviewStatus
  /** 历史版本数 */
  versionCount: number
  /** 多版本 risk_score 标准差 */
  scoreStdDev: number
  /** 是否已有标注 */
  hasAnnotation: boolean
  /** 不确定性得分 0-100 */
  uncertaintyScore: number
  /** 不确定性因素列表 */
  uncertaintyFactors: string[]
  /** 标注建议 */
  suggestion: string
  /** 已有标注条数 */
  annotationCount: number
}

/** 队列统计概览（queue 接口内嵌） */
export interface QueueStats {
  /** 病例总数 */
  totalCases: number
  /** 已分析病例数 */
  analyzedCases: number
  /** 已标注病例数 */
  annotatedCases: number
  /** 高不确定性病例数（uncertainty > 70） */
  highUncertainty: number
  /** 平均不确定性得分 */
  avgUncertainty: number
}

/** 标注优先级队列响应 */
export interface QueueResult {
  /** 队列（按不确定性降序） */
  queue: QueueItem[]
  /** 本次返回条数 */
  total: number
  /** 全局统计概览 */
  stats: QueueStats
}

/** 不确定性分布桶 */
export interface UncertaintyBucket {
  /** 桶标签，如 "0-10" */
  bucket: string
  count: number
}

/** 模型稳定性统计 */
export interface ModelStability {
  /** 多版本病例 risk_score 平均标准差 */
  avgStdDev: number
  /** 不稳定病例数（多版本波动大） */
  unstableCases: number
}

/** 主动学习统计概览 */
export interface LearningStats {
  /** 病例总数 */
  totalCases: number
  /** 已分析病例数 */
  analyzedCases: number
  /** 已标注病例数 */
  annotatedCases: number
  /** 待审核病例数 */
  pendingReview: number
  /** 高不确定性病例数 */
  highUncertainty: number
  /** 不确定性分布（10 桶 0-100） */
  uncertaintyDistribution: UncertaintyBucket[]
  /** 模型稳定性 */
  modelStability: ModelStability
}

/** 分配标注任务请求体 */
export interface AssignPayload {
  /** 病例 ID 列表 */
  caseIds: string[]
  /** 分配人（标注人账号） */
  assignee: string
}

/** 分配标注任务响应 */
export interface AssignResult {
  /** 已分配条数 */
  assigned: number
  /** 分配人 */
  assignee: string
}

/** 单条模型迭代建议 */
export interface RecommendationItem {
  /** 建议类型：highUncertainty / modelStability / misdiagnosis */
  type: string
  /** 优先级 high/medium/low */
  priority: RecommendationPriority
  /** 建议消息 */
  message: string
  /** 触发指标（含数值与阈值） */
  metric: string
}

/** 模型迭代建议响应 */
export interface Recommendations {
  /** 建议列表 */
  recommendations: RecommendationItem[]
  /** 模型健康分 0-100 */
  healthScore: number
}

/** 队列筛选参数 */
export interface QueueParams {
  /** 返回队列长度，默认 20 */
  limit?: number
  /** 仅返回未标注病例 */
  onlyUnannotated?: boolean
  /** 仅返回高不确定性病例（>70） */
  onlyHighUncertainty?: boolean
}
