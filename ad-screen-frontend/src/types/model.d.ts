/** 多模态融合策略 */
export type FusionStrategy = 'feature' | 'pixel'

/** 模型科研配置 */
export interface ModelConfig {
  strategy: FusionStrategy
  mriWeight: number // 0-1，PET 权重 = 1 - mriWeight
  riskThreshold: number // 0.3-0.8 高风险判定阈值
  roiRegions: string[] // ROI 脑区检测范围
  snapshotVersion: string
  updatedAt: string
  updatedBy: string
}

/** 单折交叉验证指标 */
export interface FoldMetric {
  fold: number
  auc: number
  acc: number
  sen: number
  spe: number
  f1: number
}

/** 训练曲线点 */
export interface TrainCurvePoint {
  epoch: number
  trainLoss: number
  valAuc: number
}

/** AI 推理日志 */
export interface InferenceLog {
  id: string
  caseId: string
  patientName: string
  modelVersion: string
  strategy: FusionStrategy
  durationSec: number
  riskScore: number
  status: '成功' | '失败'
  /** 推理来源：real=真实模型，simulated=模拟演示 */
  source?: 'real' | 'simulated'
  operator: string
  time: string
}

/** 真实 TransMF 模型运行时状态 */
export interface ModelRuntimeStatus {
  realModelAvailable: boolean
  ensembleLoaded: boolean
  device: string
  gpuName: string
  modelCount: number
  checkpointCount: number
  torchVersion: string
  monaiVersion: string
  modelArch: string
  modelParams: {
    dim?: number
    depth?: number
    heads?: number
    dropout?: number
    numClasses?: number
  }
  lastInference: {
    prob: number
    probMin: number
    probMax: number
    probStd: number
    nModels: number
    device: string
    elapsed: number
  } | null
  error?: string
}
