<script setup lang="ts">
/**
 * 模型性能监控视图（researcher / admin）
 * ------------------------------------------------------------------
 * 跟踪生产环境中 AI 模型的实际表现：
 * 1. 顶部：时间窗口选择器（7 / 30 / 90 天）+ 漂移状态指示灯
 * 2. 风险分数分布直方图（柱）+ 累积分布曲线（折线）
 * 3. 混淆矩阵热力图（2×2）+ 5 项核心性能指标
 * 4. 数据漂移对比图（当前窗口 vs 历史基线，分组柱状图）
 * 5. 性能指标雷达图（accuracy / sensitivity / specificity / f1 / precision）
 * 6. 告警卡片列表（el-alert 按 level 区分颜色）
 *
 * 设计约束：
 * - 医疗低饱和蓝灰色调色板：['#5B8AB8', '#7BA7C7', '#A3C4D9', '#C9DCE8']
 * - ChartBase 组件统一封装 ECharts
 * - 分模块容错加载：单个 API 失败不影响整体页面
 * - 全中文注释，TypeScript 严格模式禁止 any
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  apiGetScoreDistribution,
  apiGetConfusionMatrix,
  apiGetDriftDetection,
  apiGetAlerts
} from '@/api/modelMonitor'
import type {
  ScoreDistribution,
  ConfusionMatrixResult,
  DriftDetectionResult,
  AlertsResult,
  ModelAlert,
  DriftLevel
} from '@/types/modelMonitor'
import ChartBase from '@/components/ChartBase.vue'
import type { EChartsOption } from 'echarts'

// 医疗低饱和蓝灰色调色板（统一全图配色）
const PALETTE: string[] = ['#5B8AB8', '#7BA7C7', '#A3C4D9', '#C9DCE8']

// ---------- 顶部时间窗口选择 ----------
const windowDays = ref<7 | 30 | 90>(30)
const loading = ref(true)
const generatedAt = ref('')

// ---------- 各模块数据（容错：单模块失败置 null） ----------
const scoreDist = ref<ScoreDistribution | null>(null)
const confusion = ref<ConfusionMatrixResult | null>(null)
const drift = ref<DriftDetectionResult | null>(null)
const alerts = ref<AlertsResult | null>(null)
const failedModules = ref<string[]>([])

/** 并行加载所有数据模块，单模块失败不拖垮整页 */
async function loadAll(): Promise<void> {
  loading.value = true
  failedModules.value = []
  const params = { days: windowDays.value }
  const [s, c, d, a] = await Promise.allSettled([
    apiGetScoreDistribution(params),
    apiGetConfusionMatrix(),
    apiGetDriftDetection(params),
    apiGetAlerts()
  ])
  scoreDist.value = s.status === 'fulfilled' ? s.value : null
  confusion.value = c.status === 'fulfilled' ? c.value : null
  drift.value = d.status === 'fulfilled' ? d.value : null
  alerts.value = a.status === 'fulfilled' ? a.value : null

  const fails: string[] = []
  if (s.status === 'rejected') fails.push('分数分布')
  if (c.status === 'rejected') fails.push('混淆矩阵')
  if (d.status === 'rejected') fails.push('漂移检测')
  if (a.status === 'rejected') fails.push('性能告警')
  failedModules.value = fails
  generatedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
  loading.value = false
}

watch(windowDays, () => { void loadAll() })
onMounted(() => { void loadAll() })

// ---------- 漂移状态指示灯 ----------
const driftBadge = computed<{ color: string; bg: string; label: string; icon: string }>(() => {
  const level: DriftLevel = drift.value?.driftLevel ?? 'green'
  if (level === 'green') {
    return { color: '#2E9E6B', bg: '#EDF7F2', label: '无漂移', icon: 'CircleCheckFilled' }
  }
  if (level === 'yellow') {
    return { color: '#D99A2B', bg: '#FBF4E6', label: '轻微漂移', icon: 'WarningFilled' }
  }
  return { color: '#C94F4F', bg: '#FAEDED', label: '显著漂移', icon: 'CircleCloseFilled' }
})

// ==================== ECharts 配置 ====================

