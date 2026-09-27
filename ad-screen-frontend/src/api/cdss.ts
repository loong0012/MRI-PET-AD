/**
 * CDSS 临床决策支持 API（临床角色）
 * 基于规则引擎：AI 评分 + 年龄 + MMSE + 认知趋势 + 依从性 → 分级诊疗建议
 */
import type {
  CdssRecommendation, CdssListResult, CdssStats, CdssRule
} from '@/types/cdss.d'
import { httpGet } from '@/utils/request'

/** 生成单病例 CDSS 建议 */
export function apiGetRecommendation(caseId: string): Promise<CdssRecommendation> {
  return httpGet<CdssRecommendation>(`/cdss/recommend/${caseId}`)
}

/** 批量生成 CDSS 建议（分页） */
export function apiListRecommendations(
  priority = '', page = 1, pageSize = 20
): Promise<CdssListResult> {
  return httpGet<CdssListResult>('/cdss/recommend', {
    priority, page: String(page), pageSize: String(pageSize)
  })
}

/** CDSS 建议分布统计 */
export function apiGetCdssStats(): Promise<CdssStats> {
  return httpGet<CdssStats>('/cdss/stats')
}

/** CDSS 规则配置列表 */
export function apiListRules(): Promise<CdssRule[]> {
  return httpGet<CdssRule[]>('/cdss/rules')
}
