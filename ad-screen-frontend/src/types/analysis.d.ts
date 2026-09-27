import type { RiskLevel } from './case.d'

/** 异常脑区条目 */
export interface AbnormalRegion {
  region: string
  side: '左侧' | '右侧' | '双侧'
  atrophy: '轻度萎缩' | '中度萎缩' | '重度萎缩'
  metabolism: number // rCMRglc 相对值
  zScore: number // 与正常对照偏差
}

/** 量化指标 */
export interface QuantMetric {
  key: string
  label: string
  value: number
  unit: string
  refRange: string
  status: 'normal' | 'warn' | 'abnormal'
}

/** AI 多模态融合分析结果 */
export interface AnalysisResult {
  caseId: string
  riskScore: number // 0-100 AD 风险评分
  riskLevel: RiskLevel
  stage: string // 病程分期，如 "MCI（轻度认知障碍）"
  stageCode: 'CN' | 'MCI' | 'AD-E' | 'AD-L'
  stageDesc: string
  confidence: number // 模型推理置信度 0-1
  hippocampusVolumeL: number // 左侧海马体积 cm³
  hippocampusVolumeR: number
  meanSUV: number // PET 平均脑代谢 SUV
  corticalThickness: number // 皮层平均厚度 mm
  ventricleVolume: number // 脑室体积 cm³
  mtaScore: string // 内侧颞叶萎缩视觉评分
  abnormalRegions: AbnormalRegion[]
  metrics: QuantMetric[]
  summary: string // AI 综合诊断摘要
  modelVersion: string
  fusionStrategy: 'feature' | 'pixel'
  inferenceTime: number // 推理耗时（秒）
  finishTime: string
  /** 推理来源：real=真实 TransMF 模型；simulated=模拟演示（缺省按模拟处理） */
  inferenceSource?: 'real' | 'simulated'
  /** 真实模型集成 AD 概率（0-1） */
  ensembleProb?: number
  /** 集成中单模型 AD 概率区间 */
  probMin?: number
  probMax?: number
  /** 集成概率标准差（模型一致度） */
  probStd?: number
  /** 集成模型数量（75 个快照） */
  modelCount?: number
  /** 推理设备 cuda:0 / cpu */
  device?: string
  /** 2026 版 PET/MRI 指南：生物学分期（Stage 0/A/B/C/D） */
  biologicalStage?: string
  biologicalStageDesc?: string
  /** ARIA 风险评估（抗 Aβ 单抗治疗相关） */
  ariaRisk?: AriaRisk
  /** 2026 版指南：图像质量控制（运动伪影 + PET-MRI 配准质量 + 头皮活动 + 注射点漏液） */
  imageQuality?: ImageQuality
  /** 2026 版指南：PET 视觉判读征象（Aβ 阴/阳性征象、FDG 代谢模式、tau 分布） */
  petPatterns?: PetPatterns
  /** 显像剂类型（fdg / amyloid / tau） */
  petTracer?: 'fdg' | 'amyloid' | 'tau'
  /** 2026 版指南 表2：PET 显像剂推荐参数（剂量/等待/采集/半衰期/适应证） */
  tracerParameters?: TracerParameters
  /** 2026 版指南：检查前准备规则清单（按显像剂生成） */
  prepInstructions?: PrepInstructions
  /** 2026 版指南：MRI 定量评估工具（SPM/CAT12/Freesurfer） */
  mriQuantification?: MriQuantification
  /** 2026 版指南：标准化成像协议参数 */
  scannerInfo?: ScannerInfo
}

/** 2026 版指南 表2：PET 显像剂推荐参数 */
export interface TracerParameters {
  code: string
  name: string
  cnName: string
  type: 'metabolic' | 'amyloid' | 'tau'
  typeName: string
  dose: string
  uptakeMinutes: number
  acquisitionMinutes: number
  halfLife: string
  indication: string
  suvrThreshold?: number
}

/** 2026 版指南：检查前准备规则清单 */
export interface PrepInstructions {
  tracerType: string
  tracerName: string
  items: string[]
  fastingRequired: boolean
  glucoseControl: boolean
  specialNotes: string[]
  longitudinalProtocol: string
}

