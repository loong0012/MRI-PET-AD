/**
 * 影像数据质控中心 API
 */
import { httpGet } from '@/utils/request'

export type QualitySeverity = 'high' | 'medium' | 'low'

export interface QualityCategoryStat {
  key: string
  label: string
  count: number
}

export interface QualitySummary {
  totalCases: number
  issueTotal: number
  affectedCases: number
  healthyCases: number
  high: number
  medium: number
  low: number
  categories: QualityCategoryStat[]
}

export interface QualityIssue {
  id: string
  category: string
  categoryLabel: string
  severity: QualitySeverity
  caseId: string
  patientName: string
  examDate: string
  message: string
  suggestion: string
}

export function apiQualitySummary(): Promise<QualitySummary> {
  return httpGet('/quality/summary')
}

export function apiQualityIssues(category = '', severity = ''): Promise<{ list: QualityIssue[]; total: number }> {
  return httpGet('/quality/issues', { category, severity })
}
