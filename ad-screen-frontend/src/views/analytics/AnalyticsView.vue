<script setup lang="ts">
/**
 * 科研统计分析看板（researcher / admin）
 * ------------------------------------------------------------------
 * 面向科研管理员的队列级统计视图，全部基于真实病例库聚合：
 * 1. 顶部概览卡：在库病例 / 高风险占比 / 医生认可率 / 随访覆盖
 * 2. 月度筛查趋势（真实检查日期聚合，柱=筛查量，线=高风险占比）
 * 3. 队列画像：年龄段分布 / 性别分布 / AI 风险评分直方图
 * 4. 质量指标：AI 分级医生认可率（按等级细分）/ 认知变化 / 用药依从性 / 随访方式
 * 5. 一键导出科研统计汇总 CSV（指标-分项-数值三列，便于二次分析）
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  apiGetDemographics, apiGetScoreHistogram, apiGetAgreement,
  apiGetFollowUpStats, apiGetMonthlyTrend, apiGetCorrelation, apiGetPathway, apiGetModelComparison,
  apiGetCohortComparison
} from '@/api/analytics'
import type {
  Demographics, ScoreHistogram, AgreementStats, FollowUpStats, MonthlyTrendPoint, DistItem,
  CorrelationResult, PathwayResult
} from '@/types/analytics'
import type { ModelComparisonResult, ModelComparisonItem } from '@/types/modelComparison.d'
import type { CohortComparisonResult, CohortGroup } from '@/types/cohortComparison.d'
import ChartBase from '@/components/ChartBase.vue'
import { downloadCsv } from '@/utils/export'
import { formatDate } from '@/utils/format'
import type { EChartsOption } from 'echarts'

// ---------- 筛选状态 ----------
/** 模态筛选（作用于全部统计）；趋势月数（仅作用于月度趋势图） */
const filterModality = ref<'' | 'MRI' | 'PET' | 'MRI+PET'>('')
const trendMonths = ref<3 | 6 | 12>(6)
/** 数据生成时间（每次成功加载刷新） */
const generatedAt = ref('')
const MODALITY_LABELS: Record<string, string> = { '': '全部模态', MRI: '仅 MRI', PET: '仅 PET', 'MRI+PET': 'MRI+PET 多模态' }

// ---------- 页面数据（分模块容错加载：单模块失败不拖垮整页） ----------
const loading = ref(true)
const demo = ref<Demographics | null>(null)
const histogram = ref<ScoreHistogram | null>(null)
const agreement = ref<AgreementStats | null>(null)
const fuStats = ref<FollowUpStats | null>(null)
const trend = ref<MonthlyTrendPoint[]>([])
const correlation = ref<CorrelationResult | null>(null)
const pathway = ref<PathwayResult | null>(null)
const modelComparison = ref<ModelComparisonResult | null>(null)
const cohortComparison = ref<CohortComparisonResult | null>(null)
/** 加载失败模块（key → 模块中文名），供错误条与重试提示 */
const failedModules = ref<string[]>([])

async function loadAll(): Promise<void> {
  loading.value = true
  failedModules.value = []
  const p = { modality: filterModality.value, months: trendMonths.value }
  const [d, h, a, f, t, corr, path, mc, cc] = await Promise.allSettled([
    apiGetDemographics(p),
    apiGetScoreHistogram(p),
    apiGetAgreement(p),
    apiGetFollowUpStats(p),
    apiGetMonthlyTrend(p),
    apiGetCorrelation(p),
    apiGetPathway(p),
    apiGetModelComparison(p),
    apiGetCohortComparison(p)
  ])
  demo.value = d.status === 'fulfilled' ? d.value : null
  histogram.value = h.status === 'fulfilled' ? h.value : null
  agreement.value = a.status === 'fulfilled' ? a.value : null
  fuStats.value = f.status === 'fulfilled' ? f.value : null
  trend.value = t.status === 'fulfilled' ? t.value : []
  correlation.value = corr.status === 'fulfilled' ? corr.value : null
  pathway.value = path.status === 'fulfilled' ? path.value : null
  modelComparison.value = mc.status === 'fulfilled' ? mc.value : null
  cohortComparison.value = cc.status === 'fulfilled' ? cc.value : null
  const fails: string[] = []
  if (d.status === 'rejected') fails.push('队列画像')
  if (h.status === 'rejected') fails.push('评分直方图')
  if (a.status === 'rejected') fails.push('医生认可率')
  if (f.status === 'rejected') fails.push('随访质量')
  if (t.status === 'rejected') fails.push('月度趋势')
  if (corr.status === 'rejected') fails.push('关联分析')
  if (path.status === 'rejected') fails.push('路径流转')
  if (mc.status === 'rejected') fails.push('模型对比')
  if (cc.status === 'rejected') fails.push('队列对比')
  failedModules.value = fails
  generatedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
  loading.value = false
}

/** 筛选变更自动重查 */
watch([filterModality, trendMonths], () => { void loadAll() })

onMounted(() => { void loadAll() })

// ---------- 概览卡片 ----------
const cards = computed(() => {
  const total = demo.value?.total ?? 0
  const highRiskTrendSum = trend.value.reduce((s, p) => s + p.highRisk, 0)
  const trendTotal = trend.value.reduce((s, p) => s + p.total, 0)
  return [
    {
      label: '在库病例总数',
      value: String(total),
      unit: '例',
      color: '#2F6DA3',
      bg: '#E8F1F8',
      hint: '不含已删除病例'
    },
    {
      label: `近 ${trendMonths.value} 月高风险占比`,
      value: trendTotal ? ((highRiskTrendSum / trendTotal) * 100).toFixed(1) : '0.0',
      unit: '%',
      color: '#C94F4F',
      bg: '#FAEDED',
      hint: `高风险 ${highRiskTrendSum} / 筛查 ${trendTotal} 例`
    },
    {
      label: '医生认可率',
      value: String(agreement.value?.approvalRate ?? 0),
      unit: '%',
      color: '#2E9E6B',
      bg: '#EDF7F2',
      hint: (agreement.value?.reviewed ?? 0) > 0
        ? `已复核 ${agreement.value?.reviewed ?? 0} / ${agreement.value?.total ?? 0} 份（最新版本口径）`
        : `待医生复核 ${agreement.value?.pending ?? 0} 份，暂无已处理版本`
    },
    {
      label: '随访覆盖率',
      value: fuStats.value?.planCount ? ((fuStats.value.coveredCases / fuStats.value.planCount) * 100).toFixed(1) : '0.0',
      unit: '%',
      color: '#D99A2B',
      bg: '#FBF4E6',
      hint: `已随访 ${fuStats.value?.coveredCases ?? 0} / 建计划 ${fuStats.value?.planCount ?? 0} 例`
    }
  ]
})

