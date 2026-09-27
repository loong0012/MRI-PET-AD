<script setup lang="ts">
/**
 * CDSS 临床决策支持工作台
 * ------------------------------------------------------------------
 * 基于规则引擎的多维度诊疗建议：AI 评分 + 年龄 + MMSE + 认知趋势 + 依从性 → 分级诊疗
 * 1. 建议看板：4 个优先级汇总卡 + 建议列表（按优先级筛选 + 分页），点击「查看建议」
 *    弹出完整 CDSS 建议（修正因子 / 推理时间线 / 建议措施 / 转诊 / 用药 / 免责声明）
 * 2. 分布统计：优先级分布饼图 / 风险等级柱状图 / 修正因子排行横柱图
 * 3. 规则配置：规则列表，按基础规则 / 修正因子分类展示
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  apiGetRecommendation,
  apiListRecommendations,
  apiGetCdssStats,
  apiListRules
} from '@/api/cdss'
import type { CdssRecommendation, CdssStats, CdssRule, CdssModifier } from '@/types/cdss.d'
import ChartBase from '@/components/ChartBase.vue'
import DisclaimerBar from '@/components/DisclaimerBar.vue'
import type { EChartsOption } from 'echarts'

// ---------- Tab 状态 ----------
const activeTab = ref<'board' | 'stats' | 'rules'>('board')

// ---------- 优先级元数据 ----------
type PriorityKey = 'urgent' | 'high' | 'moderate' | 'low' | ''

const PRIORITY_META: Record<Exclude<PriorityKey, ''>, { name: string; color: string; bg: string; desc: string }> = {
  urgent: { name: '紧急', color: '#C94F4F', bg: '#FAEDED', desc: '需立即处置或转诊' },
  high: { name: '高优先', color: '#D97A2B', bg: '#FBEFE7', desc: '建议 24h 内干预' },
  moderate: { name: '中优先', color: '#2F6DA3', bg: '#E8F1F8', desc: '建议一周内复诊' },
  low: { name: '低优先', color: '#5B8C5A', bg: '#EDF7F2', desc: '常规随访即可' }
}

const PRIORITY_ORDER: Exclude<PriorityKey, ''>[] = ['urgent', 'high', 'moderate', 'low']

// ---------- 统计数据 ----------
const stats = ref<CdssStats | null>(null)
const statsLoading = ref(false)

async function loadStats(): Promise<void> {
  statsLoading.value = true
  try {
    stats.value = await apiGetCdssStats()
  } finally {
    statsLoading.value = false
  }
}

/** 优先级汇总卡（4 张）：数量取自 stats.priorityDist，缺省 0 */
const priorityCards = computed(() => {
  const dist = stats.value?.priorityDist ?? []
  return PRIORITY_ORDER.map((key) => {
    const meta = PRIORITY_META[key]
    const item = dist.find((d) => d.key === key)
    return {
      key,
      name: meta.name,
      value: item?.value ?? 0,
      color: meta.color,
      bg: meta.bg,
      desc: meta.desc
    }
  })
})

// ---------- 建议列表 ----------
const list = ref<CdssRecommendation[]>([])
const listLoading = ref(false)
const filterPriority = ref<PriorityKey>('')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)

async function loadList(): Promise<void> {
  listLoading.value = true
  try {
    const res = await apiListRecommendations(filterPriority.value, page.value, pageSize.value)
    list.value = res.list
    total.value = res.total
  } finally {
    listLoading.value = false
  }
}

function pickPriority(key: PriorityKey): void {
  filterPriority.value = filterPriority.value === key ? '' : key
  page.value = 1
  void loadList()
}

function onPageChange(p: number): void {
  page.value = p
  void loadList()
}

watch(filterPriority, () => {
  page.value = 1
})

// ---------- 建议详情弹窗 ----------
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<CdssRecommendation | null>(null)

async function viewRecommendation(row: CdssRecommendation): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await apiGetRecommendation(row.caseId)
  } catch {
    // 后端单条失败时回退使用列表行数据
    detail.value = row
  } finally {
    detailLoading.value = false
  }
}

/** 修正因子影响文案映射 */
const MODIFIER_IMPACT_TEXT: Record<CdssModifier['impact'], string> = {
  'level+1': '上调一级',
  watch: '重点关注',
  referral: '建议转诊'
}

