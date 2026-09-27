import type { AnalysisResult, InterventionSection, FollowUpPlan, InferenceProgress } from '@/types/analysis'
import type { CaseRecord } from '@/types/case'
import { httpPost, httpGet } from '@/utils/request'
import { ensureMediaToken } from '@/utils/mediaToken'
import { buildAnalysis, saveAnalysis, getInterventions, saveInterventions, getFollowUp, saveFollowUp, pushInferenceLog, pushCaseLog, cases as dbCases } from '@/mock/db'
import { now } from '@/utils/format'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

/** AI 推理阶段文案（加载弹窗步骤展示） */
const STAGES = [
  { p: 12, s: '加载 MRI / PET 序列数据…' },
  { p: 30, s: '影像预处理（偏置场校正 / 强度归一化）…' },
  { p: 52, s: 'MRI-PET 空间配准与融合…' },
  { p: 76, s: 'TransMF 多模态融合模型推理中…' },
  { p: 92, s: '海马定量与代谢量化分析…' },
  { p: 100, s: '分析完成' }
]

/** 推理被主动取消（用户取消/组件卸载），调用方不应再弹错误提示 */
export class InferenceCanceledError extends Error {
  constructor(public readonly reason: 'user' | 'unmount') {
    super('推理已取消')
    this.name = 'InferenceCanceledError'
  }
}

/** 可取消的推理 Promise（cancel 关闭 SSE/定时器，后端在途任务不受影响） */
export interface CancellableInference extends Promise<AnalysisResult> {
  cancel: (reason?: 'user' | 'unmount') => void
}

/** 看门狗超时（毫秒）：期间未收到任何 progress 事件即判定假死 */
const STALL_TIMEOUT_MS = 45000

/** SSE 通道错误（区别于服务端业务 error）；receivedProgress=false 表示连接从未建立，可安全回退 POST */
class InferenceStreamError extends Error {
  constructor(message: string, readonly receivedProgress: boolean) {
    super(message)
    this.name = 'InferenceStreamError'
  }
}

/**
 * SSE 流式推理（真实后端模式）
 * 通过 EventSource 订阅 /analysis/{id}/run-stream，实时接收进度事件：
 *   {type:'progress', progress, stage} / {type:'complete', result} / {type:'error', message}
 * @param registerCancel 注册取消函数（由外层 promise 暴露给调用方）
 * @returns receivedProgress 供外层判断：只有「从未收到进度」（连接未建立）才允许回退 POST
 */
async function streamInference(
  caseId: string,
  onProgress: (p: InferenceProgress) => void,
  registerCancel: (fn: (reason: 'user' | 'unmount') => void) => void
): Promise<{ result: AnalysisResult; receivedProgress: boolean }> {
  // EventSource 无法自定义请求头，先换取 10 分钟短期媒体票据拼在 query 上
  const token = await ensureMediaToken()
  return new Promise((resolve, reject) => {
    const base = import.meta.env.VITE_API_BASE || '/api'
    const url = token
      ? `${base}/analysis/${caseId}/run-stream?token=${encodeURIComponent(token)}`
      : `${base}/analysis/${caseId}/run-stream`
    const es = new EventSource(url)
    let finished = false
    let receivedProgress = false
    let stallTimer: ReturnType<typeof setTimeout> | null = null

    const clearStall = (): void => {
      if (stallTimer) {
        clearTimeout(stallTimer)
        stallTimer = null
      }
    }
    const armStall = (): void => {
      clearStall()
      stallTimer = setTimeout(() => {
        if (finished) return
        finished = true
        es.close()
        reject(new InferenceStreamError('推理服务长时间无响应，请稍后重试', receivedProgress))
      }, STALL_TIMEOUT_MS)
    }
    const cancel = (reason: 'user' | 'unmount'): void => {
      if (finished) return
      finished = true
      clearStall()
      es.close()
      reject(new InferenceCanceledError(reason))
    }
    registerCancel(cancel)
    // 连接建立后即开始看门狗（首个 progress 到达前服务端排队也算存活等待）
    es.onopen = () => armStall()

    es.onmessage = (ev: MessageEvent<string>) => {
      let data: {
        type?: string
        progress?: number
        stage?: string
        result?: AnalysisResult
        message?: string
      }
      try {
        data = JSON.parse(ev.data)
      } catch {
        return
      }
      if (data.type === 'progress' && typeof data.progress === 'number') {
        receivedProgress = true
        armStall()
        onProgress({ progress: data.progress, stage: data.stage ?? '' })
      } else if (data.type === 'complete' && data.result) {
        finished = true
        clearStall()
        es.close()
        resolve({ result: data.result, receivedProgress })
      } else if (data.type === 'error') {
        finished = true
        clearStall()
        es.close()
        reject(new Error(data.message || '推理失败，请重试'))
      }
    }

    es.onerror = () => {
      // 服务端在 complete 后会关闭连接；未完成时的 error 才视为失败
      if (finished) return
      finished = true
      clearStall()
      es.close()
      reject(new InferenceStreamError('推理连接中断，请重新发起分析', receivedProgress))
    }
  })
}

