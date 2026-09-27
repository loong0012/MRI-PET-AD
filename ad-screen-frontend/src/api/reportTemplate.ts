/**
 * 报告模板管理 API
 * - 模板列表（首次访问自动初始化 3 套默认模板）
 * - 模板详情 / 新增 / 编辑 / 删除 / 设为默认
 */
import { httpGet, httpPost, httpPut, httpDelete } from '@/utils/request'
import type {
  ReportTemplate,
  ReportTemplateListResult,
  TemplateSaveBody
} from '@/types/reportTemplate.d'

/** 模板列表（按 isDefault DESC, createdAt DESC 排序） */
export function apiListReportTemplates(): Promise<ReportTemplateListResult> {
  return httpGet<ReportTemplateListResult>('/report-template/list')
}

/** 模板详情 */
export function apiGetReportTemplate(templateId: number): Promise<ReportTemplate> {
  return httpGet<ReportTemplate>(`/report-template/${templateId}`)
}

/** 新增模板；返回 { id } */
export function apiCreateReportTemplate(body: TemplateSaveBody): Promise<{ id: number }> {
  return httpPost<{ id: number }>('/report-template/', body)
}

/** 编辑模板 */
export function apiUpdateReportTemplate(templateId: number, body: TemplateSaveBody): Promise<void> {
  return httpPut<void>(`/report-template/${templateId}`, body)
}

/** 删除模板（默认模板不允许删除） */
export function apiDeleteReportTemplate(templateId: number): Promise<void> {
  return httpDelete<void>(`/report-template/${templateId}`)
}

/** 设为默认模板 */
export function apiSetDefaultReportTemplate(templateId: number): Promise<void> {
  return httpPost<void>(`/report-template/${templateId}/set-default`)
}
