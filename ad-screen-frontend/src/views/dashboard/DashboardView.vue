<script setup lang="ts">
/**
 * 工作台仪表盘
 * ------------------------------------------------------------------
 * 1. 顶部四张统计卡片：待分析病例 / 已完成筛查 / AD 高风险 / 今日推理，
 *    附较昨日变化趋势（医疗大屏读数风格，等宽数字字体）
 * 2. 中部图表：近 6 个月筛查量趋势折线 + 风险等级分布环形图
 * 3. 底部：近期任务列表 + 病例快捷操作入口
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiGetDashboardStats, apiGetTrend, apiGetRiskDistribution, apiGetRecentTasks, apiGetDistributions, apiGetTodoSummary } from '@/api/dashboard'
import { apiGetCognitiveTrend } from '@/api/analytics'
import { apiFollowUpSummary } from '@/api/followup'
import { apiListFavorites } from '@/api/case'
import type { CaseRecord } from '@/types/case'
import type { DashboardStats, TaskItem, TrendPoint, RiskDistItem, DashboardDistribution, TodoItem } from '@/types/dashboard'
import type { CognitiveTrendPoint } from '@/types/analytics'
import { useUserStore } from '@/stores/user'
import ChartBase from '@/components/ChartBase.vue'
import type { EChartsOption } from 'echarts'

const router = useRouter()
const userStore = useUserStore()

// ---------- Hero 横幅：问候语 + 今日日期 ----------
const greeting = computed<string>(() => {
  const h = new Date().getHours()
  if (h < 6) return '凌晨好'
  if (h < 9) return '早上好'
  if (h < 12) return '上午好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
const todayText = computed<string>(() => {
  const d = new Date()
  const weeks = ['日', '一', '二', '三', '四', '五', '六']
  return `${d.getFullYear()} 年 ${d.getMonth() + 1} 月 ${d.getDate()} 日 · 星期${weeks[d.getDay()]}`
})

// ---------- 工作台布局定制（显隐 + 顺序，按用户名 localStorage 持久化） ----------
interface WidgetDef {
  key: string
  label: string
  defaultVisible: boolean
}
const ALL_WIDGETS: WidgetDef[] = [
  { key: 'todo', label: '我的待办', defaultVisible: true },
  { key: 'stats', label: '统计概览卡片', defaultVisible: true },
  { key: 'trend', label: '筛查趋势 + 风险分布', defaultVisible: true },
  { key: 'cognition', label: '认知趋势', defaultVisible: true },
  { key: 'dist', label: '模态/科室/风险概览', defaultVisible: true },
  { key: 'tasks', label: '近期任务 + 快捷操作', defaultVisible: true },
]

const layoutDialogVisible = ref(false)
/** 可见 widget 有序数组（持久化） */
const visibleWidgetKeys = ref<string[]>(ALL_WIDGETS.filter((w) => w.defaultVisible).map((w) => w.key))
/** 用于设置弹窗的工作副本（确认后再写回） */
const draftOrder = ref<string[]>([])
const draftVisible = ref<Record<string, boolean>>({})

const STORAGE_KEY = () => `ad_dashboard_layout_${userStore.username || 'default'}`
/** 已完成"新组件迁移"的 key 记录：新增默认组件只自动补位一次，用户之后可永久隐藏 */
const SEEN_KEY = () => `ad_dashboard_widgets_seen_${userStore.username || 'default'}`

function loadLayout(): void {
  try {
    const raw = localStorage.getItem(STORAGE_KEY())
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr) && arr.length > 0) {
        // 过滤掉未知 key，保证只渲染已知组件
        visibleWidgetKeys.value = arr.filter((k: string) => ALL_WIDGETS.some((w) => w.key === k))
        if (visibleWidgetKeys.value.length === 0) {
          visibleWidgetKeys.value = ALL_WIDGETS.map((w) => w.key)
        }
      }
    }
    // 新组件迁移：默认可见且用户从未见过的组件自动前置展示一次
    const seen: string[] = JSON.parse(localStorage.getItem(SEEN_KEY()) || '[]')
    const current = new Set(visibleWidgetKeys.value)
    const newcomers = ALL_WIDGETS
      .filter((w) => w.defaultVisible && !current.has(w.key) && !seen.includes(w.key))
      .map((w) => w.key)
    if (newcomers.length > 0) {
      visibleWidgetKeys.value = [...newcomers, ...visibleWidgetKeys.value]
      localStorage.setItem(SEEN_KEY(), JSON.stringify([...seen, ...newcomers]))
      persistLayout()
    }
  } catch { /* 损坏则用默认 */ }
}

function persistLayout(): void {
  // 隐私模式/配额异常时持久化会抛错：内存态已更新，轻提示一次，不中断流程
  try {
    localStorage.setItem(STORAGE_KEY(), JSON.stringify(visibleWidgetKeys.value))
  } catch {
    ElMessage.warning('本地存储不可用，布局设置仅当前页面生效')
  }
}

/** 打开布局设置弹窗 */
function openLayoutDialog(): void {
  draftOrder.value = [...visibleWidgetKeys.value]
  draftVisible.value = {}
  for (const w of ALL_WIDGETS) draftVisible.value[w.key] = visibleWidgetKeys.value.includes(w.key)
  layoutDialogVisible.value = true
}

