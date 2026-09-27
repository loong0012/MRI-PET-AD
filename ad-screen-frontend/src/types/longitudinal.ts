/**
 * 纵向影像量化 TS 类型
 * ------------------------------------------------------------------
 * 对应后端 routers/longitudinal.py 与 services/longitudinal_service.py：
 * - LongitudinalTimepoint：单期影像量化快照
 * - LongitudinalRates：相邻两期年化变化率
 * - LongitudinalResult：完整纵向量化结果（含多期、变化率与 NIA-AA 分级）
 */
export interface LongitudinalTimepoint {
  /** 病例 ID */
  caseId: string
  /** 检查日期 yyyy-mm-dd */
  examDate: string
  /** 该患者第几期（0,1,2…） */
  timepointIdx: number
  /** 左海马体积 cm³ */
  hippocampusVolL: number | null
  /** 右海马体积 cm³ */
  hippocampusVolR: number | null
  /** 平均 SUVr */
  meanSuv: number | null
  /** 皮层厚度 mm */
  corticalThickness: number | null
  /** 脑室体积 cm³ */
  ventricleVol: number | null
  /** MTA 评级（0-3） */
  mtaScore: string | null
  /** 该期备注（如"该期 AI 分析未完成"） */
  note?: string
}

export interface LongitudinalRates {
  /** 海马体积年变化率 Δ cm³/年（null 表示单期或缺失） */
  hv: number | null
  /** SUVr 年变化率 Δ SUV/年 */
  suv: number | null
  /** 皮层厚度年变化率 Δ mm/年 */
  cort: number | null
  /** 海马体积相对前期的百分比变化（正=增大，负=萎缩） */
  hvPct?: number | null
  /** SUVr 相对前期的百分比变化（正=上升，负=下降） */
  suvPct?: number | null
  /** 皮层厚度相对前期的百分比变化 */
  cortPct?: number | null
  /** 相邻两期间隔天数 */
  intervalDays?: number
  /** 变化率备注（间隔过短 / 日期异常等） */
  note?: string
}

/** NIA-AA 纵向进展分级 */
export type NiaaaStage = 'CN' | 'MCI' | 'AD-E' | 'AD-L' | 'baseline'

/**
 * MCID 显著性检验（最小临床重要差异）
 * 判断各指标变化是否超过临床显著阈值
 */
export interface McidSignificanceItem {
  /** 是否超过 MCID 阈值（true=临床显著进展） */
  exceeded: boolean
  /** 变化方向：萎缩/下降/稳定/增长 等 */
  direction: 'atrophy' | 'growth' | 'decline' | 'increase' | 'thinning' | 'thickening' | 'stable'
  /** 相对百分比变化 */
  changePct: number | null
  /** MCID 阈值%（海马/SUV） */
  mcidPct?: number
  /** MCID 阈值绝对值（皮层厚度 mm/年） */
  mcidRate?: number
  /** 临床解读说明 */
  note: string
}

export interface McidSignificance {
  /** 海马体积 MCID 检验 */
  hv: McidSignificanceItem
  /** SUVr MCID 检验 */
  suv: McidSignificanceItem
  /** 皮层厚度 MCID 检验 */
  cort: McidSignificanceItem
  /** 任一指标超过 MCID 即为显著进展 */
  anyExceeded: boolean
  /** 综合进展判定：significant=显著进展 / stable=稳定 / insufficient_data=数据不足 */
  progression: 'significant' | 'stable' | 'insufficient_data'
}

/** 随访频率与临床路径推荐 */
export interface FollowupRecommendation {
  /** 建议随访间隔（月） */
  intervalMonths: number
  /** 优先级：routine=常规 / enhanced=加强 / urgent=紧急 */
  priority: 'routine' | 'enhanced' | 'urgent'
  /** 建议措施列表 */
  actions: string[]
  /** 推荐依据 */
  rationale: string
}

export interface LongitudinalResult {
  /** 是否可计算纵向变化（≥2 期 + 含 AI 量化指标） */
  available: boolean
  /** 不可用原因（available=false 时返回） */
  reason?: string
  /** 多期时间点（按 examDate 升序） */
  timepoints: LongitudinalTimepoint[]
  /** 年化变化率（多期取最近一对相邻期；单期为 null） */
  rates: LongitudinalRates
  /** NIA-AA 纵向进展分级 */
  niaaaStage: NiaaaStage
  /** 备注：间隔过短 / 指标缺失等说明 */
  progressionNote: string
  /** MCID 显著性检验结果（可选，后端可能未升级时为 undefined） */
  significance?: McidSignificance
  /** 随访频率与临床路径推荐（可选） */
  followupRecommendation?: FollowupRecommendation
}
