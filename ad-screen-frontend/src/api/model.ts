import type { ModelConfig, FoldMetric, TrainCurvePoint, InferenceLog, ModelRuntimeStatus } from '@/types/model'
import type { SystemLog, CaseLog } from '@/types/system'
import { httpGet, httpPost } from '@/utils/request'
import { modelConfig, foldMetrics, trainCurve, inferenceLogs, systemLogs, caseLogs } from '@/mock/db'
import { now } from '@/utils/format'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

/** 获取模型科研配置 */
export function apiGetModelConfig(): Promise<ModelConfig> {
  if (useMock) return Promise.resolve({ ...modelConfig })
  return httpGet<ModelConfig>('/model/config')
}

/** 获取真实 TransMF 模型运行时状态 */
export function apiGetModelStatus(): Promise<ModelRuntimeStatus> {
  if (useMock) {
    return Promise.resolve({
      realModelAvailable: true,
      ensembleLoaded: true,
      device: 'cuda:0',
      gpuName: 'NVIDIA GPU（演示）',
      modelCount: 75,
      checkpointCount: 75,
      torchVersion: '2.x',
      monaiVersion: '1.3.x',
      modelArch: 'TransMF（sNet + CrossTransformer_MOD_AVG）',
      modelParams: { dim: 128, depth: 3, heads: 4, dropout: 0.15, numClasses: 2 },
      lastInference: null
    })
  }
  return httpGet<ModelRuntimeStatus>('/model/status')
}

/** 热重载 TransMF 集成模型（释放旧权重并重新加载） */
export function apiReloadModel(): Promise<ModelRuntimeStatus> {
  if (useMock) return apiGetModelStatus()
  return httpPost<ModelRuntimeStatus>('/model/reload')
}

/** 保存模型科研配置 */
export function apiSaveModelConfig(cfg: ModelConfig, operator: string): Promise<void> {
  if (useMock) {
    Object.assign(modelConfig, cfg, { updatedAt: now(), updatedBy: operator })
    return Promise.resolve()
  }
  return httpPost<void>('/model/config', cfg)
}

/** 5 折交叉验证指标（75 模型快照集成实测值） */
export function apiGetFoldMetrics(): Promise<FoldMetric[]> {
  if (useMock) return Promise.resolve(foldMetrics)
  return httpGet<FoldMetric[]>('/model/fold-metrics')
}

/** 训练曲线 */
export function apiGetTrainCurve(): Promise<TrainCurvePoint[]> {
  if (useMock) return Promise.resolve(trainCurve)
  return httpGet<TrainCurvePoint[]>('/model/train-curve')
}

/** 推理日志列表 */
export function apiGetInferenceLogs(): Promise<InferenceLog[]> {
  if (useMock) return Promise.resolve([...inferenceLogs])
  return httpGet<InferenceLog[]>('/model/inference-logs')
}

/** 系统操作日志 */
export function apiGetSystemLogs(): Promise<SystemLog[]> {
  if (useMock) return Promise.resolve([...systemLogs])
  return httpGet<SystemLog[]>('/system/logs')
}

/** 病例操作记录 */
export function apiGetCaseLogs(): Promise<CaseLog[]> {
  if (useMock) return Promise.resolve([...caseLogs])
  return httpGet<CaseLog[]>('/system/case-logs')
}