/** 上下移动顺序 */
function moveWidget(key: string, dir: -1 | 1): void {
  const idx = draftOrder.value.indexOf(key)
  const target = idx + dir
  if (target < 0 || target >= draftOrder.value.length) return
  const arr = [...draftOrder.value]
  ;[arr[idx], arr[target]] = [arr[target], arr[idx]]
  draftOrder.value = arr
}

/** 切换显隐（隐藏时从顺序中移除，显示时追加到末尾） */
function toggleWidget(key: string): void {
  const vis = draftVisible.value[key]
  if (vis) {
    // 隐藏
    draftOrder.value = draftOrder.value.filter((k) => k !== key)
  } else {
    // 显示
    draftOrder.value = [...draftOrder.value, key]
  }
  draftVisible.value[key] = !vis
}

/** 确认保存布局 */
function saveLayout(): void {
  if (draftOrder.value.length === 0) {
    ElMessage.warning('至少保留一个可见区块')
    return
  }
  visibleWidgetKeys.value = [...draftOrder.value]
  persistLayout()
  layoutDialogVisible.value = false
  ElMessage.success('工作台布局已更新')
}

/** 恢复默认布局 */
function resetLayout(): void {
  visibleWidgetKeys.value = ALL_WIDGETS.map((w) => w.key)
  persistLayout()
  layoutDialogVisible.value = false
  ElMessage.success('已恢复默认布局')
}

onMounted(() => {
  loadLayout()
})

// ---------- 数据加载 ----------
const stats = ref<DashboardStats | null>(null)
const trend = ref<TrendPoint[]>([])
const riskDist = ref<RiskDistItem[]>([])
const tasks = ref<TaskItem[]>([])
const distributions = ref<DashboardDistribution | null>(null)
const favorites = ref<CaseRecord[]>([])
const loading = ref(true)

// ---------- 随访待办（临床角色） ----------
const followUp = ref<{ overdue: number; due7: number } | null>(null)
const canViewFollowUp = computed(() => userStore.hasRole(['radiologist', 'neurologist', 'admin']))

// ---------- 我的待办聚合（审核/随访/预警/标注，按角色返回） ----------
const todoItems = ref<TodoItem[]>([])
const todoTotal = ref(0)
/** 配色基调 → 主色/浅底/图标（与全站医疗色板一致） */
const TONE_META: Record<TodoItem['tone'], { color: string; bg: string; icon: string }> = {
  blue: { color: '#2F6DA3', bg: '#E8F1F8', icon: 'DocumentChecked' },
  amber: { color: '#D99A2B', bg: '#FBF4E6', icon: 'Calendar' },
  red: { color: '#C94F4F', bg: '#FAEDED', icon: 'WarningFilled' },
  green: { color: '#2E9E6B', bg: '#EDF7F2', icon: 'Aim' }
}
function toneMeta(tone: TodoItem['tone']) {
  return TONE_META[tone]
}
function goTodo(item: TodoItem): void {
  void router.push(item.route)
}

// ---------- 统计卡片配置（图标/主色/跳转目标） ----------
const statCards = computed(() => [
  {
    label: '待分析病例',
    value: stats.value?.pendingCases ?? 0,
    delta: stats.value?.pendingDelta ?? 0,
    deltaGood: false,
    icon: 'Loading',
    color: '#2F6DA3',
    bg: '#E8F1F8',
    to: '/cases'
  },
  {
    label: '已完成筛查',
    value: stats.value?.completedScreening ?? 0,
    delta: stats.value?.completedDelta ?? 0,
    deltaGood: true,
    icon: 'CircleCheck',
    color: '#2E9E6B',
    bg: '#EDF7F2',
    to: '/cases'
  },
  {
    label: 'AD 高风险病例',
    value: stats.value?.highRiskCases ?? 0,
    delta: stats.value?.highRiskDelta ?? 0,
    deltaGood: false,
    icon: 'WarningFilled',
    color: '#C94F4F',
    bg: '#FAEDED',
    to: '/cases'
  },
  {
    label: '今日推理任务',
    value: stats.value?.todayInference ?? 0,
    delta: stats.value?.todayDelta ?? 0,
    deltaGood: true,
    icon: 'Cpu',
    color: '#D99A2B',
    bg: '#FBF4E6',
    to: '/cases'
  }
])

// ---------- 趋势折线图（筛查总量 + 高风险量双序列） ----------
const trendOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['筛查总例数', 'AD 高风险例数'], right: 12, top: 0, textStyle: { color: '#5C6B7A', fontSize: 12 } },
  grid: { left: 46, right: 20, top: 36, bottom: 28 },
  xAxis: {
    type: 'category',
    data: trend.value.map((t) => t.month),
    axisLine: { lineStyle: { color: '#E3E9EF' } },
    axisLabel: { color: '#93A1AF' },
    axisTick: { show: false }
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: '#EFF3F7' } },
    axisLabel: { color: '#93A1AF' }
  },
  series: [
    {
      name: '筛查总例数',
      type: 'line',
      smooth: true,
      symbolSize: 6,
      data: trend.value.map((t) => t.total),
      lineStyle: { width: 2.5, color: '#2F6DA3' },
      itemStyle: { color: '#2F6DA3' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(47,109,163,.18)' },
            { offset: 1, color: 'rgba(47,109,163,0)' }
          ]
        }
      }
    },
    {
      name: 'AD 高风险例数',
      type: 'line',
      smooth: true,
      symbolSize: 6,
      data: trend.value.map((t) => t.highRisk),
      lineStyle: { width: 2.5, color: '#C94F4F' },
      itemStyle: { color: '#C94F4F' }
    }
  ]
}))