// ---------- 月度趋势：柱（筛查量）+ 线（高风险占比）双轴 ----------
const trendOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  legend: { top: 0, textStyle: { color: '#5C6B7A', fontSize: 12 } },
  grid: { left: 8, right: 8, top: 36, bottom: 8, containLabel: true },
  xAxis: {
    type: 'category',
    data: trend.value.map((p) => p.month),
    axisLine: { lineStyle: { color: '#E3E9EF' } },
    axisLabel: { color: '#5C6B7A', fontSize: 12 }
  },
  yAxis: [
    {
      type: 'value',
      name: '例',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    {
      type: 'value',
      name: '%',
      min: 0,
      max: 100,
      splitLine: { show: false },
      axisLabel: { color: '#93A1AF', formatter: '{value}%' }
    }
  ],
  series: [
    {
      name: '筛查量',
      type: 'bar',
      data: trend.value.map((p) => p.total),
      barWidth: 22,
      itemStyle: {
        color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: '#7FB0D9' }, { offset: 1, color: '#2F6DA3' }] },
        borderRadius: [4, 4, 0, 0]
      }
    },
    {
      name: '高风险占比',
      type: 'line',
      yAxisIndex: 1,
      data: trend.value.map((p) => p.positiveRate),
      smooth: true,
      symbolSize: 7,
      lineStyle: { color: '#C94F4F', width: 2.5 },
      itemStyle: { color: '#C94F4F' }
    }
  ]
}))

// ---------- 年龄段分布柱状图 ----------
const ageOption = computed<EChartsOption>(() => {
  const data = demo.value?.ageGroups ?? []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: '{b} 岁：{c} 例' },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.name),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 12 }
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    series: [{
      type: 'bar',
      data: data.map((d) => d.value),
      barWidth: 26,
      itemStyle: {
        color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: '#8FBFE8' }, { offset: 1, color: '#2F6DA3' }] },
        borderRadius: [4, 4, 0, 0]
      },
      label: { show: true, position: 'top', color: '#5C6B7A', fontSize: 11 }
    }]
  }
})

// ---------- 性别分布环形图 ----------
const GENDER_COLORS: Record<string, string> = { 男: '#2F6DA3', 女: '#7B5EA7' }
const genderOption = computed<EChartsOption>(() => {
  const data = demo.value?.genderDist ?? []
  return {
    tooltip: { trigger: 'item', formatter: '{b}：{c} 例（{d}%）' },
    legend: { orient: 'vertical', right: 16, top: 'center', textStyle: { color: '#5C6B7A', fontSize: 12 } },
    color: data.map((d) => GENDER_COLORS[d.name] ?? '#93A1AF'),
    series: [{
      type: 'pie',
      radius: ['48%', '72%'],
      center: ['38%', '50%'],
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data: data.map((d) => ({ name: d.name, value: d.value }))
    }]
  }
})

// ---------- 风险评分直方图 ----------
const scoreOption = computed<EChartsOption>(() => {
  const hist = histogram.value?.histogram ?? []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (ps) => {
      const p = Array.isArray(ps) ? ps[0] : ps
      return `评分 ${p.name} 分：${p.value} 例`
    } },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: hist.map((b) => b.bucket),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11, interval: 0, rotate: 0 }
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    series: [{
      type: 'bar',
      data: hist.map((b) => b.count),
      barCategoryGap: '30%',
      itemStyle: {
        // 低分绿 → 高分红，直观对应风险语义
        color: (p: { dataIndex: number }) => {
          const hues = ['#2E9E6B', '#5FA86E', '#8AAE71', '#B3A873', '#D99A2B', '#D98B2B', '#D97B2B', '#D96B2B', '#C95F4F', '#C94F4F']
          return hues[p.dataIndex] ?? '#2F6DA3'
        },
        borderRadius: [3, 3, 0, 0]
      },
      // 分级边界参考线：35 低/轻度分界 · 60 轻度/AD早期分界 · 80 早期/中晚期分界
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#93A1AF', type: 'dashed', width: 1 },
        label: { color: '#93A1AF', fontSize: 10, formatter: (p: { name: string }) => p.name },
        data: [
          { xAxis: '30-40', name: '低/轻度 35' },
          { xAxis: '50-60', name: '轻度/早期 60' },
          { xAxis: '70-80', name: '早期/晚期 80' }
        ]
      }
    }]
  }
})

// ---------- 医生认可率（按风险等级横向条形） ----------
const agreementOption = computed<EChartsOption>(() => {
  const rows = [...(agreement.value?.byLevel ?? [])].sort((a, b) => a.approvalRate - b.approvalRate)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (ps) => {
      const p = Array.isArray(ps) ? ps[0] : ps
      const row = rows[p.dataIndex]
      return `${row.levelName}：认可 ${row.approved}/${row.total} 份（${p.value}%）`
    } },
    grid: { left: 8, right: 42, top: 10, bottom: 10, containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      max: 100,
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF', formatter: '{value}%' }
    },
    yAxis: {
      type: 'category',
      data: rows.map((r) => r.levelName),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisTick: { show: false },
      axisLabel: { color: '#5C6B7A', fontSize: 12 }
    },
    series: [{
      type: 'bar',
      data: rows.map((r) => r.approvalRate),
      barWidth: 14,
      itemStyle: {
        color: (p) => (Number(p.value) >= 80 ? '#2E9E6B' : Number(p.value) >= 60 ? '#D99A2B' : '#C94F4F'),
        borderRadius: [0, 7, 7, 0]
      },
      label: { show: true, position: 'right', formatter: '{c}%', color: '#5C6B7A', fontSize: 12 }
    }]
  }
})