/**
 * 启动多模态 AI 融合分析
 * Mock 模式：模拟推理时序（约 4s）+ onProgress 驱动加载弹窗
 * 真实模式：优先 SSE 流式进度；SSE 不可用时回退同步 POST
 */
export function apiRunInference(
  caseInfo: CaseRecord,
  operator: string,
  onProgress: (p: InferenceProgress) => void
): CancellableInference {
  if (useMock) {
    let cancelFn: (reason: 'user' | 'unmount') => void = () => undefined
    const timers: ReturnType<typeof setTimeout>[] = []
    const promise = new Promise<AnalysisResult>((resolve, reject) => {
      cancelFn = (reason) => {
        timers.forEach(clearTimeout)
        reject(new InferenceCanceledError(reason))
      }
      let i = 0
      const tick = (): void => {
        const st = STAGES[Math.min(i, STAGES.length - 1)]
        onProgress({ progress: st.p, stage: st.s })
        i++
        if (i <= STAGES.length) {
          timers.push(setTimeout(tick, 500 + Math.random() * 350))
        } else {
          const result = buildAnalysis(caseInfo)
          saveAnalysis(caseInfo, result)
          pushInferenceLog({
            id: `INF${Date.now()}`,
            caseId: caseInfo.id,
            patientName: caseInfo.patient.name,
            modelVersion: result.modelVersion,
            strategy: result.fusionStrategy,
            durationSec: result.inferenceTime,
            riskScore: result.riskScore,
            status: '成功',
            source: result.inferenceSource ?? 'simulated',
            operator,
            time: now()
          })
          pushCaseLog(caseInfo.id, caseInfo.patient.name, '启动 AI 分析', operator, `推理完成，风险评分 ${result.riskScore}`)
          resolve(result)
        }
      }
      timers.push(setTimeout(tick, 400))
    })
    return Object.assign(promise, { cancel: (reason: 'user' | 'unmount' = 'user') => cancelFn(reason) })
  }
  // 真实后端：SSE 流式推理；仅当连接从未建立（未收到任何进度）时才回退同步 POST，
  // 推理中途断流不再自动 POST，避免同一病例被重复推理
  let cancelFn: (reason: 'user' | 'unmount') => void = () => undefined
  const promise = (async (): Promise<AnalysisResult> => {
    try {
      const { result } = await streamInference(caseInfo.id, onProgress, (fn) => {
        cancelFn = fn
      })
      return result
    } catch (e) {
      if (e instanceof InferenceCanceledError) throw e
      if (e instanceof InferenceStreamError && !e.receivedProgress) {
        // 连接从未建立（SSE 通道未开启/票据握手失败）：回退同步接口，httpPost 失败已由请求层统一提示
        return httpPost<AnalysisResult>(`/analysis/${caseInfo.id}/run`)
      }
      throw e
    }
  })()
  return Object.assign(promise, {
    cancel: (reason: 'user' | 'unmount' = 'user'): void => cancelFn(reason)
  })
}

/** 重新推理（复用 run 接口，业务上先清除旧结果） */
export function apiRerunInference(
  caseInfo: CaseRecord,
  operator: string,
  onProgress: (p: InferenceProgress) => void
): CancellableInference {
  return apiRunInference(caseInfo, operator, onProgress)
}

/** 获取 AI 分析结果（无结果时抛错由调用方处理） */
export function apiGetAnalysis(caseId: string): Promise<AnalysisResult> {
  if (useMock) {
    const c = dbCases.find((x) => x.id === caseId)
    if (!c || c.riskScore === null) {
      return Promise.reject(new Error('尚未生成 AI 分析结果'))
    }
    return Promise.resolve(buildAnalysis(c))
  }
  return httpGet<AnalysisResult>(`/analysis/${caseId}`)
}

/** 保存 AI 分析结果 */
export function apiSaveAnalysis(caseInfo: CaseRecord, result: AnalysisResult, operator: string): Promise<void> {
  if (useMock) {
    saveAnalysis(caseInfo, result)
    pushCaseLog(caseInfo.id, caseInfo.patient.name, '保存 AI 分析结果', operator, `保存风险评分 ${result.riskScore} 的分析结果`)
    return Promise.resolve()
  }
  return httpPost<void>(`/analysis/${caseInfo.id}/save`, result)
}

/** 获取干预方案（无则按病例生成 AI 默认方案） */
export function apiGetInterventions(caseInfo: CaseRecord): Promise<InterventionSection[]> {
  if (useMock) return Promise.resolve(getInterventions(caseInfo))
  return httpGet<InterventionSection[]>(`/intervention/${caseInfo.id}`)
}

/** 保存干预方案（医生编辑后） */
export function apiSaveInterventions(caseId: string, patientName: string, sections: InterventionSection[], operator: string): Promise<void> {
  if (useMock) {
    saveInterventions(caseId, sections)
    pushCaseLog(caseId, patientName, '编辑干预方案', operator, '更新干预方案内容')
    return Promise.resolve()
  }
  return httpPost<void>(`/intervention/${caseId}`, sections)
}