// ---------- 认知功能纵向趋势（随访 MMSE / MoCA 月平均） ----------
const cognitiveTrend = ref<CognitiveTrendPoint[]>([])
const cognitiveLoading = ref(false)
const cognitiveTrendOption = computed<EChartsOption>(() => {
  const data = cognitiveTrend.value
  if (!data.length) return {}
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['平均 MMSE', '平均 MoCA'], right: 12, top: 0, textStyle: { color: '#5C6B7A', fontSize: 12 } },
    grid: { left: 46, right: 20, top: 36, bottom: 28 },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.month),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#93A1AF', fontSize: 11 },
      axisTick: { show: false }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 30,
      name: '认知评分',
      nameTextStyle: { color: '#5C6B7A', fontSize: 11 },
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF', fontSize: 11 }
    },
    series: [
      {
        name: '平均 MMSE',
        type: 'line',
        smooth: true,
        symbolSize: 6,
        connectNulls: true,
        data: data.map((d) => d.avgMMSE),
        lineStyle: { width: 2.5, color: '#2F6DA3' },
        itemStyle: { color: '#2F6DA3' }
      },
      {
        name: '平均 MoCA',
        type: 'line',
        smooth: true,
        symbolSize: 6,
        connectNulls: true,
        data: data.map((d) => d.avgMoCA),
        lineStyle: { width: 2.5, color: '#D99A2B', type: 'dashed' },
        itemStyle: { color: '#D99A2B' }
      }
    ]
  }
})

async function loadCognitiveTrend(): Promise<void> {
  cognitiveLoading.value = true
  try {
    cognitiveTrend.value = await apiGetCognitiveTrend(12)
  } catch {
    cognitiveTrend.value = []
  } finally {
    cognitiveLoading.value = false
  }
}

// ---------- 风险等级分布环形图（四级风险色） ----------
const RISK_COLORS: Record<string, string> = {
  low: '#2E9E6B',
  mci: '#D99A2B',
  'ad-early': '#D96B2B',
  'ad-late': '#C94F4F'
}
const riskOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'item', formatter: '{b}：{c} 例（{d}%）' },
  legend: { orient: 'vertical', right: 16, top: 'center', textStyle: { color: '#5C6B7A', fontSize: 12 } },
  color: riskDist.value.map((r) => RISK_COLORS[r.key] ?? '#93A1AF'),
  series: [
    {
      type: 'pie',
      radius: ['52%', '74%'],
      center: ['38%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data: riskDist.value.map((r) => ({ name: r.name, value: r.value }))
    }
  ]
}))

// ---------- 任务状态标签色 ----------
function taskTagType(status: TaskItem['status']): 'info' | 'warning' | 'success' | 'danger' {
  switch (status) {
    case '已完成':
      return 'success'
    case '执行中':
      return 'warning'
    case '失败':
      return 'danger'
    default:
      return 'info'
  }
}

// ---------- 模态分布环形图 ----------
const MODALITY_COLORS = ['#2F6DA3', '#7B5EA7', '#2E9E6B']
const modalityOption = computed<EChartsOption>(() => {
  const data = distributions.value?.modalityDistribution ?? []
  return {
    tooltip: { trigger: 'item', formatter: '{b}：{c} 例（{d}%）' },
    legend: { orient: 'vertical', right: 12, top: 'center', textStyle: { color: '#5C6B7A', fontSize: 12 } },
    color: MODALITY_COLORS,
    series: [
      {
        type: 'pie',
        radius: ['48%', '72%'],
        center: ['36%', '50%'],
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        label: { show: false },
        data: data.map((d) => ({ name: d.name, value: d.value }))
      }
    ]
  }
})

// ---------- 科室分布横向柱状图 ----------
const departmentOption = computed<EChartsOption>(() => {
  const data = distributions.value?.departmentDistribution ?? []
  const sorted = [...data].sort((a, b) => a.value - b.value)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 28, top: 10, bottom: 10, containLabel: true },
    xAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    yAxis: {
      type: 'category',
      data: sorted.map((d) => d.name),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisTick: { show: false },
      axisLabel: { color: '#5C6B7A', fontSize: 12 }
    },
    series: [
      {
        type: 'bar',
        data: sorted.map((d) => d.value),
        barWidth: 14,
        itemStyle: {
          color: { type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
            colorStops: [{ offset: 0, color: '#7FB0D9' }, { offset: 1, color: '#2F6DA3' }] },
          borderRadius: [0, 7, 7, 0]
        },
        label: { show: true, position: 'right', color: '#5C6B7A', fontSize: 12 }
      }
    ]
  }
})

/** 平均风险分颜色（与风险等级色一致） */
const avgScoreColor = computed<string>(() => {
  const s = distributions.value?.avgRiskScore ?? 0
  if (s >= 80) return '#C94F4F'
  if (s >= 62) return '#D96B2B'
  if (s >= 35) return '#D99A2B'
  return '#2E9E6B'
})

