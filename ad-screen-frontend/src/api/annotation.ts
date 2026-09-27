/**
 * 影像 ROI 标注 API
 * ------------------------------------------------------------------
 * 后端路由前缀 /annotation，统一在 /api 下；鉴权依赖 get_current_user_qs。
 * - GET    /annotation/{caseId}            获取该病例所有标注
 * - POST   /annotation/{caseId}            新增标注，返回 { id }
 * - DELETE /annotation/{annotationId}     删除标注
 * - GET    /annotation/{caseId}/export     导出标注 JSON
 */
import { httpGet, httpPost, httpDelete } from '@/utils/request'
import type { AnnotationItem, AnnotationCreateBody, AnnotationExport } from '@/types/annotation'

/** 获取病例所有 ROI 标注（按 createdAt 倒序） */
export function apiListAnnotations(caseId: string): Promise<AnnotationItem[]> {
  return httpGet<AnnotationItem[]>(`/annotation/${caseId}`)
}

/** 新增 ROI 标注，返回新记录 id */
export function apiCreateAnnotation(caseId: string, body: AnnotationCreateBody): Promise<{ id: number }> {
  return httpPost<{ id: number }>(`/annotation/${caseId}`, body)
}

/** 删除指定标注 */
export function apiDeleteAnnotation(annotationId: number): Promise<void> {
  return httpDelete<void>(`/annotation/${annotationId}`)
}

/** 导出病例的标注 JSON（用于训练数据导出） */
export function apiExportAnnotations(caseId: string): Promise<AnnotationExport> {
  return httpGet<AnnotationExport>(`/annotation/${caseId}/export`)
}
