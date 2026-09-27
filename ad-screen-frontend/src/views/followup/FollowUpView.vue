<script setup lang="ts">
/**
 * 随访管理工作台
 * ------------------------------------------------------------------
 * 临床闭环：筛查 → AI 分析 → 干预方案 → 随访复查。
 * 1. 顶部汇总卡：已逾期 / 7 天内到期 / 30 天内到期 / 在管随访总数（点击即筛选）
 * 2. 工作台列表：患者、风险分级、下次随访日、逾期/临期徽标、上次量表与认知变化
 * 3. 记录随访：MMSE/MoCA 复评、认知变化、用药依从性、不良反应、处置备注，
 *    保存后按随访周期自动滚动下次随访日期
 * 4. 随访历史抽屉：时间线 + MMSE 变化趋势迷你图 + 较基线差值
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import {
  apiFollowUpSummary,
  apiFollowUpList,
  apiRecordVisit,
  apiListVisits,
  apiBatchPreview,
  apiBatchGenerate,
  type FollowUpScope,
  type BatchScope
} from '@/api/followup'
import type { FollowUpSummary, FollowUpWorkItem, FollowUpVisit, FollowUpVisitPayload } from '@/types/followup'
import type { BatchFollowUpPreview, BatchGenerateResult } from '@/types/modelComparison.d'
import { useUserStore } from '@/stores/user'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import type { RiskLevel } from '@/types/case'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// ---------- 汇总与列表 ----------
const summary = ref<FollowUpSummary>({ overdue: 0, due7: 0, due30: 0, total: 0 })
const list = ref<FollowUpWorkItem[]>([])
const loading = ref(false)
const scope = ref<FollowUpScope>(
  ['overdue', 'week', 'month', 'all'].includes(String(route.query.scope)) ? (String(route.query.scope) as FollowUpScope) : 'all'
)
const keyword = ref(typeof route.query.keyword === 'string' ? route.query.keyword : '')

const SCOPE_TABS: { key: FollowUpScope; label: string }[] = [
  { key: 'all', label: '全部在管' },
  { key: 'overdue', label: '已逾期' },
  { key: 'week', label: '7 天内到期' },
  { key: 'month', label: '30 天内到期' }
]

async function fetchAll(): Promise<void> {
  const [s] = await Promise.all([apiFollowUpSummary(), fetchList()])
  summary.value = s
}

// 列表请求序号：快速切换 scope 卡片/搜索时丢弃过期响应，防止旧结果覆盖当前筛选
let listReqSeq = 0

async function fetchList(): Promise<void> {
  const seq = ++listReqSeq
  loading.value = true
  try {
    const res = await apiFollowUpList(scope.value, keyword.value)
    // 仅最后一次请求允许写入列表与 loading
    if (seq === listReqSeq) list.value = res.list
  } finally {
    if (seq === listReqSeq) loading.value = false
  }
}

function changeScope(s: FollowUpScope): void {
  scope.value = s
  fetchList()
}

function onSearch(): void {
  fetchList()
}

// ---------- 月历视图 ----------
const viewMode = ref<'list' | 'calendar'>('list')
const calDate = ref(new Date())
const calendarItems = ref<FollowUpWorkItem[]>([])
const calendarLoading = ref(false)

// 月历请求序号：与列表同理，切月/重拉时丢弃过期响应
let calendarReqSeq = 0

async function fetchCalendarItems(): Promise<void> {
  const seq = ++calendarReqSeq
  calendarLoading.value = true
  try {
    const res = await apiFollowUpList('all', '')
    if (seq === calendarReqSeq) calendarItems.value = res.list
  } finally {
    if (seq === calendarReqSeq) calendarLoading.value = false
  }
}

function switchView(mode: 'list' | 'calendar'): void {
  viewMode.value = mode
  if (mode === 'calendar') fetchCalendarItems()
}

/** 日期 → 当日到期随访病例 */
const calendarMap = computed(() => {
  const m = new Map<string, FollowUpWorkItem[]>()
  for (const it of calendarItems.value) {
    if (!it.nextDate) continue
    const key = it.nextDate.slice(0, 10)
    const arr = m.get(key)
    if (arr) arr.push(it)
    else m.set(key, [it])
  }
  return m
})

/** 已逾期病例（月历顶部提醒，日期早于今天） */
const overdueItems = computed(() =>
  calendarItems.value
    .filter((it) => it.daysDiff !== null && it.daysDiff < 0)
    .sort((a, b) => (a.daysDiff ?? 0) - (b.daysDiff ?? 0))
)

function dayClass(dayStr: string): string {
  return calendarMap.value.has(dayStr) ? 'cal-day-has-task' : ''
}

