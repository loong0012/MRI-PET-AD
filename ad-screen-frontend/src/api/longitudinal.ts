/**
 * 纵向影像量化 API 客户端
 * ------------------------------------------------------------------
 * 对接后端 routers/longitudinal.py：
 * - GET  /case/longitudinal/{patientNo}            查询多期指标 + 变化率 + NIA-AA 分级
 * - POST /case/longitudinal/{patientNo}/recompute  强制重算（admin/researcher）
 * - GET  /case/longitudinal/by-case/{caseId}       按病例 ID 反查患者纵向时间线
 * Mock 模式回退到单期 baseline 占位，保证前端展示不报错。
 */
import { httpGet, httpPost } from '@/utils/request'
import type { LongitudinalResult } from '@/types/longitudinal'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

/** 按患者编号查询纵向影像量化结果 */
export function apiGetLongitudinal(patientNo: string): Promise<LongitudinalResult> {
  if (useMock) {
    return Promise.resolve({
      available: false,
      reason: 'single_timepoint',
      timepoints: [],
      rates: { hv: null, suv: null, cort: null },
      niaaaStage: 'baseline',
      progressionNote: '演示模式下无多期数据'
    })
  }
  return httpGet<LongitudinalResult>(`/case/longitudinal/${encodeURIComponent(patientNo)}`)
}

/** 按病例 ID 反查纵向时间线（前端从病例详情页直接进入） */
export function apiGetLongitudinalByCase(caseId: string): Promise<LongitudinalResult> {
  if (useMock) {
    return Promise.resolve({
      available: false,
      reason: 'single_timepoint',
      timepoints: [],
      rates: { hv: null, suv: null, cort: null },
      niaaaStage: 'baseline',
      progressionNote: '演示模式下无多期数据'
    })
  }
  return httpGet<LongitudinalResult>(`/case/longitudinal/by-case/${encodeURIComponent(caseId)}`)
}

/** 强制重算纵向指标（仅 admin / researcher 角色可调用） */
export function apiRecomputeLongitudinal(patientNo: string): Promise<LongitudinalResult> {
  if (useMock) {
    return Promise.resolve({
      available: false,
      reason: 'single_timepoint',
      timepoints: [],
      rates: { hv: null, suv: null, cort: null },
      niaaaStage: 'baseline',
      progressionNote: '演示模式下无多期数据'
    })
  }
  return httpPost<LongitudinalResult>(`/case/longitudinal/${encodeURIComponent(patientNo)}/recompute`)
}
