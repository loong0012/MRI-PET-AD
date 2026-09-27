/**
 * 数据脱敏审计与导出合规 —— 类型定义
 * 对应后端 routers/privacy.py：
 *   - POST /privacy/audit   脱敏审计报告
 *   - POST /privacy/mask    应用脱敏规则
 */

/** PHI 字段脱敏动作 */
export type PhiAction = 'mask' | 'drop' | 'keep'

/** 合规评级等级 */
export type ComplianceLevel = 'green' | 'yellow' | 'red'

/** 脱敏审计请求参数 */
export interface AuditParams {
  /** 要审计的病例 ID 列表 */
  caseIds: string[]
  /** 是否扫描标注自由文本中的 PHI */
  includeAnnotations?: boolean
  /** 是否包含影像路径（路径文件名可能含患者标识） */
  includeImaging?: boolean
}

/** PHI 字段命中统计 */
export interface PhiFieldHit {
  /** 字段键（首个候选键，如 name / phone / idcard） */
  field: string
  /** 中文标签 */
  label: string
  /** 命中病例数 */
  hits: number
  /** 建议动作：mask 掩码 / drop 剔除 / keep 保留 */
  action: PhiAction
  /** 动作中文标签 */
  actionLabel: string
  /** 脱敏样例值（drop 动作为空字符串） */
  sample: string
}

/** PHI 扫描结果 */
export interface PhiScanResult {
  /** PHI 命中总数 */
  totalHits: number
  /** 按字段聚合的命中明细 */
  byField: PhiFieldHit[]
  /** 标注自由文本中检测到的疑似 PHI 条数 */
  annotationHits?: number
}

/** k-匿名分组 */
export interface KAnonymityGroup {
  /** 分组名（如 男 / 60-69 / ADNI2） */
  group: string
  /** 该分组病例数 */
  count: number
  /** 是否满足 k-匿名（count >= k） */
  satisfied: boolean
}

/** k-匿名违反分组 */
export interface KAnonymityViolation {
  /** 分组名 */
  group: string
  /** 当前病例数 */
  count: number
  /** 距离 k 的差额 */
  deficit: number
}

/** k-匿名检查结果 */
export interface KAnonymityResult {
  /** k 阈值（默认 5） */
  k: number
  /** 是否整体满足 k-匿名 */
  satisfied: boolean
  /** 准标识符列表 */
  quasiIdentifiers: string[]
  /** 全部分组及满足情况 */
  groups: KAnonymityGroup[]
  /** 违反 k-匿名的分组列表 */
  violations: KAnonymityViolation[]
}

/** 合规评级结果 */
export interface ComplianceResult {
  /** 评级等级：green 可直接导出 / yellow 需脱敏后导出 / red k-匿名违反不可导出 */
  level: ComplianceLevel
  /** 等级中文说明 */
  levelText: string
  /** 综合摘要 */
  summary: string
  /** 是否允许导出 */
  canExport: boolean
  /** 警告信息列表 */
  warnings: string[]
}

/** 脱敏样例对比 */
export interface SampleCase {
  /** 样例病例 ID */
  caseId: string
  /** 原始 PHI 字段子集 */
  original: Record<string, unknown>
  /** 脱敏后字段（含年龄泛化为年龄段） */
  masked: Record<string, unknown>
}

/** 审计请求选项回显 */
export interface AuditOptions {
  includeAnnotations: boolean
  includeImaging: boolean
}

/** 脱敏审计报告 */
export interface AuditReport {
  /** 审计病例总数 */
  totalCases: number
  /** PHI 扫描结果 */
  phiScan: PhiScanResult
  /** k-匿名检查结果 */
  kAnonymity: KAnonymityResult
  /** 合规评级结果 */
  compliance: ComplianceResult
  /** 脱敏样例对比 */
  sampleCase: SampleCase
  /** 请求选项回显 */
  options?: AuditOptions
}

/** 脱敏应用请求参数 */
export interface MaskParams {
  /** 要脱敏的病例 ID 列表 */
  caseIds: string[]
  /** 指定要脱敏的字段键集合；空=全部 PHI 命中字段 */
  fields?: string[]
}

/** 单病例脱敏结果 */
export interface MaskedCase {
  /** 病例 ID */
  caseId: string
  /** 脱敏后的 patient_json */
  patientJson: Record<string, unknown>
}

/** 脱敏应用结果 */
export interface MaskResult {
  /** 脱敏后的病例列表 */
  masked: MaskedCase[]
  /** 脱敏病例总数 */
  totalMasked: number
}

/** 导出确认事件载荷 */
export interface ExportApprovedPayload {
  /** 是否应用脱敏后再导出 */
  applyMask: boolean
  /** 要脱敏的字段键集合（applyMask=true 时有效，空=全部 PHI 命中字段） */
  fields?: string[]
}