onMounted(async () => {
  // 各区块独立容错：任一接口失败不允许全屏 loading 遮罩锁死整页（错误提示已由请求层统一给出）
  try {
    const [s, t, r, k, d] = await Promise.allSettled([
      apiGetDashboardStats(),
      apiGetTrend(),
      apiGetRiskDistribution(),
      apiGetRecentTasks(),
      apiGetDistributions()
    ])
    if (s.status === 'fulfilled') stats.value = s.value
    if (t.status === 'fulfilled') trend.value = t.value
    if (r.status === 'fulfilled') riskDist.value = r.value
    if (k.status === 'fulfilled') tasks.value = k.value
    if (d.status === 'fulfilled') distributions.value = d.value
  } finally {
    loading.value = false
  }
  if (canViewFollowUp.value) {
    apiFollowUpSummary()
      .then((fu) => { followUp.value = { overdue: fu.overdue, due7: fu.due7 } })
      .catch(() => undefined)
  }
  // 收藏病例（工作台快捷入口）
  apiListFavorites()
    .then((f) => { favorites.value = f })
    .catch(() => undefined)
  // 我的待办聚合（按角色返回，失败不阻塞仪表盘其它区块）
  apiGetTodoSummary()
    .then((todo) => { todoItems.value = todo.items; todoTotal.value = todo.total })
    .catch(() => undefined)
  // 认知功能纵向趋势（与主趋势并行加载，失败不阻塞仪表盘）
  void loadCognitiveTrend()
  loading.value = false
})
</script>

