<script setup lang="ts">
/**
 * 患者档案清单（患者维度）
 * ------------------------------------------------------------------
 * 病例库以单次检查为维度，本页以"患者"为维度聚合：同一患者多次检查只占一行，
 * 点击进入患者全景档案查看纵向病程（风险趋势 / 认知量表 / 随访时间轴）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiCohortTrajectory, apiPatientList, type PatientListItem } from '@/api/patient'
import ChartBase from '@/components/ChartBase.vue'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import type { RiskLevel } from '@/types/case'
import type { EChartsOption } from 'echarts'

const router = useRouter()
const loading = ref(false)
const keyword = ref('')
const list = ref<PatientListItem[]>([])

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await apiPatientList(keyword.value.trim())
    list.value = res.list
  } finally {
    loading.value = false
  }
}

function openProfile(row: PatientListItem): void {
  router.push(`/patient/${encodeURIComponent(row.patientNo)}`)
}

function genderText(g: string): string {
  return g === 'M' ? '男' : g === 'F' ? '女' : '—'
}

/** 队列纵向轨迹对比 */
const cohortVisible = ref(false)
const cohortLoading = ref(false)
const selectedPatientNos = ref<string[]>([])
const cohortPatients = ref<{
  patientNo: string; name: string; gender: string; age: number | null
  riskTrend: { date: string; score: number }[]
  mmseTrend: { date: string; mmse: number }[]
  caseCount: number; visitCount: number
}[]>([])

const COHORT_PALETTE = ['#2F6DA3', '#C94F4F', '#2E9E6B', '#D99A2B', '#7B5EA7', '#D96B2B', '#5FA86E', '#8E6BB3']

function openCohortDialog(): void {
  // Pre-select patients that have multiple visits/versions if available
  selectedPatientNos.value = []
  cohortPatients.value = []
  cohortVisible.value = true
}

async function loadCohortTrajectory(): Promise<void> {
  if (selectedPatientNos.value.length < 2) {
    ElMessage.warning('请至少选择 2 名患者进行对比')
    return
  }
  if (selectedPatientNos.value.length > 8) {
    ElMessage.warning('最多支持 8 名患者对比')
    return
  }
  cohortLoading.value = true
  try {
    const res = await apiCohortTrajectory(selectedPatientNos.value)
    cohortPatients.value = res.patients
  } finally {
    cohortLoading.value = false
  }
}