// ---------- 风险分数分布直方图（柱）+ 累积分布曲线（折线） ----------
const scoreDistOption = computed<EChartsOption>(() => {
  const buckets = scoreDist.value?.buckets ?? []
  const counts = scoreDist.value?.counts ?? []
  const cumulative = scoreDist.value?.cumulative ?? []
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' }
    },
    legend: { top: 0, textStyle: { color: '#5C6B7A', fontSize: 12 } },
    grid: { left: 8, right: 8, top: 36, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: buckets,
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11, interval: 0, rotate: 30 }
    },
    yAxis: [
      {
        type: 'value',
        name: '病例数',
        splitLine: { lineStyle: { color: '#EFF3F7' } },
        axisLabel: { color: '#93A1AF' }
      },
      {
        type: 'value',
        name: '累积 %',
        min: 0,
        max: 100,
        splitLine: { show: false },
        axisLabel: { color: '#93A1AF', formatter: '{value}%' }
      }
    ],
    series: [
      {
        name: '病例数',
        type: 'bar',
        data: counts,
        barWidth: 22,
        itemStyle: {
          color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: PALETTE[1] }, { offset: 1, color: PALETTE[0] }] },
          borderRadius: [4, 4, 0, 0]
        }
      },
      {
        name: '累积分布',
        type: 'line',
        yAxisIndex: 1,
        data: cumulative,
        smooth: true,
        symbolSize: 7,
        lineStyle: { color: '#C94F4F', width: 2.5 },
        itemStyle: { color: '#C94F4F' }
      }
    ]
  }
})

// ---------- 混淆矩阵热力图（2×2） ----------
const confusionMatrixOption = computed<EChartsOption>(() => {
  const m = confusion.value?.matrix ?? [[0, 0], [0, 0]]
  // 行：实际为 AD（TP/FN） / 实际为非 AD（FP/TN）；列：预测 AD / 预测非 AD
  // 后端返回 matrix = [[TP, FN], [FP, TN]]
  // 热力图坐标 (x=列, y=行)：(0,0)=TP (1,0)=FN (0,1)=FP (1,1)=TN
  const data: [number, number, number][] = [
    [0, 0, m[0][0]],  // TP
    [1, 0, m[0][1]],  // FN
    [0, 1, m[1][0]],  // FP
    [1, 1, m[1][1]]   // TN
  ]
  const labels = ['预测 AD', '预测非 AD']
  const yLabels = ['实际 AD', '实际非 AD']
  const cellLabels = ['TP 正确识别', 'FN 漏诊', 'FP 误诊', 'TN 正确排除']
  // 求最大值用于色阶
  const maxVal = Math.max(1, ...data.map((d) => d[2]))
  return {
    tooltip: {
      position: 'top',
      formatter: (p: unknown) => {
        const param = p as { value: [number, number, number]; dataIndex: number }
        const [x, y, v] = param.value
        return `${yLabels[y]} × ${labels[x]}<br/>${cellLabels[param.dataIndex]}：${v} 例`
      }
    },
    grid: { left: 70, right: 30, top: 30, bottom: 70 },
    xAxis: {
      type: 'category',
      data: labels,
      position: 'bottom',
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 12, interval: 0 },
      splitArea: { show: true }
    },
    yAxis: {
      type: 'category',
      data: yLabels,
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 12, interval: 0 },
      splitArea: { show: true }
    },
    visualMap: {
      min: 0,
      max: maxVal,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 8,
      textStyle: { color: '#5C6B7A', fontSize: 10 },
      inRange: { color: [PALETTE[3], PALETTE[1], PALETTE[0]] }
    },
    series: [{
      type: 'heatmap',
      data,
      label: {
        show: true,
        color: '#3A4A5C',
        fontSize: 14,
        formatter: (p: unknown) => {
          const param = p as { value: [number, number, number]; dataIndex: number }
          return `${cellLabels[param.dataIndex]}\n${param.value[2]}`
        }
      },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.25)' } }
    }]
  }
})

