<script setup lang="ts">
/**
 * 高危病例预警中心
 * ------------------------------------------------------------------
 * 自动聚合三类临床风险信号并按紧急度排序：
 * 1. 随访逾期（逾期天数：≥30 高危 / ≥7 中危 / 其余关注）
 * 2. 认知下降加速（MMSE 年化下降 ≥3 分；≥5 高危 / ≥4 中危）
 * 3. AI 风险上升（最新版较首版评分 +5；高分/大涨为高危）
 * 支持按类型、级别筛选；顶部汇总卡点击即筛选。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiWarningSummary, apiWarningList, apiHandleWarning,
  type WarningSummary, type WarningItem, type WarningType, type WarningLevel,
  type WarningHandleStatus
} from '@/api/warning'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import type { RiskLevel } from '@/types/case'

const router = useRouter()

const summary = ref<WarningSummary>({ total: 0, high: 0, medium: 0, low: 0, overdue: 0, cognition: 0, riskRise: 0, handled: 0, ignored: 0 })
const list = ref<WarningItem[]>([])
const loading = ref(false)
const type = ref<WarningType>('all')
const level = ref<WarningLevel>('')
const status = ref<WarningHandleStatus | 'all'>('open')

const TYPE_TABS: { key: WarningType; label: string }[] = [
  { key: 'all', label: '全部预警' },
  { key: 'overdue', label: '随访逾期' },
  { key: 'cognition', label: '认知下降加速' },
  { key: 'risk', label: 'AI 风险上升' }
]

async function fetchAll(): Promise<void> {
  loading.value = true
  try {
    const [s, res] = await Promise.all([
      apiWarningSummary(),
      apiWarningList(type.value, level.value, status.value)
    ])
    summary.value = s
    list.value = res.list
  } finally {
    loading.value = false
  }
}

function changeType(t: WarningType | string | number | undefined): void {
  type.value = t as WarningType
  fetchAll()
}

function changeStatus(s: WarningHandleStatus | 'all'): void {
  status.value = s
  fetchAll()
}

function changeLevel(l: WarningLevel): void {
  level.value = level.value === l ? '' : l
  fetchAll()
}

function resetLevel(): void {
  level.value = ''
  fetchAll()
}

/** 处置预警（已处理/忽略需填写处置说明，便于质控追溯） */
async function onHandle(row: WarningItem, action: 'handled' | 'ignored' | 'reopen'): Promise<void> {
  let note = ''
  if (action === 'handled' || action === 'ignored') {
    try {
      const res = await ElMessageBox.prompt(
        action === 'handled' ? '请填写处置措施（如：已电话随访并安排门诊）' : '请填写忽略原因',
        action === 'handled' ? '标记预警为已处理' : '忽略该预警',
        {
          confirmButtonText: '确认',
          cancelButtonText: '取消',
          inputType: 'textarea',
          inputPlaceholder: action === 'handled' ? '处置措施 / 随访结论…' : '忽略原因…',
          inputValidator: (v: string) => (v && v.trim().length > 0) || '处置说明不能为空'
        }
      )
      note = res.value
    } catch {
      return
    }
  }
  try {
    await apiHandleWarning(row.dedupKey, action, note)
    ElMessage.success(action === 'reopen' ? '预警已重新打开' : '处置结果已记录')
    fetchAll()
  } catch {
    /* 拦截器已提示 */
  }
}

const levelTagType: Record<string, 'danger' | 'warning' | 'info'> = {
  high: 'danger',
  medium: 'warning',
  low: 'info'
}

const typeTagType: Record<string, 'danger' | 'warning' | 'primary'> = {
  'followup-overdue': 'danger',
  'cognition-decline': 'warning',
  'risk-rise': 'primary'
}

function goAction(row: WarningItem): void {
  if (row.type === 'followup-overdue') {
    router.push({ path: '/followup', query: { keyword: row.caseId } })
  } else {
    router.push(`/analysis/${row.caseId}`)
  }
}

const levelCards = computed(() => [
  { key: '' as WarningLevel, label: '预警总数', value: summary.value.total, color: '#2F6DA3', bg: '#E8F1F8', icon: 'BellFilled' },
  { key: 'high' as WarningLevel, label: '高危', value: summary.value.high, color: '#C94F4F', bg: '#FAEDED', icon: 'WarningFilled' },
  { key: 'medium' as WarningLevel, label: '中危', value: summary.value.medium, color: '#D96B2B', bg: '#FBEFE7', icon: 'InfoFilled' },
  { key: 'low' as WarningLevel, label: '关注', value: summary.value.low, color: '#2F6DA3', bg: '#E8F1F8', icon: 'View' }
])

onMounted(fetchAll)
</script>

