/** 影像模态 */
export type Modality = 'MRI' | 'PET' | 'MRI+PET'

/** AI 四级风险等级 */
export type RiskLevel = 'low' | 'mci' | 'ad-early' | 'ad-late'

/** 病例流转状态 */
export type CaseStatus = 'pending' | 'analyzing' | 'completed' | 'reported'

/** 诊断状态（业务流转展示） */
export type DiagStatus = '待AI分析' | 'AI分析中' | 'AI已分析' | '医生已审核' | '已出报告'

/** 患者基础信息 */
export interface CasePatient {
  patientNo: string
  name: string
  gender: 'M' | 'F'
  age: number
}

/** 检查适应证（2026 版 PET/MRI 指南四类） */
export type ExamIndication = 'diagnosis' | 'staging' | 'pre_dmt' | 'dmt_monitoring'

/** PET 显像剂类型（指南表2） */
export type PetTracer = 'fdg' | 'amyloid' | 'tau'

/** 绝对禁忌（指南"检查前评估"：MRI 不兼容有源植入物） */
export type AbsoluteContraindication =
  | 'pacemaker'
  | 'defibrillator'
  | 'cochlear_implant'
  | 'dbs'
  | 'insulin_pump'

/** 相对禁忌（指南"检查前评估"：金属植入物需确认 MRI 安全等级） */
export type RelativeContraindication =
  | 'steel_nails'
  | 'artificial_joint'
  | 'aneurysm_clip'
  | 'coronary_stent'
  | 'removable_dentures'
  | 'claustrophobia'
  | 'diabetes_uncontrolled'

/** 禁忌证筛查结果（指南"检查前评估"） */
export interface Contraindications {
  absolute: AbsoluteContraindication[]
  relative: RelativeContraindication[]
  cleared: boolean
  warnings: string[]
}

/** 特殊人群标记（指南"检查前准备"） */
export type SpecialPopulation =
  | 'normal'
  | 'down_synodrome'
  | 'claustrophobia'
  | 'diabetes'
  | 'implanted_device'

/** 病例记录 */
export interface CaseRecord {
  id: string
  patient: CasePatient
  modality: Modality
  examDate: string
  department: string
  status: CaseStatus
  diagStatus: DiagStatus
  riskLevel: RiskLevel | null
  riskScore: number | null
  hasMRI: boolean
  hasPET: boolean
  createTime: string
  cloudSaved: boolean
  examIndication?: ExamIndication
  petTracer?: PetTracer
  contraindications?: Contraindications
  specialPopulation?: SpecialPopulation
}

/** 影像上传载荷 */
export interface UploadPayload {
  patientNo: string
  patientName: string
  gender: 'M' | 'F'
  age: number
  modality: Modality
  department: string
  examIndication?: ExamIndication
  petTracer?: PetTracer
  absoluteContraindications?: AbsoluteContraindication[]
  relativeContraindications?: RelativeContraindication[]
  specialPopulation?: SpecialPopulation
  files: File[]
}

/** 病例列表查询条件 */
export interface CaseQuery {
  keyword: string
  modality: Modality | ''
  status: CaseStatus | ''
  riskLevel: RiskLevel | ''
  diagStatus: string
  department: string
  gender: string
  ageMin: number | null
  ageMax: number | null
  hasFollowup: boolean | null
  favorites: boolean | null
  dateRange: [string, string] | null
  page: number
  pageSize: number
}

/** 分页通用返回 */
export interface PageResult<T> {
  list: T[]
  total: number
}

/** 全局搜索结果（精简字段，顶栏下拉） */
export interface SearchCaseItem {
  id: string
  patientName: string
  modality: Modality
  riskLevel: RiskLevel | null
  riskScore: number | null
  examDate: string
  hasAnalysis: boolean
}

/** 相似病例推荐结果（top-k 综合匹配） */
export interface SimilarCase {
  caseId: string
  patientName: string
  age: number
  gender: 'M' | 'F'
  riskScore: number
  riskLevel: RiskLevel
  stageCode: 'CN' | 'MCI' | 'AD-E' | 'AD-L'
  abnormalRegionCount: number
  examDate: string
}