// ---------- 数据漂移对比图（当前 vs 基线，分组柱状图） ----------
const driftCompareOption = computed<EChartsOption>(() => {
  const buckets = drift.value?.buckets ?? []
  const curr = drift.value?.currentCounts ?? []
  const base = drift.value?.baselineCounts ?? []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['当前窗口', '历史基线'], top: 0 },
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: buckets,
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11, interval: 0, rotate: 30 }
    },
    yAxis: {
      type: 'value',
      name: '病例数',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    series: [
      {
        name: '当前窗口',
        type: 'bar',
        data: curr,
        barWidth: 14,
        itemStyle: { color: PALETTE[0], borderRadius: [3, 3, 0, 0] }
      },
      {
        name: '历史基线',
        type: 'bar',
        data: base,
        barWidth: 14,
        itemStyle: { color: PALETTE[2], borderRadius: [3, 3, 0, 0] }
      }
    ]
  }
})

// ---------- 性能指标雷达图 ----------
const radarOption = computed<EChartsOption>(() => {
  const m = confusion.value?.metrics
  const indicators = [
    { name: '准确率', max: 100 },
    { name: '灵敏度', max: 100 },
    { name: '特异度', max: 100 },
    { name: 'F1', max: 100 },
    { name: '精确率', max: 100 }
  ]
  const values = m ? [m.accuracy, m.sensitivity, m.specificity, m.f1, m.precision] : [0, 0, 0, 0, 0]
  return {
    tooltip: { trigger: 'item' },
    radar: {
      indicator: indicators,
      radius: '65%',
      axisName: { color: '#5C6B7A', fontSize: 12 },
      splitArea: {
        areaStyle: { color: ['#F4F6F9', '#E8EFF5'] }
      },
      splitLine: { lineStyle: { color: '#E3E9EF' } },
      axisLine: { lineStyle: { color: '#E3E9EF' } }
    },
    series: [{
      type: 'radar',
      data: [{
        value: values,
        name: '性能指标',
        areaStyle: { color: 'rgba(91, 138, 184, 0.25)' },
        lineStyle: { color: PALETTE[0], width: 2 },
        itemStyle: { color: PALETTE[0] }
      }]
    }]
  }
})

// ---------- 告警等级映射（el-alert type） ----------
const ALERT_TYPE_MAP: Record<string, 'error' | 'warning' | 'info'> = {
  high: 'error',
  medium: 'warning',
  low: 'info'
}
const ALERT_LABEL_MAP: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低'
}
const ALERT_TYPE_LABEL_MAP: Record<string, string> = {
  drift: '数据漂移',
  performance: '性能下降',
  sampleVolume: '样本量'
}

/** 排序：high → medium → low */
function sortAlerts(list: ModelAlert[]): ModelAlert[] {
  const order: Record<string, number> = { high: 0, medium: 1, low: 2 }
  return [...list].sort((a, b) => (order[a.level] ?? 3) - (order[b.level] ?? 3))
}
</script>

