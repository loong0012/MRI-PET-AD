<script setup lang="ts">
/**
 * 预后预测与认知衰退轨迹面板
 * ------------------------------------------------------------------
 * 独立组件，由主代理集成到 PatientProfileView.vue。
 * 基于 FollowUpVisit 纵向 MMSE 数据 + AI 风险评分，展示：
 * 1. 历史实线 + 预测虚线的认知衰退轨迹折线图
 * 2. MMSE=24 的 AD 诊断阈值红色参考线
 * 3. 风险分层卡片（level 标签 + 因素列表）
 * 4. 预测摘要（衰退速率 / 36 月预测 / 转 AD 时间窗）
 * 数据不足 2 次随访时显示 el-empty 兜底。
 */
import { computed, ref, watch } from 'vue'
import type { EChartsOption } from 'echarts'
import { apiPrognosis } from '@/api/prognosis'
import type { PrognosisResult, PrognosisLevel } from '@/types/prognosis'
import ChartBase from '@/components/ChartBase.vue'

const props = defineProps<{
  /** 患者编号 */
  patientNo: string
}>()

const loading = ref(false)
const result = ref<PrognosisResult | null>(null)

// ---------- 数据加载 ----------
async function fetchPrognosis(): Promise<void> {
  if (!props.patientNo) return
  loading.value = true
  try {
    result.value = await apiPrognosis(props.patientNo)
  } finally {
    loading.value = false
  }
}

watch(() => props.patientNo, fetchPrognosis, { immediate: true })

// ---------- 数据派生 ----------
/** 是否有可用预测（数据点 ≥2） */
const hasPrediction = computed(() => result.value?.prediction != null)

/** 风险等级配色 */
const LEVEL_STYLE: Record<PrognosisLevel, { color: string; bg: string; label: string }> = {
  high: { color: '#C94F4F', bg: '#FAEDED', label: '高风险' },
  medium: { color: '#D99A2B', bg: '#FBF3E5', label: '中风险' },
  low: { color: '#2E9E6B', bg: '#E8F5EF', label: '低风险' },
}

/** 置信度中文 */
const CONFIDENCE_TEXT: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

// ---------- ECharts 配置 ----------
const chartOption = computed<EChartsOption>(() => {
  // historical 后端可能缺字段，两级可选链兜底，避免整个图表 computed 崩溃
  const hist = result.value?.historical?.mmse ?? []
  const pred = result.value?.prediction
  if (!pred) return {}

  const histDates = hist.map((p) => p.date)
  const fcDates = pred.forecast.map((p) => p.date)
  // 拼接：历史日期 + 预测日期（预测起点用最后一个历史日期桥接）
  const allDates = [...histDates, ...fcDates]

  // 历史序列：历史位置填值，预测位置填 null
  const histData: (number | null)[] = hist.map((p) => p.mmse)
  while (histData.length < allDates.length) histData.push(null)

  // 预测序列：历史位置填 null，最后一个历史位置填桥接值，预测位置填预测值
  const fcData: (number | null)[] = new Array(allDates.length).fill(null)
  // 桥接点：让预测虚线从最后一个历史点开始
  const bridgeIdx = histDates.length - 1
  if (bridgeIdx >= 0) {
    fcData[bridgeIdx] = hist[bridgeIdx]?.mmse ?? null
  }
  pred.forecast.forEach((p, i) => {
    fcData[histDates.length + i] = p.mmse
  })

  return {
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '—' : `${v} 分`),
    },
    legend: {
      data: ['历史 MMSE', '预测 MMSE'],
      right: 10,
      top: 4,
      textStyle: { color: '#5A6577', fontSize: 12 },
    },
    grid: { left: 44, right: 20, top: 38, bottom: 34 },
    xAxis: {
      type: 'category',
      data: allDates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#C8D1DC' } },
      axisLabel: { color: '#7A8699', fontSize: 10, rotate: allDates.length > 6 ? 30 : 0 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 30,
      splitLine: { lineStyle: { color: '#EDF1F5' } },
      axisLabel: { color: '#7A8699', fontSize: 11 },
      name: 'MMSE',
      nameTextStyle: { color: '#7A8699', fontSize: 11 },
    },
    series: [
      {
        name: '历史 MMSE',
        type: 'line',
        data: histData,
        connectNulls: false,
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        lineStyle: { width: 2.5, color: '#2F6DA3' },
        itemStyle: { color: '#2F6DA3' },
      },
      {
        name: '预测 MMSE',
        type: 'line',
        data: fcData,
        connectNulls: true,
        smooth: true,
        symbol: 'diamond',
        symbolSize: 8,
        lineStyle: { width: 2.5, color: '#D99A2B', type: 'dashed' },
        itemStyle: { color: '#D99A2B' },
        // 置信带：预测区域用浅色面积
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(217,154,43,0.18)' },
              { offset: 1, color: 'rgba(217,154,43,0.02)' },
            ],
          },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            {
              yAxis: 24,
              lineStyle: { color: '#C94F4F', type: 'dashed', width: 1.5 },
              label: { formatter: 'AD 阈值 24 分', color: '#C94F4F', fontSize: 10, position: 'insideEndTop' },
            },
          ],
        },
      },
    ],
  }
})
</script>

