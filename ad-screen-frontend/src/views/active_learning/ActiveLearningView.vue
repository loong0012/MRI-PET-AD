<script setup lang="ts">
/**
 * 主动学习标注优先级队列看板（researcher / admin）
 * ------------------------------------------------------------------
 * 基于模型不确定性筛选高价值待标注病例，按不确定性得分排序输出标注队列：
 * 1. 顶部统计卡：总病例数 / 已分析 / 已标注 / 高不确定性病例数 / 模型健康分
 * 2. 左侧：不确定性分布柱状图（10 桶 0-100）+ 模型迭代建议卡片（el-alert 按 priority 配色）
 * 3. 右侧：标注优先级队列表格（不确定性得分用 el-progress 配色 + 进度条）
 * 4. 工具栏：刷新 / 筛选（仅未标注 / 仅高不确定性）/ 批量分配
 * 5. 行操作：去标注（跳转 /viewer/{caseId}）、分配标注任务（el-popover 输入分配人）
 *
 * 设计约束：
 * - 医疗蓝灰风格（主色 #2F6DA3 / 危险红 #C94F4F / 警告橙 #D99A2B / 成功绿 #2E9E6B / 紫 #8E6BB3）
 * - 不确定性得分配色：>70 红 / 40-70 橙 / <40 绿
 * - ChartBase 组件统一封装 ECharts
 * - 全中文注释，TypeScript 严格模式
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  apiGetQueue,
  apiGetStats,
  apiAssignTask,
  apiGetRecommendations
} from '@/api/activeLearning'
import type {
  QueueResult,
  LearningStats,
  Recommendations,
  QueueItem,
  ALReviewStatus,
  RecommendationPriority
} from '@/types/activeLearning'
import type { RiskLevel } from '@/types/case'
import ChartBase from '@/components/ChartBase.vue'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import type { EChartsOption } from 'echarts'

const router = useRouter()

// ---------- 加载状态与数据 ----------
const loading = ref(true)
const queueResult = ref<QueueResult | null>(null)
const stats = ref<LearningStats | null>(null)
const recommendations = ref<Recommendations | null>(null)
const generatedAt = ref('')

// ---------- 筛选条件 ----------
const filterUnannotated = ref(false)
const filterHighUncertainty = ref(false)

// ---------- 表格批量选择 ----------
const selectedRows = ref<QueueItem[]>([])

// ---------- 分配标注任务（行级 el-popover + 批量） ----------
// 行级 popover 可见性映射：{ [caseId]: boolean }
const rowPopoverVisible = reactive<Record<string, boolean>>({})
// 当前编辑中的分配人输入（行级 / 批量共享一个输入框，因同时只开一个）
const assigneeInput = ref('')

/** 数据请求序号：快速切换筛选开关时丢弃过期响应，避免旧筛选结果覆盖当前筛选 */
let loadReqSeq = 0

/** 并行加载队列 / 统计 / 建议，单模块失败不拖垮整页 */
async function loadAll(): Promise<void> {
  const seq = ++loadReqSeq
  loading.value = true
  const [q, s, r] = await Promise.allSettled([
    apiGetQueue({
      limit: 50,
      onlyUnannotated: filterUnannotated.value,
      onlyHighUncertainty: filterHighUncertainty.value
    }),
    apiGetStats(),
    apiGetRecommendations()
  ])
  // 在途期间又发起了新请求：本次结果已过期，整体丢弃（loading 由最新请求负责复位）
  if (seq !== loadReqSeq) return
  queueResult.value = q.status === 'fulfilled' ? q.value : null
  stats.value = s.status === 'fulfilled' ? s.value : null
  recommendations.value = r.status === 'fulfilled' ? r.value : null
  generatedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
  loading.value = false
}

watch([filterUnannotated, filterHighUncertainty], () => { void loadAll() })
onMounted(() => { void loadAll() })

// ---------- 顶部统计卡数据 ----------
const queueStats = computed(() => queueResult.value?.stats ?? null)
const healthScore = computed(() => recommendations.value?.healthScore ?? 0)

// 健康分配色
const healthColor = computed<{ color: string; bg: string; label: string }>(() => {
  const v = healthScore.value
  if (v >= 80) return { color: '#2E9E6B', bg: '#EDF7F2', label: '健康' }
  if (v >= 60) return { color: '#D99A2B', bg: '#FBF4E6', label: '关注' }
  return { color: '#C94F4F', bg: '#FAEDED', label: '需干预' }
})