<template>
  <div class="page-wrap" v-loading="loading">
    <!-- ==================== 顶部：时间窗口 + 漂移指示灯 ==================== -->
    <div class="card-ad p-4 flex items-center gap-3 flex-wrap">
      <span class="text-[13px] text-sub">时间窗口：</span>
      <el-radio-group v-model="windowDays">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
        <el-radio-button :value="90">近 90 天</el-radio-button>
      </el-radio-group>

      <el-divider direction="vertical" class="!h-6" />

      <div
        class="flex items-center gap-2 px-3 py-1.5 rounded-lg"
        :style="{ background: driftBadge.bg, color: driftBadge.color }"
      >
        <el-icon :size="16"><component :is="driftBadge.icon" /></el-icon>
        <span class="text-[13px] font-medium">漂移状态：{{ driftBadge.label }}</span>
        <span v-if="drift" class="text-[12px] opacity-80">PSI={{ drift.psi }} · KS={{ drift.ksStatistic }}</span>
      </div>

      <div class="flex-1" />
      <span v-if="generatedAt" class="text-xs text-hint">数据生成于 {{ generatedAt }}</span>
      <el-button :icon="'Refresh'" @click="loadAll">刷新</el-button>
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

    <!-- ==================== 概览卡：核心指标速览 ==================== -->
    <div class="grid grid-cols-4 gap-4 mt-4">
      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 0ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" :style="{ background: PALETTE[0] }" />
        <div class="flex items-start justify-between relative">
          <div>
            <div class="text-[13px] text-sub">已审核样本</div>
            <div class="mt-2 font-num text-[30px] leading-none font-semibold text-ink">
              {{ confusion?.totalReviewed ?? 0 }}
              <span class="text-[12px] text-hint font-sans font-normal ml-1">例</span>
            </div>
          </div>
          <div
            class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm"
            :style="{ background: '#E8F1F8', color: PALETTE[0] }"
          >
            <el-icon :size="20"><DataAnalysis /></el-icon>
          </div>
        </div>
        <div class="mt-3 text-xs text-hint">待审核 {{ confusion?.pendingCount ?? 0 }} 例</div>
      </div>

      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 70ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" :style="{ background: PALETTE[0] }" />
        <div class="flex items-start justify-between relative">
          <div>
            <div class="text-[13px] text-sub">模型准确率</div>
            <div class="mt-2 font-num text-[30px] leading-none font-semibold text-ink">
              {{ confusion?.metrics.accuracy ?? 0 }}
              <span class="text-[12px] text-hint font-sans font-normal ml-1">%</span>
            </div>
          </div>
          <div
            class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm"
            :style="{ background: '#EDF7F2', color: '#2E9E6B' }"
          >
            <el-icon :size="20"><CircleCheck /></el-icon>
          </div>
        </div>
        <div class="mt-3 text-xs text-hint">(TP+TN) / 已审核总数</div>
      </div>

      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 140ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" :style="{ background: '#D99A2B' }" />
        <div class="flex items-start justify-between relative">
          <div>
            <div class="text-[13px] text-sub">PSI 漂移指数</div>
            <div class="mt-2 font-num text-[30px] leading-none font-semibold text-ink">
              {{ drift?.psi ?? 0 }}
            </div>
          </div>
          <div
            class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm"
            :style="{ background: driftBadge.bg, color: driftBadge.color }"
          >
            <el-icon :size="20"><component :is="driftBadge.icon" /></el-icon>
          </div>
        </div>
        <div class="mt-3 text-xs text-hint">&lt;0.1 无漂移 · 0.1-0.25 轻微 · &gt;0.25 显著</div>
      </div>

      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 210ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" :style="{ background: '#C94F4F' }" />
        <div class="flex items-start justify-between relative">
          <div>
            <div class="text-[13px] text-sub">告警总数</div>
            <div class="mt-2 font-num text-[30px] leading-none font-semibold text-ink">
              {{ alerts?.summary.total ?? 0 }}
              <span class="text-[12px] text-hint font-sans font-normal ml-1">条</span>
            </div>
          </div>
          <div
            class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm"
            :style="{ background: '#FAEDED', color: '#C94F4F' }"
          >
            <el-icon :size="20"><WarningFilled /></el-icon>
          </div>
        </div>
        <div class="mt-3 text-xs text-hint">
          高 {{ alerts?.summary.high ?? 0 }} · 中 {{ alerts?.summary.medium ?? 0 }} · 低 {{ alerts?.summary.low ?? 0 }}
        </div>
      </div>
    </div>

    <!-- ==================== 分数分布 + 混淆矩阵 ==================== -->
    <div class="grid grid-cols-2 gap-4 mt-4 anim-fade-up" style="--anim-delay: 280ms">
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">风险分数分布</span>
          <span class="text-xs text-hint">
            近 {{ scoreDist?.days ?? windowDays }} 天 · {{ scoreDist?.totalCases ?? 0 }} 例 · 均值 {{ scoreDist?.avgScore ?? 0 }} ± {{ scoreDist?.stdScore ?? 0 }}
          </span>
        </div>
        <div class="p-4">
          <ChartBase
            :option="scoreDistOption"
            :height="300"
            :empty="(scoreDist?.counts ?? []).every((c) => c === 0)"
          />
        </div>
      </div>

      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">混淆矩阵</span>
          <span class="text-xs text-hint">基于 review_status 代理真实标签 · 已审核 {{ confusion?.totalReviewed ?? 0 }} 例</span>
        </div>
        <div class="p-4">
          <ChartBase
            :option="confusionMatrixOption"
            :height="300"
            :empty="(confusion?.totalReviewed ?? 0) === 0"
          />
        </div>
      </div>
    </div>

    <!-- ==================== 漂移对比 + 性能雷达 ==================== -->
    <div class="grid grid-cols-2 gap-4 mt-4 anim-fade-up" style="--anim-delay: 360ms">
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">数据漂移对比</span>
          <span class="text-xs text-hint">
            当前 {{ drift?.currentTotal ?? 0 }} 例 vs 基线 {{ drift?.baselineTotal ?? 0 }} 例 · KS={{ drift?.ksStatistic ?? 0 }}
          </span>
        </div>
        <div class="p-4">
          <ChartBase
            :option="driftCompareOption"
            :height="300"
            :empty="(drift?.currentCounts ?? []).every((c) => c === 0) && (drift?.baselineCounts ?? []).every((c) => c === 0)"
          />
        </div>
      </div>

      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">性能指标雷达</span>
          <span class="text-xs text-hint">准确率 / 灵敏度 / 特异度 / F1 / 精确率</span>
        </div>
        <div class="p-4">
          <ChartBase
            :option="radarOption"
            :height="300"
            :empty="(confusion?.totalReviewed ?? 0) === 0"
          />
        </div>
      </div>
    </div>

    <!-- ==================== 性能指标明细 ==================== -->
    <div v-if="confusion" class="card-ad mt-4 anim-fade-up" style="--anim-delay: 440ms">
      <div class="card-ad__header">
        <span class="card-ad__title">性能指标明细</span>
        <span class="text-xs text-hint">单位均为 %（已审核 {{ confusion.totalReviewed }} 例，待审核 {{ confusion.pendingCount }} 例）</span>
      </div>
      <div class="p-4">
        <div class="grid grid-cols-5 gap-4">
          <div
            v-for="(item, idx) in [
              { label: '准确率', value: confusion.metrics.accuracy, hint: '(TP+TN)/总数' },
              { label: '灵敏度', value: confusion.metrics.sensitivity, hint: 'TP/(TP+FN) 漏诊率补' },
              { label: '特异度', value: confusion.metrics.specificity, hint: 'TN/(TN+FP) 误诊率补' },
              { label: 'F1 分数', value: confusion.metrics.f1, hint: '2·TP/(2·TP+FP+FN)' },
              { label: '精确率', value: confusion.metrics.precision, hint: 'TP/(TP+FP)' }
            ]"
            :key="item.label"
            class="rounded-card bg-page border border-line px-4 py-3.5"
          >
            <div class="text-[12px] text-sub">{{ item.label }}</div>
            <div class="mt-1 font-num text-[24px] leading-none font-semibold" :style="{ color: PALETTE[idx % PALETTE.length] }">
              {{ item.value }}
              <span class="text-[11px] text-hint font-sans font-normal ml-0.5">%</span>
            </div>
            <div class="mt-1.5 text-[11px] text-hint">{{ item.hint }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== 告警卡片列表 ==================== -->
    <div class="card-ad mt-4 anim-fade-up" style="--anim-delay: 520ms">
      <div class="card-ad__header">
        <span class="card-ad__title">性能告警</span>
        <span v-if="alerts" class="text-xs text-hint">
          共 {{ alerts.summary.total }} 条 · 高 {{ alerts.summary.high }} · 中 {{ alerts.summary.medium }} · 低 {{ alerts.summary.low }}
        </span>
      </div>
      <div class="p-4 space-y-3">
        <template v-if="alerts && alerts.alerts.length > 0">
          <el-alert
            v-for="(a, i) in sortAlerts(alerts.alerts)"
            :key="`${a.type}-${i}`"
            :type="ALERT_TYPE_MAP[a.level] ?? 'info'"
            :closable="false"
            show-icon
          >
            <template #title>
              <div class="flex items-center gap-2 flex-wrap">
                <el-tag size="small" :type="ALERT_TYPE_MAP[a.level] ?? 'info'" effect="dark">
                  {{ ALERT_LABEL_MAP[a.level] ?? a.level }}
                </el-tag>
                <el-tag size="small" type="info" effect="plain">{{ ALERT_TYPE_LABEL_MAP[a.type] ?? a.type }}</el-tag>
                <span class="text-[13px] text-ink">{{ a.message }}</span>
              </div>
            </template>
            <template #default>
              <div class="text-[11px] text-hint mt-1">
                触发值：{{ a.value }} · 阈值：{{ a.threshold }} · 检测时间：{{ a.detectedAt }}
              </div>
            </template>
          </el-alert>
        </template>
        <el-empty v-else description="暂无告警，模型运行正常" :image-size="60" />
      </div>
    </div>
  </div>
</template>
