<script setup lang="ts">
/**
 * 纵向影像量化对比视图
 * ------------------------------------------------------------------
 * 以 patientNo 为入口，展示同一患者多期影像的：
 * 1. 三联并排轴位切片（默认海马层面 idx=70/128，无真实影像时占位）
 * 2. 多指标趋势折线图（HV / SUV / 皮层厚度，含阈值参考线）
 * 3. NIA-AA 纵向进展分级卡片（CN/MCI/AD-E/AD-L 彩色徽章 + 备注）
 * 4. 单期患者显示占位提示
 *
 * 后端接口：GET /case/longitudinal/{patientNo}（详见 routers/longitudinal.py）
 */
import { computed, onMounted, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { ElMessage } from 'element-plus'
import { apiGetLongitudinal, apiRecomputeLongitudinal } from '@/api/longitudinal'
import { apiSliceUrl } from '@/api/imaging'
import { useUserStore } from '@/stores/user'
import ChartBase from '@/components/ChartBase.vue'
import type { LongitudinalResult, LongitudinalTimepoint, NiaaaStage, McidSignificanceItem } from '@/types/longitudinal'

const props = defineProps<{ patientNo: string }>()
const userStore = useUserStore()

const loading = ref(false)
const result = ref<LongitudinalResult | null>(null)

/** 是否可重算（仅 admin/researcher） */
const canRecompute = computed(() => {
  const role = userStore.userInfo?.role
  return role === 'admin' || role === 'researcher'
})

/** 三联切片默认取海马层面（128³ 体数据的 70 切片） */
const MID_SLICE = 70

/** 三联切片模态选择 */
const sliceModality = ref<'MRI' | 'PET'>('MRI')

/** 三联切片 URL（取三期或最近三期） */
const sliceCards = computed(() => {
  const tps = result.value?.timepoints ?? []
  const recent = tps.slice(-3)
  return recent.map((tp: LongitudinalTimepoint, idx: number) => ({
    key: `${tp.caseId}-${idx}`,
    label: `T${tp.timepointIdx + 1} · ${tp.examDate}`,
    caseId: tp.caseId,
    src: apiSliceUrl(tp.caseId, sliceModality.value, MID_SLICE, 'axial'),
    note: tp.note ?? ''
  }))
})

// ---------- NIA-AA 分级配色 ----------
const STAGE_META: Record<NiaaaStage, { label: string; toneBg: string; toneFg: string; desc: string }> = {
  CN: { label: 'CN', toneBg: 'bg-[#E6F2EC]', toneFg: 'text-[#2E9E6B]', desc: '无明显进展' },
  MCI: { label: 'MCI', toneBg: 'bg-[#FDF4DC]', toneFg: 'text-[#D99A2B]', desc: '轻度进展' },
  'AD-E': { label: 'AD-E', toneBg: 'bg-[#FBE8DA]', toneFg: 'text-[#D96B2B]', desc: '早期 AD 进展' },
  'AD-L': { label: 'AD-L', toneBg: 'bg-[#F6D9D9]', toneFg: 'text-[#C94F4F]', desc: '晚期 AD 进展' },
  baseline: { label: '基线', toneBg: 'bg-[#E6EEF5]', toneFg: 'text-[#5A7896]', desc: '基线期，待随访' }
}
const stageMeta = computed(() => STAGE_META[result.value?.niaaaStage ?? 'baseline'])

// ---------- 变化率显示格式 ----------
function fmtRate(v: number | null | undefined, unit: string): string {
  if (v === null || v === undefined) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(3)} ${unit}`
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

// ---------- MCID 显著性配色 ----------
const SIG_TONE: Record<string, { bg: string; fg: string }> = {
  exceeded: { bg: 'bg-[#FBE8DA]', fg: 'text-[#D96B2B]' },
  stable: { bg: 'bg-[#E6F2EC]', fg: 'text-[#2E9E6B]' },
  unknown: { bg: 'bg-[#F0F2F5]', fg: 'text-[#7A8699]' },
}

function sigTone(item: McidSignificanceItem | undefined) {
  if (!item) return SIG_TONE.unknown
  return item.exceeded ? SIG_TONE.exceeded : SIG_TONE.stable
}

// ---------- 随访推荐优先级配色 ----------
const PRIORITY_META: Record<string, { label: string; bg: string; fg: string }> = {
  routine: { label: '常规', bg: 'bg-[#E6F2EC]', fg: 'text-[#2E9E6B]' },
  enhanced: { label: '加强', bg: 'bg-[#FDF4DC]', fg: 'text-[#D99A2B]' },
  urgent: { label: '紧急', bg: 'bg-[#F6D9D9]', fg: 'text-[#C94F4F]' },
}
const priorityMeta = computed(() => {
  const p = result.value?.followupRecommendation?.priority ?? 'routine'
  return PRIORITY_META[p] ?? PRIORITY_META.routine
})

// ---------- 趋势折线图 ----------
const trendOption = computed<EChartsOption>(() => {
  const tps = result.value?.timepoints ?? []
  const x = tps.map((t) => `T${t.timepointIdx + 1}·${t.examDate}`)
  const hv = tps.map((t) => (t.hippocampusVolL != null && t.hippocampusVolR != null)
    ? Number(((t.hippocampusVolL + t.hippocampusVolR) / 2).toFixed(3))
    : null)
  const suv = tps.map((t) => t.meanSuv)
  const cort = tps.map((t) => t.corticalThickness)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['海马体积均值 cm³', 'SUVr', '皮层厚度 mm'], top: 0, textStyle: { color: '#7A8699', fontSize: 11 } },
    grid: { left: 48, right: 20, top: 36, bottom: 40 },
    xAxis: { type: 'category', data: x, axisLine: { lineStyle: { color: '#C8D1DC' } }, axisLabel: { color: '#7A8699', fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '体积/SUV', splitLine: { lineStyle: { color: '#EDF1F5' } }, axisLabel: { color: '#7A8699', fontSize: 11 } },
      { type: 'value', name: '厚度 mm', splitLine: { show: false }, axisLabel: { color: '#7A8699', fontSize: 11 } }
    ],
    series: [
      { name: '海马体积均值 cm³', type: 'line', data: hv, smooth: true, symbol: 'circle', symbolSize: 8, lineStyle: { width: 3, color: '#2F6DA3' }, itemStyle: { color: '#2F6DA3' } },
      { name: 'SUVr', type: 'line', data: suv, smooth: true, symbol: 'circle', symbolSize: 8, lineStyle: { width: 3, color: '#D99A2B' }, itemStyle: { color: '#D99A2B' } },
      { name: '皮层厚度 mm', type: 'line', yAxisIndex: 1, data: cort, smooth: true, symbol: 'circle', symbolSize: 8, lineStyle: { width: 3, color: '#2E9E6B' }, itemStyle: { color: '#2E9E6B' } }
    ]
  }
})

async function fetchLongitudinal(): Promise<void> {
  if (!props.patientNo) return
  loading.value = true
  try {
    result.value = await apiGetLongitudinal(props.patientNo)
  } catch (e: unknown) {
    ElMessage.error('加载纵向数据失败：' + (e instanceof Error ? e.message : '未知错误'))
  } finally {
    loading.value = false
  }
}

async function recompute(): Promise<void> {
  if (!props.patientNo) return
  loading.value = true
  try {
    result.value = await apiRecomputeLongitudinal(props.patientNo)
    ElMessage.success('已重新计算纵向指标')
  } catch (e: unknown) {
    ElMessage.error('重算失败：' + (e instanceof Error ? e.message : '未知错误'))
  } finally {
    loading.value = false
  }
}

onMounted(fetchLongitudinal)
</script>

<template>
  <div v-loading="loading" class="page-wrap">
    <!-- 单期或不可用占位 -->
    <div v-if="result && !result.available" class="card-ad anim-fade-up px-6 py-10 text-center">
      <div class="w-14 h-14 rounded-full bg-[#F8FAFC] flex items-center justify-center mx-auto mb-3">
        <el-icon class="text-[28px] text-hint"><DataAnalysis /></el-icon>
      </div>
      <p class="text-[14px] text-sub font-medium">暂无纵向对比数据</p>
      <p class="mt-1 text-[12px] text-hint">需 ≥2 期影像方可生成纵向对比分析</p>
      <p v-if="result.progressionNote" class="mt-2 text-[12px] text-hint">{{ result.progressionNote }}</p>
    </div>

    <template v-else-if="result && result.available">
      <!-- NIA-AA 分级卡片 + 变化率摘要 -->
      <div class="grid grid-cols-1 lg:grid-cols-4 gap-3 anim-fade-up" style="--anim-delay: 0ms">
        <div :class="['rounded-card px-4 py-3.5 flex flex-col gap-1 shadow-sm', stageMeta.toneBg, stageMeta.toneFg]">
          <div class="text-[11px] opacity-80">NIA-AA 纵向分级</div>
          <div class="text-[24px] font-bold tracking-wide leading-none">{{ stageMeta.label }}</div>
          <div class="text-[11px] opacity-80">{{ stageMeta.desc }}</div>
        </div>
        <div class="card-ad px-4 py-3.5">
          <div class="text-[11px] text-hint">海马体积 Δ/年</div>
          <div class="text-[18px] font-num font-semibold text-ink mt-1">{{ fmtRate(result.rates.hv, 'cm³/年') }}</div>
        </div>
        <div class="card-ad px-4 py-3.5">
          <div class="text-[11px] text-hint">SUVr Δ/年</div>
          <div class="text-[18px] font-num font-semibold text-ink mt-1">{{ fmtRate(result.rates.suv, 'SUV/年') }}</div>
        </div>
        <div class="card-ad px-4 py-3.5">
          <div class="text-[11px] text-hint">皮层厚度 Δ/年</div>
          <div class="text-[18px] font-num font-semibold text-ink mt-1">{{ fmtRate(result.rates.cort, 'mm/年') }}</div>
        </div>
      </div>

      <div v-if="result.progressionNote" class="text-[12px] text-hint px-1 anim-fade-up" style="--anim-delay: 80ms">
        <el-icon class="align-[-2px] text-primary"><InfoFilled /></el-icon>
        {{ result.progressionNote }}
      </div>

      <!-- MCID 显著性检验 + 随访频率推荐 -->
      <div v-if="result.significance || result.followupRecommendation" class="grid grid-cols-1 lg:grid-cols-2 gap-3 anim-fade-up" style="--anim-delay: 100ms">
        <div v-if="result.significance" class="card-ad p-4">
          <div class="card-ad__title mb-3">MCID 显著性检验</div>
          <div class="text-[11px] text-hint mb-3">最小临床重要差异阈值：海马萎缩 {{ result.significance.hv.mcidPct ?? 3 }}%/年 · SUVr {{ result.significance.suv.mcidPct ?? 2 }}%/年 · 皮层 {{ result.significance.cort.mcidRate ?? 0.05 }}mm/年</div>
          <div class="space-y-2.5">
            <div v-for="(item, key) in { hv: result.significance.hv, suv: result.significance.suv, cort: result.significance.cort }" :key="key" :class="['rounded-card px-3 py-2 flex items-center justify-between', sigTone(item).bg]">
              <div>
                <div class="text-[11px] opacity-80">{{ key === 'hv' ? '海马体积' : key === 'suv' ? 'SUVr' : '皮层厚度' }}</div>
                <div :class="['text-[13px] font-medium', sigTone(item).fg]">{{ item.note }}</div>
              </div>
              <div class="text-right">
                <div class="text-[10px] opacity-70">变化%</div>
                <div :class="['text-[15px] font-num font-bold', sigTone(item).fg]">{{ fmtPct(item.changePct) }}</div>
              </div>
            </div>
          </div>
          <div class="mt-3 pt-3 border-t border-line flex items-center justify-between">
            <span class="text-[12px] text-sub">综合判定</span>
            <span :class="['text-[12px] font-semibold px-2 py-0.5 rounded-full', result.significance.anyExceeded ? 'bg-[#FBE8DA] text-[#D96B2B]' : 'bg-[#E6F2EC] text-[#2E9E6B]']">
              {{ result.significance.progression === 'significant' ? '显著进展' : result.significance.progression === 'stable' ? '稳定' : '数据不足' }}
            </span>
          </div>
        </div>

        <div v-if="result.followupRecommendation" class="card-ad p-4">
          <div class="card-ad__title mb-3">随访频率与临床路径推荐</div>
          <div class="flex items-center gap-3 mb-3">
            <div :class="['rounded-card px-3 py-2', priorityMeta.bg]">
              <div class="text-[10px] opacity-80">优先级</div>
              <div :class="['text-[16px] font-bold', priorityMeta.fg]">{{ priorityMeta.label }}</div>
            </div>
            <div class="rounded-card px-3 py-2 bg-[#E8F1F8]">
              <div class="text-[10px] text-hint">建议间隔</div>
              <div class="text-[16px] font-num font-bold text-primary">{{ result.followupRecommendation.intervalMonths }} 个月</div>
            </div>
          </div>
          <div class="text-[11px] text-hint mb-1.5">推荐依据</div>
          <div class="text-[12px] text-sub mb-3 leading-relaxed">{{ result.followupRecommendation.rationale }}</div>
          <div class="text-[11px] text-hint mb-1.5">建议措施</div>
          <ul class="space-y-1.5">
            <li v-for="(act, i) in result.followupRecommendation.actions" :key="i" class="text-[12px] text-sub flex items-start gap-2">
              <el-icon class="text-primary text-[13px] mt-0.5 flex-shrink-0"><CircleCheckFilled /></el-icon>
              <span>{{ act }}</span>
            </li>
          </ul>
        </div>
      </div>

      <!-- 三联并排切片 -->
      <div class="card-ad anim-fade-up" style="--anim-delay: 120ms">
        <div class="card-ad__header">
          <span class="card-ad__title">多期影像对比 · 海马层面</span>
          <el-radio-group v-model="sliceModality" size="small">
            <el-radio-button label="MRI">MRI</el-radio-button>
            <el-radio-button label="PET">PET</el-radio-button>
          </el-radio-group>
        </div>
        <div class="p-4">
          <div class="grid grid-cols-3 gap-3">
            <div v-for="(card, i) in sliceCards" :key="card.key" class="space-y-1.5 anim-fade-up" :style="{ '--anim-delay': `${160 + i * 60}ms` }">
              <div class="text-[12px] text-sub text-center font-medium">{{ card.label }}</div>
              <div class="rounded-card overflow-hidden border border-line bg-[#F0F2F5]">
                <img :src="card.src" :alt="card.label" class="w-full aspect-square object-cover" referrerpolicy="no-referrer" />
              </div>
              <div v-if="card.note" class="text-[11px] text-[#D99A2B] text-center">{{ card.note }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 趋势折线图 -->
      <div class="card-ad anim-fade-up" style="--anim-delay: 260ms">
        <div class="card-ad__header">
          <span class="card-ad__title">量化指标趋势</span>
          <span class="text-xs text-hint">海马体积 / SUVr / 皮层厚度 多期变化</span>
        </div>
        <div class="p-4">
          <ChartBase :option="trendOption" :height="320" :empty="!result.timepoints.length" />
        </div>
      </div>
    </template>

    <!-- 重算按钮 -->
    <div v-if="canRecompute && result" class="flex justify-end anim-fade-up" style="--anim-delay: 300ms">
      <el-button size="small" type="primary" plain :icon="'Refresh'" :loading="loading" @click="recompute">重新计算</el-button>
    </div>
  </div>
</template>