// ---------- 认知变化 / 依从性 / 随访方式 ----------
const COGNITION_COLORS = ['#2E9E6B', '#2F6DA3', '#D99A2B', '#C94F4F']
const cognitionOption = computed<EChartsOption>(() => pieOption(fuStats.value?.cognitionDist ?? [], COGNITION_COLORS))
const adherenceOption = computed<EChartsOption>(() => pieOption(fuStats.value?.adherenceDist ?? [], ['#2E9E6B', '#D99A2B', '#D97B2B', '#C94F4F']))
const visitTypeOption = computed<EChartsOption>(() => {
  const data = fuStats.value?.visitTypeDist ?? []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.name),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    series: [{
      type: 'bar',
      data: data.map((d) => d.value),
      barWidth: 22,
      itemStyle: { color: '#7B5EA7', borderRadius: [4, 4, 0, 0] },
      label: { show: true, position: 'top', color: '#5C6B7A', fontSize: 11 }
    }]
  }
})

function pieOption(data: DistItem[], colors: string[]): EChartsOption {
  return {
    tooltip: { trigger: 'item', formatter: '{b}：{c} 次（{d}%）' },
    legend: { orient: 'vertical', right: 12, top: 'center', textStyle: { color: '#5C6B7A', fontSize: 12 } },
    color: colors,
    series: [{
      type: 'pie',
      radius: ['46%', '70%'],
      center: ['36%', '50%'],
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data: data.map((d) => ({ name: d.name, value: d.value }))
    }]
  }
}

// ==================== 风险因子关联分析 ====================

/** 箱线图统计：从已排序数组计算 [min, Q1, median, Q3, max] */
function boxStats(sorted: number[]): number[] {
  const n = sorted.length
  if (n === 0) return [0, 0, 0, 0, 0]
  const q1 = sorted[Math.floor(n * 0.25)]
  const median = sorted[Math.floor(n * 0.5)]
  const q3 = sorted[Math.floor(n * 0.75)]
  return [sorted[0], q1, median, q3, sorted[n - 1]]
}

// ---------- 风险因子相关矩阵热力图 ----------
const corrHeatmapOption = computed<EChartsOption>(() => {
  const factorLabels = correlation.value?.factorLabels ?? []
  const matrix = correlation.value?.matrix ?? []
  const data: [number, number, number][] = []
  for (let i = 0; i < matrix.length; i++) {
    for (let j = 0; j < matrix[i].length; j++) {
      data.push([i, j, matrix[i][j]])
    }
  }
  return {
    tooltip: {
      position: 'top',
      formatter: (p: unknown) => {
        const param = p as { value: [number, number, number] }
        const [i, j, v] = param.value
        return `${factorLabels[i]} × ${factorLabels[j]}<br/>相关系数：${v.toFixed(3)}`
      }
    },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: factorLabels,
      splitArea: { show: true },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11, interval: 0 }
    },
    yAxis: {
      type: 'category',
      data: factorLabels,
      splitArea: { show: true },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11, interval: 0 }
    },
    visualMap: {
      min: -1,
      max: 1,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      textStyle: { color: '#5C6B7A', fontSize: 10 },
      inRange: { color: ['#C94F4F', '#ffffff', '#2F6DA3'] }
    },
    series: [{
      type: 'heatmap',
      data,
      label: {
        show: true,
        color: '#3A4A5C',
        fontSize: 11,
        formatter: (p: unknown) => {
          const param = p as { value: [number, number, number] }
          return param.value[2].toFixed(2)
        }
      },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.25)' } }
    }]
  }
})

// ---------- 年龄 vs AI 风险评分散点 + 回归线 ----------
const ageRiskScatterOption = computed<EChartsOption>(() => {
  const scatter = correlation.value?.scatter.ageRisk
  const points = scatter?.points ?? []
  const reg = scatter?.regression
  const xValues = points.map((p) => p.x)
  const xMin = xValues.length ? Math.min(...xValues) : 0
  const xMax = xValues.length ? Math.max(...xValues) : 0
  const markLineData = reg
    ? [[
        { coord: [xMin, reg.slope * xMin + reg.intercept] },
        { coord: [xMax, reg.slope * xMax + reg.intercept] }
      ]]
    : []
  return {
    tooltip: {
      formatter: (p: unknown) => {
        const param = p as { data: { value: [number, number] } & { name: string } }
        if (param.data && param.data.value) {
          const pt = points.find((pp) => pp.x === param.data.value[0] && pp.y === param.data.value[1])
          if (pt) return `${pt.name}<br/>病例：${pt.caseId}<br/>${scatter?.xLabel ?? ''}：${pt.x}<br/>${scatter?.yLabel ?? ''}：${pt.y}`
        }
        return ''
      }
    },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'value',
      name: '年龄（岁）',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#93A1AF' },
      splitLine: { lineStyle: { color: '#EFF3F7' } }
    },
    yAxis: {
      type: 'value',
      name: 'AI 风险评分',
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#93A1AF' },
      splitLine: { lineStyle: { color: '#EFF3F7' } }
    },
    series: [{
      type: 'scatter',
      data: points.map((p) => ({ value: [p.x, p.y], name: p.name })),
      symbolSize: 9,
      itemStyle: { color: '#2F6DA3', opacity: 0.7, borderColor: '#fff', borderWidth: 1 },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#C94F4F', width: 2, type: 'solid' },
        data: markLineData as unknown as Array<[unknown, unknown]>
      }
    }]
  } as unknown as EChartsOption
})

// ---------- MMSE vs AI 风险评分散点 + 回归线 ----------
const mmseRiskScatterOption = computed<EChartsOption>(() => {
  const scatter = correlation.value?.scatter.mmseRisk
  const points = scatter?.points ?? []
  const reg = scatter?.regression
  const xValues = points.map((p) => p.x)
  const xMin = xValues.length ? Math.min(...xValues) : 0
  const xMax = xValues.length ? Math.max(...xValues) : 0
  const markLineData = reg
    ? [[
        { coord: [xMin, reg.slope * xMin + reg.intercept] },
        { coord: [xMax, reg.slope * xMax + reg.intercept] }
      ]]
    : []
  return {
    tooltip: {
      formatter: (p: unknown) => {
        const param = p as { data: { value: [number, number] } & { name: string } }
        if (param.data && param.data.value) {
          const pt = points.find((pp) => pp.x === param.data.value[0] && pp.y === param.data.value[1])
          if (pt) return `${pt.name}<br/>病例：${pt.caseId}<br/>${scatter?.xLabel ?? ''}：${pt.x}<br/>${scatter?.yLabel ?? ''}：${pt.y}`
        }
        return ''
      }
    },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'value',
      name: 'MMSE',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#93A1AF' },
      splitLine: { lineStyle: { color: '#EFF3F7' } }
    },
    yAxis: {
      type: 'value',
      name: 'AI 风险评分',
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#93A1AF' },
      splitLine: { lineStyle: { color: '#EFF3F7' } }
    },
    series: [{
      type: 'scatter',
      data: points.map((p) => ({ value: [p.x, p.y], name: p.name })),
      symbolSize: 8,
      itemStyle: { color: '#2E9E6B', opacity: 0.7, borderColor: '#fff', borderWidth: 1 },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#C94F4F', width: 2, type: 'solid' },
        data: markLineData as unknown as Array<[unknown, unknown]>
      }
    }]
  } as unknown as EChartsOption
})

