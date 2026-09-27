/**
 * 报告模板类型定义
 * - 多套模板可切换：简版(brief) / 详版(detailed) / 科研版(research) / 患者科普版(patient)
 * - contentStructure 控制报告各模块显隐（13 个字段：10 基础 + 3 新增）
 * - headerConfig 渲染报告抬头（医院/科室/标题/logo URL）
 */

/** 模板类型 */
export type ReportTemplateType = 'brief' | 'detailed' | 'research' | 'patient'

/** 抬头配置 */
export interface ReportHeaderConfig {
  hospitalName: string
  department: string
  logoUrl: string
  title: string
  subtitle?: string
  systemName?: string
  systemVersion?: string
  modelVersion?: string
  guideline?: string
}

/** 报告模板（含 id 等元信息） */
export interface ReportTemplate {
  id: number
  name: string
  templateType: ReportTemplateType
  contentStructure: Record<string, boolean>
  headerConfig: ReportHeaderConfig
  isDefault: boolean
  createdBy: string
  createdAt: string
  updatedAt?: string
}

/** 模板新增 / 编辑请求体 */
export interface TemplateSaveBody {
  name: string
  templateType: ReportTemplateType
  contentStructure: Record<string, boolean>
  headerConfig: ReportHeaderConfig
  isDefault: boolean
}

/** 模板列表响应 */
export interface ReportTemplateListResult {
  list: ReportTemplate[]
  total: number
}
