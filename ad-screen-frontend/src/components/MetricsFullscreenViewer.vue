<script setup lang="ts">
/**
 * AI 量化分析全屏查看器（分析详情页指标表点击进入）
 * ------------------------------------------------------------------
 * 优化结果可视化呈现：
 * 1. 顶部：风险仪表盘（0-100 分，四色分级：低/MCI/早AD/晚AD）+ 病程分期 + 置信度
 * 2. 中部：关键脑结构指标卡（海马体积 / 皮层厚度 / 脑室体积 / SUV / MTA）
 * 3. 下部：全数量化指标列表，每条带"参考范围进度条"，测量值在条上以标记点显示，
 *    异常项高亮 + 偏差百分比，直观展示指标偏离正常范围的方向与程度。
 */
import { computed } from 'vue'
import type { AnalysisResult, QuantMetric, AbnormalRegion } from '@/types/analysis'
import type { EChartsOption } from 'echarts'
import RiskLevelTag from './RiskLevelTag.vue'
import ChartBase from '@/components/ChartBase.vue'

const props = defineProps<{
  modelValue: boolean
  analysis: AnalysisResult | null
}>()

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v)
})

const a = computed(() => props.analysis)

// ---------- 风险仪表盘 ----------
const SCORE_COLOR: Record<string, string> = {
  low: '#2E9E6B',
  mci: '#D99A2B',
  'ad-early': '#D96B2B',
  'ad-late': '#C94F4F'
}
const SCORE_LABEL: Record<string, string> = {
  low: '低风险',
  mci: 'MCI 风险',
  'ad-early': 'AD 早期',
  'ad-late': 'AD 中晚期'
}

/** 仪表盘角度：0-180° 对应 0-100 分 */
const gaugeAngle = computed(() => (a.value?.riskScore ?? 0) * 1.8)

// ---------- 关键脑结构指标卡 ----------
type MetricStatus = 'normal' | 'warn' | 'abnormal'
interface KeyCard {
  label: string
  value: number
  unit: string
  ref: string
  status: MetricStatus
}

/** 关键卡与指标表使用同一数据源（analysis.metrics），杜绝阈值/标度不一致 */
const KEY_CARD_MAP: { key: string; label: string }[] = [
  { key: 'hvL', label: '左侧海马体积' },
  { key: 'hvR', label: '右侧海马体积' },
  { key: 'cort', label: '皮层平均厚度' },
  { key: 'ven', label: '脑室体积' },
  { key: 'suv', label: '脑代谢 SUV' },
  { key: 'mta', label: 'MTA 萎缩评分' }
]

const keyMetrics = computed<KeyCard[]>(() => {
  const map = new Map((a.value?.metrics ?? []).map((m) => [m.key, m]))
  const cards = KEY_CARD_MAP.map(({ key, label }) => {
    const m = map.get(key)
    return m ? { label, value: m.value, unit: m.unit, ref: m.refRange, status: m.status } : null
  })
  return cards.filter((c): c is KeyCard => c !== null)
})

/** 各状态指标数量（顶部快速分诊） */
const metricCounts = computed(() => {
  const list = a.value?.metrics ?? []
  return {
    total: list.length,
    abnormal: list.filter((m) => m.status === 'abnormal').length,
    warn: list.filter((m) => m.status === 'warn').length,
    normal: list.filter((m) => m.status === 'normal').length
  }
})