// 当日任务弹窗
const dayDialogVisible = ref(false)
const selectedDay = ref('')
const selectedDayItems = ref<FollowUpWorkItem[]>([])
function selectDay(dayStr: string): void {
  const items = calendarMap.value.get(dayStr)
  if (!items || items.length === 0) return
  selectedDay.value = dayStr
  selectedDayItems.value = items
  dayDialogVisible.value = true
}

// ---------- 汇总卡配置 ----------
const summaryCards = computed(() => [
  { key: 'overdue' as FollowUpScope, label: '已逾期', value: summary.value.overdue, unit: '人', color: '#C94F4F', bg: '#FAEDED', icon: 'WarningFilled', hint: '需立即联系安排复诊' },
  { key: 'week' as FollowUpScope, label: '7 天内到期', value: summary.value.due7, unit: '人', color: '#D96B2B', bg: '#FBEFE7', icon: 'AlarmClock', hint: '本周应完成随访' },
  { key: 'month' as FollowUpScope, label: '30 天内到期', value: summary.value.due30, unit: '人', color: '#D99A2B', bg: '#FBF4E6', icon: 'Calendar', hint: '近期随访计划' },
  { key: 'all' as FollowUpScope, label: '在管随访总数', value: summary.value.total, unit: '人', color: '#2F6DA3', bg: '#E8F1F8', icon: 'User', hint: '全部纳入随访管理病例' }
])

// ---------- 随访状态徽标 ----------
function dueTag(item: FollowUpWorkItem): { text: string; type: 'danger' | 'warning' | 'success' | 'info' } {
  const d = item.daysDiff
  if (d === null) return { text: '未排期', type: 'info' }
  if (d < 0) return { text: `逾期 ${-d} 天`, type: 'danger' }
  if (d === 0) return { text: '今日到期', type: 'danger' }
  if (d <= 7) return { text: `${d} 天后到期`, type: 'warning' }
  if (d <= 30) return { text: `${d} 天后`, type: 'warning' }
  return { text: `${d} 天后`, type: 'success' }
}

// ---------- 记录随访弹窗 ----------
const visitVisible = ref(false)
const visitSaving = ref(false)
const currentItem = ref<FollowUpWorkItem | null>(null)

/** 日期工具：加月 */
function addMonths(dateStr: string, months: number): string {
  const dt = new Date(dateStr)
  if (Number.isNaN(dt.getTime())) return ''
  const d = new Date(dt)
  const day = d.getDate()
  d.setMonth(d.getMonth() + months)
  // 处理月末溢出（如 1/31 + 1 月）
  if (d.getDate() < day) d.setDate(0)
  return formatLocalDate(d)
}
/** 本地时区 YYYY-MM-DD（不能用 toISOString：UTC 在东八区会回退一天） */
function formatLocalDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
function todayStr(): string {
  return formatLocalDate(new Date())
}

const VISIT_TYPES = ['门诊复诊', '电话随访', '影像复查', '线上随访']
const CHANGE_OPTS = ['改善', '稳定', '轻度下降', '明显下降']
const ADHERENCE_OPTS = ['规律', '偶有漏服', '经常漏服', '已停药']
const ADVERSE_OPTS = ['无', '轻微', '需处理']

const visitForm = reactive<FollowUpVisitPayload>({
  visitDate: todayStr(),
  visitType: '门诊复诊',
  mmse: null,
  moca: null,
  cognitionChange: '稳定',
  medicationAdherence: '规律',
  adverseEvent: '无',
  notes: '',
  nextDate: '',
  operator: ''
})

function openVisit(item: FollowUpWorkItem): void {
  currentItem.value = item
  visitForm.visitDate = todayStr()
  visitForm.visitType = '门诊复诊'
  visitForm.mmse = item.lastVisit?.mmse ?? null
  visitForm.moca = item.lastVisit?.moca ?? null
  visitForm.cognitionChange = '稳定'
  visitForm.medicationAdherence = item.lastVisit?.medicationAdherence ?? '规律'
  visitForm.adverseEvent = '无'
  visitForm.notes = ''
  visitForm.nextDate = addMonths(todayStr(), item.cycleMonths || 6)
  visitForm.operator = userStore.realName || userStore.userInfo?.username || ''
  visitVisible.value = true
}

/** 切换随访日期时，下次日期按周期联动 */
function onVisitDateChange(): void {
  if (currentItem.value && visitForm.visitDate) {
    visitForm.nextDate = addMonths(visitForm.visitDate, currentItem.value.cycleMonths || 6)
  }
}