<template>
  <div class="page-wrap" v-loading="loading">
    <!-- ==================== 品牌 Hero 横幅 ==================== -->
    <section class="hero-banner anim-fade-up overflow-hidden rounded-card relative">
      <!-- 背景渐变（与 Logo brand-gradient-bg 一致） -->
      <div class="hero-banner__bg" />
      <!-- 装饰光斑 -->
      <div class="hero-glow hero-glow--1" />
      <div class="hero-glow hero-glow--2" />
      <!-- 抽象脑部 SVG 装饰（右侧淡纹，与登录页呼应） -->
      <svg class="hero-brain-svg" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <linearGradient id="heroBrainGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="rgba(255,255,255,0.12)" />
            <stop offset="100%" stop-color="rgba(255,255,255,0.02)" />
          </linearGradient>
        </defs>
        <path d="M100 30 C 60 30, 35 65, 40 105 C 42 130, 55 155, 80 165 C 90 170, 95 165, 100 160 C 105 165, 110 170, 120 165 C 145 155, 158 130, 160 105 C 165 65, 140 30, 100 30 Z"
              fill="url(#heroBrainGrad)" stroke="rgba(255,255,255,0.20)" stroke-width="1.2" />
        <path d="M100 35 C 75 40, 60 70, 65 100 C 70 125, 85 145, 100 150 M100 35 C 125 40, 140 70, 135 100 C 130 125, 115 145, 100 150"
              fill="none" stroke="rgba(255,255,255,0.24)" stroke-width="1.4" stroke-linecap="round" />
        <path d="M70 70 C 80 85, 85 95, 80 110 M130 70 C 120 85, 115 95, 120 110 M85 130 C 95 135, 105 135, 115 130"
              fill="none" stroke="rgba(255,255,255,0.16)" stroke-width="1" stroke-linecap="round" />
      </svg>

      <div class="relative z-10 flex items-center justify-between gap-6 px-7 py-6">
        <!-- 左侧：Logo + 项目名 + 副标题 -->
        <div class="flex items-center gap-5 min-w-0">
          <!-- Logo 图标（与顶栏 / 登录页一致的品牌渐变方块） -->
          <div class="hero-logo shrink-0">
            <svg width="30" height="30" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="16" cy="16" r="13" stroke="currentColor" stroke-width="1.4" opacity="0.35" />
              <path d="M16 6.5 C12 6.5 8.5 10 8.5 15 C8.5 19.5 11 23 15 24.5 C14 22.5 13.5 20.5 14 18.5 C12.8 17.3 12.5 15.5 13 14 C12.5 12.3 13.5 10.8 15.2 10.3 C15.2 9 15.5 7.7 16 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M16 6.5 C20 6.5 23.5 10 23.5 15 C23.5 19.5 21 23 17 24.5 C18 22.5 18.5 20.5 18 18.5 C19.2 17.3 19.5 15.5 19 14 C19.5 12.3 18.5 10.8 16.8 10.3 C16.8 9 16.5 7.7 16 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M16 7.5 L16 24" stroke="currentColor" stroke-width="1.2" opacity="0.45" stroke-linecap="round" />
              <circle cx="16" cy="16" r="1.8" fill="currentColor" />
              <circle cx="3.5" cy="16" r="1.1" fill="currentColor" opacity="0.5" />
              <circle cx="28.5" cy="16" r="1.1" fill="currentColor" opacity="0.5" />
            </svg>
          </div>
          <div class="leading-tight min-w-0">
            <div class="flex items-baseline gap-3">
              <h1 class="hero-title">
                脑影<span class="hero-title__dot">·</span>明衰
              </h1>
              <span class="hero-en">BrainImaging · Dementia Insight</span>
            </div>
            <p class="hero-subtitle mt-1.5">
              阿尔茨海默病一体化 MRI/PET 脑成像智能诊断系统
            </p>
          </div>
        </div>

        <!-- 右侧：个性化欢迎 + 布局定制 -->
        <div class="flex flex-col items-end gap-3 shrink-0">
          <div class="text-right">
            <div class="hero-greeting">{{ greeting }}，{{ userStore.realName }}</div>
            <div class="hero-role mt-0.5">
              {{ userStore.roleName }}
              <span v-if="userStore.userInfo?.department"> · {{ userStore.userInfo.department }}</span>
            </div>
          </div>
          <el-button
            class="hero-btn"
            :icon="'Setting'"
            @click="openLayoutDialog"
          >布局定制</el-button>
        </div>
      </div>

      <!-- 底部：今日日期 + 模型版本信息条 -->
      <div class="relative z-10 px-7 py-2.5 border-t border-white/15 flex items-center justify-between text-[12px] text-white/70">
        <div class="flex items-center gap-2">
          <el-icon :size="13"><Calendar /></el-icon>
          <span>{{ todayText }}</span>
        </div>
        <div class="flex items-center gap-4">
          <span class="flex items-center gap-1.5">
            <el-icon :size="13"><Cpu /></el-icon>
            模型 TransMF-15ens-v4
          </span>
          <span class="w-1 h-1 rounded-full bg-white/30" />
          <span class="flex items-center gap-1.5">
            <el-icon :size="13"><DocumentChecked /></el-icon>
            2026 版 PET/MRI 指南对齐
          </span>
        </div>
      </div>
    </section>

    <template v-for="wkey in visibleWidgetKeys" :key="wkey">
    <!-- ==================== 我的待办聚合 ==================== -->
    <div v-if="wkey === 'todo' && todoItems.length > 0" class="anim-fade-up" style="--anim-delay: 0ms">
      <div class="flex items-center justify-between mb-2.5">
        <div class="text-[14px] font-semibold text-ink flex items-center gap-2">
          我的待办
          <el-tag v-if="todoTotal > 0" type="danger" size="small" round effect="light">{{ todoTotal }}</el-tag>
        </div>
        <span class="text-xs text-hint">登录即见工作优先级，点击卡片直达处理页</span>
      </div>
      <div
        class="grid gap-4"
        :style="{ gridTemplateColumns: `repeat(${todoItems.length}, minmax(0, 1fr))` }"
      >
        <div
          v-for="(item, i) in todoItems"
          :key="item.key"
          class="kpi-card card-interactive cursor-pointer relative overflow-hidden"
          :style="{ '--anim-delay': `${i * 70}ms`, '--kpi-color': toneMeta(item.tone).color }"
          @click="goTodo(item)"
        >
          <div class="kpi-card__bar" />
          <div
            class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.07] pointer-events-none"
            :style="{ background: toneMeta(item.tone).color }"
          />
          <div class="relative pl-5 pr-4 py-4">
            <div class="flex items-start justify-between">
              <div class="min-w-0">
                <div class="text-[12.5px] text-sub font-medium">{{ item.title }}</div>
                <div
                  class="mt-1.5 font-num text-[32px] leading-none font-bold tracking-tight"
                  :style="{ color: toneMeta(item.tone).color }"
                >
                  {{ item.count }}
                  <span class="text-[12px] text-hint font-sans font-normal ml-0.5">{{ item.unit }}</span>
                </div>
              </div>
              <div
                class="kpi-card__icon flex items-center justify-center shrink-0"
                :style="{ background: toneMeta(item.tone).bg, color: toneMeta(item.tone).color }"
              >
                <el-icon :size="19"><component :is="toneMeta(item.tone).icon" /></el-icon>
              </div>
            </div>
            <div class="mt-3 text-xs text-hint truncate" :title="item.desc">{{ item.desc }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== 统计卡片行 ==================== -->
    <div v-else-if="wkey === 'stats'" class="grid grid-cols-4 gap-4">
      <div
        v-for="(card, i) in statCards"
        :key="card.label"
        class="kpi-card card-interactive anim-fade-up cursor-pointer relative overflow-hidden"
        :style="{ '--anim-delay': `${i * 70}ms`, '--kpi-color': card.color }"
        @click="router.push(card.to)"
      >
        <!-- 左侧色条：专业系统 KPI 卡标配 -->
        <div class="kpi-card__bar" />
        <!-- 右上装饰光斑 -->
        <div
          class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.07] pointer-events-none"
          :style="{ background: card.color }"
        />
        <div class="relative pl-5 pr-4 py-4">
          <div class="flex items-start justify-between">
            <div class="min-w-0">
              <div class="text-[12.5px] text-sub font-medium">{{ card.label }}</div>
              <div class="mt-1.5 font-num text-[32px] leading-none font-bold text-ink tracking-tight">
                {{ card.value }}
                <span class="text-[12px] text-hint font-sans font-normal ml-0.5">例</span>
              </div>
            </div>
            <div
              class="kpi-card__icon flex items-center justify-center shrink-0"
              :style="{ background: card.bg, color: card.color }"
            >
              <el-icon :size="19"><component :is="card.icon" /></el-icon>
            </div>
          </div>
          <!-- 趋势徽章 -->
          <div
            class="kpi-delta mt-3"
            :class="card.delta >= 0 ? (card.deltaGood ? 'is-good' : 'is-bad') : 'is-flat'"
          >
            <el-icon :size="11"><component :is="card.delta >= 0 ? 'Top' : 'Bottom'" /></el-icon>
            <span>较昨日 {{ card.delta >= 0 ? '+' : '' }}{{ card.delta }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== 图表行：趋势 + 风险分布 ==================== -->
    <div v-else-if="wkey === 'trend'" class="grid grid-cols-3 gap-4 anim-fade-up" style="--anim-delay: 280ms">
      <div class="card-ad col-span-2 card-interactive">
        <div class="card-ad__header">
          <span class="card-ad__title">
            <el-icon class="mr-1 text-primary"><TrendCharts /></el-icon>近 6 个月筛查量趋势
          </span>
          <span class="text-xs text-hint">单位：例</span>
        </div>
        <div class="p-4">
          <ChartBase :option="trendOption" :height="300" :empty="trend.length === 0" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">
            <el-icon class="mr-1 text-primary"><PieChart /></el-icon>风险等级分布
          </span>
        </div>
        <div class="p-4">
          <ChartBase :option="riskOption" :height="300" :empty="riskDist.length === 0" />
        </div>
      </div>
    </div>

    <!-- ==================== 认知功能纵向趋势 ==================== -->
    <div v-else-if="wkey === 'cognition'" class="card-ad anim-fade-up" style="--anim-delay: 330ms">
      <div class="card-ad__header">
        <span class="card-ad__title">
          <el-icon class="mr-1 text-primary"><Brain /></el-icon>认知功能纵向趋势
        </span>
        <span class="text-xs text-hint">最近 12 个月 · 全部随访患者平均 MMSE / MoCA</span>
      </div>
      <div class="p-4">
        <div v-if="cognitiveLoading" class="h-[300px] flex items-center justify-center">
          <el-icon class="is-loading" :size="24"><Loading /></el-icon>
          <span class="ml-2 text-hint text-[13px]">加载中...</span>
        </div>
        <ChartBase
          v-else
          :option="cognitiveTrendOption"
          :height="300"
          :empty="cognitiveTrend.length === 0"
        />
      </div>
    </div>

    <!-- ==================== 分布统计行：模态 / 科室 / 风险概览 ==================== -->
    <div v-else-if="wkey === 'dist'" class="grid grid-cols-3 gap-4 anim-fade-up" style="--anim-delay: 380ms">
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">
            <el-icon class="mr-1 text-primary"><Picture /></el-icon>影像模态分布
          </span>
        </div>
        <div class="p-4">
          <ChartBase :option="modalityOption" :height="240" :empty="(distributions?.modalityDistribution ?? []).every((m) => m.value === 0)" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">
            <el-icon class="mr-1 text-primary"><OfficeBuilding /></el-icon>申请科室分布
          </span>
        </div>
        <div class="p-4">
          <ChartBase :option="departmentOption" :height="240" :empty="(distributions?.departmentDistribution ?? []).length === 0" />
        </div>
      </div>
      <div class="card-ad">
        <div class="card-ad__header">
          <span class="card-ad__title">
            <el-icon class="mr-1 text-primary"><DataAnalysis /></el-icon>风险概览
          </span>
        </div>
        <div class="p-5 flex flex-col gap-3">
          <div class="metric-tile">
            <div class="metric-tile__info">
              <div class="metric-tile__label">已分析病例平均风险分</div>
              <div class="metric-tile__sub">共 {{ distributions?.scoredCount ?? 0 }} 例已出分</div>
            </div>
            <div class="metric-tile__value" :style="{ color: avgScoreColor }">
              {{ distributions?.avgRiskScore ?? 0 }}<span class="metric-tile__unit">/100</span>
            </div>
          </div>
          <div class="metric-tile metric-tile--clickable" @click="router.push('/cases')">
            <div class="metric-tile__info">
              <div class="metric-tile__label">待协作审核版本</div>
              <div class="metric-tile__sub">分析结果等待医生复核</div>
            </div>
            <div class="metric-tile__value" style="color: #D99A2B">
              {{ distributions?.reviewPending ?? 0 }}<span class="metric-tile__unit">份</span>
            </div>
          </div>
          <div
            v-if="canViewFollowUp"
            class="metric-tile metric-tile--clickable"
            @click="router.push({ path: '/followup', query: { scope: (followUp?.overdue ?? 0) > 0 ? 'overdue' : 'week' } })"
          >
            <div class="metric-tile__info">
              <div class="metric-tile__label">随访待办</div>
              <div class="metric-tile__sub">
                <span :class="(followUp?.overdue ?? 0) > 0 ? 'text-risk-late font-medium' : ''">逾期 {{ followUp?.overdue ?? 0 }}</span>
                · 7 天内到期 {{ followUp?.due7 ?? 0 }}
              </div>
            </div>
            <div class="metric-tile__value" :class="(followUp?.overdue ?? 0) > 0 ? 'text-risk-late' : 'text-primary'">
              {{ followUp?.due7 ?? 0 }}<span class="metric-tile__unit">人</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== 近期任务 + 快捷操作 ==================== -->
    <div v-else-if="wkey === 'tasks'" class="grid grid-cols-3 gap-4 anim-fade-up" style="--anim-delay: 480ms">
      <div class="card-ad col-span-2">
        <div class="card-ad__header">
          <span class="card-ad__title">
            <el-icon class="mr-1 text-primary"><List /></el-icon>近期任务
          </span>
          <el-link type="primary" :underline="'never'" @click="router.push('/cases')">查看全部</el-link>
        </div>
        <el-table :data="tasks" size="default" class="w-full">
          <el-table-column prop="caseId" label="病例编号" width="110">
            <template #default="{ row }">
              <span class="font-num text-[13px]">{{ row.caseId }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="patientName" label="患者" min-width="90" />
          <el-table-column prop="type" label="任务类型" width="100" />
          <el-table-column prop="status" label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="taskTagType(row.status)" size="small" effect="light" round>{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="operator" label="操作人" width="90" />
          <el-table-column prop="time" label="时间" min-width="150">
            <template #default="{ row }">
              <span class="font-num text-[12px] text-sub">{{ row.time }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 病例快捷操作入口 + 我的收藏 -->
      <div class="flex flex-col gap-4">
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">
              <el-icon class="mr-1 text-primary"><Operation /></el-icon>快捷操作
            </span>
          </div>
          <div class="p-4 grid grid-cols-2 gap-3">
            <div class="quick-tile" @click="router.push('/cases?upload=1')">
              <div class="quick-tile__icon"><el-icon :size="22"><UploadFilled /></el-icon></div>
              <span class="quick-tile__label">影像上传</span>
            </div>
            <div class="quick-tile" @click="router.push('/cases')">
              <div class="quick-tile__icon"><el-icon :size="22"><Search /></el-icon></div>
              <span class="quick-tile__label">病例检索</span>
            </div>
            <div class="quick-tile" @click="router.push('/cases?batch=1')">
              <div class="quick-tile__icon"><el-icon :size="22"><DocumentAdd /></el-icon></div>
              <span class="quick-tile__label">批量导入</span>
            </div>
            <div
              v-if="userStore.hasRole(['researcher', 'admin'])"
              class="quick-tile"
              @click="router.push('/model')"
            >
              <div class="quick-tile__icon"><el-icon :size="22"><DataAnalysis /></el-icon></div>
              <span class="quick-tile__label">模型配置</span>
            </div>
          </div>
        </div>

        <!-- 我的收藏 -->
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">
              <el-icon class="mr-1 align-[-2px]" style="color:#E6A23C"><StarFilled /></el-icon>我的收藏
              <el-tag size="small" type="warning" effect="light" class="ml-1.5 !align-middle">{{ favorites.length }}</el-tag>
            </span>
          </div>
          <div class="p-3">
            <div v-if="favorites.length === 0" class="py-6 text-center text-[13px] text-hint">
              暂无收藏病例，可在病例库点击星标收藏
            </div>
            <div v-else class="space-y-1.5">
              <div
                v-for="c in favorites.slice(0, 6)"
                :key="c.id"
                class="flex items-center justify-between px-2 py-1.5 rounded hover:bg-[#F5F8FB] cursor-pointer"
                @click="router.push(`/cases?keyword=${c.id}`)"
              >
                <div class="leading-tight min-w-0">
                  <div class="text-[13px] text-ink truncate">{{ c.patient.name }}</div>
                  <div class="text-[11px] text-hint font-num">{{ c.id }} · {{ c.modality }}</div>
                </div>
                <el-icon class="text-hint shrink-0 ml-2"><ArrowRight /></el-icon>
              </div>
              <div v-if="favorites.length > 6" class="text-center pt-1">
                <el-link type="primary" :underline="'never'" @click="router.push({ path: '/cases', query: { favorites: '1' } })">查看全部 {{ favorites.length }} 例</el-link>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </template>

    <!-- 布局定制弹窗 -->
    <el-dialog v-model="layoutDialogVisible" title="工作台布局定制" width="480px">
      <div class="text-[12px] text-hint mb-3">拖拽或使用上下按钮调整区块顺序，勾选控制显隐（按用户名本地保存）</div>
      <div class="space-y-2">
        <div
          v-for="w in ALL_WIDGETS"
          :key="w.key"
          class="flex items-center gap-3 p-3 rounded-card border border-line bg-page"
        >
          <el-checkbox v-model="draftVisible[w.key]" @change="toggleWidget(w.key)">
            <span class="text-[13px]">{{ w.label }}</span>
          </el-checkbox>
          <div class="flex-1" />
          <el-button
            :icon="'Top'"
            circle
            size="small"
            :disabled="draftOrder.indexOf(w.key) <= 0"
            @click="moveWidget(w.key, -1)"
          />
          <el-button
            :icon="'Bottom'"
            circle
            size="small"
            :disabled="draftOrder.indexOf(w.key) < 0 || draftOrder.indexOf(w.key) >= draftOrder.length - 1"
            @click="moveWidget(w.key, 1)"
          />
        </div>
      </div>
      <template #footer>
        <el-button @click="resetLayout">恢复默认</el-button>
        <el-button @click="layoutDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveLayout">保存布局</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ==========================================================
   品牌 Hero 横幅 —— 与 Logo / 登录页品牌面板设计语言一致
   渐变背景 + 发光光斑 + 脑部 SVG 装饰，凸显"脑影明衰"
   ========================================================== */
.hero-banner {
  position: relative;
  color: #fff;
  box-shadow: 0 4px 20px rgba(47, 109, 163, 0.18);
}
.hero-banner__bg {
  position: absolute;
  inset: 0;
  /* 与全局 .brand-gradient-bg 完全一致：#24567f → #2f6da3 → #3f86c4 */
  background: linear-gradient(135deg, #24567f 0%, #2f6da3 55%, #3f86c4 100%);
  z-index: 0;
}
.hero-banner::after {
  /* 顶部高光亮带，增强渐变层次 */
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.10), rgba(255, 255, 255, 0) 45%);
  pointer-events: none;
  z-index: 1;
}

/* 发光光斑（与登录页 brand-glow 呼应） */
.hero-glow {
  position: absolute;
  border-radius: 9999px;
  filter: blur(50px);
  pointer-events: none;
  z-index: 0;
}
.hero-glow--1 {
  width: 260px;
  height: 260px;
  background: rgba(120, 180, 230, 0.30);
  top: -90px;
  right: 120px;
}
.hero-glow--2 {
  width: 200px;
  height: 200px;
  background: rgba(90, 74, 142, 0.22);
  bottom: -70px;
  left: 30%;
}

/* 抽象脑部 SVG：右侧淡纹装饰 */
.hero-brain-svg {
  position: absolute;
  width: 180px;
  height: 180px;
  top: 50%;
  right: 280px;
  transform: translateY(-50%);
  opacity: 0.55;
  pointer-events: none;
  z-index: 0;
}

/* Logo 图标方块：与顶栏 / 登录页一致的品牌渐变 + 白图标 */
.hero-logo {
  width: 60px;
  height: 60px;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.22), rgba(255, 255, 255, 0.08));
  border: 1px solid rgba(255, 255, 255, 0.28);
  backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.25);
}