// ---------- 参考范围进度条 ----------
/** 从 "2.6 ~ 3.6" / "≥2.5" / "<38" 等字符串解析上下限；single=仅有理想值（如 "0"） */
type RefMode = 'range' | 'gte' | 'lte' | 'single'
function parseRefRange(ref: string): { low: number; high: number; mode: RefMode } {
  const m = ref.match(/([\d.]+)\s*[-~]\s*([\d.]+)/)
  if (m) return { low: Number(m[1]), high: Number(m[2]), mode: 'range' }
  const g = ref.match(/[≥>]\s*([\d.]+)/)
  if (g) return { low: Number(g[1]), high: Number(g[1]) * 2, mode: 'gte' }
  const l = ref.match(/[≤<]\s*([\d.]+)/)
  if (l) return { low: 0, high: Number(l[1]), mode: 'lte' }
  const s = ref.match(/^\s*=?\s*([\d.]+)\s*$/)
  if (s) return { low: Number(s[1]), high: Number(s[1]), mode: 'single' }
  return { low: 0, high: 100, mode: 'range' }
}

/** 定性序数量表（0-4 级评分：MTA / Fazekas），不适用连续百分比偏差 */
const ORDINAL_KEYS = new Set(['mta', 'wmh'])
function isOrdinal(m: QuantMetric): boolean {
  return ORDINAL_KEYS.has(m.key) || m.unit === '级'
}

function clampPct(v: number): number {
  return Math.max(0, Math.min(100, v))
}

interface BarGeom {
  /** 参考范围带左缘 % */
  bandLeft: number
  /** 参考范围带宽度 % */
  bandWidth: number
  /** 测量值标记位置 % */
  marker: number
}

/**
 * 计算条形几何：构造同时容纳参考区间与测量值的显示域，
 * 使"正常区间带"与"测量点"都落在可见区域，直观呈现偏离方向与程度。
 */
function barGeom(m: QuantMetric): BarGeom {
  const parsed = parseRefRange(m.refRange)
  let lo = 0
  let hi = 100
  let dLo = 0
  let dHi = 100

  if (isOrdinal(m)) {
    // 0-4 定性量表固定显示域
    dLo = 0
    dHi = 4
    if (parsed.mode === 'range') { lo = parsed.low; hi = parsed.high }
    else if (parsed.mode === 'lte') { lo = 0; hi = parsed.high }
    else { lo = 0; hi = Math.max(1, parsed.low) } // 单点理想值按 0~1 正常带
  } else if (parsed.mode === 'range') {
    lo = parsed.low
    hi = parsed.high
    const span = hi - lo || Math.abs(hi) || 1
    dLo = Math.min(lo, m.value) - 0.35 * span
    dHi = Math.max(hi, m.value) + 0.35 * span
  } else if (parsed.mode === 'gte') {
    lo = parsed.low
    const span = lo || 1
    dLo = Math.min(lo, m.value) - 0.25 * span
    dHi = lo + 0.6 * span
    hi = dHi // 正常带自阈值延伸至显示域右缘
  } else if (parsed.mode === 'lte') {
    hi = parsed.high
    dLo = 0
    dHi = Math.max(hi, m.value) * 1.25 || 1
    lo = 0 // 正常带自显示域左缘延伸至阈值
  } else {
    // single 连续值：以该值为中心构造显示域
    lo = parsed.low
    hi = parsed.high
    const span = lo || 1
    dLo = Math.min(lo, m.value) - 0.5 * span
    dHi = Math.max(hi, m.value) + 0.5 * span
  }

  const pct = (v: number): number => (dHi > dLo ? ((v - dLo) / (dHi - dLo)) * 100 : 50)
  const left = clampPct(pct(lo))
  const right = clampPct(pct(hi))
  return {
    bandLeft: left,
    bandWidth: Math.max(3, right - left),
    marker: clampPct(pct(m.value))
  }
}

function fmtDev(dev: number): string {
  return `${dev >= 0 ? '+' : ''}${dev.toFixed(1)}%`
}

/**
 * 偏差百分比：连续型指标相对参考带临界值的偏离；
 * 定性量表（MTA / Fazekas）与不可解析区间返回空串，显示 "—"，避免误导。
 */