/** 修正因子影响 tag 类型 */
function modifierTagType(impact: CdssModifier['impact']): 'danger' | 'warning' | 'info' {
  if (impact === 'level+1') return 'danger'
  if (impact === 'referral') return 'warning'
  return 'info'
}

/** 抗痴呆药物键 → 中文名（用于用药交互警示展示） */
function drugNameCn(key: string): string {
  const map: Record<string, string> = {
    donepezil: '胆碱酯酶抑制剂（多奈哌齐/卡巴拉汀/加兰他敏）',
    memantine: '美金刚',
  }
  return map[key] ?? key
}

/** 相互作用严重度 → el-tag 类型（severe=danger / caution=warning / monitor=info） */
function interactionTagType(severity: string): 'danger' | 'warning' | 'info' {
  if (severity === 'severe') return 'danger'
  if (severity === 'caution') return 'warning'
  return 'info'
}

/** 照护者负担风险等级 → el-tag 类型 */
function caregiverTagType(risk: string): 'danger' | 'warning' | 'info' | 'success' {
  if (risk === 'high') return 'danger'
  if (risk === 'moderate') return 'warning'
  if (risk === 'mild') return 'info'
  return 'success'
}

/** 是否存在 severe 级药物相互作用（用于卡片边框颜色区分） */
const hasSevereInteraction = computed(() => {
  const items = detail.value?.medicationInteractions?.items ?? []
  return items.some((it) => it.severity === 'severe')
})

/** 认知趋势颜色：下降越严重越红 */
function trendColor(trend: string): string {
  if (trend.includes('明显下降')) return '#C94F4F'
  if (trend.includes('轻度下降') || trend.includes('下降')) return '#D99A2B'
  if (trend.includes('改善')) return '#2E9E6B'
  return '#2F6DA3'
}

// ---------- 规则配置 ----------
const rules = ref<CdssRule[]>([])
const rulesLoading = ref(false)

async function loadRules(): Promise<void> {
  rulesLoading.value = true
  try {
    rules.value = await apiListRules()
  } finally {
    rulesLoading.value = false
  }
}

/** 规则类型 tag */
function ruleTypeMeta(type: CdssRule['type']): { label: string; type: 'primary' | 'success' } {
  return type === 'base'
    ? { label: '基础规则', type: 'primary' }
    : { label: '修正因子', type: 'success' }
}

/** 规则优先级 tag 颜色 */
function rulePriorityColor(priority: string): string {
  return PRIORITY_META[priority as Exclude<PriorityKey, ''>]?.color ?? '#5C6B7A'
}

const baseRules = computed(() => rules.value.filter((r) => r.type === 'base'))
const modifierRules = computed(() => rules.value.filter((r) => r.type === 'modifier'))

// ==================== 分布统计图表 ====================

// ---------- 优先级分布饼图 ----------
const priorityPieOption = computed<EChartsOption>(() => {
  const data = (stats.value?.priorityDist ?? []).map((d) => ({
    name: d.name,
    value: d.value,
    itemStyle: { color: d.color }
  }))
  return {
    tooltip: { trigger: 'item', formatter: '{b}：{c} 例（{d}%）' },
    legend: { orient: 'vertical', right: 16, top: 'center', textStyle: { color: '#5C6B7A', fontSize: 12 } },
    series: [{
      type: 'pie',
      radius: ['46%', '70%'],
      center: ['36%', '50%'],
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data
    }]
  } as unknown as EChartsOption
})

// ---------- 风险等级柱状图 ----------
const levelBarOption = computed<EChartsOption>(() => {
  const data = stats.value?.levelDist ?? []
  const palette = ['#2E9E6B', '#D99A2B', '#D96B2B', '#C94F4F']
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: '{b}：{c} 例' },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.name),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 11, interval: 0 }
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    series: [{
      type: 'bar',
      data: data.map((d, i) => ({
        value: d.value,
        itemStyle: {
          color: palette[i % palette.length],
          borderRadius: [4, 4, 0, 0]
        }
      })),
      barWidth: 28,
      label: { show: true, position: 'top', color: '#5C6B7A', fontSize: 11 }
    }]
  } as unknown as EChartsOption
})