// ---------- 性别 × AI 风险评分 箱线图 ----------
const genderBoxOption = computed<EChartsOption>(() => {
  const groups = correlation.value?.boxplots.genderRisk ?? []
  const boxData = groups.map((g) => boxStats([...g.values].sort((a, b) => a - b)))
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: unknown) => {
        const param = p as { name: string; value: number[] }
        if (!param.value || param.value.length < 5) return ''
        return `${param.name}<br/>最大：${param.value[4]}<br/>Q3：${param.value[3]}<br/>中位：${param.value[2]}<br/>Q1：${param.value[1]}<br/>最小：${param.value[0]}`
      }
    },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: groups.map((g) => g.name),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      name: 'AI 风险评分',
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#93A1AF' },
      splitLine: { lineStyle: { color: '#EFF3F7' } }
    },
    series: [{
      type: 'boxplot',
      data: boxData,
      itemStyle: { color: '#E8F1F8', borderColor: '#2F6DA3', borderWidth: 1.5 }
    }]
  }
})

// ---------- 风险等级 × MMSE 箱线图 ----------
const levelMmseBoxOption = computed<EChartsOption>(() => {
  const groups = correlation.value?.boxplots.levelMmse ?? []
  const boxData = groups.map((g) => boxStats([...g.values].sort((a, b) => a - b)))
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: unknown) => {
        const param = p as { name: string; value: number[] }
        if (!param.value || param.value.length < 5) return ''
        return `${param.name}<br/>最大：${param.value[4]}<br/>Q3：${param.value[3]}<br/>中位：${param.value[2]}<br/>Q1：${param.value[1]}<br/>最小：${param.value[0]}`
      }
    },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: groups.map((g) => g.name),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11, interval: 0, rotate: groups.length > 4 ? 20 : 0 }
    },
    yAxis: {
      type: 'value',
      name: 'MMSE',
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#93A1AF' },
      splitLine: { lineStyle: { color: '#EFF3F7' } }
    },
    series: [{
      type: 'boxplot',
      data: boxData,
      itemStyle: { color: '#EDF7F2', borderColor: '#2E9E6B', borderWidth: 1.5 }
    }]
  }
})

// ---------- 临床路径流转 桑基图 ----------
const pathwaySankeyOption = computed<EChartsOption>(() => {
  const nodes = pathway.value?.nodes ?? []
  const links = pathway.value?.links ?? []
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: unknown) => {
        const param = p as { name: string; data: { source?: string; target?: string; value?: number } }
        if (param.data && param.data.source && param.data.target) {
          return `${param.data.source} → ${param.data.target}<br/>病例数：${param.data.value ?? 0} 例`
        }
        return `${param.name}<br/>病例数：${param.data?.value ?? 0} 例`
      }
    },
    series: [{
      type: 'sankey',
      data: nodes,
      links,
      nodeAlign: 'left',
      orient: 'horizontal',
      layoutIterations: 32,
      left: 8,
      right: 16,
      top: 16,
      bottom: 16,
      label: { fontSize: 12, color: '#5C6B7A' },
      lineStyle: { color: 'gradient', opacity: 0.5, curveness: 0.5 },
      itemStyle: { borderWidth: 0, borderColor: '#fff' },
      emphasis: { focus: 'adjacency', lineStyle: { opacity: 0.8 } }
    }]
  }
})

// ---------- 科研统计汇总 CSV 导出（指标-分项-数值） ----------
function exportSummary(): void {
  const headers = ['统计模块', '分项', '数值']
  const rows: (string | number)[][] = []
  // 导出条件与时间戳（科研数据可追溯）
  rows.push(['导出信息', `筛选条件（${MODALITY_LABELS[filterModality.value]}，趋势近 ${trendMonths.value} 个月）`, generatedAt.value || new Date().toLocaleString('zh-CN', { hour12: false })])
  rows.push(['队列概览', '在库病例总数', demo.value?.total ?? 0])
  for (const g of demo.value?.ageGroups ?? []) rows.push(['年龄段分布', `${g.name} 岁`, g.value])
  for (const g of demo.value?.genderDist ?? []) rows.push(['性别分布', g.name, g.value])
  if (histogram.value) {
    rows.push(['风险评分直方图', '已出分病例总数', histogram.value.total])
    for (const b of histogram.value.histogram) rows.push(['风险评分直方图', `${b.bucket} 分`, b.count])
  }
  if (agreement.value) {
    rows.push(['医生审核', '版本总数', agreement.value.total])
    rows.push(['医生审核', '待审核', agreement.value.pending])
    rows.push(['医生审核', '已通过', agreement.value.approved])
    rows.push(['医生审核', '已驳回', agreement.value.rejected])
    rows.push(['医生审核', '认可率(%)', agreement.value.approvalRate])
    for (const l of agreement.value.byLevel) {
      rows.push(['分等级认可率', `${l.levelName}`, `${l.approved}/${l.total}（${l.approvalRate}%）`])
    }
  }
  if (fuStats.value) {
    rows.push(['随访质量', '随访计划数', fuStats.value.planCount])
    rows.push(['随访质量', '随访记录数', fuStats.value.visitCount])
    rows.push(['随访质量', '覆盖病例数', fuStats.value.coveredCases])
    rows.push(['随访质量', '平均 MMSE', fuStats.value.avgMmse ?? '—'])
    rows.push(['随访质量', '平均 MoCA', fuStats.value.avgMoca ?? '—'])
    for (const c of fuStats.value.cognitionDist) rows.push(['认知变化分布', c.name, c.value])
    for (const c of fuStats.value.adherenceDist) rows.push(['用药依从性分布', c.name, c.value])
    for (const c of fuStats.value.visitTypeDist) rows.push(['随访方式分布', c.name, c.value])
  }
  for (const p of trend.value) rows.push(['月度趋势', p.month, `筛查${p.total}/高风险${p.highRisk}(${p.positiveRate}%)`])
  downloadCsv(`科研统计汇总_${formatDate(new Date())}.csv`, headers, rows)
  ElMessage.success('科研统计汇总 CSV 已导出')
}