function deviationPct(m: QuantMetric): string {
  if (isOrdinal(m)) return ''
  const { low, high, mode } = parseRefRange(m.refRange)
  if (mode === 'gte') return low !== 0 ? fmtDev(((m.value - low) / low) * 100) : ''
  if (mode === 'lte') return high !== 0 ? fmtDev(((m.value - high) / high) * 100) : ''
  if (mode === 'single') {
    // 仅标注高于/低于理想值的方向，不给相对 0 的无穷百分比
    return m.value > high ? `高于理想值 ${(m.value - high).toFixed(1)}${m.unit}` : m.value < high ? `低于理想值 ${(high - m.value).toFixed(1)}${m.unit}` : '处于理想值'
  }
  // range
  if (high <= low) return ''
  const mid = (low + high) / 2
  if (mid === 0) return ''
  return fmtDev(((m.value - mid) / mid) * 100)
}

const STATUS_COLOR: Record<string, string> = {
  normal: '#2E9E6B',
  warn: '#D99A2B',
  abnormal: '#C94F4F'
}
const STATUS_TEXT: Record<string, string> = {
  normal: '正常',
  warn: '临界',
  abnormal: '异常'
}

function statusStyle(s: string): string {
  return `background: ${STATUS_COLOR[s]}1A; border: 1px solid ${STATUS_COLOR[s]}55; color: ${STATUS_COLOR[s]};`
}

/**
 * 测量值相对参考区间的方向：'down' 偏低 / 'up' 偏高 / null 在正常范围内。
 * 定性量表不判定方向。
 */
function deviationDir(m: QuantMetric): 'up' | 'down' | null {
  if (isOrdinal(m)) return null
  const { low, high, mode } = parseRefRange(m.refRange)
  if (mode === 'gte') return m.value < low ? 'down' : null
  if (mode === 'lte') return m.value > high ? 'up' : null
  if (mode === 'single') return m.value < low ? 'down' : m.value > high ? 'up' : null
  if (high <= low) return null
  if (m.value < low) return 'down'
  if (m.value > high) return 'up'
  return null
}

// ---------- 异常脑区 Z 值分布 ----------
/** 按 Z 值严重度升序（最负在前），便于医生优先关注最重脑区 */
const regions = computed<AbnormalRegion[]>(() =>
  [...(a.value?.abnormalRegions ?? [])].sort((p, q) => p.zScore - q.zScore)
)

// ---------- 多维度对比雷达图 ----------
/**
 * 6 维雷达图：患者实测值 vs 参考范围均值。
 * 维度按 KEY_CARD_MAP 顺序（左/右海马体积、SUV、皮层厚度、脑室体积、MTA）。
 * 各维 max 由参考范围派生（range 取 high*1.2；gte 取 low*1.5；lte 取 high*1.2；
 * single/解析失败回退到 value*2），保证患者值与参考均值都落入雷达可见域。
 * MTA 为字符串评分（如 "2/3"），按数字均值化。
 */