// ---------- 修正因子排行横柱图 ----------
const modifierBarOption = computed<EChartsOption>(() => {
  const data = [...(stats.value?.modifierDist ?? [])].sort((a, b) => b.value - a.value)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: '{b}：{c} 次' },
    grid: { left: 8, right: 42, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    yAxis: {
      type: 'category',
      data: data.map((d) => d.name),
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisTick: { show: false },
      axisLabel: { color: '#5C6B7A', fontSize: 12 }
    },
    series: [{
      type: 'bar',
      data: data.map((d) => d.value),
      barWidth: 14,
      itemStyle: {
        color: { type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
          colorStops: [{ offset: 0, color: '#7FB0D9' }, { offset: 1, color: '#2F6DA3' }] },
        borderRadius: [0, 7, 7, 0]
      },
      label: { show: true, position: 'right', color: '#5C6B7A', fontSize: 12 }
    }]
  } as unknown as EChartsOption
})

const priorityDistEmpty = computed(() => (stats.value?.priorityDist ?? []).every((d) => d.value === 0))
const levelDistEmpty = computed(() => (stats.value?.levelDist ?? []).every((d) => d.value === 0))
const modifierDistEmpty = computed(() => (stats.value?.modifierDist ?? []).length === 0 || (stats.value?.modifierDist ?? []).every((d) => d.value === 0))

// ---------- 初始化加载 ----------
onMounted(() => {
  void loadStats()
  void loadList()
  void loadRules()
})

// 切换到统计 Tab 时若 stats 为空则补拉
watch(activeTab, (tab) => {
  if (tab === 'stats' && !stats.value) void loadStats()
  if (tab === 'rules' && rules.value.length === 0) void loadRules()
})

// 处理 ElMessage 未使用告警（保留以备扩展）
void ElMessage
</script>

