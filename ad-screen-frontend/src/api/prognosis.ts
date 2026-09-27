/**
 * 预后预测 API（认知衰退轨迹 / 队列批量预测）
 */
import { httpGet, httpPost } from '@/utils/request'
import type { PrognosisResult, CohortForecastItem } from '@/types/prognosis'

/**
 * 单患者认知衰退轨迹预测
 * 返回历史 MMSE/MoCA 序列 + AI 风险评分序列 + 未来 12/24/36 月 MMSE 预测 + 转 AD 时间窗
 * 数据不足 2 次随访时 prediction 字段为 null
 */
export function apiPrognosis(patientNo: string): Promise<PrognosisResult> {
  return httpGet<PrognosisResult>(`/prognosis/${patientNo}`)
}

/**
 * 队列批量预测（最多 8 名患者）
 * 返回每名患者的衰退速率、36 月预测值、风险层级摘要
 */
export function apiCohortForecast(patientNos: string[]): Promise<CohortForecastItem[]> {
  return httpPost<CohortForecastItem[]>('/prognosis/cohort-forecast', { patientNos })
}