async function submitVisit(): Promise<void> {
  if (!currentItem.value) return
  if (!visitForm.visitDate) {
    ElMessage.warning('请选择随访日期')
    return
  }
  if (visitForm.mmse !== null && (visitForm.mmse < 0 || visitForm.mmse > 30)) {
    ElMessage.warning('MMSE 评分范围为 0–30')
    return
  }
  if (visitForm.moca !== null && (visitForm.moca < 0 || visitForm.moca > 30)) {
    ElMessage.warning('MoCA 评分范围为 0–30')
    return
  }
  // 下次随访日期不能早于本次随访日（否则保存后该病例立即变"逾期"）；YYYY-MM-DD 可直接字典序比较
  if (visitForm.nextDate && visitForm.nextDate < visitForm.visitDate) {
    ElMessage.warning('下次随访日期不能早于本次随访日期')
    return
  }
  visitSaving.value = true
  try {
    await apiRecordVisit(currentItem.value.caseId, { ...visitForm })
    ElMessage.success('随访记录已保存，下次随访日期已自动更新')
    visitVisible.value = false
    await fetchAll()
    // 月历模式下同步刷新徽标/当日任务/逾期条，避免切走再切回才更新
    if (viewMode.value === 'calendar') void fetchCalendarItems()
  } finally {
    visitSaving.value = false
  }
}

// ---------- 随访历史抽屉 ----------
const historyVisible = ref(false)
const historyLoading = ref(false)
const historyItem = ref<FollowUpWorkItem | null>(null)
const visits = ref<FollowUpVisit[]>([])

async function openHistory(item: FollowUpWorkItem): Promise<void> {
  historyItem.value = item
  historyVisible.value = true
  historyLoading.value = true
  try {
    visits.value = await apiListVisits(item.caseId)
  } finally {
    historyLoading.value = false
  }
}

/** 历史时间线（正序：早→晚） */
const visitsAsc = computed(() => [...visits.value].sort((a, b) => a.visitDate.localeCompare(b.visitDate)))

/** MMSE 基线与最新分差 */
const mmseDelta = computed<{ delta: number | null; baseline: number | null }>(() => {
  const scored = visitsAsc.value.filter((v) => v.mmse !== null)
  if (scored.length === 0) return { delta: null, baseline: null }
  const baseline = scored[0].mmse
  const latest = scored[scored.length - 1].mmse
  return { delta: latest! - baseline!, baseline }
})

/** MoCA 基线与最新分差 */
const mocaDelta = computed<{ delta: number | null }>(() => {
  const scored = visitsAsc.value.filter((v) => v.moca !== null)
  if (scored.length === 0) return { delta: null }
  return { delta: scored[scored.length - 1].moca! - scored[0].moca! }
})

/** MMSE 年化下降速率（分/年）：≥3 分/年提示进展加速 */
const mmseDeclineRate = computed<number | null>(() => {
  const scored = visitsAsc.value.filter((v) => v.mmse !== null)
  if (scored.length < 2) return null
  const first = scored[0], last = scored[scored.length - 1]
  const d0 = new Date(first.visitDate).getTime()
  const d1 = new Date(last.visitDate).getTime()
  const years = (d1 - d0) / (365.25 * 24 * 3600 * 1000)
  if (years <= 0) return null
  return (last.mmse! - first.mmse!) / years
})

// ---------- 认知趋势图（MMSE + MoCA 双线 ECharts） ----------
const trendChartRef = ref<HTMLDivElement | null>(null)
let trendChart: echarts.ECharts | null = null

const trendScored = computed(() =>
  visitsAsc.value.filter((v) => v.mmse !== null || v.moca !== null)
)

function renderTrendChart() {
  if (!trendChartRef.value) return
  if (!trendChart) trendChart = echarts.init(trendChartRef.value)
  const data = trendScored.value
  const mmseData = data.map((v) => (v.mmse !== null ? [v.visitDate, v.mmse] : [v.visitDate, null]))
  const mocaData = data.map((v) => (v.moca !== null ? [v.visitDate, v.moca] : [v.visitDate, null]))
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['MMSE', 'MoCA'], bottom: 0, textStyle: { fontSize: 11 } },
    grid: { left: 38, right: 16, top: 16, bottom: 40 },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: data.map((v) => v.visitDate.slice(5)),
      axisLabel: { fontSize: 10, color: '#93A1AF' }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 30,
      splitLine: { lineStyle: { color: '#EEF2F6' } },
      axisLabel: { fontSize: 10, color: '#93A1AF' }
    },
    series: [
      {
        name: 'MMSE',
        type: 'line',
        data: mmseData.map((d) => d[1]),
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        lineStyle: { width: 2.5, color: '#2F6DA3' },
        itemStyle: { color: '#2F6DA3' },
        connectNulls: true
      },
      {
        name: 'MoCA',
        type: 'line',
        data: mocaData.map((d) => d[1]),
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        lineStyle: { width: 2.5, color: '#7B61C4' },
        itemStyle: { color: '#7B61C4' },
        connectNulls: true
      }
    ]
  })
  trendChart.resize()
}