const radarOption = computed<EChartsOption>(() => {
  // 6 维度定义（与 KEY_CARD_MAP 同源）
  const dims: Array<{ name: string; key: string }> = [
    { name: '左海马体积', key: 'hvL' },
    { name: '右海马体积', key: 'hvR' },
    { name: 'PET SUV', key: 'suv' },
    { name: '皮层厚度', key: 'cort' },
    { name: '脑室体积', key: 'ven' },
    { name: 'MTA 评分', key: 'mta' }
  ]
  const metrics = a.value?.metrics ?? []
  const indicators: Array<{ name: string; max: number }> = []
  const patientValues: number[] = []
  const refMidValues: number[] = []

  for (const dim of dims) {
    // metrics 的 key 与 KEY_CARD_MAP 的 key 一致，直接按 key 取条目
    const metric = metrics.find((m: QuantMetric) => m.key === dim.key)
    const parsed = parseRefRange(metric?.refRange ?? '')

    let maxVal: number
    let midVal: number
    if (parsed.mode === 'range' && parsed.high > 0) {
      maxVal = parsed.high * 1.2
      midVal = (parsed.low + parsed.high) / 2
    } else if (parsed.mode === 'gte') {
      maxVal = parsed.low * 1.5
      midVal = parsed.low
    } else if (parsed.mode === 'lte') {
      maxVal = parsed.high * 1.2
      midVal = parsed.high / 2
    } else {
      // single 或解析失败：以测量值为中心回退
      const v = metric?.value ?? 0
      maxVal = v * 2 || 10
      midVal = v
    }
    const safeMax = Math.max(maxVal, 0.1)
    indicators.push({ name: dim.name, max: safeMax })

    // 患者实测：从 analysis 顶层字段取（mtaScore 为 string，需解析为数字）
    let patientVal: number
    if (dim.key === 'hvL') patientVal = a.value?.hippocampusVolumeL ?? 0
    else if (dim.key === 'hvR') patientVal = a.value?.hippocampusVolumeR ?? 0
    else if (dim.key === 'suv') patientVal = a.value?.meanSUV ?? 0
    else if (dim.key === 'cort') patientVal = a.value?.corticalThickness ?? 0
    else if (dim.key === 'ven') patientVal = a.value?.ventricleVolume ?? 0
    else if (dim.key === 'mta') {
      // MTA 形如 "2/3"（左/右评分），取所有数字的均值
      const mtaStr = a.value?.mtaScore ?? '0'
      const nums = mtaStr.match(/[\d.]+/g)
      patientVal = nums ? nums.reduce((s: number, n: string) => s + parseFloat(n), 0) / nums.length : 0
    } else {
      patientVal = 0
    }
    patientValues.push(Math.min(patientVal, safeMax))
    refMidValues.push(Math.min(midVal, safeMax))
  }

  return {
    tooltip: { trigger: 'item' },
    legend: { data: ['患者实测', '参考均值'], top: 8, textStyle: { fontSize: 12 } },
    radar: {
      indicator: indicators,
      shape: 'polygon',
      splitNumber: 4,
      axisName: { color: '#5b6b7c', fontSize: 11 },
      splitArea: { areaStyle: { color: ['#fafbfc', '#f4f6f8'] } },
      splitLine: { lineStyle: { color: '#dde3ea' } },
      axisLine: { lineStyle: { color: '#dde3ea' } }
    },
    series: [{
      type: 'radar',
      data: [
        {
          value: patientValues,
          name: '患者实测',
          areaStyle: { color: 'rgba(64, 158, 255, 0.35)' },
          lineStyle: { color: '#409EFF', width: 2 },
          itemStyle: { color: '#409EFF' }
        },
        {
          value: refMidValues,
          name: '参考均值',
          areaStyle: { color: 'rgba(160, 174, 192, 0.15)' },
          lineStyle: { color: '#a0aec0', width: 1.5, type: 'dashed' },
          itemStyle: { color: '#a0aec0' }
        }
      ]
    }]
  }
})

const ATROPHY_COLOR: Record<string, string> = {
  轻度萎缩: '#D99A2B',
  中度萎缩: '#D96B2B',
  重度萎缩: '#C94F4F'
}

function atrophyStyle(level: string): string {
  const c = ATROPHY_COLOR[level] ?? '#5B8CAF'
  return `background:${c}1A;border:1px solid ${c}55;color:${c};`
}

/** Z 值严重度配色：|Z|≥3 显著异常（红），2~3 异常（橙），<2 临界（黄） */
function zColor(z: number): string {
  const az = Math.abs(z)
  return az >= 3 ? '#C94F4F' : az >= 2 ? '#D96B2B' : '#D99A2B'
}

/** Z 值条几何：显示域 -4（左）~ 0（右），填充自测量点延伸至 0 基准线 */
const Z_AXIS_MIN = -4
function zBarLeft(z: number): number {
  const pct = ((z - Z_AXIS_MIN) / (0 - Z_AXIS_MIN)) * 100
  return clampPct(pct)
}
/** Z=-2 临界阈值刻度位置（%） */
const Z_THRESHOLD_LEFT = ((-2 - Z_AXIS_MIN) / (0 - Z_AXIS_MIN)) * 100