// ==================== 不确定性得分配色 ====================

/** 不确定性得分对应的进度条颜色：>70 红 / 40-70 橙 / <40 绿 */
function uncertaintyColor(score: number): string {
  if (score > 70) return '#C94F4F'
  if (score >= 40) return '#D99A2B'
  return '#2E9E6B'
}

// ==================== 审核状态映射 ====================

const REVIEW_STATUS_META: Record<ALReviewStatus, { text: string; color: string; bg: string }> = {
  pending: { text: '待审核', color: '#D99A2B', bg: '#FBF4E6' },
  approved: { text: '已通过', color: '#2E9E6B', bg: '#EDF7F2' },
  rejected: { text: '已驳回', color: '#C94F4F', bg: '#FAEDED' }
}

function reviewStatusMeta(status: string): { text: string; color: string; bg: string } {
  return REVIEW_STATUS_META[(status as ALReviewStatus)] ?? { text: status || '—', color: '#9CA3AF', bg: '#F3F4F6' }
}

// ==================== 模态文本 ====================

function modalityText(m: string): string {
  const map: Record<string, string> = { MRI: 'MRI', PET: 'PET', 'MRI+PET': 'MRI+PET' }
  return map[m] || m || '—'
}

// ==================== ECharts：不确定性分布柱状图 ====================

const distOption = computed<EChartsOption>(() => {
  const buckets = stats.value?.uncertaintyDistribution ?? []
  const labels = buckets.map((b) => b.bucket)
  const counts = buckets.map((b) => b.count)
  // 桶颜色：低桶绿 → 中桶橙 → 高桶红（按桶中位值分级）
  const colors = labels.map((_, i) => {
    const mid = i * 10 + 5
    if (mid > 70) return '#C94F4F'
    if (mid >= 40) return '#D99A2B'
    return '#2E9E6B'
  })
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 8, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { lineStyle: { color: '#E3E9EF' } },
      axisLabel: { color: '#5C6B7A', fontSize: 10, interval: 0, rotate: 35 }
    },
    yAxis: {
      type: 'value',
      name: '病例数',
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    series: [
      {
        name: '病例数',
        type: 'bar',
        data: counts.map((v, i) => ({ value: v, itemStyle: { color: colors[i], borderRadius: [4, 4, 0, 0] } })),
        barWidth: 18
      }
    ]
  }
})

// ==================== 建议卡片配色 ====================

const REC_ALERT_TYPE: Record<RecommendationPriority, 'error' | 'warning' | 'info'> = {
  high: 'error',
  medium: 'warning',
  low: 'info'
}
const REC_LABEL: Record<RecommendationPriority, string> = { high: '高', medium: '中', low: '低' }
const REC_TYPE_LABEL: Record<string, string> = {
  highUncertainty: '高不确定性',
  modelStability: '模型稳定性',
  misdiagnosis: '误诊率'
}

/** 建议按优先级排序：high → medium → low */
function sortRecommendations(list: Recommendations['recommendations']): Recommendations['recommendations'] {
  const order: Record<string, number> = { high: 0, medium: 1, low: 2 }
  return [...list].sort((a, b) => (order[a.priority] ?? 3) - (order[b.priority] ?? 3))
}

// ==================== 表格选择 ====================

function onSelectionChange(rows: QueueItem[]): void {
  selectedRows.value = rows
}

// ==================== 分配标注任务 ====================

/** 单行分配确认 */
async function confirmRowAssign(row: QueueItem): Promise<void> {
  const assignee = assigneeInput.value.trim()
  if (!assignee) {
    ElMessage.warning('请输入分配人账号')
    return
  }
  try {
    await apiAssignTask({ caseIds: [row.caseId], assignee })
    ElMessage.success(`已将病例 ${row.caseId} 分配给 ${assignee}`)
    rowPopoverVisible[row.caseId] = false
    assigneeInput.value = ''
  } catch {
    /* 错误已由 request 拦截器统一提示 */
  }
}

/** 批量分配确认 */
const batchPopoverVisible = ref(false)