// ==================== AI 模型版本对比 ====================

const MODEL_COLORS = ['#2F6DA3', '#D97A2B', '#5B8C5A', '#7B5EA7', '#C94F4F', '#D99A2B']

/** 模型对比柱状图：样本量 + 高风险占比 + 通过率 */
const modelCompareOption = computed<EChartsOption>(() => {
  const models = modelComparison.value?.models ?? []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['样本量', '高风险占比(%)', '医生通过率(%)'], top: 0 },
    grid: { left: 50, right: 50, top: 40, bottom: 30 },
    xAxis: {
      type: 'category',
      data: models.map((m: ModelComparisonItem) => m.modelVersion),
      axisLabel: { interval: 0, rotate: models.length > 3 ? 20 : 0 }
    },
    yAxis: [
      { type: 'value', name: '样本量', position: 'left' },
      { type: 'value', name: '%', position: 'right', max: 100 }
    ],
    series: [
      { name: '样本量', type: 'bar', data: models.map((m: ModelComparisonItem) => m.sampleCount), itemStyle: { color: '#2F6DA3' } },
      { name: '高风险占比(%)', type: 'bar', yAxisIndex: 1, data: models.map((m: ModelComparisonItem) => m.highRiskRate), itemStyle: { color: '#D97A2B' } },
      { name: '医生通过率(%)', type: 'bar', yAxisIndex: 1, data: models.map((m: ModelComparisonItem) => m.approvalRate), itemStyle: { color: '#5B8C5A' } }
    ]
  } as unknown as EChartsOption
})

/** 模型评分分布对比折线图 */
const modelScoreDistOption = computed<EChartsOption>(() => {
  const dist = modelComparison.value?.scoreDist ?? []
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: dist.map((d) => d.modelVersion), top: 0 },
    grid: { left: 50, right: 30, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: dist[0]?.buckets ?? [], axisLabel: { rotate: 30 } },
    yAxis: { type: 'value', name: '病例数' },
    series: dist.map((d, i) => ({
      name: d.modelVersion,
      type: 'line',
      smooth: true,
      data: d.counts,
      itemStyle: { color: MODEL_COLORS[i % MODEL_COLORS.length] }
    }))
  } as unknown as EChartsOption
})

/** 模型风险等级分布堆叠柱状图 */
const modelLevelDistOption = computed<EChartsOption>(() => {
  const models = modelComparison.value?.models ?? []
  const levels = [
    { key: 'low', name: '低风险', color: '#5B8C5A' },
    { key: 'mci', name: 'MCI', color: '#2F6DA3' },
    { key: 'ad-early', name: 'AD 早期', color: '#D99A2B' },
    { key: 'ad-late', name: 'AD 中晚期', color: '#C94F4F' }
  ]
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: levels.map((l) => l.name), top: 0 },
    grid: { left: 50, right: 30, top: 40, bottom: 30 },
    xAxis: {
      type: 'category',
      data: models.map((m: ModelComparisonItem) => m.modelVersion),
      axisLabel: { interval: 0, rotate: models.length > 3 ? 20 : 0 }
    },
    yAxis: { type: 'value', name: '病例数' },
    series: levels.map((l) => ({
      name: l.name,
      type: 'bar',
      stack: 'total',
      data: models.map((m: ModelComparisonItem) => m.levelDist[l.key as 'low'] ?? 0),
      itemStyle: { color: l.color }
    }))
  } as unknown as EChartsOption
})

// ==================== 多中心队列对比分析 ====================

/** 队列对比低饱和蓝灰医疗色调色板 */
const COHORT_PALETTE = ['#5B8AB8', '#7BA7C7', '#A3C4D9', '#C9DCE8']

/** 队列风险等级堆叠柱状图：x=cohort，各风险等级堆叠 */
const cohortLevelStackOption = computed<EChartsOption>(() => {
  const stacks = cohortComparison.value?.levelStack ?? []
  const levels = [
    { key: 'low', name: '低风险', color: '#5B8C5A' },
    { key: 'mci', name: 'MCI', color: '#2F6DA3' },
    { key: 'adEarly', name: 'AD 早期', color: '#D99A2B' },
    { key: 'adLate', name: 'AD 中晚期', color: '#C94F4F' }
  ]
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps: unknown) => {
        const arr = Array.isArray(ps) ? ps : [ps]
        const items = arr as Array<{ name: string; seriesName: string; value: number }>
        if (!items.length) return ''
        const cohort = items[0].name
        const total = items.reduce((s, x) => s + (Number(x.value) || 0), 0)
        const lines = items.map((x) => `${x.seriesName}：${x.value} 例`).join('<br/>')
        return `${cohort}<br/>${lines}<br/>合计：${total} 例`
      }
    },
    legend: { data: levels.map((l) => l.name), top: 0, textStyle: { color: '#5C6B7A', fontSize: 12 } },
    grid: { left: 8, right: 16, top: 40, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: stacks.map((s) => s.cohort),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 12, interval: 0 }
    },
    yAxis: {
      type: 'value',
      name: '病例数',
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    series: levels.map((l, i) => ({
      name: l.name,
      type: 'bar',
      stack: 'cohort',
      barWidth: 32,
      data: stacks.map((s) => s[l.key as 'low']),
      itemStyle: {
        color: l.color,
        borderRadius: i === levels.length - 1 ? [4, 4, 0, 0] : 0
      }
    }))
  } as unknown as EChartsOption
})

