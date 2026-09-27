import type { CaseRecord } from './case.d'
import type { AnalysisResult, InterventionSection, FollowUpPlan } from './analysis.d'

/** 报告模板 */
export interface ReportTemplate {
  key: 'standard' | 'brief' | 'research' | 'patient'
  name: string
  desc: string
}

/** NIA-AA A/T/N 框架对齐（基于影像代理，无 CSF 数据） */
export interface NiaaAlignment {
  A: 'A+' | 'A-' | 'A_unknown'
  A_reason: string
  T: 'T+' | 'T-'
  T_reason: string
  N: 'N+' | 'N-'
  N_reason: string
  /** 2026 版指南：生物学分期（Stage 0/A/B/C/D） */
  biologicalStage?: string
  biologicalStageDesc?: string
  reasoning: string
}

/** 矛盾证据：影像-认知 / 模态间不一致提示 */
export interface ConflictEvidence {
  hasConflict: boolean
  items: Array<{ type: string; description: string }>
}

/** 患者通俗科普版（按病程分期映射） */
export interface PatientFriendly {
  summary: string
  lifestyle: string[]
  followUp: string
  /** 家属常见问题（通俗科普，第 32 轮新增） */
  faq?: Array<{ q: string; a: string }>
  /** 照护者日常建议（第 32 轮新增） */
  caregiverAdvice?: string[]
  /** 需立即就诊的警示信号（第 32 轮新增） */
  warningSigns?: string[]
}

/** 报告聚合数据 */
export interface ReportData {
  caseInfo: CaseRecord
  analysis: AnalysisResult
  interventions: InterventionSection[]
  followUp: FollowUpPlan
  reportNo: string
  reportDate: string
  hospital: string
  /** 系统品牌信息 */
  systemName?: string
  systemVersion?: string
  modelVersion?: string
  guideline?: string
  doctorName: string
  /** NIA-AA A/T/N 框架对齐（规则驱动；旧报告可能缺失） */
  niaaAlignment?: NiaaAlignment | null
  /** 矛盾证据（规则驱动；旧报告可能缺失） */
  conflictEvidence?: ConflictEvidence | null
  /** 患者通俗科普版（规则驱动；旧报告可能缺失） */
  patientFriendly?: PatientFriendly | null
  /** 2026 版指南：ARIA 风险评估 */
  ariaRisk?: import('./analysis.d').AriaRisk | null
  /** 2026 版指南：检查适应证 */
  examIndication?: string
  /** 2026 版指南：PET 显像剂类型 */
  petTracer?: string
  /** 2026 版指南表5：结构化检查所见（MRI + PET 分节） */
  examFindings?: ExamFindings
  /** 2026 版指南表5：5 段式诊断意见（FDG/Aβ/tau/MRI/A-T-N综合） */
  diagnosticConclusions?: DiagnosticConclusions
  /** 2026 版指南：图像质量控制 */
  imageQuality?: import('./analysis.d').ImageQuality | null
  /** 2026 版指南：禁忌证筛查结果 */
  contraindications?: import('./case.d').Contraindications | null
  /** 2026 版指南：特殊人群标记（中文展示） */
  specialPopulation?: string
  /** 2026 版指南：特殊人群标记代码 */
  specialPopulationCode?: string
  /** 2026 版指南：7 条推荐意见及等级汇总 */
  recommendationsSummary?: RecommendationsSummary
  /** 2026 版指南：临床决策辅助（按推荐意见生成下一步建议） */
  clinicalDecisionSupport?: ClinicalDecisionSupport
}

/** 2026 版指南：7 条推荐意见及等级汇总 */
export interface RecommendationsSummary {
  title: string
  totalCount: number
  appliedCount: number
  recommendations: Array<{
    id: number
    topic: string
    recommendation: string
    grade: string
    applied: boolean
    note: string
  }>
  guidelineReference: string
}

/** 2026 版指南：临床决策辅助（按推荐意见生成下一步建议） */
export interface ClinicalDecisionSupport {
  title: string
  totalRecommendations: number
  recommendations: Array<{
    category: string
    priority: 'high' | 'medium' | 'low'
    action: string
    guidelineRef: string
  }>
  guidelineReference: string
}

/** 指南表5：检查所见（结构化分节） */
export interface ExamFindings {
  mriFindings: MriFindings
  petFindings: PetFindings
}

/** MRI 表现（指南表5：对称性/白质高信号/脑室脑沟/海马萎缩/SWI 出血点） */
export interface MriFindings {
  symmetry: string
  whiteMatterHyperintensity: string
  ventricleSulci: string
  hippocampusAtrophy: string
  hippocampusVolumeL: number
  hippocampusVolumeR: number
  corticalThickness: number
  swiHemorrhage: string
}

/** PET 检查所见（按显像剂类型分支） */
export interface PetFindings {
  tracer: 'fdg' | 'amyloid' | 'tau'
  tracerName: string
  fdgFindings?: {
    metabolicReduction: string[]
    patternDescription: string
    meanSUV: number | null
  }
  amyloidFindings?: {
    positive: boolean
    patterns: string[]
    conclusion: string
    suvr: number | null
    affectedRegions: string[]
  }
  tauFindings?: {
    positive: boolean
    distribution: string
    braakStage: string
    suvr: number | null
    suvrThreshold: number | null
    affectedRegions: string[]
  }
}

/** 指南表5：5 段式诊断意见 */
export interface DiagnosticConclusions {
  sections: DiagnosticConclusionSection[]
}

/** 单条诊断意见 */
export interface DiagnosticConclusionSection {
  title: string
  content: string
  // 各段附加的结构化字段（按 section.title 取用）
  meanSUV?: number
  positive?: boolean | null
  suvr?: number | null
  braakStage?: string | null
  biologicalStage?: string
  mta?: string
  fazekas?: string
  ariaRisk?: string
  aLabel?: string
  tLabel?: string
  nLabel?: string
  clinicalStage?: string
}