<template>
  <div class="page-wrap">
    <!-- 汇总卡：点击按级别筛选 -->
    <div class="grid grid-cols-4 gap-4">
      <div
        v-for="(card, i) in levelCards"
        :key="card.label"
        class="card-ad anim-fade-up p-5 cursor-pointer relative overflow-hidden transition-transform hover:-translate-y-0.5"
        :style="{ '--anim-delay': `${i * 70}ms` }"
        :class="level === card.key ? 'ring-2 ring-primary/50' : ''"
        @click="card.key === '' ? resetLevel() : changeLevel(card.key)"
      >
        <div
          class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none"
          :style="{ background: card.color }"
        />
        <div class="flex items-start justify-between relative">
          <div>
            <div class="text-[13px] text-sub">{{ card.label }}</div>
            <div class="mt-2 font-num text-[30px] leading-none font-semibold" :style="{ color: card.color }">
              {{ card.value }}<span class="text-[12px] text-hint font-sans font-normal ml-1">例</span>
            </div>
          </div>
          <div class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm" :style="{ background: card.bg, color: card.color }">
            <el-icon :size="20"><component :is="card.icon" /></el-icon>
          </div>
        </div>
      </div>
    </div>

    <!-- 主体 -->
    <div class="card-ad anim-fade-up" style="--anim-delay: 280ms">
      <div class="card-ad__header">
        <div class="flex items-center gap-3 flex-wrap">
          <span class="card-ad__title">预警病例列表</span>
          <el-radio-group v-model="type" size="small" @change="changeType(type)">
            <el-radio-button
              v-for="t in TYPE_TABS"
              :key="t.key"
              :value="t.key"
            >{{ t.label }}</el-radio-button>
          </el-radio-group>
          <el-radio-group v-model="status" size="small" @change="changeStatus(status as WarningHandleStatus | 'all')">
            <el-radio-button value="open">待处理（{{ summary.total }}）</el-radio-button>
            <el-radio-button value="handled">已处理（{{ summary.handled }}）</el-radio-button>
            <el-radio-button value="ignored">已忽略（{{ summary.ignored }}）</el-radio-button>
            <el-radio-button value="all">全部</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="level" class="flex items-center gap-2">
          <el-tag :type="levelTagType[level]" effect="light" round closable @close="resetLevel">
            {{ level === 'high' ? '高危' : level === 'medium' ? '中危' : '关注' }}
          </el-tag>
        </div>
      </div>

      <el-table :data="list" v-loading="loading" class="w-full">
        <el-table-column label="级别" width="86" align="center">
          <template #default="{ row }">
            <el-tag :type="levelTagType[row.level]" effect="dark" size="small" round>{{ row.levelText }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="预警类型" width="140">
          <template #default="{ row }">
            <el-tag :type="typeTagType[row.type]" effect="light" size="small" round>{{ row.typeLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="病例编号" width="120">
          <template #default="{ row }">
            <el-link type="primary" underline="never" class="font-num" @click="goAction(row)">{{ row.caseId }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="patientName" label="患者姓名" min-width="120" />
        <el-table-column label="AI 风险" width="170">
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <RiskLevelTag :risk-level="(row.riskLevel as RiskLevel) ?? null" />
              <span v-if="row.riskScore !== null" class="font-num text-[13px] text-sub">{{ row.riskScore }}分</span>
              <span v-else class="text-hint text-[12px]">未分析</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="预警指标" width="150" align="center">
          <template #default="{ row }">
            <span
              class="font-num text-[13px] font-medium"
              :style="{ color: row.level === 'high' ? '#C94F4F' : row.level === 'medium' ? '#D96B2B' : '#2F6DA3' }"
            >{{ row.metric }}</span>
          </template>
        </el-table-column>
        <el-table-column label="详情" min-width="280">
          <template #default="{ row }">
            <div class="text-[13px] text-sub">{{ row.detail }}</div>
            <el-popover
              v-if="row.handleStatus !== 'open' && row.handleNote"
              trigger="hover"
              placement="top-start"
              :width="280"
            >
              <template #reference>
                <div class="mt-1 text-[12px] text-hint cursor-help truncate max-w-[320px]">
                  <el-icon class="align-middle mr-0.5"><Document /></el-icon>
                  处置记录：{{ row.handleNote }}
                </div>
              </template>
              <div class="text-[12px] leading-5">
                <div class="text-sub">{{ row.handleStatus === 'handled' ? '处置措施' : '忽略原因' }}：{{ row.handleNote }}</div>
                <div class="text-hint mt-1">{{ row.handler }} · {{ row.handledAt }}</div>
              </div>
            </el-popover>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.handleStatus === 'handled'" type="success" size="small" effect="light" round>已处理</el-tag>
            <el-tag v-else-if="row.handleStatus === 'ignored'" type="info" size="small" effect="plain" round>已忽略</el-tag>
            <el-tag v-else type="danger" size="small" effect="light" round>待处理</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" size="small" text @click="goAction(row)">
              {{ row.type === 'followup-overdue' ? '去随访' : '查看分析' }}
            </el-button>
            <template v-if="row.handleStatus === 'open'">
              <el-button type="success" size="small" text @click="onHandle(row, 'handled')">已处理</el-button>
              <el-button type="info" size="small" text @click="onHandle(row, 'ignored')">忽略</el-button>
            </template>
            <el-button v-else type="warning" size="small" text @click="onHandle(row, 'reopen')">重新打开</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            :description="status === 'open' ? '当前筛选条件下暂无待处理预警' : '暂无对应状态的预警记录'"
            :image-size="90"
          />
        </template>
        </el-table>
    </div>
  </div>
</template>