/** 各队列评分分布箱线图 */
const cohortScoreBoxOption = computed<EChartsOption>(() => {
  const boxes = cohortComparison.value?.scoreBoxplot ?? []
  const boxData = boxes.map((b) => b.boxStats)
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: unknown) => {
        const param = p as { name: string; value: number[] }
        if (!param.value || param.value.length < 5) return ''
        return `${param.name}<br/>最大：${param.value[4]}<br/>Q3：${param.value[3]}<br/>中位：${param.value[2]}<br/>Q1：${param.value[1]}<br/>最小：${param.value[0]}`
      }
    },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: boxes.map((b) => b.cohort),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 12, interval: 0 }
    },
    yAxis: {
      type: 'value',
      name: 'AI 风险评分',
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#93A1AF' },
      splitLine: { lineStyle: { color: '#EFF3F7' } }
    },
    series: [{
      type: 'boxplot',
      data: boxData,
      itemStyle: {
        color: '#E8F1F8',
        borderColor: '#5B8AB8',
        borderWidth: 1.5
      }
    }]
  } as unknown as EChartsOption
})

/** 队列 AD 率对比条形图（突出捷径学习差异） */
const cohortAdRateOption = computed<EChartsOption>(() => {
  const groups = cohortComparison.value?.groups ?? []
  const sorted: CohortGroup[] = [...groups].sort((a, b) => b.adRate - a.adRate)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps: unknown) => {
        const arr = Array.isArray(ps) ? ps : [ps]
        const p = arr[0] as { name: string; value: number }
        const g = sorted.find((x) => x.cohort === p.name)
        if (!g) return ''
        return `${g.cohort}<br/>AD 率：${g.adRate}%<br/>高风险 ${g.highRiskCount}/${g.sampleCount} 例`
      }
    },
    grid: { left: 8, right: 42, top: 10, bottom: 10, containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      max: 100,
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF', formatter: '{value}%' }
    },
    yAxis: {
      type: 'category',
      data: sorted.map((g) => g.cohort),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisTick: { show: false },
      axisLabel: { color: '#5C6B7A', fontSize: 12 }
    },
    series: [{
      type: 'bar',
      data: sorted.map((g) => g.adRate),
      barWidth: 16,
      itemStyle: {
        color: (p: { dataIndex: number }) => COHORT_PALETTE[p.dataIndex % COHORT_PALETTE.length],
        borderRadius: [0, 7, 7, 0]
      },
      label: { show: true, position: 'right', formatter: '{c}%', color: '#5C6B7A', fontSize: 12 }
    }]
  } as unknown as EChartsOption
})
</script>