// 行级/批量 popover 共用一个 assigneeInput：任一 popover 关闭（取消、点外部、确认后）即清空，
// 避免给 A 输入的分配人残留到 B 的 popover 中造成误分配
watch(batchPopoverVisible, (open) => {
  if (!open) assigneeInput.value = ''
})
watch(
  rowPopoverVisible,
  (map) => {
    if (!Object.values(map).some(Boolean)) assigneeInput.value = ''
  },
  { deep: true }
)

async function confirmBatchAssign(): Promise<void> {
  if (selectedRows.value.length === 0) {
    ElMessage.warning('请先勾选要分配的病例')
    return
  }
  const assignee = assigneeInput.value.trim()
  if (!assignee) {
    ElMessage.warning('请输入分配人账号')
    return
  }
  try {
    const res = await apiAssignTask({
      caseIds: selectedRows.value.map((r) => r.caseId),
      assignee
    })
    ElMessage.success(`已将 ${res.assigned} 例病例分配给 ${res.assignee}`)
    batchPopoverVisible.value = false
    assigneeInput.value = ''
  } catch {
    /* 错误已由 request 拦截器统一提示 */
  }
}

// ==================== 跳转标注 ====================

function goToAnnotate(caseId: string): void {
  router.push(`/viewer/${caseId}`)
}
</script>

