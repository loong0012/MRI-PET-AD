/**
 * 预后预测与认知衰退轨迹相关类型
 * 对应后端 routers/prognosis.py 返回结构
 */

/** 预后风险等级（high / medium / low） */
export type PrognosisLevel = 'high' | 'medium' | 'low'

/** 置信度等级（数据点越多越高） */
export type ConfidenceLevel = 'low' | 'medium' | 'high'

/** 历史随访 MMSE/MoCA 数据点 */
export interface MmseHistoryPoint {
  /** 随访日期 YYYY-MM-DD */
  date: string
  /** MMSE 评分（0-30），可能为 null */
  mmse: number | null
  /** MoCA 评分（0-30），可能为 null */
  moca: number | null
}

/** 历史 AI 风险评分数据点 */
export interface RiskHistoryPoint {
  /** 检查日期 YYYY-MM-DD */
  date: string
  /** AI 风险评分 0-100 */
  score: number
  /** 风险等级（low / mci / ad-early / ad-late） */
  level: string
}

/** 历史数据（MMSE 序列 + 风险评分序列） */
export interface HistoricalData {
  mmse: MmseHistoryPoint[]
  risk: RiskHistoryPoint[]
}

/** 未来某时间点的 MMSE 预测值 */
export interface ForecastPoint {
  /** 预测月数（12 / 24 / 36） */
  months: number
  /** 预测 MMSE 分数 */
  mmse: number
  /** 预测日期 YYYY-MM-DD */
  date: string
}

/** 预计转 AD（MMSE 降至 24 分）时间窗 */
export interface AdConversionWindow {
  /** 预计月数 */
  months: number
  /** 预计日期 YYYY-MM-DD */
  date: string
}

/** 预测结果 */
export interface PredictionResult {
  /** 每日斜率（线性回归） */
  slope: number
  /** 截距 */
  intercept: number
  /** 年衰退速率（分/年，负值表示衰退） */
  declineRate: number
  /** 拟合优度 R²（0-1） */
  r2: number
  /** 未来 12/24/36 月预测点 */
  forecast: ForecastPoint[]
  /** 预计转 AD 时间窗，无可行预测时为 null */
  adConversionWindow: AdConversionWindow | null
  /** 置信度（基于数据点数） */
  confidenceLevel: ConfidenceLevel
  /** 用于回归的数据点数 */
  dataPoints: number
}

/** 风险分层 */
export interface RiskStratification {
  /** 风险层级（high / medium / low） */
  level: PrognosisLevel
  /** 风险因素描述列表 */
  factors: string[]
}

/** 单患者预后预测完整返回 */
export interface PrognosisResult {
  /** 患者编号 */
  patientNo: string
  /** 患者基本信息 */
  patient: {
    name: string
    gender: string
    age: number | null
  }
  /** 历史数据 */
  historical: HistoricalData
  /** 预测结果，数据不足时为 null */
  prediction: PredictionResult | null
  /** 数据不足时的提示信息 */
  message?: string
  /** 风险分层（仅 prediction 非 null 时存在） */
  riskStratification?: RiskStratification
}

/** 队列批量预测摘要条目 */
export interface CohortForecastItem {
  /** 患者编号 */
  patientNo: string
  /** 患者姓名 */
  name: string
  /** 年衰退速率（分/年，负值表示衰退） */
  declineRate: number | null
  /** 36 月预测 MMSE 值 */
  forecast36M: number | null
  /** 风险层级（high / medium / low） */
  level: PrognosisLevel | null
  /** 是否有可用预测（数据是否充足） */
  hasPrediction: boolean
}