<template>
  <div class="page-wrap">
    <el-tabs v-model="activeTab" class="cdss-tabs">
      <!-- ==================== Tab1 建议看板 ==================== -->
      <el-tab-pane label="建议看板" name="board">
        <!-- 优先级汇总卡 -->
        <div class="grid grid-cols-4 gap-4 mb-4">
          <div
            v-for="card in priorityCards"
            :key="card.key"
            class="card-ad p-5 cursor-pointer relative overflow-hidden transition-transform hover:-translate-y-0.5"
            :class="filterPriority === card.key ? 'ring-2 ring-primary/50' : ''"
            @click="pickPriority(card.key)"
          >
            <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" :style="{ background: card.color }" />
            <div class="flex items-start justify-between relative">
              <div>
                <div class="text-[13px] text-sub">{{ card.name }}优先级</div>
                <div class="mt-2 font-num text-[30px] leading-none font-semibold" :style="{ color: card.color }">
                  {{ card.value }}
                  <span class="text-[12px] text-hint font-sans font-normal ml-1">例</span>
                </div>
              </div>
              <div class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm" :style="{ background: card.bg, color: card.color }">
                <el-icon :size="20"><WarningFilled /></el-icon>
              </div>
            </div>
            <div class="mt-3 text-[11px] text-hint">{{ card.desc }}</div>
          </div>
        </div>

        <!-- 建议列表 -->
        <div class="card-ad">
          <div class="card-ad__header">
            <div class="flex items-center gap-3">
              <span class="card-ad__title">CDSS 建议列表</span>
              <el-tag v-if="filterPriority" closable effect="light" round :color="PRIORITY_META[filterPriority].color" @close="pickPriority(filterPriority as PriorityKey)">
                {{ PRIORITY_META[filterPriority].name }}优先级
              </el-tag>
              <span v-else class="text-xs text-hint">全部优先级</span>
            </div>
            <el-button text :icon="'Refresh'" @click="loadList">刷新</el-button>
          </div>
          <el-table :data="list" v-loading="listLoading" class="w-full" size="default">
            <el-table-column label="病例 ID" width="120">
              <template #default="{ row }">
                <span class="font-num text-[13px] text-ink">{{ row.caseId }}</span>
              </template>
            </el-table-column>
            <el-table-column label="患者" min-width="120">
              <template #default="{ row }">
                <div class="text-[13px] text-ink">{{ row.patientName || '—' }}</div>
                <div class="text-[11px] text-hint">{{ row.gender }} · {{ row.age ?? '—' }} 岁</div>
              </template>
            </el-table-column>
            <el-table-column label="年龄" width="70" align="center">
              <template #default="{ row }">
                <span class="font-num text-[13px] text-sub">{{ row.age ?? '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="AI 评分" width="100" align="center">
              <template #default="{ row }">
                <span class="font-num text-[14px] font-semibold" :style="{ color: row.riskScore >= 60 ? '#C94F4F' : row.riskScore >= 35 ? '#D99A2B' : '#2E9E6B' }">
                  {{ row.riskScore }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="MMSE" width="80" align="center">
              <template #default="{ row }">
                <span v-if="row.mmse !== null" class="font-num text-[13px] text-sub">{{ row.mmse }}</span>
                <span v-else class="text-[12px] text-hint">—</span>
              </template>
            </el-table-column>
            <el-table-column label="认知趋势" width="130">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :style="{ color: trendColor(row.cognitionTrend), borderColor: trendColor(row.cognitionTrend) + '66' }">
                  {{ row.cognitionTrend || '—' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="优先级" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="dark" round :color="row.priorityColor" :style="{ background: row.priorityColor, borderColor: row.priorityColor }">
                  {{ row.priorityName }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="风险等级" width="110">
              <template #default="{ row }">
                <span class="text-[13px] text-ink">{{ row.levelName || row.level || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button type="primary" size="small" :icon="'View'" @click="viewRecommendation(row as CdssRecommendation)">查看建议</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="当前筛选条件下暂无 CDSS 建议" :image-size="80" />
            </template>
          </el-table>
          <div class="flex justify-end px-5 py-3 border-t border-line">
            <el-pagination
              :current-page="page"
              :page-size="pageSize"
              :total="total"
              :page-sizes="[10, 20, 50]"
              layout="total, sizes, prev, pager, next, jumper"
              background
              @current-change="onPageChange"
              @size-change="(s: number) => { pageSize = s; page = 1; void loadList() }"
            />
          </div>
        </div>
      </el-tab-pane>

      <!-- ==================== Tab2 分布统计 ==================== -->
      <el-tab-pane label="分布统计" name="stats">
        <div v-loading="statsLoading" class="space-y-4">
          <!-- 概览总数 -->
          <div class="card-ad p-5 flex items-center justify-between">
            <div>
              <div class="text-[13px] text-sub">CDSS 建议总数</div>
              <div class="mt-1 font-num text-[28px] font-semibold text-primary">
                {{ stats?.total ?? 0 }}
                <span class="text-[12px] text-hint font-sans font-normal ml-1">例</span>
              </div>
            </div>
            <div class="text-[12px] text-hint text-right max-w-[60%]">
              基于规则引擎聚合：AI 评分 + 年龄 + MMSE + 认知趋势 + 依从性，输出四级优先级与修正因子分布
            </div>
          </div>

          <div class="grid grid-cols-2 gap-4">
            <div class="card-ad">
              <div class="card-ad__header">
                <span class="card-ad__title">优先级分布</span>
                <span class="text-xs text-hint">环形饼图</span>
              </div>
              <div class="p-4">
                <ChartBase :option="priorityPieOption" :height="300" :empty="priorityDistEmpty" />
              </div>
            </div>
            <div class="card-ad">
              <div class="card-ad__header">
                <span class="card-ad__title">风险等级分布</span>
                <span class="text-xs text-hint">柱状图</span>
              </div>
              <div class="p-4">
                <ChartBase :option="levelBarOption" :height="300" :empty="levelDistEmpty" />
              </div>
            </div>
          </div>

          <div class="card-ad">
            <div class="card-ad__header">
              <span class="card-ad__title">修正因子触发排行</span>
              <span class="text-xs text-hint">横向柱状图 · 按触发次数倒序</span>
            </div>
            <div class="p-4">
              <ChartBase :option="modifierBarOption" :height="320" :empty="modifierDistEmpty" />
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- ==================== Tab3 规则配置 ==================== -->
      <el-tab-pane label="规则配置" name="rules">
        <div v-loading="rulesLoading" class="space-y-4">
          <!-- 基础规则 -->
          <div>
            <div class="flex items-center gap-2 mb-3">
              <el-tag type="primary" effect="dark" size="default" round>基础规则</el-tag>
              <span class="text-[12px] text-hint">基于 AI 评分与临床指标的分级判定</span>
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div
                v-for="rule in baseRules"
                :key="rule.id"
                class="card-ad p-4 hover:shadow-card transition-shadow"
              >
                <div class="flex items-start justify-between mb-2">
                  <span class="text-[14px] font-semibold text-ink">{{ rule.name }}</span>
                  <el-tag size="small" effect="plain" :color="rulePriorityColor(rule.priority)" :style="{ color: rulePriorityColor(rule.priority), borderColor: rulePriorityColor(rule.priority) + '66' }">
                    {{ PRIORITY_META[rule.priority as Exclude<PriorityKey, ''>]?.name ?? rule.priority }}
                  </el-tag>
                </div>
                <p class="text-[12.5px] text-sub leading-5 m-0">{{ rule.desc }}</p>
              </div>
              <el-empty v-if="baseRules.length === 0 && !rulesLoading" description="暂无基础规则" :image-size="70" />
            </div>
          </div>

          <!-- 修正因子规则 -->
          <div>
            <div class="flex items-center gap-2 mb-3 mt-4">
              <el-tag type="success" effect="dark" size="default" round>修正因子</el-tag>
              <span class="text-[12px] text-hint">在基础规则之上叠加的临床调整项</span>
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div
                v-for="rule in modifierRules"
                :key="rule.id"
                class="card-ad p-4 hover:shadow-card transition-shadow"
              >
                <div class="flex items-start justify-between mb-2">
                  <span class="text-[14px] font-semibold text-ink">{{ rule.name }}</span>
                  <el-tag size="small" effect="plain" :color="rulePriorityColor(rule.priority)" :style="{ color: rulePriorityColor(rule.priority), borderColor: rulePriorityColor(rule.priority) + '66' }">
                    {{ PRIORITY_META[rule.priority as Exclude<PriorityKey, ''>]?.name ?? rule.priority }}
                  </el-tag>
                </div>
                <p class="text-[12.5px] text-sub leading-5 m-0">{{ rule.desc }}</p>
              </div>
              <el-empty v-if="modifierRules.length === 0 && !rulesLoading" description="暂无修正因子规则" :image-size="70" />
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- ==================== 建议详情弹窗 ==================== -->
    <el-dialog
      v-model="detailVisible"
      :title="`CDSS 建议 · ${detail?.patientName ?? ''}（${detail?.caseId ?? ''}）`"
      width="720px"
      append-to-body
      :close-on-click-modal="false"
    >
      <div v-loading="detailLoading">
        <template v-if="detail">
          <!-- 优先级 + 风险等级头部 -->
          <div class="flex items-center gap-3 flex-wrap mb-4 px-1">
            <el-tag size="default" effect="dark" round :color="detail.priorityColor" :style="{ background: detail.priorityColor, borderColor: detail.priorityColor }">
              {{ detail.priorityName }}优先级
            </el-tag>
            <el-tag size="default" effect="plain" type="info">{{ detail.levelName || detail.level }}</el-tag>
            <span class="text-[12px] text-hint">{{ detail.priorityDesc }}</span>
            <div class="flex-1" />
            <span class="text-[12px] text-hint font-num">{{ detail.generatedAt }}</span>
          </div>

          <!-- 关键指标 -->
          <div class="grid grid-cols-4 gap-3 mb-4">
            <div class="rounded-card border border-line px-3 py-2.5 bg-page">
              <div class="text-[11px] text-hint">AI 风险评分</div>
              <div class="mt-0.5 font-num text-[18px] font-semibold" :style="{ color: detail.riskScore >= 60 ? '#C94F4F' : detail.riskScore >= 35 ? '#D99A2B' : '#2E9E6B' }">
                {{ detail.riskScore }}
              </div>
            </div>
            <div class="rounded-card border border-line px-3 py-2.5 bg-page">
              <div class="text-[11px] text-hint">MMSE</div>
              <div class="mt-0.5 font-num text-[18px] font-semibold text-sub">
                {{ detail.mmse !== null ? detail.mmse : '—' }}
              </div>
            </div>
            <div class="rounded-card border border-line px-3 py-2.5 bg-page">
              <div class="text-[11px] text-hint">MoCA</div>
              <div class="mt-0.5 font-num text-[18px] font-semibold text-sub">
                {{ detail.moca !== null ? detail.moca : '—' }}
              </div>
            </div>
            <div class="rounded-card border border-line px-3 py-2.5 bg-page">
              <div class="text-[11px] text-hint">用药依从性</div>
              <div class="mt-0.5 text-[13px] font-medium text-ink">{{ detail.medicationAdherence || '—' }}</div>
            </div>
          </div>

          <!-- 修正因子标签 -->
          <div v-if="detail.modifiers && detail.modifiers.length > 0" class="mb-4">
            <div class="text-[13px] font-medium text-ink mb-2">修正因子</div>
            <div class="flex flex-wrap gap-2">
              <el-tag
                v-for="m in detail.modifiers"
                :key="m.key"
                size="default"
                effect="light"
                :type="modifierTagType(m.impact)"
                round
              >
                {{ m.label }} · {{ MODIFIER_IMPACT_TEXT[m.impact] }}
              </el-tag>
            </div>
          </div>

          <!-- 推理时间线 -->
          <div v-if="detail.reasoning && detail.reasoning.length > 0" class="mb-4">
            <div class="text-[13px] font-medium text-ink mb-2">推理步骤</div>
            <el-timeline>
              <el-timeline-item
                v-for="(step, idx) in detail.reasoning"
                :key="idx"
                :hollow="idx !== 0"
                :color="detail.priorityColor"
                :timestamp="`步骤 ${idx + 1}`"
                placement="top"
              >
                <div class="text-[12.5px] text-sub leading-5">{{ step }}</div>
              </el-timeline-item>
            </el-timeline>
          </div>

          <!-- 建议措施清单 -->
          <div v-if="detail.measures && detail.measures.length > 0" class="mb-4">
            <div class="text-[13px] font-medium text-ink mb-2">建议措施</div>
            <div class="rounded-card border border-line p-3 bg-page">
              <div class="flex flex-col gap-1.5">
                <div v-for="(m, idx) in detail.measures" :key="idx" class="flex items-start gap-2">
                  <el-checkbox :model-value="true" size="small" class="!pointer-events-none" />
                  <span class="text-[12.5px] text-ink leading-5">{{ m }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 转诊 / 用药 -->
          <div class="grid grid-cols-2 gap-3 mb-4">
            <div class="rounded-card border border-line p-3">
              <div class="text-[12px] text-hint mb-1">转诊建议</div>
              <div class="text-[13px] text-ink leading-5">{{ detail.referral || '—' }}</div>
            </div>
            <div class="rounded-card border border-line p-3">
              <div class="text-[12px] text-hint mb-1">用药建议</div>
              <div class="text-[13px] text-ink leading-5">{{ detail.medication || '—' }}</div>
            </div>
          </div>

          <!-- 用药交互警示（第 32 轮新增） -->
          <div
            v-if="detail.medicationInteractions && detail.medicationInteractions.hasWarnings"
            class="mb-4 rounded-card border p-3"
            :class="hasSevereInteraction ? 'border-[var(--ad-danger,#C94F4F)] bg-[var(--ad-notify-danger-bg,rgba(201,79,79,.08))]' : 'border-[var(--ad-warn,#D97A2B)] bg-[var(--ad-notify-warn-bg,rgba(217,122,43,.08))]'"
          >
            <div class="flex items-center gap-1.5 mb-2">
              <span class="text-[13px] font-medium text-ink">抗痴呆药物相互作用警示</span>
              <el-tag size="small" type="warning" effect="light" round>
                {{ detail.medicationInteractions.summary }}
              </el-tag>
            </div>
            <div v-if="detail.medicationInteractions.recommendedDrugs?.length" class="text-[12px] text-hint mb-2">
              本次推荐用药：
              <el-tag
                v-for="d in detail.medicationInteractions.recommendedDrugs"
                :key="d"
                size="small"
                type="info"
                effect="plain"
                class="!mx-1"
                round
              >
                {{ drugNameCn(d) }}
              </el-tag>
              <span
                v-if="detail.medicationInteractions.currentMedsDetected?.length"
                class="ml-2"
              >
                · 随访识别当前用药：
                <el-tag
                  v-for="d in detail.medicationInteractions.currentMedsDetected"
                  :key="`cur-${d}`"
                  size="small"
                  type="success"
                  effect="plain"
                  class="!mx-1"
                  round
                >
                  {{ drugNameCn(d) }}
                </el-tag>
              </span>
            </div>
            <div class="space-y-2">
              <div
                v-for="(it, i) in detail.medicationInteractions.items"
                :key="i"
                class="rounded-md border border-line bg-page p-2"
              >
                <div class="flex items-center gap-2 flex-wrap">
                  <el-tag
                    size="small"
                    :type="interactionTagType(it.severity)"
                    effect="dark"
                    round
                  >
                    {{ it.severityName }}
                  </el-tag>
                  <span class="text-[12.5px] font-medium text-ink">{{ drugNameCn(it.drug) }}</span>
                  <span class="text-[12px] text-hint">+</span>
                  <el-tag
                    v-for="t in it.targetKeywords"
                    :key="t"
                    size="small"
                    type="warning"
                    effect="plain"
                    round
                  >
                    {{ t }}
                  </el-tag>
                </div>
                <div class="mt-1.5 text-[12.5px] text-sub leading-5">{{ it.mechanism }}</div>
                <div class="mt-1 text-[12px] text-ink leading-5">
                  <span class="text-hint">处置：</span>{{ it.recommendation }}
                </div>
              </div>
            </div>
          </div>

          <!-- 照护者负担评估（第 32 轮新增） -->
          <div v-if="detail.caregiverAssessment" class="mb-4 rounded-card border border-line p-3 bg-page">
            <div class="flex items-center gap-2 mb-2 flex-wrap">
              <span class="text-[13px] font-medium text-ink">照护者负担评估</span>
              <el-tag
                size="small"
                :type="caregiverTagType(detail.caregiverAssessment.burdenRisk)"
                effect="light"
                round
              >
                {{ detail.caregiverAssessment.burdenRiskName }}
              </el-tag>
              <span class="text-[12px] text-hint font-num">
                Zarit 代理评分：{{ detail.caregiverAssessment.zaritProxyScore }}
                <span class="text-hint">/ 88</span>
              </span>
            </div>
            <div v-if="detail.caregiverAssessment.modifiers?.length" class="mb-2">
              <div class="text-[12px] text-hint mb-1">修正因子</div>
              <ul class="space-y-1 text-[12.5px] text-sub leading-5">
                <li
                  v-for="(m, i) in detail.caregiverAssessment.modifiers"
                  :key="i"
                  class="flex gap-2"
                >
                  <span class="text-primary shrink-0">·</span>
                  <span>{{ m }}</span>
                </li>
              </ul>
            </div>
            <div class="mb-2">
              <div class="text-[12px] text-hint mb-1">照护者支持建议</div>
              <ul class="space-y-1 text-[12.5px] text-sub leading-5">
                <li
                  v-for="(s, i) in detail.caregiverAssessment.supportRecommendations"
                  :key="i"
                  class="flex gap-2"
                >
                  <span class="text-primary shrink-0">·</span>
                  <span>{{ s }}</span>
                </li>
              </ul>
            </div>
            <div v-if="detail.caregiverAssessment.warningSigns?.length">
              <div class="text-[12px] text-hint mb-1">需立即介入的警示信号</div>
              <el-alert type="warning" :closable="false" show-icon>
                <ul class="space-y-1 text-[12px] text-sub leading-5">
                  <li
                    v-for="(w, i) in detail.caregiverAssessment.warningSigns"
                    :key="i"
                    class="flex gap-2"
                  >
                    <span class="text-[var(--ad-danger,#C94F4F)] shrink-0">!</span>
                    <span>{{ w }}</span>
                  </li>
                </ul>
              </el-alert>
            </div>
          </div>

          <!-- 免责声明 -->
          <DisclaimerBar variant="plain" />
        </template>
        <el-empty v-else-if="!detailLoading" description="暂无建议详情" :image-size="80" />
      </div>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style>
/* Tab 头部对齐页面基调 */
.cdss-tabs .el-tabs__header {
  margin-bottom: 0;
  padding: 0 4px;
}
.cdss-tabs .el-tabs__nav-wrap::after {
  height: 1px;
  background-color: var(--ad-border);
}
</style>