<template>
  <div class="page-wrap" v-loading="loading">
    <!-- ==================== 顶部统计卡 ==================== -->
    <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
      <!-- 总病例数 -->
      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 0ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" style="background: #2F6DA3" />
        <div class="text-[13px] text-sub">病例总数</div>
        <div class="mt-2 font-num text-[28px] leading-none font-semibold text-ink">
          {{ queueStats?.totalCases ?? 0 }}
          <span class="text-[12px] text-hint font-sans font-normal ml-1">例</span>
        </div>
        <div class="mt-3 text-xs text-hint">未删除病例库全量</div>
      </div>

      <!-- 已分析 -->
      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 70ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" style="background: #2F6DA3" />
        <div class="text-[13px] text-sub">已分析</div>
        <div class="mt-2 font-num text-[28px] leading-none font-semibold text-ink">
          {{ queueStats?.analyzedCases ?? 0 }}
          <span class="text-[12px] text-hint font-sans font-normal ml-1">例</span>
        </div>
        <div class="mt-3 text-xs text-hint">含 AI 分析版本</div>
      </div>

      <!-- 已标注 -->
      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 140ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" style="background: #2E9E6B" />
        <div class="text-[13px] text-sub">已标注</div>
        <div class="mt-2 font-num text-[28px] leading-none font-semibold text-ink">
          {{ queueStats?.annotatedCases ?? 0 }}
          <span class="text-[12px] text-hint font-sans font-normal ml-1">例</span>
        </div>
        <div class="mt-3 text-xs text-hint">含 ROI 标注</div>
      </div>

      <!-- 高不确定性 -->
      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 210ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" style="background: #C94F4F" />
        <div class="text-[13px] text-sub">高不确定性</div>
        <div class="mt-2 font-num text-[28px] leading-none font-semibold text-ink">
          {{ queueStats?.highUncertainty ?? 0 }}
          <span class="text-[12px] text-hint font-sans font-normal ml-1">例</span>
        </div>
        <div class="mt-3 text-xs text-hint">不确定性得分 &gt; 70</div>
      </div>

      <!-- 模型健康分 -->
      <div class="card-ad anim-fade-up p-5 relative overflow-hidden" style="--anim-delay: 280ms">
        <div class="absolute -right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08] pointer-events-none" :style="{ background: healthColor.color }" />
        <div class="text-[13px] text-sub">模型健康分</div>
        <div class="mt-2 font-num text-[28px] leading-none font-semibold" :style="{ color: healthColor.color }">
          {{ healthScore }}
        </div>
        <div class="mt-3 flex items-center gap-1.5">
          <span
            class="inline-flex items-center rounded-full px-2 h-5 text-[11px] font-medium"
            :style="{ color: healthColor.color, background: healthColor.bg }"
          >
            {{ healthColor.label }}
          </span>
          <span class="text-xs text-hint">综合不确定性 / 稳定性 / 误诊</span>
        </div>
      </div>
    </div>

    <!-- ==================== 工具栏 ==================== -->
    <div class="card-ad mt-4 p-4 flex items-center gap-3 flex-wrap">
      <span class="text-[13px] text-sub font-medium">筛选：</span>
      <el-checkbox v-model="filterUnannotated">仅未标注</el-checkbox>
      <el-checkbox v-model="filterHighUncertainty">仅高不确定性(&gt;70)</el-checkbox>

      <el-divider direction="vertical" class="!h-6" />

      <el-popover
        v-model:visible="batchPopoverVisible"
        placement="bottom"
        :width="280"
        trigger="click"
      >
        <template #reference>
          <el-button
            type="primary"
            plain
            :icon="'Share'"
            :disabled="selectedRows.length === 0"
          >
            批量分配
            <span v-if="selectedRows.length > 0" class="ml-1">({{ selectedRows.length }})</span>
          </el-button>
        </template>
        <div class="space-y-3">
          <div class="text-[13px] font-medium text-ink">分配 {{ selectedRows.length }} 例给标注人</div>
          <el-input
            v-model="assigneeInput"
            placeholder="输入分配人账号"
            clearable
            @keyup.enter="confirmBatchAssign"
          />
          <div class="flex justify-end gap-2">
            <el-button size="small" @click="batchPopoverVisible = false">取消</el-button>
            <el-button size="small" type="primary" @click="confirmBatchAssign">确认</el-button>
          </div>
        </div>
      </el-popover>

      <div class="flex-1" />
      <span v-if="generatedAt" class="text-xs text-hint">数据生成于 {{ generatedAt }}</span>
      <el-button :icon="'Refresh'" @click="loadAll">刷新</el-button>
    </div>

    <!-- ==================== 左：分布图 + 建议 / 右：队列表格 ==================== -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 mt-4">
      <!-- 左列 -->
      <div class="lg:col-span-4 flex flex-col gap-4">
        <!-- 不确定性分布柱状图 -->
        <div class="card-ad anim-fade-up" style="--anim-delay: 350ms">
          <div class="card-ad__header">
            <span class="card-ad__title">不确定性分布</span>
            <span class="text-xs text-hint">
              均值 {{ queueStats?.avgUncertainty ?? 0 }} · 高不确定性 {{ queueStats?.highUncertainty ?? 0 }} 例
            </span>
          </div>
          <div class="p-4">
            <ChartBase
              :option="distOption"
              :height="260"
              :empty="(stats?.uncertaintyDistribution ?? []).every((b) => b.count === 0)"
            />
          </div>
        </div>

        <!-- 模型迭代建议 -->
        <div class="card-ad anim-fade-up" style="--anim-delay: 420ms">
          <div class="card-ad__header">
            <span class="card-ad__title">模型迭代建议</span>
            <span v-if="recommendations" class="text-xs text-hint">
              共 {{ recommendations.recommendations.length }} 条
            </span>
          </div>
          <div class="p-4 space-y-3">
            <template v-if="recommendations && recommendations.recommendations.length > 0">
              <el-alert
                v-for="(rec, i) in sortRecommendations(recommendations.recommendations)"
                :key="`${rec.type}-${i}`"
                :type="REC_ALERT_TYPE[rec.priority] ?? 'info'"
                :closable="false"
                show-icon
              >
                <template #title>
                  <div class="flex items-center gap-2 flex-wrap">
                    <el-tag size="small" :type="REC_ALERT_TYPE[rec.priority] ?? 'info'" effect="dark">
                      {{ REC_LABEL[rec.priority] ?? rec.priority }}
                    </el-tag>
                    <el-tag size="small" type="info" effect="plain">
                      {{ REC_TYPE_LABEL[rec.type] ?? rec.type }}
                    </el-tag>
                    <span class="text-[13px] text-ink">{{ rec.message }}</span>
                  </div>
                </template>
                <template #default>
                  <div class="text-[11px] text-hint mt-1">{{ rec.metric }}</div>
                </template>
              </el-alert>
            </template>
            <el-empty v-else description="模型状态良好，暂无迭代建议" :image-size="60" />
          </div>
        </div>
      </div>

      <!-- 右列：标注优先级队列 -->
      <div class="lg:col-span-8">
        <div class="card-ad anim-fade-up" style="--anim-delay: 350ms">
          <div class="card-ad__header">
            <span class="card-ad__title">标注优先级队列</span>
            <span class="text-xs text-hint">
              共 {{ queueResult?.total ?? 0 }} 条 · 按不确定性降序
            </span>
          </div>
          <div class="p-4">
            <el-table
              :data="queueResult?.queue ?? []"
              class="w-full"
              row-key="caseId"
              stripe
              @selection-change="onSelectionChange"
            >
              <el-table-column type="selection" width="42" reserve-selection />

              <el-table-column label="病例 ID" width="105" show-overflow-tooltip>
                <template #default="{ row }">
                  <span class="font-num text-[12px] text-ink">{{ row.caseId }}</span>
                </template>
              </el-table-column>

              <el-table-column prop="patientName" label="患者" width="80" show-overflow-tooltip />

              <el-table-column label="模态" width="80" align="center">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain">{{ modalityText(row.modality) }}</el-tag>
                </template>
              </el-table-column>

              <el-table-column label="AI 评分" width="110" align="center">
                <template #default="{ row }">
                  <div class="flex flex-col items-center gap-1">
                    <span class="font-num text-[13px] font-semibold text-ink">{{ row.riskScore }}</span>
                    <RiskLevelTag :risk-level="(row.riskLevel || null) as RiskLevel | null" />
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="审核状态" width="90" align="center">
                <template #default="{ row }">
                  <span
                    class="inline-flex items-center rounded-full px-2 h-5 text-[11px] font-medium whitespace-nowrap"
                    :style="{
                      color: reviewStatusMeta(row.reviewStatus).color,
                      background: reviewStatusMeta(row.reviewStatus).bg
                    }"
                  >
                    {{ reviewStatusMeta(row.reviewStatus).text }}
                  </span>
                </template>
              </el-table-column>

              <el-table-column label="不确定性得分" width="160">
                <template #default="{ row }">
                  <div class="flex items-center gap-2">
                    <el-progress
                      :percentage="Math.min(100, Math.max(0, row.uncertaintyScore))"
                      :color="uncertaintyColor(row.uncertaintyScore)"
                      :stroke-width="10"
                      :show-text="false"
                      class="flex-1 !min-w-0"
                    />
                    <span
                      class="font-num text-[12px] font-semibold w-9 text-right"
                      :style="{ color: uncertaintyColor(row.uncertaintyScore) }"
                    >
                      {{ row.uncertaintyScore }}
                    </span>
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="不确定性因素" min-width="180">
                <template #default="{ row }">
                  <div v-if="row.uncertaintyFactors && row.uncertaintyFactors.length" class="flex flex-wrap gap-1">
                    <el-tag
                      v-for="(f, idx) in row.uncertaintyFactors"
                      :key="idx"
                      size="small"
                      type="info"
                      effect="plain"
                    >
                      {{ f }}
                    </el-tag>
                  </div>
                  <span v-else class="text-xs text-hint">—</span>
                </template>
              </el-table-column>

              <el-table-column label="标注建议" min-width="200" show-overflow-tooltip>
                <template #default="{ row }">
                  <span class="text-[12px] text-sub">{{ row.suggestion || '—' }}</span>
                </template>
              </el-table-column>

              <el-table-column label="操作" width="150" fixed="right">
                <template #default="{ row }">
                  <div class="flex items-center gap-1">
                    <el-button size="small" type="primary" link @click="goToAnnotate(row.caseId)">
                      去标注
                    </el-button>
                    <el-popover
                      v-model:visible="rowPopoverVisible[row.caseId]"
                      placement="left"
                      :width="240"
                      trigger="click"
                    >
                      <template #reference>
                        <el-button size="small" type="primary" link>分配</el-button>
                      </template>
                      <div class="space-y-2.5">
                        <div class="text-[13px] font-medium text-ink">分配病例 {{ row.caseId }}</div>
                        <el-input
                          v-model="assigneeInput"
                          placeholder="输入分配人账号"
                          clearable
                          size="small"
                          @keyup.enter="confirmRowAssign(row)"
                        />
                        <div class="flex justify-end gap-2">
                          <el-button size="small" @click="rowPopoverVisible[row.caseId] = false">取消</el-button>
                          <el-button size="small" type="primary" @click="confirmRowAssign(row)">确认</el-button>
                        </div>
                      </div>
                    </el-popover>
                  </div>
                </template>
              </el-table-column>

              <template #empty>
                <el-empty description="暂无标注优先级队列数据" :image-size="80" />
              </template>
            </el-table>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