<template>
  <div class="space-y-4">
    <!-- ============ 衰退轨迹折线图 ============ -->
    <div class="card-ad">
      <div class="card-ad__header">
        <span class="card-ad__title">认知衰退轨迹预测</span>
        <span class="text-[12px] text-hint">基于纵向 MMSE 线性回归 · 预测 12/24/36 月</span>
      </div>
      <div class="px-4 pb-2">
        <div v-if="loading" class="h-[300px] flex items-center justify-center text-[13px] text-hint">
          加载中...
        </div>
        <ChartBase v-else-if="hasPrediction" :option="chartOption" :height="300" />
        <div v-else class="h-[300px] flex items-center justify-center">
          <el-empty :image-size="80" description="随访数据不足 2 次，暂无法预测认知衰退轨迹" />
        </div>
      </div>
    </div>

    <!-- ============ 预测摘要 + 风险分层 ============ -->
    <div v-if="hasPrediction && result" class="grid gap-4" style="grid-template-columns: 1.3fr 0.7fr">
      <!-- 预测摘要 -->
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">预测摘要</span>
          <span class="text-[12px] text-hint">
            置信度：{{ CONFIDENCE_TEXT[result.prediction!.confidenceLevel] ?? '—' }}
            （{{ result.prediction!.dataPoints }} 个数据点）
          </span>
        </div>
        <div class="grid grid-cols-3">
          <!-- 年衰退速率 -->
          <div class="px-5 py-4 border-r border-line">
            <div class="text-[12px] text-hint">年衰退速率</div>
            <div
              class="mt-1 font-num text-[22px] font-semibold"
              :style="{ color: result.prediction!.declineRate <= -3 ? '#C94F4F' : result.prediction!.declineRate <= -1 ? '#D99A2B' : '#2E9E6B' }"
            >
              {{ result.prediction!.declineRate > 0 ? '+' : '' }}{{ result.prediction!.declineRate }}
              <span class="text-[12px] text-hint font-sans ml-1">分/年</span>
            </div>
            <div class="mt-1 text-[11px] text-hint">R² = {{ result.prediction!.r2 }}</div>
          </div>
          <!-- 36 月预测值 -->
          <div class="px-5 py-4 border-r border-line">
            <div class="text-[12px] text-hint">36 月预测 MMSE</div>
            <div
              class="mt-1 font-num text-[22px] font-semibold"
              :style="{ color: (result.prediction!.forecast[2]?.mmse ?? 30) < 24 ? '#C94F4F' : '#2F6DA3' }"
            >
              {{ result.prediction!.forecast[2]?.mmse ?? '—' }}
              <span class="text-[12px] text-hint font-sans ml-1">分</span>
            </div>
            <div class="mt-1 text-[11px] text-hint">{{ result.prediction!.forecast[2]?.date ?? '' }}</div>
          </div>
          <!-- 转 AD 时间窗 -->
          <div class="px-5 py-4">
            <div class="text-[12px] text-hint">预计转 AD 时间窗</div>
            <div v-if="result.prediction!.adConversionWindow" class="mt-1 font-num text-[22px] font-semibold text-[#C94F4F]">
              {{ result.prediction!.adConversionWindow.months }}
              <span class="text-[12px] text-hint font-sans ml-1">月</span>
            </div>
            <div v-else class="mt-1 font-num text-[22px] font-semibold text-[#2E9E6B]">—</div>
            <div class="mt-1 text-[11px] text-hint">
              {{ result.prediction!.adConversionWindow ? `预计 ${result.prediction!.adConversionWindow.date}` : '趋势平稳或改善' }}
            </div>
          </div>
        </div>
      </div>

      <!-- 风险分层 -->
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">风险分层</span>
        </div>
        <div class="px-5 py-4">
          <div
            v-if="result.riskStratification"
            class="rounded-card px-4 py-3"
            :style="{
              background: LEVEL_STYLE[result.riskStratification.level].bg,
              color: LEVEL_STYLE[result.riskStratification.level].color,
            }"
          >
            <div class="flex items-center justify-between">
              <span class="text-[13px] font-medium">综合风险等级</span>
              <span class="text-[18px] font-semibold">{{ LEVEL_STYLE[result.riskStratification.level].label }}</span>
            </div>
          </div>
          <div class="mt-3 space-y-1.5">
            <div v-for="(f, i) in result.riskStratification?.factors ?? []" :key="i" class="flex items-start gap-1.5 text-[12px] text-ink-secondary">
              <span class="mt-0.5 inline-block w-1 h-1 rounded-full bg-[#7A8699] shrink-0" />
              <span>{{ f }}</span>
            </div>
            <div v-if="!(result.riskStratification?.factors?.length)" class="text-[12px] text-hint">暂无风险因素</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
