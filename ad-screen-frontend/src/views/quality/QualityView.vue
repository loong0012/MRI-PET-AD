<script setup lang="ts">
/**
 * 影像数据质控中心
 * ------------------------------------------------------------------
 * 对全库病例执行六类质控规则扫描（模态完整性 / 文件存活 / 信息完整 /
 * 重复建档 / 流转停滞 / 审核异常），按严重度分级，支持分类筛选，
 * 每条问题给出修复建议并可直达相关病例。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  apiQualitySummary, apiQualityIssues,
  type QualitySummary, type QualityIssue, type QualitySeverity
} from '@/api/quality'

const router = useRouter()
const loading = ref(false)
const summary = ref<QualitySummary | null>(null)
const issues = ref<QualityIssue[]>([])
const activeCategory = ref('')
const activeSeverity = ref<'' | QualitySeverity>('')

const SEVERITY_META: Record<QualitySeverity, { text: string; type: 'danger' | 'warning' | 'info'; color: string; bg: string }> = {
  high: { text: '高危', type: 'danger', color: '#C94F4F', bg: '#FAEDED' },
  medium: { text: '中危', type: 'warning', color: '#D96B2B', bg: '#FBEFE7' },
  low: { text: '提示', type: 'info', color: '#2F6DA3', bg: '#E8F1F8' }
}

const CATEGORY_ICONS: Record<string, string> = {
  'modality-incomplete': 'PictureRounded',
  'file-missing': 'FolderDelete',
  'info-incomplete': 'EditPen',
  'duplicate': 'CopyDocument',
  'stalled': 'Timer',
  'review-abnormal': 'Stamp'
}

async function fetchAll(): Promise<void> {
  loading.value = true
  try {
    const [s, res] = await Promise.all([
      apiQualitySummary(),
      apiQualityIssues(activeCategory.value, activeSeverity.value)
    ])
    summary.value = s
    issues.value = res.list
  } finally {
    loading.value = false
  }
}

function pickCategory(key: string): void {
  activeCategory.value = activeCategory.value === key ? '' : key
  fetchAll()
}

function pickSeverity(sev: '' | QualitySeverity): void {
  activeSeverity.value = activeSeverity.value === sev ? '' : sev
  fetchAll()
}

const healthyRate = computed(() => {
  if (!summary.value || summary.value.totalCases === 0) return 100
  return Math.round(summary.value.healthyCases / summary.value.totalCases * 100)
})

const overviewCards = computed(() => {
  const s = summary.value
  return [
    { label: '在库病例', value: s?.totalCases ?? 0, unit: '份', color: '#2F6DA3', bg: '#E8F1F8', icon: 'Notebook', sev: '' as '' | QualitySeverity },
    { label: '高危问题', value: s?.high ?? 0, unit: '项', color: '#C94F4F', bg: '#FAEDED', icon: 'WarningFilled', sev: 'high' as const },
    { label: '中危问题', value: s?.medium ?? 0, unit: '项', color: '#D96B2B', bg: '#FBEFE7', icon: 'InfoFilled', sev: 'medium' as const },
    { label: '提示问题', value: s?.low ?? 0, unit: '项', color: '#2F6DA3', bg: '#E8F1F8', icon: 'View', sev: 'low' as const }
  ]
})

function fixTarget(row: QualityIssue): { path: string; text: string } {
  if (row.category === 'review-abnormal') return { path: `/report/${row.caseId}`, text: '去审核' }
  if (row.category === 'stalled') return { path: `/analysis/${row.caseId}`, text: '去处理' }
  return { path: `/analysis/${row.caseId}`, text: '查看病例' }
}

onMounted(fetchAll)
</script>

<template>
  <div class="page-wrap">
    <!-- 顶部概览 -->
    <div class="grid grid-cols-4 gap-4 mb-4">
      <div
        v-for="(card, i) in overviewCards"
        :key="card.label"
        class="card-ad anim-fade-up p-5 cursor-pointer relative overflow-hidden transition-transform hover:-translate-y-0.5"
        :style="{ '--anim-delay': `${i * 70}ms` }"
        :class="card.sev && activeSeverity === card.sev ? 'ring-2 ring-primary/50' : ''"
        @click="card.sev ? pickSeverity(card.sev) : undefined"
      >
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" :style="{ background: card.color }" />
        <div class="flex items-start justify-between relative">
          <div>
            <div class="text-[13px] text-sub">{{ card.label }}</div>
            <div class="mt-2 font-num text-[30px] leading-none font-semibold" :style="{ color: card.color }">
              {{ card.value }}<span class="text-[12px] text-hint font-sans font-normal ml-1">{{ card.unit }}</span>
            </div>
          </div>
          <div class="w-10 h-10 rounded-lg flex items-center justify-center shadow-sm" :style="{ background: card.bg, color: card.color }">
            <el-icon :size="20"><component :is="card.icon" /></el-icon>
          </div>
        </div>
      </div>
    </div>

    <!-- 健康率 + 分类卡 -->
    <div class="card-ad anim-fade-up mb-4" style="--anim-delay: 280ms">
      <div class="card-ad__header">
        <span class="card-ad__title">质控分类</span>
        <div class="flex items-center gap-3 w-[320px]">
          <span class="text-[12px] text-hint whitespace-nowrap">病例健康率 {{ healthyRate }}%</span>
          <el-progress :percentage="healthyRate" :stroke-width="8" :show-text="false"
            :color="healthyRate >= 90 ? '#2E9E6B' : healthyRate >= 70 ? '#D99A2B' : '#C94F4F'" class="flex-1" />
        </div>
      </div>
      <div class="grid grid-cols-6 gap-3 px-5 pb-5">
        <div
          v-for="cat in summary?.categories ?? []"
          :key="cat.key"
          class="border rounded-xl px-4 py-3.5 cursor-pointer transition-all hover:shadow-sm"
          :class="activeCategory === cat.key ? 'border-primary bg-[#F4F8FC]' : 'border-line bg-white'"
          @click="pickCategory(cat.key)"
        >
          <div class="flex items-center justify-between">
            <el-icon :size="17" :class="cat.count > 0 ? 'text-[#D96B2B]' : 'text-[#2E9E6B]'">
              <component :is="CATEGORY_ICONS[cat.key] ?? 'Document'" />
            </el-icon>
            <span
              class="font-num text-[20px] font-semibold leading-none"
              :class="cat.count > 0 ? 'text-[#C94F4F]' : 'text-[#2E9E6B]'"
            >{{ cat.count }}</span>
          </div>
          <div class="mt-2 text-[12.5px] text-sub">{{ cat.label }}</div>
        </div>
      </div>
    </div>

    <!-- 问题清单 -->
    <div class="card-ad anim-fade-up" style="--anim-delay: 360ms">
      <div class="card-ad__header">
        <div class="flex items-center gap-3">
          <span class="card-ad__title">问题清单</span>
          <el-tag v-if="activeCategory" closable type="warning" effect="light" round @close="pickCategory(activeCategory)">
            {{ summary?.categories.find((c) => c.key === activeCategory)?.label }}
          </el-tag>
          <el-tag v-if="activeSeverity" closable :type="SEVERITY_META[activeSeverity].type" effect="light" round @close="pickSeverity('')">
            {{ SEVERITY_META[activeSeverity].text }}
          </el-tag>
        </div>
        <el-button text :icon="'Refresh'" @click="fetchAll">重新扫描</el-button>
      </div>
      <el-table :data="issues" v-loading="loading" class="w-full">
        <el-table-column label="严重度" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="SEVERITY_META[row.severity as QualitySeverity].type" effect="dark" size="small" round>
              {{ SEVERITY_META[row.severity as QualitySeverity].text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="问题分类" width="130">
          <template #default="{ row }">
            <el-tag effect="plain" size="small" round>{{ row.categoryLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="病例编号" width="115">
          <template #default="{ row }">
            <el-link type="primary" underline="never" class="font-num" @click="router.push(`/analysis/${row.caseId}`)">{{ row.caseId }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="patientName" label="患者" width="90" />
        <el-table-column label="问题描述" min-width="230">
          <template #default="{ row }">
            <span class="text-[13px] text-ink">{{ row.message }}</span>
          </template>
        </el-table-column>
        <el-table-column label="修复建议" min-width="240">
          <template #default="{ row }">
            <span class="text-[12.5px] text-sub">{{ row.suggestion }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" size="small" text @click="router.push(fixTarget(row as QualityIssue).path)">
              {{ fixTarget(row as QualityIssue).text }}
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="当前筛选条件下未发现质控问题" :image-size="90" />
        </template>
      </el-table>
    </div>
  </div>
</template>