/* 项目主标题：大字号 + 品牌渐变强调点 */
.hero-title {
  font-size: 30px;
  font-weight: 700;
  letter-spacing: 0.06em;
  line-height: 1.1;
  color: #fff;
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
}
.hero-title__dot {
  background: linear-gradient(135deg, #9bdcff 0%, #c9a3ff 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  margin: 0 3px;
  font-weight: 800;
}
.hero-en {
  font-size: 12px;
  letter-spacing: 0.18em;
  color: rgba(255, 255, 255, 0.55);
  text-transform: uppercase;
  font-weight: 500;
}
.hero-subtitle {
  font-size: 13.5px;
  color: rgba(255, 255, 255, 0.82);
  font-weight: 300;
  letter-spacing: 0.02em;
}

/* 右侧欢迎语 */
.hero-greeting {
  font-size: 16px;
  font-weight: 600;
  color: #fff;
}
.hero-role {
  font-size: 12.5px;
  color: rgba(255, 255, 255, 0.70);
}

/* 布局定制按钮：浅玻璃风 */
.hero-btn {
  background: rgba(255, 255, 255, 0.14) !important;
  border: 1px solid rgba(255, 255, 255, 0.28) !important;
  color: #fff !important;
  backdrop-filter: blur(6px);
  transition: background 0.2s ease, transform 0.2s ease;
}
.hero-btn:hover {
  background: rgba(255, 255, 255, 0.24) !important;
  transform: translateY(-1px);
}

/* ==========================================================
   KPI 统计卡 —— 对标专业医疗影像系统（syngo / uAI / NeuAI）
   左侧色条 + 大字号等宽读数 + 彩色趋势徽章
   ========================================================== */
.kpi-card {
  background: var(--ad-surface);
  border: 1px solid var(--ad-border);
  border-radius: 8px;
  box-shadow: var(--ad-shadow-card);
  transition:
    transform 0.22s cubic-bezier(0.22, 0.61, 0.36, 1),
    box-shadow 0.22s ease,
    border-color 0.22s ease;
}
.kpi-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 16px rgba(16, 32, 48, 0.08), 0 14px 36px rgba(16, 32, 48, 0.1);
  border-color: color-mix(in srgb, var(--kpi-color) 35%, var(--ad-border));
}
/* 左侧色条 */
.kpi-card__bar {
  position: absolute;
  left: 0;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--kpi-color);
  z-index: 1;
}
/* 右上角图标 */
.kpi-card__icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  box-shadow: 0 2px 6px rgba(16, 32, 48, 0.08);
}

