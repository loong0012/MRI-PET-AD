/**
 * 患者教育 / AD 科普知识库 API
 * 公开内容，全角色可访问（无需鉴权）
 */
import type { KnowledgeCategory, KnowledgeArticle } from '@/types/knowledge.d'
import { httpGet } from '@/utils/request'

/** 获取所有分类（含各分类文章数） */
export function apiGetKnowledgeCategories(): Promise<KnowledgeCategory[]> {
  return httpGet<KnowledgeCategory[]>('/knowledge/categories')
}

/** 获取文章列表（可按分类 + 关键词筛选） */
export function apiGetKnowledgeArticles(params?: {
  category?: string
  keyword?: string
}): Promise<KnowledgeArticle[]> {
  return httpGet<KnowledgeArticle[]>('/knowledge/articles', params)
}

/** 获取文章详情（含 Markdown 正文） */
export function apiGetKnowledgeArticle(id: number): Promise<KnowledgeArticle> {
  return httpGet<KnowledgeArticle>(`/knowledge/${id}`)
}