<template>
  <div class="page-wrap" v-loading="loading">
    <!-- ==================== 筛选与操作栏 ==================== -->
    <div class="card-ad p-4 flex items-center gap-3 flex-wrap">
      <el-select v-model="filterModality" class="!w-44" placeholder="影像模态">
        <el-option label="全部模态" value="" />
        <el-option label="仅 MRI" value="MRI" />
        <el-option label="仅 PET" value="PET" />
        <el-option label="MRI+PET 多模态" value="MRI+PET" />
      </el-select>
      <el-radio-group v-model="trendMonths">
        <el-radio-button :value="3">近 3 月</el-radio-button>
        <el-radio-button :value="6">近 6 月</el-radio-button>
        <el-radio-button :value="12">近 12 月</el-radio-button>
      </el-radio-group>
      <span class="text-xs text-hint">统计口径：排除已删除病例 · 每病例取最新分析版本</span>
      <div class="flex-1" />
      <span v-if="generatedAt" class="text-xs text-hint">数据生成于 {{ generatedAt }}</span>
      <el-button :icon="'Refresh'" @click="loadAll">刷新</el-button>
      <el-button type="primary" plain :icon="'Download'" @click="exportSummary">导出汇总 CSV</el-button>
    </div>

    <!-- 加载失败提示（分模块容错） -->
    <el-alert
      v-if="failedModules.length > 0"
      type="warning"
      :closable="false"
      class="mt-4"
      show-icon
    >
      <template #title>
        {{ failedModules.join('、') }}模块加载失败，其余模块已正常展示，可点击「刷新」重试
      </template>
    </el-alert>

    <!-- ==================== 概览卡行 ==================== -->
    <div class="grid grid-cols-4 gap-4 mt-4">
      <div
        v-for="(card, i) in cards"
        :key="card.label"
        class="card-ad anim-fade-up p-5 relative overflow-hidden"
        :style="{ '--anim-delay': `${i * 70}ms` }"
      >
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" :style="{ background: card.color }" />
        <div class="flex items-start justify-between relative">
          <div>
            <div class="text-[13px] text-sub">{{ card.label }}</div>
            <div class="mt-2 font-num text-[30px] leading-none font-semibold text-ink">
              {{ card.value }}
              <span class="text-[12px] text-hint font-sans font-normal ml-1">{{ card.unit }}</span>
            </div>
          </div>
          <div class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm" :style="{ background: card.bg, color: card.color }">
            <el-icon :size="20"><component :is="['User', 'WarningFilled', 'CircleCheck', 'Connection'][i]" /></el-icon>
          </div>
        </div>
        <div class="mt-3 text-xs text-hint">{{ card.hint }}</div>
      </div>
    </div>

    <!-- ==================== 月度趋势 + 年龄段 ==================== -->
    <div class="grid grid-cols-3 gap-4 anim-fade-up" style="--anim-delay: 240ms">
      <div class="card-ad col-span-2">
        <div class="card-ad__header">
          <span class="card-ad__title">近 {{ trendMonths }} 个月筛查量与高风险占比</span>
          <span class="text-xs text-hint">按检查日期真实聚合 · {{ MODALITY_LABELS[filterModality] }}</span>
        </div>
        <div class="p-4">
          <ChartBase :option="trendOption" :height="300" :empty="trend.length === 0" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">年龄段分布</span>
        </div>
        <div class="p-4">
          <ChartBase :option="ageOption" :height="300" :empty="!(demo?.ageGroups ?? []).some((g) => g.value > 0)" />
        </div>
      </div>
    </div>

    <!-- ==================== 评分直方图 + 性别分布 ==================== -->
    <div class="grid grid-cols-3 gap-4 anim-fade-up" style="--anim-delay: 320ms">
      <div class="card-ad col-span-2">
        <div class="card-ad__header">
          <span class="card-ad__title">AI 风险评分直方图</span>
          <span class="text-xs text-hint">已出分 {{ histogram?.total ?? 0 }} 例</span>
        </div>
        <div class="p-4">
          <ChartBase :option="scoreOption" :height="260" :empty="!(histogram?.histogram ?? []).some((b) => b.count > 0)" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">性别分布</span>
        </div>
        <div class="p-4">
          <ChartBase :option="genderOption" :height="260" :empty="!(demo?.genderDist ?? []).some((g) => g.value > 0)" />
        </div>
      </div>
    </div>

    <!-- ==================== 质量指标行 ==================== -->
    <div class="grid grid-cols-3 gap-4 anim-fade-up" style="--anim-delay: 400ms">
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">AI 分级医生认可率</span>
          <span class="text-xs text-hint">按风险等级</span>
        </div>
        <div class="p-4">
          <ChartBase :option="agreementOption" :height="240" :empty="(agreement?.byLevel ?? []).length === 0" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">随访认知变化分布</span>
        </div>
        <div class="p-4">
          <ChartBase :option="cognitionOption" :height="240" :empty="(fuStats?.cognitionDist ?? []).every((d) => d.value === 0)" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">用药依从性分布</span>
        </div>
        <div class="p-4">
          <ChartBase :option="adherenceOption" :height="240" :empty="(fuStats?.adherenceDist ?? []).every((d) => d.value === 0)" />
        </div>
      </div>
    </div>

    <!-- ==================== 随访质量概览 + 方式分布 ==================== -->
    <div class="grid grid-cols-3 gap-4 anim-fade-up" style="--anim-delay: 480ms">
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">随访质量概览</span>
        </div>
        <div class="p-5 flex flex-col gap-4">
          <div class="rounded-card bg-page border border-line px-4 py-3.5 flex items-center justify-between">
            <div>
              <div class="text-[12px] text-sub">计划 / 记录 / 覆盖病例</div>
              <div class="mt-0.5 text-[11px] text-hint">一病例一计划，可多次随访</div>
            </div>
            <div class="font-num text-[20px] leading-none font-semibold text-primary">
              {{ fuStats?.planCount ?? 0 }}
              <span class="text-[12px] text-hint font-sans font-normal">/ {{ fuStats?.visitCount ?? 0 }} / {{ fuStats?.coveredCases ?? 0 }}</span>
            </div>
          </div>
          <div class="rounded-card bg-page border border-line px-4 py-3.5 flex items-center justify-between">
            <div>
              <div class="text-[12px] text-sub">平均 MMSE / MoCA</div>
              <div class="mt-0.5 text-[11px] text-hint">满分 30 分，随访复评均值</div>
            </div>
            <div class="font-num text-[20px] leading-none font-semibold text-[#2E9E6B]">
              {{ fuStats?.avgMmse ?? '—' }}
              <span class="text-[12px] text-hint font-sans font-normal">/ {{ fuStats?.avgMoca ?? '—' }}</span>
            </div>
          </div>
          <div
            class="rounded-card bg-page border border-line px-4 py-3.5 flex items-center justify-between cursor-pointer hover:border-primary/60 transition-colors"
            @click="$router.push('/followup')"
          >
            <div>
              <div class="text-[12px] text-sub">随访管理</div>
              <div class="mt-0.5 text-[11px] text-hint">查看计划与随访记录明细</div>
            </div>
            <el-icon class="text-hint"><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
      <div class="card-ad col-span-2">
        <div class="card-ad__header">
          <span class="card-ad__title">随访方式分布</span>
        </div>
        <div class="p-4">
          <ChartBase :option="visitTypeOption" :height="240" :empty="(fuStats?.visitTypeDist ?? []).length === 0" />
        </div>
      </div>
    </div>

    <!-- ==================== 风险因子关联分析 ==================== -->
    <div class="grid grid-cols-2 gap-4 anim-fade-up mt-4" style="--anim-delay: 560ms">
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">风险因子相关矩阵</span>
          <span class="text-xs text-hint">皮尔逊相关系数 · {{ correlation?.sampleCount ?? 0 }} 例样本</span>
        </div>
        <div class="p-4">
          <ChartBase :option="corrHeatmapOption" :height="280" :empty="!correlation" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">年龄 vs AI 风险评分</span>
          <span v-if="correlation" class="text-xs text-hint">R² = {{ correlation.scatter.ageRisk.regression.r2 }}</span>
        </div>
        <div class="p-4">
          <ChartBase :option="ageRiskScatterOption" :height="280" :empty="(correlation?.scatter.ageRisk.points.length ?? 0) === 0" />
        </div>
      </div>
    </div>

    <!-- ==================== 散点回归 + 箱线图 ==================== -->
    <div class="grid grid-cols-3 gap-4 anim-fade-up mt-4" style="--anim-delay: 640ms">
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">MMSE vs AI 风险评分</span>
        </div>
        <div class="p-4">
          <ChartBase :option="mmseRiskScatterOption" :height="260" :empty="(correlation?.scatter.mmseRisk.points.length ?? 0) === 0" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">性别 × AI 风险评分</span>
        </div>
        <div class="p-4">
          <ChartBase :option="genderBoxOption" :height="260" :empty="(correlation?.boxplots.genderRisk.length ?? 0) === 0" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">风险等级 × MMSE</span>
        </div>
        <div class="p-4">
          <ChartBase :option="levelMmseBoxOption" :height="260" :empty="(correlation?.boxplots.levelMmse.length ?? 0) === 0" />
        </div>
      </div>
    </div>

    <!-- ==================== 临床路径流转桑基图 ==================== -->
    <div class="card-ad anim-fade-up mt-4" style="--anim-delay: 720ms">
      <div class="card-ad__header">
        <span class="card-ad__title">临床路径流转分布</span>
        <span class="text-xs text-hint">全队列 {{ pathway?.totalCases ?? 0 }} 例 · 建档 → 待阅片 → AI 分析 → 报告 → 随访 → 干预</span>
      </div>
      <div class="p-4">
        <ChartBase :option="pathwaySankeyOption" :height="320" :empty="!pathway || pathway.links.length === 0" />
      </div>
      <!-- 瓶颈分析表 -->
      <div v-if="pathway && pathway.bottlenecks.length > 0" class="px-4 pb-4">
        <div class="text-[13px] font-medium text-ink mb-2">阶段瓶颈分析</div>
        <div class="flex flex-wrap gap-2">
          <el-tag v-for="b in pathway.bottlenecks" :key="b.stage" :type="b.pct > 50 ? 'danger' : b.pct > 20 ? 'warning' : 'info'" effect="light" round>
            {{ b.stage }}：{{ b.count }} 例（{{ b.pct }}%）
          </el-tag>
        </div>
      </div>
    </div>

    <!-- ==================== AI 模型版本对比 ==================== -->
    <div class="card-ad mt-4">
      <div class="card-ad__header">
        <span class="card-ad__title">AI 模型版本对比</span>
        <span v-if="modelComparison" class="text-[11px] text-hint">
          共 {{ modelComparison.totalVersions }} 个版本 · {{ modelComparison.totalCases }} 例病例
        </span>
      </div>

      <div v-if="modelComparison" class="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4">
        <div>
          <div class="text-[13px] font-medium text-ink mb-2">核心指标对比</div>
          <ChartBase :option="modelCompareOption" :height="320" />
        </div>
        <div>
          <div class="text-[13px] font-medium text-ink mb-2">评分分布对比</div>
          <ChartBase :option="modelScoreDistOption" :height="320" />
        </div>
        <div class="lg:col-span-2">
          <div class="text-[13px] font-medium text-ink mb-2">风险等级分布</div>
          <ChartBase :option="modelLevelDistOption" :height="280" />
        </div>
      </div>

      <!-- 模型指标对比表 -->
      <div v-if="modelComparison && modelComparison.models.length > 0" class="px-4 pb-4">
        <div class="text-[13px] font-medium text-ink mb-2">模型指标明细</div>
        <el-table :data="modelComparison.models" size="small" border>
          <el-table-column label="模型版本" prop="modelVersion" width="180" />
          <el-table-column label="样本量" prop="sampleCount" width="80" />
          <el-table-column label="评分均值" prop="avgScore" width="90" />
          <el-table-column label="高风险占比(%)" prop="highRiskRate" width="120" />
          <el-table-column label="通过率(%)" prop="approvalRate" width="100" />
          <el-table-column label="驳回率(%)" prop="rejectionRate" width="100" />
          <el-table-column label="待审核" prop="pendingCount" width="80" />
          <el-table-column label="等级分布(低/MCI/早/中晚)">
            <template #default="{ row }">
              <span class="font-num text-[12px]">
                {{ row.levelDist.low }}/{{ row.levelDist.mci }}/{{ row.levelDist['ad-early'] }}/{{ row.levelDist['ad-late'] }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-empty v-else description="暂无模型版本对比数据" :image-size="60" />
    </div>

    <!-- ==================== 多中心队列对比分析 ==================== -->
    <div class="card-ad anim-fade-up mt-4" style="--anim-delay: 800ms">
      <div class="card-ad__header">
        <span class="card-ad__title">多中心队列对比分析</span>
        <span v-if="cohortComparison" class="text-[11px] text-hint">
          共 {{ cohortComparison.groups.length }} 个队列 · {{ cohortComparison.totalCases }} 例病例
        </span>
      </div>

      <div v-if="cohortComparison && cohortComparison.groups.length > 0" class="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4">
        <div>
          <div class="text-[13px] font-medium text-ink mb-2">各队列风险等级分布</div>
          <ChartBase :option="cohortLevelStackOption" :height="320" />
        </div>
        <div>
          <div class="text-[13px] font-medium text-ink mb-2">各队列评分分布对比</div>
          <ChartBase :option="cohortScoreBoxOption" :height="320" />
        </div>
        <div class="lg:col-span-2">
          <div class="text-[13px] font-medium text-ink mb-2">AD 检出率对比（捷径学习诊断指标）</div>
          <ChartBase :option="cohortAdRateOption" :height="260" />
        </div>
      </div>

      <!-- 队列指标对比表 -->
      <div v-if="cohortComparison && cohortComparison.groups.length > 0" class="px-4 pb-4">
        <div class="text-[13px] font-medium text-ink mb-2">队列指标明细</div>
        <el-table :data="cohortComparison.groups" size="small" border>
          <el-table-column label="队列" prop="cohort" width="120" />
          <el-table-column label="样本量" prop="sampleCount" width="80" />
          <el-table-column label="评分均值" prop="avgScore" width="90" />
          <el-table-column label="最低分" prop="minScore" width="80" />
          <el-table-column label="最高分" prop="maxScore" width="80" />
          <el-table-column label="高风险占比(%)" prop="highRiskRate" width="120" />
          <el-table-column label="AD 检出率(%)" prop="adRate" width="110" />
          <el-table-column label="等级分布(低/MCI/早/中晚)">
            <template #default="{ row }">
              <span class="font-num text-[12px]">
                {{ row.levelDist.low }}/{{ row.levelDist.mci }}/{{ row.levelDist.adEarly }}/{{ row.levelDist.adLate }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 显著性检验结果 -->
      <div v-if="cohortComparison && cohortComparison.significance.length > 0" class="px-4 pb-4">
        <div class="text-[13px] font-medium text-ink mb-2">队列间 AD 检出率差异显著性检验（Fisher exact test）</div>
        <el-table :data="cohortComparison.significance" size="small" border>
          <el-table-column label="队列 A" prop="cohortA" width="120" />
          <el-table-column label="队列 B" prop="cohortB" width="120" />
          <el-table-column label="队列 A AD/总数">
            <template #default="{ row }">
              <span class="font-num text-[12px]">{{ row.adA }}/{{ row.totalA }}</span>
            </template>
          </el-table-column>
          <el-table-column label="队列 B AD/总数">
            <template #default="{ row }">
              <span class="font-num text-[12px]">{{ row.adB }}/{{ row.totalB }}</span>
            </template>
          </el-table-column>
          <el-table-column label="p 值">
            <template #default="{ row }">
              <span v-if="row.pValue === null" class="text-hint">未检验</span>
              <span v-else-if="row.pValue < 0.05" class="text-[#C94F4F] font-medium">{{ row.pValue }}</span>
              <span v-else class="text-sub">{{ row.pValue }}</span>
            </template>
          </el-table-column>
          <el-table-column label="显著性">
            <template #default="{ row }">
              <el-tag v-if="row.pValue === null" type="info" size="small" effect="light">未检验</el-tag>
              <el-tag v-else-if="row.pValue < 0.01" type="danger" size="small" effect="light">极显著</el-tag>
              <el-tag v-else-if="row.pValue < 0.05" type="warning" size="small" effect="light">显著</el-tag>
              <el-tag v-else type="info" size="small" effect="light">不显著</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-empty v-else-if="cohortComparison" description="暂无队列对比数据" :image-size="60" />
    </div>
  </div>
</template>
