/**
 * CDSS 临床决策支持系统类型定义
 * 基于规则引擎的多维度诊疗建议
 */

/** CDSS 修正因子 */
export interface CdssModifier {
  key: string
  label: string
  impact: 'level+1' | 'watch' | 'referral'
}

/** 药物相互作用警示条目（第 32 轮新增） */
export interface MedicationInteractionItem {
  /** 触发警示的抗痴呆药物键：donepezil / memantine */
  drug: string
  /** 冲突药物关键词列表 */
  targetKeywords: string[]
  /** 相互作用机制描述 */
  mechanism: string
  /** 严重度：severe(禁用) / caution(慎用) / monitor(需监测) */
  severity: 'severe' | 'caution' | 'monitor'
  severityName: string
  /** 处置建议 */
  recommendation: string
}

/** 药物相互作用警示聚合（第 32 轮新增） */
export interface MedicationInteractions {
  hasWarnings: boolean
  items: MedicationInteractionItem[]
  /** 本次推荐用药（按风险等级映射） */
  recommendedDrugs: string[]
  /** 从随访 notes 解析到的当前用药 */
  currentMedsDetected: string[]
  summary: string
}

/** 照护者负担评估结果（第 32 轮新增，Zarit 简化代理评分） */
export interface CaregiverAssessment {
  /** 负担风险等级：low / mild / moderate / high */
  burdenRisk: string
  burdenRiskName: string
  /** Zarit 简化代理评分 0-88 */
  zaritProxyScore: number
  scoreRange: string
  /** 修正因子描述列表 */
  modifiers: string[]
  /** 分级支持建议（照护者维度） */
  supportRecommendations: string[]
  /** 需立即专业介入的警示信号 */
  warningSigns: string[]
}

/** CDSS 单病例建议 */
export interface CdssRecommendation {
  caseId: string
  patientName: string
  age: number
  gender: string
  /** 优先级：urgent / high / moderate / low */
  priority: string
  priorityName: string
  priorityColor: string
  priorityDesc: string
  /** 风险等级：low / mci / ad-early / ad-late */
  level: string
  levelName: string
  measures: string[]
  referral: string
  medication: string
  modifiers: CdssModifier[]
  reasoning: string[]
  riskScore: number
  mmse: number | null
  moca: number | null
  cognitionTrend: string
  medicationAdherence: string
  generatedAt: string
  disclaimer: string
  /** 药物相互作用警示（第 32 轮新增；旧记录可能缺失） */
  medicationInteractions?: MedicationInteractions
  /** 照护者负担评估（第 32 轮新增；旧记录可能缺失） */
  caregiverAssessment?: CaregiverAssessment
}

/** CDSS 批量列表项 */
export interface CdssListItem extends CdssRecommendation {}

/** CDSS 分布统计 */
export interface CdssStats {
  total: number
  priorityDist: { key: string; name: string; value: number; color: string }[]
  levelDist: { key: string; name: string; value: number }[]
  modifierDist: { key: string; name: string; value: number }[]
}

/** CDSS 规则配置 */
export interface CdssRule {
  id: string
  name: string
  desc: string
  type: 'base' | 'modifier' | 'safety' | 'support'
  priority: string
}

/** CDSS 批量分页结果 */
export interface CdssListResult {
  list: CdssListItem[]
  total: number
  page: number
  pageSize: number
}