const riskTrajectoryOption = computed<EChartsOption>(() => {
  const allDates = Array.from(
    new Set(cohortPatients.value.flatMap(p => p.riskTrend.map(t => t.date)))
  ).sort()
  const series = cohortPatients.value.map((p, i) => ({
    name: `${p.name}（${p.patientNo}）`,
    type: 'line' as const,
    smooth: true,
    symbol: 'circle',
    symbolSize: 6,
    lineStyle: { width: 2 },
    itemStyle: { color: COHORT_PALETTE[i % COHORT_PALETTE.length] },
    data: allDates.map(d => {
      const pt = p.riskTrend.find(t => t.date === d)
      return pt ? pt.score : null
    })
  }))
  return {
    color: COHORT_PALETTE,
    tooltip: { trigger: 'axis' },
    legend: {
      type: 'scroll',
      bottom: 0,
      textStyle: { fontSize: 11 }
    },
    grid: { left: 40, right: 20, top: 20, bottom: 40 },
    xAxis: {
      type: 'category',
      data: allDates,
      axisLabel: { fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      name: '风险评分',
      axisLabel: { fontSize: 11 }
    },
    series
  }
})

const mmseTrajectoryOption = computed<EChartsOption>(() => {
  const allDates = Array.from(
    new Set(cohortPatients.value.flatMap(p => p.mmseTrend.map(t => t.date)))
  ).sort()
  const series = cohortPatients.value.map((p, i) => ({
    name: `${p.name}（${p.patientNo}）`,
    type: 'line' as const,
    smooth: true,
    symbol: 'circle',
    symbolSize: 6,
    lineStyle: { width: 2 },
    itemStyle: { color: COHORT_PALETTE[i % COHORT_PALETTE.length] },
    data: allDates.map(d => {
      const pt = p.mmseTrend.find(t => t.date === d)
      return pt ? pt.mmse : null
    })
  }))
  return {
    color: COHORT_PALETTE,
    tooltip: { trigger: 'axis' },
    legend: {
      type: 'scroll',
      bottom: 0,
      textStyle: { fontSize: 11 }
    },
    grid: { left: 40, right: 20, top: 20, bottom: 40 },
    xAxis: {
      type: 'category',
      data: allDates,
      axisLabel: { fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 30,
      name: 'MMSE',
      axisLabel: { fontSize: 11 }
    },
    series
  }
})

onMounted(fetchList)
</script>

<template>
  <div class="page-wrap">
    <div class="card-ad anim-fade-up">
      <div class="card-ad__header">
        <div class="flex items-center gap-3">
          <span class="card-ad__title">患者档案</span>
          <span class="text-[12px] text-hint">按患者聚合全部检查记录，点击行查看全景病程档案</span>
        </div>
        <div class="flex items-center gap-2">
          <el-input
            v-model="keyword"
            placeholder="搜索患者编号 / 姓名"
            clearable
            class="!w-64"
            :prefix-icon="'Search'"
            @keyup.enter="fetchList"
            @clear="fetchList"
          />
          <el-button type="primary" :icon="'Search'" @click="fetchList">查询</el-button>
          <el-button type="primary" plain :icon="'TrendCharts'" @click="openCohortDialog">队列轨迹对比</el-button>
        </div>
      </div>

      <el-table
        :data="list"
        v-loading="loading"
        class="w-full cursor-pointer"
        @row-click="openProfile"
      >
        <el-table-column label="患者编号" prop="patientNo" width="160">
          <template #default="{ row }">
            <span class="font-num text-[13px] text-primary">{{ row.patientNo }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="姓名" min-width="100">
          <template #default="{ row }">
            <span class="font-medium text-ink">{{ row.name || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="性别" width="70" align="center">
          <template #default="{ row }">{{ genderText(row.gender) }}</template>
        </el-table-column>
        <el-table-column label="年龄" width="70" align="center">
          <template #default="{ row }">{{ row.age ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="department" label="首诊科室" min-width="120" />
        <el-table-column label="检查次数" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="light" round>{{ row.caseCount }} 次</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近检查" width="130">
          <template #default="{ row }">
            <span class="font-num text-[13px]">{{ (row.latestExamDate || '').slice(0, 10) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最新风险分级" min-width="180">
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <RiskLevelTag :risk-level="(row.latestRiskLevel as RiskLevel) ?? null" />
              <span v-if="row.latestRiskScore !== null" class="font-num text-[12px] text-sub">
                {{ row.latestRiskScore }} 分
              </span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" size="small" text @click.stop="openProfile(row)">全景档案</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="未找到匹配患者" :image-size="90" />
        </template>
      </el-table>
    </div>

    <el-dialog v-model="cohortVisible" title="队列纵向轨迹对比" width="900px" :close-on-click-modal="false">
      <!-- Patient selection -->
      <div class="mb-4">
        <div class="text-[13px] text-sub mb-2">选择患者（2-8 名，输入患者编号或姓名搜索）</div>
        <el-select v-model="selectedPatientNos" multiple filterable placeholder="选择患者" class="w-full" :max="8">
          <el-option v-for="p in list" :key="p.patientNo" :label="`${p.name}（${p.patientNo}）`" :value="p.patientNo" />
        </el-select>
        <div class="mt-2 flex items-center gap-2">
          <el-button type="primary" :loading="cohortLoading" :disabled="selectedPatientNos.length < 2" @click="loadCohortTrajectory">生成轨迹对比</el-button>
          <span class="text-xs text-hint">支持 2-8 名患者的 AI 风险评分与 MMSE 评分纵向叠加对比</span>
        </div>
      </div>
      <!-- Charts -->
      <div v-if="cohortPatients.length > 0" class="grid grid-cols-1 gap-4">
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">AI 风险评分变化轨迹</span>
          </div>
          <div class="p-3">
            <ChartBase :option="riskTrajectoryOption" :height="300" />
          </div>
        </div>
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">MMSE 评分变化轨迹</span>
          </div>
          <div class="p-3">
            <ChartBase :option="mmseTrajectoryOption" :height="300" :empty="cohortPatients.every(p => p.mmseTrend.length === 0)" />
          </div>
        </div>
      </div>
      <el-empty v-else-if="!cohortLoading" description="选择患者后点击「生成轨迹对比」" :image-size="80" />
    </el-dialog>
  </div>
</template>