/** 获取随访计划 */
export function apiGetFollowUp(caseInfo: CaseRecord): Promise<FollowUpPlan> {
  if (useMock) return Promise.resolve(getFollowUp(caseInfo))
  return httpGet<FollowUpPlan>(`/followup/${caseInfo.id}`)
}

/** 保存随访计划 */
export function apiSaveFollowUp(caseId: string, plan: FollowUpPlan): Promise<void> {
  if (useMock) {
    saveFollowUp(caseId, plan)
    return Promise.resolve()
  }
  return httpPost<void>(`/followup/${caseId}`, plan)
}

/** 批量分析结果条目 */
export interface BatchResultItem {
  caseId: string
  status: 'success' | 'failed'
  riskScore?: number
  riskLevel?: string
  error?: string
}

/** 批量分析返回 */
export interface BatchAnalysisResult {
  total: number
  success: number
  failed: number
  results: BatchResultItem[]
}

/** 批量分析提交响应（异步任务队列） */
export interface BatchSubmitResult {
  taskId: string
  total: number
}

/**
 * 批量 AI 分析（异步任务队列）：提交后立即返回 taskId，前端轮询进度
 */
export function apiBatchAnalysis(caseIds: string[], operator: string): Promise<BatchSubmitResult> {
  if (useMock) {
    // Mock：直接返回模拟 taskId
    return Promise.resolve({ taskId: 'mock-' + Date.now().toString(36), total: caseIds.length })
  }
  return httpPost<BatchSubmitResult>('/analysis/batch', { caseIds, operator })
}

/** 分析结果历史版本 */
export interface AnalysisVersion {
  id: number
  caseId: string
  version: number
  modelVersion: string
  fusionStrategy: string
  riskLevel: string
  riskScore: number
  source: string
  operator: string
  createdAt: string
  reviewStatus: 'pending' | 'approved' | 'rejected'
  reviewer: string
  reviewComment: string
  reviewedAt: string
  result?: AnalysisResult
}

/** 获取病例的分析历史版本列表 */
export function apiListVersions(caseId: string): Promise<AnalysisVersion[]> {
  if (useMock) {
    const c = dbCases.find((x) => x.id === caseId)
    if (!c || c.riskScore === null) return Promise.resolve([])
    const r1 = buildAnalysis(c)
    // v2：模拟模型迭代后评分/指标略有差异，便于演示版本对比
    const r2: AnalysisResult = {
      ...r1,
      riskScore: Math.min(100, Math.round((r1.riskScore + 3.5) * 10) / 10),
      confidence: Math.min(0.99, r1.confidence + 0.04),
      hippocampusVolumeL: Math.round((r1.hippocampusVolumeL - 0.4) * 100) / 100,
      hippocampusVolumeR: Math.round((r1.hippocampusVolumeR - 0.3) * 100) / 100,
      meanSUV: Math.round((r1.meanSUV - 0.08) * 100) / 100,
      modelVersion: r1.modelVersion + '-iter2'
    }
    return Promise.resolve([
      {
        id: 2, caseId, version: 2, modelVersion: r2.modelVersion,
        fusionStrategy: r2.fusionStrategy, riskLevel: r2.riskLevel, riskScore: r2.riskScore,
        source: r2.inferenceSource ?? 'simulated', operator: 'rad01', createdAt: now(),
        reviewStatus: 'pending', reviewer: '', reviewComment: '', reviewedAt: '', result: r2
      },
      {
        id: 1, caseId, version: 1, modelVersion: r1.modelVersion,
        fusionStrategy: r1.fusionStrategy, riskLevel: r1.riskLevel, riskScore: r1.riskScore,
        source: r1.inferenceSource ?? 'simulated', operator: 'rad01', createdAt: now(),
        reviewStatus: 'approved', reviewer: 'dr01', reviewComment: 'Mock 演示版本', reviewedAt: now(), result: r1
      }
    ])
  }
  return httpGet<AnalysisVersion[]>(`/analysis/${caseId}/versions`)
}

/** 获取单个分析版本的完整结果（含 result） */
export function apiGetVersion(versionId: number): Promise<AnalysisVersion> {
  return httpGet<AnalysisVersion>(`/analysis/version/${versionId}`)
}

/** 提交协作审核（通过 / 驳回） */
export function apiReviewVersion(
  versionId: number,
  action: 'approve' | 'reject',
  reviewer: string,
  comment: string
): Promise<AnalysisVersion> {
  if (useMock) {
    return Promise.resolve({
      id: versionId,
      caseId: '',
      version: 1,
      modelVersion: '',
      fusionStrategy: '',
      riskLevel: '',
      riskScore: 0,
      source: 'simulated',
      operator: '',
      createdAt: '',
      reviewStatus: action === 'approve' ? 'approved' : 'rejected',
      reviewer,
      reviewComment: comment,
      reviewedAt: now()
    })
  }
  return httpPost<AnalysisVersion>(`/analysis/version/${versionId}/review`, { action, reviewer, comment })
}