watch(
  () => [historyVisible.value, trendScored.value.length],
  async () => {
    if (historyVisible.value && trendScored.value.length >= 2) {
      await nextTick()
      renderTrendChart()
    }
  },
  { immediate: false }
)

const CHANGE_COLOR: Record<string, string> = {
  改善: '#2E9E6B',
  稳定: '#2F6DA3',
  轻度下降: '#D99A2B',
  明显下降: '#C94F4F'
}

function goCaseAnalysis(item: FollowUpWorkItem): void {
  router.push(`/analysis/${item.caseId}`)
}

// ---------- 批量随访计划生成 ----------
const batchVisible = ref(false)
const batchLoading = ref(false)
const batchScope = ref<BatchScope>('missing')
const batchPreview = ref<BatchFollowUpPreview | null>(null)
const batchGenerating = ref(false)

const BATCH_SCOPES: { key: BatchScope; label: string; desc: string }[] = [
  { key: 'missing', label: '缺失计划', desc: '仅无随访计划的病例' },
  { key: 'urgent', label: 'AD 病例', desc: 'AD 早/中晚期（3 月周期）' },
  { key: 'mci', label: 'MCI 病例', desc: '轻度认知障碍（6 月周期）' },
  { key: 'low', label: '低风险病例', desc: '低风险（12 月周期）' },
  { key: 'all', label: '全部病例', desc: '所有未删除病例' }
]

const RISK_LABEL_MAP: Record<string, string> = {
  low: '低风险', mci: 'MCI', 'ad-early': 'AD 早期', 'ad-late': 'AD 中晚期'
}

async function openBatchDialog(): Promise<void> {
  batchVisible.value = true
  await fetchBatchPreview()
}

async function fetchBatchPreview(): Promise<void> {
  batchLoading.value = true
  try {
    batchPreview.value = await apiBatchPreview(batchScope.value)
  } catch {
    batchPreview.value = null
  } finally {
    batchLoading.value = false
  }
}

function changeBatchScope(s: BatchScope): void {
  batchScope.value = s
  void fetchBatchPreview()
}

async function confirmBatchGenerate(): Promise<void> {
  if (!batchPreview.value || batchPreview.value.total === 0) {
    ElMessage.warning('没有可生成的病例')
    return
  }
  batchGenerating.value = true
  try {
    const res: BatchGenerateResult = await apiBatchGenerate({ scope: batchScope.value })
    ElMessage.success(res ? `批量生成完成：新建 ${res.created} 个，更新 ${res.updated} 个` : '生成完成')
    batchVisible.value = false
    await fetchAll()
    // 月历模式下同步刷新（批量生成会改变多个病例的下次随访日）
    if (viewMode.value === 'calendar') void fetchCalendarItems()
  } catch {
    ElMessage.error('批量生成失败，请稍后重试')
  } finally {
    batchGenerating.value = false
  }
}

onMounted(() => {
  fetchAll()
})

// 离开页面时释放 ECharts 实例与 canvas 监听（本页唯一裸用 echarts.init，未走 ChartBase）
onBeforeUnmount(() => {
  trendChart?.dispose()
  trendChart = null
})
</script>