/* 趋势徽章：好（绿）/ 差（红）/ 平（灰） */
.kpi-delta {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  font-family: 'Cascadia Mono', Consolas, monospace;
}
.kpi-delta.is-good {
  background: #edf7f2;
  color: #2e9e6b;
}
.kpi-delta.is-bad {
  background: #faecec;
  color: #c94f4f;
}
.kpi-delta.is-flat {
  background: #f2f6fa;
  color: #93a1af;
}

/* ==========================================================
   快捷操作磁贴 —— 专业系统工作台入口
   渐变图标 + 悬浮抬升 + 主色描边
   ========================================================== */
.quick-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 16px 8px;
  border-radius: 10px;
  border: 1px solid var(--ad-border);
  background: #fff;
  cursor: pointer;
  transition:
    transform 0.2s cubic-bezier(0.22, 0.61, 0.36, 1),
    border-color 0.2s ease,
    box-shadow 0.2s ease;
}
.quick-tile:hover {
  transform: translateY(-3px);
  border-color: var(--ad-primary);
  box-shadow: 0 8px 20px rgba(47, 109, 163, 0.15);
}
.quick-tile__icon {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--ad-primary-light), #fff);
  color: var(--ad-primary);
  transition: transform 0.2s ease;
}
.quick-tile:hover .quick-tile__icon {
  transform: scale(1.08);
}
.quick-tile__label {
  font-size: 13px;
  font-weight: 500;
  color: var(--ad-ink);
}

/* ==========================================================
   指标磁贴 —— 风险概览区读数卡
   ========================================================== */
.metric-tile {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--ad-bg);
  border: 1px solid var(--ad-border);
  transition: border-color 0.2s ease, background 0.2s ease;
}
.metric-tile--clickable {
  cursor: pointer;
}
.metric-tile--clickable:hover {
  border-color: color-mix(in srgb, var(--ad-primary) 50%, var(--ad-border));
  background: #f7fafd;
}
.metric-tile__label {
  font-size: 12.5px;
  color: var(--ad-ink-2);
  font-weight: 500;
}
.metric-tile__sub {
  font-size: 11px;
  color: var(--ad-ink-3);
  margin-top: 2px;
}
.metric-tile__value {
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.02em;
}
.metric-tile__unit {
  font-size: 12px;
  color: var(--ad-ink-3);
  font-family: -apple-system, sans-serif;
  font-weight: 400;
  margin-left: 2px;
}
</style>

