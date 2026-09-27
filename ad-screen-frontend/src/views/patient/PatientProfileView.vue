<script setup lang="ts">
/**
 * 患者全景档案（Patient 360）
 * ------------------------------------------------------------------
 * 以患者为中心纵向聚合：
 * 1. 头部：人口学信息 + 检查/随访/干预汇总 + MMSE 总体变化
 * 2. AI 风险变化曲线（历次检查最新版本评分）
 * 3. MMSE / MoCA 认知量表趋势（跨全部随访）
 * 4. 历次检查病例表（直达阅片 / AI 分析 / 报告）
 * 5. 病程时间轴（建档 → AI 分析 → 干预 → 随访）
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { EChartsOption } from 'echarts'
import { apiPatientProfile, type PatientProfile, type TimelineEvent } from '@/api/patient'
import ChartBase from '@/components/ChartBase.vue'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import PrognosisPanel from '@/components/PrognosisPanel.vue'
import type { RiskLevel } from '@/types/case'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const profile = ref<PatientProfile | null>(null)

const patientNo = computed(() => String(route.params.patientNo ?? ''))

async function fetchProfile(): Promise<void> {
  loading.value = true
  try {
    profile.value = await apiPatientProfile(patientNo.value)
  } finally {
    loading.value = false
  }
}

function genderText(g: string): string {
  return g === 'M' ? '男' : g === 'F' ? '女' : '—'
}

const statusText: Record<string, string> = {
  pending: '待分析',
  analyzing: '分析中',
  completed: '已分析',
  reported: '已出报告'
}
const statusTag: Record<string, 'info' | 'warning' | 'primary' | 'success'> = {
  pending: 'info',
  analyzing: 'warning',
  completed: 'primary',
  reported: 'success'
}

// ---------- AI 风险变化 ----------
const riskOption = computed<EChartsOption>(() => {
  const trend = profile.value?.riskTrend ?? []
  return {
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => `${v} 分`,
      formatter: (params: unknown) => {
        const arr = params as Array<{ axisValue: string; data: number; dataIndex: number }>
        const p = arr[0]
        const src = trend[p.dataIndex]
        return `${p.axisValue}<br/>风险评分：<b>${p.data}</b> 分（病例 ${src?.caseId} · v${src?.version}）`
      }
    },
    grid: { left: 44, right: 20, top: 30, bottom: 34 },
    xAxis: {
      type: 'category',
      data: trend.map((t) => t.date),
      axisLine: { lineStyle: { color: '#C8D1DC' } },
      axisLabel: { color: '#7A8699', fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      splitLine: { lineStyle: { color: '#EDF1F5' } },
      axisLabel: { color: '#7A8699', fontSize: 11 }
    },
    series: [{
      name: 'AI 风险评分',
      type: 'line',
      data: trend.map((t) => t.score),
      smooth: true,
      symbol: 'circle',
      symbolSize: 8,
      lineStyle: { width: 3, color: '#2F6DA3' },
      itemStyle: { color: '#2F6DA3' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(47,109,163,0.22)' },
            { offset: 1, color: 'rgba(47,109,163,0.02)' }
          ]
        }
      },
      markLine: {
        silent: true,
        symbol: 'none',
        data: [
          { yAxis: 70, lineStyle: { color: '#C94F4F', type: 'dashed' }, label: { formatter: 'AD 高风险 70', color: '#C94F4F', fontSize: 10 } },
          { yAxis: 40, lineStyle: { color: '#D99A2B', type: 'dashed' }, label: { formatter: 'MCI 40', color: '#D99A2B', fontSize: 10 } }
        ]
      }
    }]
  }
})

// ---------- 认知量表趋势 ----------
const cognitionOption = computed<EChartsOption>(() => {
  const cog = profile.value?.cognition ?? []
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['MMSE', 'MoCA'], right: 10, top: 4, textStyle: { color: '#5A6577', fontSize: 12 } },
    grid: { left: 44, right: 20, top: 38, bottom: 34 },
    xAxis: {
      type: 'category',
      data: cog.map((t) => t.date),
      axisLine: { lineStyle: { color: '#C8D1DC' } },
      axisLabel: { color: '#7A8699', fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 30,
      splitLine: { lineStyle: { color: '#EDF1F5' } },
      axisLabel: { color: '#7A8699', fontSize: 11 }
    },
    series: [
      {
        name: 'MMSE',
        type: 'line',
        data: cog.map((t) => t.mmse),
        connectNulls: true,
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        lineStyle: { width: 2.5, color: '#2F6DA3' },
        itemStyle: { color: '#2F6DA3' }
      },
      {
        name: 'MoCA',
        type: 'line',
        data: cog.map((t) => t.moca),
        connectNulls: true,
        smooth: true,
        symbol: 'diamond',
        symbolSize: 7,
        lineStyle: { width: 2.5, color: '#2E9E6B' },
        itemStyle: { color: '#2E9E6B' }
      }
    ]
  }
})

// ---------- 时间轴配色 ----------
const TIMELINE_STYLE: Record<TimelineEvent['kind'], { color: string; icon: string }> = {
  case: { color: '#2F6DA3', icon: 'FolderAdd' },
  analysis: { color: '#D99A2B', icon: 'DataAnalysis' },
  intervention: { color: '#2E9E6B', icon: 'FirstAidKit' },
  visit: { color: '#8E6BB3', icon: 'Calendar' }
}

onMounted(fetchProfile)
</script>

<template>
  <div class="page-wrap" v-loading="loading">
    <template v-if="profile">
      <!-- ============ 患者头部 ============ -->
      <div class="card-ad overflow-hidden mb-4">
        <div class="flex items-start gap-5 p-6">
          <el-button :icon="'ArrowLeft'" plain class="shrink-0" @click="router.push('/patient')">返回档案列表</el-button>
          <div class="w-14 h-14 rounded-full bg-gradient-to-br from-[#2f6da3] to-[#4a8cc7] text-white flex items-center justify-center text-[24px] font-semibold shrink-0">
            {{ profile.patient.name ? profile.patient.name.slice(0, 1) : '?' }}
          </div>
          <div class="flex-1">
            <div class="flex items-center gap-3">
              <span class="text-[20px] font-semibold text-ink">{{ profile.patient.name || '姓名未知' }}</span>
              <el-tag size="small" effect="plain" round>{{ genderText(profile.patient.gender) }} · {{ profile.patient.age ?? '—' }} 岁</el-tag>
              <span class="text-[13px] text-hint font-num">患者编号：{{ profile.patient.patientNo }}</span>
            </div>
            <div class="mt-3 flex items-center gap-6 flex-wrap">
              <div class="text-[12px] text-hint">
                首诊 <span class="font-num text-sub">{{ profile.summary.firstExamDate }}</span>
                <span class="mx-2 text-line">|</span>
                最近检查 <span class="font-num text-sub">{{ profile.summary.latestExamDate }}</span>
              </div>
              <div v-if="profile.summary.mmseTrend !== null" class="text-[12px]">
                MMSE 累计变化：
                <span
                  class="font-num font-semibold"
                  :class="profile.summary.mmseTrend <= -3 ? 'text-[#C94F4F]' : profile.summary.mmseTrend < 0 ? 'text-[#D96B2B]' : 'text-[#2E9E6B]'"
                >{{ profile.summary.mmseTrend > 0 ? '+' : '' }}{{ profile.summary.mmseTrend }} 分</span>
              </div>
            </div>
          </div>
          <RiskLevelTag
            v-if="profile.summary.latestRiskLevel"
            :risk-level="profile.summary.latestRiskLevel as RiskLevel"
          />
        </div>
        <div class="grid grid-cols-4 border-t border-line">
          <div class="px-6 py-4 border-r border-line">
            <div class="text-[12px] text-hint">检查病例</div>
            <div class="mt-1 font-num text-[22px] font-semibold text-ink">{{ profile.summary.caseCount }}<span class="text-[12px] text-hint font-sans ml-1">份</span></div>
          </div>
          <div class="px-6 py-4 border-r border-line">
            <div class="text-[12px] text-hint">随访记录</div>
            <div class="mt-1 font-num text-[22px] font-semibold text-ink">{{ profile.summary.followUpCount }}<span class="text-[12px] text-hint font-sans ml-1">次</span></div>
          </div>
          <div class="px-6 py-4 border-r border-line">
            <div class="text-[12px] text-hint">干预方案</div>
            <div class="mt-1 font-num text-[22px] font-semibold text-ink">{{ profile.summary.interventionCount }}<span class="text-[12px] text-hint font-sans ml-1">份</span></div>
          </div>
          <div class="px-6 py-4">
            <div class="text-[12px] text-hint">最新风险评分</div>
            <div class="mt-1 font-num text-[22px] font-semibold" :style="{ color: (profile.summary.latestRiskScore ?? 0) >= 70 ? '#C94F4F' : (profile.summary.latestRiskScore ?? 0) >= 40 ? '#D99A2B' : '#2E9E6B' }">
              {{ profile.summary.latestRiskScore ?? '—' }}<span v-if="profile.summary.latestRiskScore !== null" class="text-[12px] text-hint font-sans ml-1">分</span>
            </div>
          </div>
        </div>
      </div>

      <!-- ============ 趋势图 ============ -->
      <div class="grid grid-cols-2 gap-4 mb-4">
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">AI 风险评分变化</span>
            <span class="text-[12px] text-hint">历次检查最新分析版本</span>
          </div>
          <div class="px-4 pb-2">
            <ChartBase v-if="profile.riskTrend.length >= 2" :option="riskOption" :height="280" />
            <div v-else class="h-[280px] flex items-center justify-center text-[13px] text-hint">
              {{ profile.riskTrend.length === 1 ? '仅 1 次检查有 AI 评分，暂无法绘制变化趋势' : '暂无可对比的 AI 分析记录' }}
            </div>
          </div>
        </div>
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">认知量表随访趋势</span>
            <span class="text-[12px] text-hint">MMSE / MoCA（0–30 分）</span>
          </div>
          <div class="px-4 pb-2">
            <ChartBase v-if="profile.cognition.length >= 2" :option="cognitionOption" :height="280" />
            <div v-else class="h-[280px] flex items-center justify-center text-[13px] text-hint">
              随访记录不足 2 次，暂无法绘制认知变化曲线
            </div>
          </div>
        </div>
      </div>

      <!-- ============ 预后预测（认知衰退轨迹） ============ -->
      <div class="mb-4">
        <PrognosisPanel :patient-no="patientNo" />
      </div>

      <!-- ============ 病例 + 时间轴 ============ -->
      <div class="grid gap-4" style="grid-template-columns: 1.15fr 0.85fr">
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">历次检查病例</span>
          </div>
          <el-table :data="profile.cases" class="w-full">
            <el-table-column label="病例编号" width="110">
              <template #default="{ row }">
                <el-link type="primary" underline="never" class="font-num" @click="router.push(`/analysis/${row.caseId}`)">{{ row.caseId }}</el-link>
              </template>
            </el-table-column>
            <el-table-column label="检查日期" width="100">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ (row.examDate || '').slice(0, 10) }}</span></template>
            </el-table-column>
            <el-table-column label="模态" width="90">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.modality }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="88" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="statusTag[row.status] ?? 'info'">{{ statusText[row.status] ?? row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="风险" min-width="120">
              <template #default="{ row }">
                <div class="flex items-center gap-1.5">
                  <RiskLevelTag :risk-level="(row.riskLevel as RiskLevel) ?? null" />
                  <span v-if="row.riskScore !== null" class="font-num text-[12px] text-hint">{{ row.riskScore }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" align="center">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="router.push(`/viewer/${row.caseId}`)">阅片</el-button>
                <el-button link type="primary" size="small" @click="router.push(`/analysis/${row.caseId}`)">分析</el-button>
                <el-button link type="primary" size="small" @click="router.push(`/report/${row.caseId}`)">报告</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty description="暂无病例" :image-size="70" /></template>
          </el-table>
        </div>

        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">纵向病程时间轴</span>
            <span class="text-[12px] text-hint">{{ profile.timeline.length }} 个事件</span>
          </div>
          <div class="px-5 py-4 max-h-[520px] overflow-y-auto">
            <el-timeline v-if="profile.timeline.length">
              <el-timeline-item
                v-for="(ev, i) in profile.timeline"
                :key="i"
                :timestamp="(ev.time || '').slice(0, 16)"
                placement="top"
                :color="TIMELINE_STYLE[ev.kind].color"
                :hollow="ev.kind === 'visit'"
              >
                <div class="flex items-center gap-2">
                  <el-tag size="small" effect="light" round :style="{ color: TIMELINE_STYLE[ev.kind].color, borderColor: TIMELINE_STYLE[ev.kind].color + '55' }">
                    {{ ev.kindLabel }}
                  </el-tag>
                  <span class="text-[13px] font-medium text-ink">{{ ev.title }}</span>
                </div>
                <div class="text-[12px] text-hint mt-1 leading-5">{{ ev.desc }}</div>
                <el-button link type="primary" size="small" class="!px-0" @click="router.push(`/analysis/${ev.caseId}`)">
                  {{ ev.caseId }}
                </el-button>
              </el-timeline-item>
            </el-timeline>
            <el-empty v-else description="暂无可展示的病程事件" :image-size="80" />
          </div>
        </div>
      </div>
    </template>

    <el-empty v-else-if="!loading" description="未找到该患者档案" :image-size="120" />
  </div>
</template>