<template>
  <div class="page-wrap">
    <!-- ==================== 汇总卡片 ==================== -->
    <div class="grid grid-cols-4 gap-4">
      <div
        v-for="(card, i) in summaryCards"
        :key="card.key"
        class="card-ad anim-fade-up p-5 cursor-pointer relative overflow-hidden transition-transform hover:-translate-y-0.5"
        :style="{ '--anim-delay': `${i * 70}ms` }"
        :class="scope === card.key ? 'ring-2 ring-primary/50' : ''"
        @click="changeScope(card.key)"
      >
        <div
          class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none"
          :style="{ background: card.color }"
        />
        <div class="flex items-start justify-between relative">
          <div>
            <div class="text-[13px] text-sub">{{ card.label }}</div>
            <div class="mt-2 font-num text-[30px] leading-none font-semibold" :style="{ color: card.color }">
              {{ card.value }}<span class="text-[12px] text-hint font-sans font-normal ml-1">{{ card.unit }}</span>
            </div>
          </div>
          <div
            class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm"
            :style="{ background: card.bg, color: card.color }"
          >
            <el-icon :size="20"><component :is="card.icon" /></el-icon>
          </div>
        </div>
        <div class="mt-3 text-[11px] text-hint">{{ card.hint }}</div>
      </div>
    </div>

    <!-- ==================== 工作台 ==================== -->
    <div class="card-ad mt-4">
      <div class="card-ad__header">
        <div class="flex items-center gap-3">
          <span class="card-ad__title">随访工作台</span>
          <el-radio-group v-model="viewMode" size="small" @change="switchView(viewMode)">
            <el-radio-button value="list">列表视图</el-radio-button>
            <el-radio-button value="calendar">月历视图</el-radio-button>
          </el-radio-group>
          <el-radio-group v-if="viewMode === 'list'" v-model="scope" size="small" @change="fetchList()">
            <el-radio-button
              v-for="t in SCOPE_TABS"
              :key="t.key"
              :value="t.key"
            >{{ t.label }}</el-radio-button>
          </el-radio-group>
        </div>
        <div class="flex items-center gap-2">
          <el-input
            v-if="viewMode === 'list'"
            v-model="keyword"
            placeholder="搜索病例编号 / 患者姓名"
            clearable
            class="!w-64"
            :prefix-icon="'Search'"
            @keyup.enter="onSearch"
            @clear="onSearch"
          />
          <el-button type="primary" plain :icon="'Operation'" @click="openBatchDialog">批量生成计划</el-button>
        </div>
      </div>

      <!-- ========== 列表视图 ========== -->
      <el-table v-if="viewMode === 'list'" :data="list" v-loading="loading" class="w-full" size="default">
        <el-table-column label="病例编号" width="112">
          <template #default="{ row }">
            <el-link type="primary" underline="never" class="font-num text-[13px]" @click="goCaseAnalysis(row)">{{ row.caseId }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="患者" min-width="120">
          <template #default="{ row }">
            <div class="text-[13px] text-ink">{{ row.patientName || '—' }}</div>
            <div class="text-[11px] text-hint">{{ row.gender }} · {{ row.age ?? '—' }} 岁 · {{ row.department || '未填科室' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="AI 风险" width="104" align="center">
          <template #default="{ row }">
            <RiskLevelTag :risk-level="(row.riskLevel as RiskLevel) ?? null" />
          </template>
        </el-table-column>
        <el-table-column label="下次随访" width="150">
          <template #default="{ row }">
            <div class="font-num text-[13px] text-ink">{{ row.nextDate || '未排期' }}</div>
            <el-tag :type="dueTag(row).type" size="small" effect="light" round class="mt-0.5">{{ dueTag(row).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="周期" width="80" align="center">
          <template #default="{ row }">
            <span class="text-[12px] text-sub">{{ row.cycleMonths }} 个月</span>
          </template>
        </el-table-column>
        <el-table-column label="上次随访" min-width="200">
          <template #default="{ row }">
            <template v-if="row.lastVisit">
              <div class="text-[12px] text-ink font-num">{{ row.lastVisit.visitDate }} · {{ row.lastVisit.visitType }}</div>
              <div class="text-[11px] mt-0.5 flex items-center gap-1.5">
                <span v-if="row.lastVisit.mmse !== null" class="text-sub">MMSE <b class="font-num">{{ row.lastVisit.mmse }}</b></span>
                <span v-if="row.lastVisit.moca !== null" class="text-sub">MoCA <b class="font-num">{{ row.lastVisit.moca }}</b></span>
                <el-tag size="small" effect="plain" :style="{ color: CHANGE_COLOR[row.lastVisit.cognitionChange], borderColor: CHANGE_COLOR[row.lastVisit.cognitionChange] + '66' }">
                  {{ row.lastVisit.cognitionChange }}
                </el-tag>
              </div>
            </template>
            <span v-else class="text-[12px] text-hint">尚无随访记录</span>
          </template>
        </el-table-column>
        <el-table-column label="随访次数" width="84" align="center">
          <template #default="{ row }">
            <span class="font-num text-[13px] text-sub">{{ row.visitCount }} 次</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" size="small" :icon="'EditPen'" @click="openVisit(row)">记录随访</el-button>
            <el-button size="small" :icon="'Clock'" @click="openHistory(row)">历史</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="当前筛选条件下暂无随访病例" :image-size="80" />
        </template>
      </el-table>

      <!-- ========== 月历视图 ========== -->
      <div v-if="viewMode === 'calendar'" v-loading="calendarLoading" class="p-4">
        <!-- 逾期提醒条 -->
        <el-alert
          v-if="overdueItems.length > 0"
          class="mb-3"
          type="error"
          :closable="false"
          show-icon
        >
          <template #title>
            <span class="text-[13px]">
              当前有 <b>{{ overdueItems.length }}</b> 例随访已逾期，最久逾期
              <b class="font-num">{{ Math.abs(overdueItems[0].daysDiff ?? 0) }}</b> 天，请优先安排
              <el-button type="danger" size="small" text @click="viewMode = 'list'; scope = 'overdue'; fetchList()">
                去处理 »
              </el-button>
            </span>
          </template>
        </el-alert>

        <el-calendar v-model="calDate" class="follow-calendar">
          <template #date-cell="{ data }">
            <div
              class="cal-day-cell"
              :class="[
                dayClass(data.day),
                data.day === todayStr() ? 'is-today' : '',
                data.type !== 'current-month' ? 'is-other-month' : ''
              ]"
              @click="selectDay(data.day)"
            >
              <div class="cal-day-num">{{ Number(data.day.slice(8)) }}</div>
              <div v-if="calendarMap.get(data.day)" class="cal-day-tags">
                <el-tag
                  v-for="(it, i) in calendarMap.get(data.day)!.slice(0, 3)"
                  :key="it.caseId"
                  size="small"
                  :type="(it.daysDiff ?? 0) < 0 ? 'danger' : 'primary'"
                  effect="light"
                  class="!mx-0 !block !mb-0.5 !w-full !overflow-hidden !text-ellipsis !whitespace-nowrap"
                >
                  {{ it.patientName }}
                </el-tag>
                <div v-if="(calendarMap.get(data.day)?.length ?? 0) > 3" class="text-[11px] text-hint text-center">
                  +{{ (calendarMap.get(data.day)?.length ?? 0) - 3 }} 例
                </div>
              </div>
            </div>
          </template>
        </el-calendar>
      </div>
    </div>

    <!-- ==================== 当日随访任务弹窗 ==================== -->
    <el-dialog
      v-model="dayDialogVisible"
      :title="`${selectedDay} 随访任务（${selectedDayItems.length} 例）`"
      width="560px"
      append-to-body
    >
      <div class="space-y-2">
        <div
          v-for="it in selectedDayItems"
          :key="it.caseId"
          class="flex items-center justify-between px-3 py-2.5 rounded-card border border-line"
        >
          <div class="min-w-0">
            <div class="text-[13px] text-ink font-medium">
              {{ it.patientName }}
              <span class="ml-1.5 text-hint font-normal font-num text-[12px]">{{ it.caseId }}</span>
            </div>
            <div class="text-[12px] text-hint mt-0.5">
              {{ it.department }} · 随访第 {{ it.visitCount + 1 }} 次
              <span v-if="it.riskScore !== null" class="ml-1.5 font-num">AI {{ it.riskScore }} 分</span>
            </div>
          </div>
          <div class="shrink-0 flex gap-1.5">
            <el-button type="primary" size="small" :icon="'EditPen'" @click="dayDialogVisible = false; openVisit(it)">记录随访</el-button>
            <el-button size="small" :icon="'Clock'" @click="dayDialogVisible = false; openHistory(it)">历史</el-button>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- ==================== 记录随访弹窗 ==================== -->
    <el-dialog
      v-model="visitVisible"
      :title="`记录随访 · ${currentItem?.patientName ?? ''}（${currentItem?.caseId ?? ''}）`"
      width="640px"
      append-to-body
      :close-on-click-modal="false"
    >
      <el-form :model="visitForm" label-width="92px" class="pr-2">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="随访日期" required>
              <el-date-picker
                v-model="visitForm.visitDate"
                type="date"
                value-format="YYYY-MM-DD"
                class="!w-full"
                :clearable="false"
                @change="onVisitDateChange"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="随访方式">
              <el-select v-model="visitForm.visitType" class="!w-full">
                <el-option v-for="t in VISIT_TYPES" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="MMSE 评分">
              <el-input-number v-model="visitForm.mmse" :min="0" :max="30" :precision="1" :controls="false" class="!w-full" placeholder="0–30" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="MoCA 评分">
              <el-input-number v-model="visitForm.moca" :min="0" :max="30" :precision="1" :controls="false" class="!w-full" placeholder="0–30" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="认知变化">
              <el-select v-model="visitForm.cognitionChange" class="!w-full">
                <el-option v-for="t in CHANGE_OPTS" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="用药依从性">
              <el-select v-model="visitForm.medicationAdherence" class="!w-full">
                <el-option v-for="t in ADHERENCE_OPTS" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="不良反应">
              <el-radio-group v-model="visitForm.adverseEvent">
                <el-radio v-for="t in ADVERSE_OPTS" :key="t" :value="t">{{ t }}</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="下次随访">
              <el-date-picker
                v-model="visitForm.nextDate"
                type="date"
                value-format="YYYY-MM-DD"
                class="!w-full"
              />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="随访备注">
              <el-input
                v-model="visitForm.notes"
                type="textarea"
                :rows="3"
                placeholder="症状变化、家属反馈、检查检验结果、处置意见等"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <div class="text-[11px] text-hint -mt-2 ml-[92px]">
          参考界值：MMSE 文盲 &gt;17 / 小学 &gt;20 / 中学及以上 &gt;24；MoCA ≥26 正常（受教育年限≤12 年加 1 分）
        </div>
      </el-form>
      <template #footer>
        <el-button @click="visitVisible = false">取消</el-button>
        <el-button type="primary" :loading="visitSaving" @click="submitVisit">保存随访记录</el-button>
      </template>
    </el-dialog>

    <!-- ==================== 随访历史抽屉 ==================== -->
    <el-drawer
      v-model="historyVisible"
      :title="`随访历史 · ${historyItem?.patientName ?? ''}（${historyItem?.caseId ?? ''}）`"
      size="520px"
      append-to-body
    >
      <div v-loading="historyLoading" class="px-2">
        <!-- 认知趋势图（MMSE + MoCA 双线） -->
        <div
          v-if="trendScored.length >= 2"
          class="rounded-card border border-line p-4 mb-5 bg-[#FAFCFE]"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-[13px] font-medium text-ink">认知评分变化趋势</span>
            <span class="flex items-center gap-3 text-[12px] font-num">
              <span
                v-if="mmseDelta.delta !== null"
                :style="{ color: mmseDelta.delta >= 0 ? '#2E9E6B' : mmseDelta.delta <= -3 ? '#C94F4F' : '#D99A2B' }"
              >
                MMSE 较基线 {{ mmseDelta.delta >= 0 ? '+' : '' }}{{ mmseDelta.delta.toFixed(1) }}
              </span>
              <span
                v-if="mocaDelta.delta !== null"
                :style="{ color: mocaDelta.delta >= 0 ? '#2E9E6B' : mocaDelta.delta <= -3 ? '#C94F4F' : '#D99A2B' }"
              >
                MoCA {{ mocaDelta.delta >= 0 ? '+' : '' }}{{ mocaDelta.delta.toFixed(1) }}
              </span>
              <span
                v-if="mmseDeclineRate !== null"
                :style="{ color: mmseDeclineRate >= -2 ? '#2E9E6B' : mmseDeclineRate <= -3 ? '#C94F4F' : '#D99A2B' }"
              >
                年化 {{ mmseDeclineRate >= 0 ? '+' : '' }}{{ mmseDeclineRate.toFixed(1) }}/年
              </span>
            </span>
          </div>
          <div ref="trendChartRef" class="w-full h-[180px]" />
          <div class="text-[11px] text-hint mt-1">MMSE/MoCA 满分 30；年化下降 ≥3 分提示认知进展加速，建议结合影像复查与临床综合评估</div>
        </div>

        <!-- 时间线 -->
        <el-timeline v-if="visits.length > 0">
          <el-timeline-item
            v-for="v in visits"
            :key="v.id"
            :timestamp="`${v.visitDate} · ${v.visitType}　随访人：${v.operator || '—'}`"
            placement="top"
            :color="CHANGE_COLOR[v.cognitionChange] || '#2F6DA3'"
          >
            <div class="rounded-card border border-line p-3">
              <div class="flex flex-wrap items-center gap-2 mb-2">
                <el-tag size="small" effect="plain" :style="{ color: CHANGE_COLOR[v.cognitionChange], borderColor: CHANGE_COLOR[v.cognitionChange] + '66' }">
                  认知{{ v.cognitionChange }}
                </el-tag>
                <el-tag size="small" type="info" effect="plain">用药{{ v.medicationAdherence }}</el-tag>
                <el-tag size="small" :type="v.adverseEvent === '无' ? 'success' : v.adverseEvent === '轻微' ? 'warning' : 'danger'" effect="plain">
                  不良反应{{ v.adverseEvent }}
                </el-tag>
              </div>
              <div class="flex gap-4 text-[12px] text-sub mb-1">
                <span v-if="v.mmse !== null">MMSE：<b class="font-num text-ink">{{ v.mmse }}</b></span>
                <span v-if="v.moca !== null">MoCA：<b class="font-num text-ink">{{ v.moca }}</b></span>
                <span v-if="v.nextDate">下次：<b class="font-num text-ink">{{ v.nextDate }}</b></span>
              </div>
              <p v-if="v.notes" class="text-[12px] text-sub leading-5 m-0">{{ v.notes }}</p>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else-if="!historyLoading" description="该病例暂无随访记录" :image-size="80" />
      </div>
    </el-drawer>

    <!-- ==================== 批量随访计划生成弹窗 ==================== -->
    <el-dialog
      v-model="batchVisible"
      title="批量生成随访计划"
      width="860px"
      :close-on-click-modal="false"
    >
      <!-- 范围选择 -->
      <div class="mb-4">
        <div class="text-[13px] text-sub mb-2">选择生成范围（按风险等级自动匹配随访周期）</div>
        <div class="flex flex-wrap gap-2">
          <el-radio-button
            v-for="s in BATCH_SCOPES"
            :key="s.key"
            :value="s.key"
            @click="changeBatchScope(s.key)"
          >{{ s.label }}</el-radio-button>
        </div>
        <div class="text-[11px] text-hint mt-1.5">
          {{ BATCH_SCOPES.find((s) => s.key === batchScope)?.desc }}
          · 周期规则：AD=3月 / MCI=6月 / 低风险=12月
        </div>
      </div>

      <!-- 预览统计 -->
      <div v-if="batchPreview" class="flex gap-3 mb-3">
        <div class="flex-1 card-ad !p-3 text-center">
          <div class="text-[11px] text-hint">待生成</div>
          <div class="font-num text-[20px] text-primary font-semibold mt-0.5">{{ batchPreview.total }}</div>
        </div>
        <div class="flex-1 card-ad !p-3 text-center">
          <div class="text-[11px] text-hint">新建</div>
          <div class="font-num text-[20px] text-[#2E9E6B] font-semibold mt-0.5">{{ batchPreview.newCount }}</div>
        </div>
        <div class="flex-1 card-ad !p-3 text-center">
          <div class="text-[11px] text-hint">更新</div>
          <div class="font-num text-[20px] text-[#D99A2B] font-semibold mt-0.5">{{ batchPreview.updateCount }}</div>
        </div>
      </div>

      <!-- 预览列表 -->
      <el-table
        :data="batchPreview?.list || []"
        v-loading="batchLoading"
        max-height="320"
        size="small"
      >
        <el-table-column label="病例 ID" width="100" prop="caseId" />
        <el-table-column label="患者" min-width="100">
          <template #default="{ row }">
            <span>{{ row.patientName || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="风险等级" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ RISK_LABEL_MAP[row.riskLevel] || row.riskLevel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="AI 评分" width="80">
          <template #default="{ row }">
            <span class="font-num">{{ row.riskScore ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="随访周期" width="80">
          <template #default="{ row }">
            <span class="font-num">{{ row.cycleMonths }}</span> 月
          </template>
        </el-table-column>
        <el-table-column label="下次随访日" width="120" prop="nextDate" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.hasPlan" size="small" type="warning">更新</el-tag>
            <el-tag v-else size="small" type="success">新建</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="batchPreview && batchPreview.total === 0" description="该范围内无病例" :image-size="60" />

      <template #footer>
        <el-button @click="batchVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="batchGenerating"
          :disabled="!batchPreview || batchPreview.total === 0"
          @click="confirmBatchGenerate"
        >确认生成（{{ batchPreview?.total || 0 }} 条）</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style>
/* 随访月历单元格 */
.follow-calendar .el-calendar-table .el-calendar-day {
  height: 92px;
  padding: 4px 6px;
}
.follow-calendar .cal-day-cell {
  height: 100%;
  cursor: default;
  border-radius: 4px;
}
.follow-calendar .cal-day-cell.cal-day-has-task {
  cursor: pointer;
}
.follow-calendar .cal-day-cell.cal-day-has-task:hover {
  background: var(--ad-primary-light);
}
.follow-calendar .cal-day-cell.is-today .cal-day-num {
  display: inline-block;
  min-width: 20px;
  height: 20px;
  line-height: 20px;
  border-radius: 50%;
  background: var(--ad-primary);
  color: #fff;
  text-align: center;
}
.follow-calendar .cal-day-cell.is-other-month {
  opacity: 0.4;
}
.follow-calendar .cal-day-num {
  font-size: 12px;
  color: var(--ad-ink-2);
  font-family: 'IBM Plex Mono', Consolas, monospace;
  margin-bottom: 2px;
}
.follow-calendar .cal-day-tags {
  margin-top: 2px;
}
</style>