/** 相对代谢水平百分比（metabolism 为 0-1 的相对值） */
function metabolismPct(v: number): string {
  return `${(v * 100).toFixed(0)}%`
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="AI 量化分析（全屏）"
    width="88vw"
    top="4vh"
    append-to-body
    class="metrics-fs-dialog"
  >
    <div v-if="a" class="space-y-5">
      <!-- 风险仪表盘 + 分期 -->
      <div class="flex items-center gap-6 p-5 rounded-card bg-gradient-to-br from-[#F4F8FC] to-[#EAF2F9] border border-[#D6E4F0]">
        <!-- 仪表盘 -->
        <div class="relative w-[180px] h-[110px] shrink-0">
          <svg viewBox="0 0 200 120" class="w-full h-full">
            <!-- 背景弧 -->
            <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="#E6EEF5" stroke-width="14" stroke-linecap="round" />
            <!-- 四色分级弧 -->
            <path d="M 20 100 A 80 80 0 0 1 60 38" fill="none" stroke="#2E9E6B" stroke-width="14" stroke-linecap="round" />
            <path d="M 60 38 A 80 80 0 0 1 100 20" fill="none" stroke="#D99A2B" stroke-width="14" />
            <path d="M 100 20 A 80 80 0 0 1 140 38" fill="none" stroke="#D96B2B" stroke-width="14" />
            <path d="M 140 38 A 80 80 0 0 1 180 100" fill="none" stroke="#C94F4F" stroke-width="14" stroke-linecap="round" />
            <!-- 指针 -->
            <g :transform="`rotate(${gaugeAngle - 90} 100 100)`">
              <line x1="100" y1="100" x2="100" y2="32" :stroke="SCORE_COLOR[a.riskLevel]" stroke-width="3" stroke-linecap="round" />
              <circle cx="100" cy="100" r="6" fill="#24303C" />
            </g>
          </svg>
          <div class="absolute inset-0 flex flex-col items-center justify-end pb-1">
            <div class="font-num text-[28px] font-bold leading-none" :style="{ color: SCORE_COLOR[a.riskLevel] }">
              {{ a.riskScore }}
            </div>
            <div class="text-[11px] text-hint mt-0.5">/ 100 分</div>
          </div>
        </div>

        <!-- 分期与置信度 -->
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-3">
            <RiskLevelTag :risk-level="a.riskLevel" size="large" />
            <span class="text-[18px] font-semibold text-ink">{{ SCORE_LABEL[a.riskLevel] }}</span>
          </div>
          <div class="mt-2 flex items-center gap-6 text-[13px]">
            <span>病程分期：<b class="text-ink">{{ a.stage }}</b></span>
            <span>推理置信度：<b class="text-ink">{{ (a.confidence * 100).toFixed(1) }}%</b></span>
            <span v-if="a.inferenceSource === 'real'">
              <el-tag size="small" effect="dark" type="primary">真实 TransMF 推理</el-tag>
            </span>
          </div>
          <p class="mt-2 text-[12px] text-sub leading-6">{{ a.summary }}</p>
        </div>
      </div>

      <!-- 关键脑结构指标卡 -->
      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <div
          v-for="(k, i) in keyMetrics"
          :key="i"
          class="p-3 rounded-card border text-center"
          :style="statusStyle(k.status)"
        >
          <div class="text-[11px] text-hint">{{ k.label }}</div>
          <div class="font-num text-[20px] font-semibold mt-1">{{ k.value }}<span class="text-[11px] font-normal ml-0.5">{{ k.unit }}</span></div>
          <div class="text-[10px] mt-0.5 opacity-70">参考 {{ k.ref }}</div>
        </div>
      </div>

      <!-- 异常脑区分布：Z 值严重度 + 相对代谢（仅在有异常脑区数据时呈现） -->
      <div v-if="regions.length" class="p-4 rounded-card border border-line">
        <div class="flex items-center justify-between mb-3">
          <div class="text-[13px] font-medium text-ink">异常脑区分布 · Z 值与代谢偏离</div>
          <div class="flex items-center gap-4 text-[11px] text-hint">
            <span class="flex items-center gap-1.5">
              <span class="inline-block w-3 h-3 rounded-full border border-dashed border-[#9AA7B2]" />
              虚线位 = Z -2 临界阈值
            </span>
            <span>共 {{ regions.length }} 个脑区 · 按严重度排序</span>
          </div>
        </div>
        <div class="space-y-2">
          <div
            v-for="(r, i) in regions"
            :key="i"
            class="flex items-center gap-3 rounded-md px-1 py-0.5 hover:bg-[#F8FBFE] transition-colors"
          >
            <!-- 脑区 / 侧别 / 萎缩程度 -->
            <div class="w-44 shrink-0 flex items-center gap-2 min-w-0">
              <span class="text-[13px] text-ink font-medium shrink-0">{{ r.region }}</span>
              <span class="text-[11px] text-hint shrink-0">{{ r.side }}</span>
              <span
                class="ml-auto text-[10.5px] px-1.5 py-0.5 rounded-full whitespace-nowrap"
                :style="atrophyStyle(r.atrophy)"
              >{{ r.atrophy }}</span>
            </div>
            <!-- Z 值轨道：-4（左）→ 0（右），点为该脑区 Z 值 -->
            <div class="flex-1 relative h-6 min-w-0">
              <div class="absolute inset-x-0 top-1/2 -translate-y-1/2 h-2 rounded-full bg-[#EEF3F7]" />
              <!-- Z = -2 临界刻度 -->
              <div
                class="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-px h-3.5 border-l border-dashed border-[#9AA7B2]"
                :style="{ left: Z_THRESHOLD_LEFT + '%' }"
              />
              <!-- 偏离填充（自测量点延伸至 0 基准） -->
              <div
                class="absolute top-1/2 -translate-y-1/2 h-2 rounded-r-full"
                :style="{ left: zBarLeft(r.zScore) + '%', width: 100 - zBarLeft(r.zScore) + '%', background: zColor(r.zScore) + '40' }"
              />
              <!-- Z 值测量点 -->
              <div
                class="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3 h-3 rounded-full border-2 border-white shadow-sm"
                :style="{ left: zBarLeft(r.zScore) + '%', background: zColor(r.zScore) }"
              />
            </div>
            <!-- Z 数值 -->
            <div
              class="w-12 shrink-0 text-right font-num text-[13px] font-semibold"
              :style="{ color: zColor(r.zScore) }"
              :title="'Z = ' + r.zScore + '（与正常对照标准差倍数）'"
            >{{ r.zScore.toFixed(1) }}</div>
            <!-- 相对代谢 -->
            <div class="w-24 shrink-0 text-right text-[12px] text-sub">
              相对代谢 <span class="font-num font-medium" :style="{ color: zColor(r.zScore) }">{{ metabolismPct(r.metabolism) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 多维度雷达图：患者实测 vs 参考范围 -->
      <div class="mt-3">
        <div class="text-[13px] font-medium text-ink mb-2">多维度对比雷达图</div>
        <ChartBase :option="radarOption" :height="320" />
      </div>

      <!-- 全数量化指标（带参考范围进度条） -->
      <div>
        <div class="flex items-center justify-between mb-3">
          <div class="text-[13px] font-medium text-ink">全数量化指标 · 参考范围可视化</div>
          <div class="flex items-center gap-4 text-[11px] text-hint">
            <span class="flex items-center gap-1.5">
              <span class="inline-block w-4 h-2 rounded-sm bg-[#2E9E6B]/25 border-x border-[#2E9E6B]/50" />
              参考范围
            </span>
            <span class="flex items-center gap-1.5">
              <span class="inline-block w-2.5 h-2.5 rounded-full bg-[#24303C] ring-2 ring-white shadow-sm" />
              本次测量值
            </span>
            <span class="font-num">
              共 {{ metricCounts.total }} 项 ·
              <span class="text-risk-late">异常 {{ metricCounts.abnormal }}</span> ·
              <span class="text-[#D99A2B]">临界 {{ metricCounts.warn }}</span> ·
              <span class="text-risk-low">正常 {{ metricCounts.normal }}</span>
            </span>
          </div>
        </div>
        <div class="space-y-2.5">
          <div
            v-for="m in a.metrics"
            :key="m.key"
            class="flex items-center gap-3 px-4 py-3 rounded-card border transition-shadow hover:shadow-[0_2px_10px_rgba(36,48,60,0.08)]"
            :style="m.status === 'abnormal' ? 'border-color:' + STATUS_COLOR.abnormal + '55; background:' + STATUS_COLOR.abnormal + '0A;' : ''"
          >
            <div class="w-44 shrink-0">
              <div class="text-[13px] text-ink font-medium">{{ m.label }}</div>
              <div class="text-[11px] text-hint font-num mt-0.5">参考 {{ m.refRange }}</div>
            </div>
            <!-- 轨道：绿色带=参考范围，圆点=测量值（越界点停在区间外侧，方向一目了然） -->
            <div class="flex-1 relative h-7">
              <div class="absolute inset-x-0 top-1/2 -translate-y-1/2 h-2.5 rounded-full bg-[#EEF3F7]" />
              <div
                class="absolute top-1/2 -translate-y-1/2 h-2.5 rounded-full bg-[#2E9E6B]/20 border-x border-[#2E9E6B]/45"
                :style="{ left: barGeom(m).bandLeft + '%', width: barGeom(m).bandWidth + '%' }"
              />
              <!-- 测量值标记 -->
              <div
                class="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-4 h-4 rounded-full border-2 border-white shadow-md"
                :style="{ left: barGeom(m).marker + '%', background: STATUS_COLOR[m.status] }"
              />
            </div>
            <!-- 数值 + 偏差（定性量表不显示百分比） -->
            <div class="w-32 shrink-0 text-right">
              <div class="font-num text-[15px] font-semibold" :style="{ color: STATUS_COLOR[m.status] }">
                {{ m.value }}<span class="text-[11px] text-hint font-normal ml-0.5">{{ m.unit }}</span>
              </div>
              <!-- 偏差（↓偏低 / ↑偏高；定性量表与在范围内不显示箭头） -->
              <div
                class="text-[11px] font-num flex items-center justify-end gap-0.5"
                :style="{ color: deviationPct(m) ? STATUS_COLOR[m.status] : '#9AA7B2' }"
              >
                <span
                  v-if="deviationDir(m) === 'down'"
                  class="font-sans leading-none"
                  title="低于参考范围"
                >↓</span>
                <span
                  v-else-if="deviationDir(m) === 'up'"
                  class="font-sans leading-none"
                  title="高于参考范围"
                >↑</span>
                {{ deviationPct(m) || '—' }}
              </div>
            </div>
            <!-- 状态 -->
            <div class="w-16 shrink-0 text-right">
              <el-tag size="small" round effect="light" :type="m.status === 'normal' ? 'success' : m.status === 'warn' ? 'warning' : 'danger'">
                {{ STATUS_TEXT[m.status] }}
              </el-tag>
            </div>
          </div>
        </div>
      </div>
    </div>
    <el-empty v-else description="暂无分析数据" />
  </el-dialog>
</template>

<style scoped>
.metrics-fs-dialog :deep(.el-dialog__body) {
  padding: 16px 20px 20px;
  max-height: 78vh;
  overflow-y: auto;
}
</style>