/** 2026 版指南：MRI 定量评估工具（SPM/CAT12/Freesurfer） */
export interface MriQuantification {
  tools: Array<{
    code: string
    name: string
    outputs: string[]
    purpose: string
  }>
  metrics: {
    totalGrayMatterVolume: number
    meanCorticalThickness: number
    hippocampalSubfields: Array<{
      name: string
      left: number
      right: number
    }>
    unit: { volume: string; thickness: string }
  }
  atrophyPattern: string
  mtaDetail?: MtaDetail
  fazekasDetail?: FazekasDetail
  swiFindings?: SwiFindings
  status: 'simulated' | 'real'
  note: string
}

/** 2026 版指南：MTA 视觉评分详细描述（T1WI 斜冠状位） */
export interface MtaDetail {
  score: number
  slicePosition: string
  grading: Array<{ grade: number; desc: string }>
  currentGrade: { grade: number; desc: string }
  abnormalThreshold: number
  isAbnormal: boolean
  ageAdjustment: string
}

/** 2026 版指南：Fazekas 评分详细描述（PV-WMH + DW-WMH） */
export interface FazekasDetail {
  totalScore: number
  pvWmh: { grade: number; desc: string }
  dwWmh: { grade: number; desc: string }
  isAbnormal: boolean
  clinicalSignificance: string
  imagingSequence: string
}

/** 2026 版指南：SWI 序列出血点检测（ARIA 监测） */
export interface SwiFindings {
  sequence: string
  microBleedCount: number
  microBleedLocations: string[]
  hemosiderinDeposit: boolean
  hemosiderinNote: string
  ariaRelevance: string
  status: 'simulated' | 'real'
}

/** 2026 版指南：标准化成像协议参数（纵向随访一致性校验基础） */
export interface ScannerInfo {
  manufacturer: string
  model: string
  fieldStrength: string
  petDetector: string
  reconMethod: string
  mriSequences: string
  longitudinalConsistency: string
  status: 'simulated' | 'real'
}

/** ARIA 风险评估 */
export interface AriaRisk {
  riskLevel: '低风险' | '中风险' | '高风险'
  riskScore: number
  factors: string[]
  recommendation: string
}

/** 图像质量控制（指南"图像质量控制"章节） */
export interface ImageQuality {
  motionArtifact: boolean // 是否存在运动伪影
  motionScore: number // 运动评分 0-1
  registrationQuality: 'good' | 'acceptable' | 'poor' // PET-MRI 配准质量
  registrationScore: number // 配准评分 0-1
  scalpActivity?: boolean // 头皮活动性摄取
  injectionLeak?: boolean // 注射点漏液
  warnings: string[]
  recommendation: string
  /** 第 28 轮新增：图像质量失败时的重新采集参数建议 */
  acquisitionAdjustment?: AcquisitionAdjustment | null
}

/** 2026 版指南：图像质量失败处置流程 */
export interface AcquisitionAdjustment {
  required: boolean
  reason: string
  adjustments: string[]
  priority: 'high' | 'medium' | 'low'
  reimbursementNote: string
}

/** PET 视觉判读征象（指南"图像分析-PET"章节） */
export interface PetPatterns {
  tracer: 'fdg' | 'amyloid' | 'tau'
  tracerName: string
  positive: boolean | null
  patterns: string[] // Aβ 阴/阳性征象标签
  conclusion: string
  affectedRegions: string[]
  suvr: number | null
  suvrThreshold: number | null
  thresholdNote: string
  braakStage?: string
  biologicalStageHint?: string
  quantificationTool?: string
  /** 第 28 轮新增：显像剂特异性伪影识别（脱靶摄取 / 白质生理性摄取等） */
  tracerSpecificArtifacts?: Array<{ artifact: string; note: string }>
}

/** 推理进度回调 */
export interface InferenceProgress {
  progress: number // 0-100
  stage: string // 当前阶段文案
}

/** 干预模块标识 */
export type InterventionKey = 'cognitive' | 'lifestyle' | 'followup' | 'clinical'

/** 单条干预方案条目 */
export interface InterventionItem {
  id: string
  title: string
  content: string
  frequency: string
  duration: string
  source: 'AI' | '医生'
}

/** 干预方案模块 */
export interface InterventionSection {
  key: InterventionKey
  title: string
  items: InterventionItem[]
}

/** 随访计划 */
export interface FollowUpPlan {
  cycleMonths: number
  nextDate: string
  reminders: string[] // 提醒日期列表
  note: string
}
