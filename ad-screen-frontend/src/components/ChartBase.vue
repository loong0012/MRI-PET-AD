<script setup lang="ts">
/**
 * ECharts 通用封装组件
 * ------------------------------------------------------------------
 * 全站唯一图表挂载点，统一负责：
 * 1. 实例初始化 / 销毁 / setOption 增量更新（notMerge 保证配置完全切换）
 * 2. 监听容器尺寸变化自适应（阅片大屏 / 医用电脑分辨率切换）
 * 3. 统一医疗主题下的公共图表基调（低饱和蓝灰、清晰网格线）
 * 调用方只传入 EChartsOption，不做任何 DOM 操作
 */
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'

const props = defineProps<{
  /** ECharts 配置项（全量 option） */
  option: EChartsOption
  /** 图表高度（默认 300px，大屏可传 360/420） */
  height?: number
  /** 是否显示暂无数据兜底（数据为空时） */
  empty?: boolean
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

/** 初始化图表实例 */
function initChart(): void {
  if (!chartEl.value) return
  // 容器可能处于 display:none / 折叠面板中，此时 clientWidth=0；
  // 等 ResizeObserver 首次回调有正尺寸时再真正初始化，避免 ECharts 告警
  if (chartEl.value.clientWidth === 0 || chartEl.value.clientHeight === 0) {
    resizeObserver = new ResizeObserver(() => {
      if (chartEl.value && chartEl.value.clientWidth > 0 && chartEl.value.clientHeight > 0) {
        resizeObserver?.disconnect()
        doInit()
      }
    })
    resizeObserver.observe(chartEl.value)
    return
  }
  doInit()
}

function doInit(): void {
  if (!chartEl.value || chart) return
  chart = echarts.init(chartEl.value)
  render()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartEl.value)
}

/** 渲染 / 更新图表 */
function render(): void {
  if (!chart) return
  if (props.empty) {
    chart.clear()
    return
  }
  chart.setOption(props.option, true)
}

onMounted(initChart)
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  chart?.dispose()
  chart = null
})

watch(() => props.option, render, { deep: true })
watch(() => props.empty, render)
</script>

<template>
  <div class="relative w-full">
    <div ref="chartEl" :style="{ height: (height ?? 300) + 'px', width: '100%' }" />
    <!-- 空数据兜底：医疗统计图表必须显式区分"无数据"与"零值" -->
    <div
      v-if="empty"
      class="absolute inset-0 flex items-center justify-center text-hint text-[13px] bg-white/70"
    >
      暂无统计数据
    </div>
  </div>
</template>
