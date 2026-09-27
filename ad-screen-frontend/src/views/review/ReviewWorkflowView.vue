<script setup lang="ts">
/**
 * 报告会签审核工作台
 * ------------------------------------------------------------------
 * 多医生会签审核工作流：
 * 1. 顶部状态筛选 tabs：待审 / 会签中 / 已签发 / 已退回 / 全部
 * 2. 工作台列表：病例编号 / 患者 / 模态 / 检查日期 / AI风险 / 审核状态 / 会签进度 / 操作
 * 3. 行操作：查看详情（弹窗）、审核决策（approve/reject）、提交审核、会签签字、撤回
 * 4. 详情弹窗：报告基本信息 + 会签记录时间线 + 审核操作按钮
 *
 * 状态机：
 *   draft → submit → pending_review
 *   pending_review → decision(approve/sign) → in_review → signed (2签)
 *                  → decision(reject) → rejected
 *   rejected → submit → pending_review (重新提交)
 *   pending_review/in_review → withdraw → draft
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiGetPendingReviews,
  apiGetReviewDetail,
  apiSubmitReview,
  apiReviewDecision,
  apiWithdrawReview,
  type ReviewQuery
} from '@/api/review'
import type {
  ReviewListItem,
  ReviewDetail,
  ReviewStatus,
  ReviewRecord
} from '@/types/review'
import { useUserStore } from '@/stores/user'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import type { RiskLevel } from '@/types/case'

const userStore = useUserStore()
const loading = ref(false)
const list = ref<ReviewListItem[]>([])
const total = ref(0)
const query = reactive<ReviewQuery>({
  page: 1,
  pageSize: 20,
  status: 'pending_review'
})

// 状态筛选 tabs
const STATUS_TABS: { key: ReviewStatus | 'all'; label: string; icon: string }[] = [
  { key: 'pending_review', label: '待审', icon: 'Clock' },
  { key: 'in_review', label: '会签中', icon: 'EditPen' },
  { key: 'signed', label: '已签发', icon: 'DocumentChecked' },
  { key: 'rejected', label: '已退回', icon: 'Close' },
  { key: 'all', label: '全部', icon: 'Files' }
]

// 状态标签配色（医疗蓝灰风格）
const STATUS_META: Record<ReviewStatus, { text: string; type: 'info' | 'warning' | 'primary' | 'success' | 'danger'; color: string; bg: string }> = {
  draft: { text: '待提交', type: 'info', color: '#9CA3AF', bg: '#F3F4F6' },
  pending_review: { text: '待审', type: 'warning', color: '#D99A2B', bg: '#FBF1E0' },
  in_review: { text: '会签中', type: 'primary', color: '#2F6DA3', bg: '#E8F1F8' },
  signed: { text: '已签发', type: 'success', color: '#2E9E6B', bg: '#E5F5EE' },
  rejected: { text: '已退回', type: 'danger', color: '#C94F4F', bg: '#FAEDED' },
  withdrawn: { text: '已撤回', type: 'info', color: '#9CA3AF', bg: '#F3F4F6' }
}

// 决策标签
const DECISION_META: Record<string, { text: string; color: string; icon: string }> = {
  submit: { text: '提交审核', color: '#2F6DA3', icon: 'Upload' },
  approve: { text: '同意', color: '#2E9E6B', icon: 'Check' },
  sign: { text: '会签签字', color: '#2E9E6B', icon: 'EditPen' },
  reject: { text: '退回', color: '#C94F4F', icon: 'CloseBold' },
  withdraw: { text: '撤回', color: '#9CA3AF', icon: 'RefreshLeft' }
}

// 角色标签
const ROLE_META: Record<string, { text: string; color: string }> = {
  radiologist: { text: '影像科医生', color: '#2F6DA3' },
  neurologist: { text: '神经科医生', color: '#8E6BB3' },
  admin: { text: '管理员', color: '#C94F4F' },
  researcher: { text: '研究员', color: '#D99A2B' }
}

// 允许审核的角色
const ALLOWED_REVIEW_ROLES = ['radiologist', 'neurologist', 'admin']
const canReview = computed(() => ALLOWED_REVIEW_ROLES.includes(userStore.role))

// 详情弹窗
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<ReviewDetail | null>(null)

// 模态映射
function modalityText(m: string): string {
  const map: Record<string, string> = { MRI: 'MRI', PET: 'PET', 'MRI+PET': 'MRI+PET' }
  return map[m] || m || '—'
}

// 角色文本
function roleText(role: string): string {
  return ROLE_META[role]?.text || role || '—'
}

function roleColor(role: string): string {
  return ROLE_META[role]?.color || '#9CA3AF'
}

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await apiGetPendingReviews(query)
    list.value = res.list
    total.value = res.total
    // 处理掉最后一页最后一条后会停在空页：自动回退上一页并重拉（分页器当前页随之同步）
    if (res.list.length === 0 && query.page > 1) {
      query.page -= 1
      loading.value = false
      return fetchList()
    }
  } catch {
    // 错误已由 request 拦截器统一提示
  } finally {
    loading.value = false
  }
}

function onStatusTabChange(key: ReviewStatus | 'all'): void {
  query.status = key
  query.page = 1
  fetchList()
}

function onPageChange(): void {
  fetchList()
}

function onSizeChange(): void {
  query.page = 1
  fetchList()
}

// 打开详情弹窗
async function openDetail(row: ReviewListItem): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await apiGetReviewDetail(row.caseId)
  } catch {
    // 错误已由 request 拦截器统一提示
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (!detail.value) return
  try {
    detail.value = await apiGetReviewDetail(detail.value.caseId)
  } catch {
    // 静默
  }
}

// 是否可提交审核
function canSubmit(status: ReviewStatus): boolean {
  return status === 'draft' || status === 'rejected' || status === 'withdrawn'
}

// 是否可执行审核决策
function canDecisionStatus(status: ReviewStatus): boolean {
  return status === 'pending_review' || status === 'in_review'
}

// 列表行 - 是否可撤回
function canWithdrawRow(row: ReviewListItem): boolean {
  if (!canReview.value) return false
  if (!canDecisionStatus(row.reviewStatus)) return false
  // 找最近一次提交人
  const submitter = [...row.reviews].reverse().find(r => r.decision === 'submit')
  return !!submitter && submitter.reviewerUsername === userStore.username
}

// 提交审核
async function onSubmit(row: ReviewListItem): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt('请输入提交备注（可选）', '提交审核', {
      confirmButtonText: '提交',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputPlaceholder: '可填写提交备注，例如：本次报告已复核，请上级医师审核',
      inputValue: ''
    })
    await apiSubmitReview(row.caseId, { comment: value || '' })
    ElMessage.success('报告已提交审核')
    await fetchList()
    if (detailVisible.value && detail.value?.caseId === row.caseId) {
      await refreshDetail()
    }
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      // 错误已由 request 拦截器统一提示
    }
  }
}

// 审核决策
async function onDecision(row: ReviewListItem, decision: 'approve' | 'reject' | 'sign'): Promise<void> {
  const actionText = decision === 'approve' ? '同意' : decision === 'sign' ? '会签签字' : '退回'
  const requireComment = decision === 'reject'
  try {
    const { value } = await ElMessageBox.prompt(
      requireComment ? '请填写退回意见（必填）' : `请填写${actionText}意见（可选）`,
      `${actionText}报告`,
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: requireComment ? '请填写退回原因，必填' : '可填写审核意见',
        inputValidator: (v: string) => requireComment ? !!(v && v.trim().length > 0) : true,
        inputErrorMessage: '退回报告时必须填写意见'
      }
    )
    await apiReviewDecision(row.caseId, { decision, comment: value || '' })
    ElMessage.success(`${actionText}操作成功`)
    await fetchList()
    if (detailVisible.value && detail.value?.caseId === row.caseId) {
      await refreshDetail()
    }
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      // 错误已由 request 拦截器统一提示
    }
  }
}

// 撤回审核
async function onWithdraw(row: ReviewListItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '确认撤回该报告的审核？撤回后回到「待提交」状态，可重新提交。',
      '撤回审核',
      {
        confirmButtonText: '撤回',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    await apiWithdrawReview(row.caseId)
    ElMessage.success('审核已撤回')
    await fetchList()
    if (detailVisible.value && detail.value?.caseId === row.caseId) {
      await refreshDetail()
    }
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') {
      // 错误已由 request 拦截器统一提示
    }
  }
}

// 会签进度
function progressText(row: ReviewListItem): string {
  return `${row.signedCount}/${row.requiredCount}`
}

function progressPercent(row: ReviewListItem): number {
  return Math.min(100, Math.round(row.signedCount / row.requiredCount * 100))
}

function progressColor(row: ReviewListItem): string {
  if (row.reviewStatus === 'signed') return '#2E9E6B'
  if (row.signedCount >= 1) return '#2F6DA3'
  return '#D99A2B'
}

// 详情弹窗内 - 是否当前用户可对详情报告做决策
const detailCanDecision = computed<boolean>(() => {
  if (!detail.value) return false
  if (!canReview.value) return false
  if (!canDecisionStatus(detail.value.reviewStatus)) return false
  // 不能审自己提交的报告
  if (detail.value.submitter && detail.value.submitter.username === userStore.username) return false
  // 不能重复签字
  const alreadySigned = detail.value.currentCycleReviews.some(
    r => (r.decision === 'approve' || r.decision === 'sign') && r.reviewerUsername === userStore.username
  )
  return !alreadySigned
})

// 详情弹窗内 - 是否可撤回
const detailCanWithdraw = computed<boolean>(() => {
  if (!detail.value) return false
  if (!canDecisionStatus(detail.value.reviewStatus)) return false
  if (!detail.value.submitter) return false
  return detail.value.submitter.username === userStore.username
})

// 详情弹窗内 - 是否可提交
const detailCanSubmit = computed<boolean>(() => {
  if (!detail.value) return false
  return canSubmit(detail.value.reviewStatus)
})

// 顶部统计卡片
const summaryCards = computed(() => {
  // 基于当前列表估算（仅显示当前筛选状态计数）
  return [
    { label: '当前筛选', value: total.value, unit: '例', color: '#2F6DA3', bg: '#E8F1F8', icon: 'Files' }
  ]
})

onMounted(fetchList)
</script>

<template>
  <div class="page-wrap">
    <!-- 顶部统计卡 -->
    <div class="grid grid-cols-1 gap-4 mb-4">
      <div
        v-for="card in summaryCards"
        :key="card.label"
        class="card-ad p-5 relative overflow-hidden"
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

    <!-- 状态筛选 tabs + 列表 -->
    <div class="card-ad anim-fade-up">
      <div class="card-ad__header">
        <div class="flex items-center gap-3">
          <span class="card-ad__title">报告会签审核</span>
          <el-tag v-if="query.status !== 'all'" closable effect="plain" round @close="onStatusTabChange('all')">
            {{ STATUS_TABS.find(t => t.key === query.status)?.label }}
          </el-tag>
        </div>
        <el-button text :icon="'Refresh'" @click="fetchList">刷新</el-button>
      </div>

      <!-- 状态 tabs -->
      <div class="px-5 pt-3 pb-2 flex items-center gap-2 border-b border-line">
        <div
          v-for="tab in STATUS_TABS"
          :key="tab.key"
          class="px-4 py-1.5 rounded-full cursor-pointer text-[13px] transition-all border"
          :class="query.status === tab.key
            ? 'border-primary bg-[#E8F1F8] text-primary font-medium'
            : 'border-line bg-white text-sub hover:border-primary/50 hover:text-primary'"
          @click="onStatusTabChange(tab.key)"
        >
          <el-icon :size="14" class="align-middle mr-1"><component :is="tab.icon" /></el-icon>
          {{ tab.label }}
        </div>
      </div>

      <!-- 列表表格 -->
      <el-table :data="list" v-loading="loading" class="w-full">
        <el-table-column label="病例编号" width="125">
          <template #default="{ row }">
            <el-link type="primary" underline="never" class="font-num" @click="openDetail(row as ReviewListItem)">
              {{ row.caseId }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column prop="patientName" label="患者姓名" width="100" show-overflow-tooltip />
        <el-table-column label="模态" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ modalityText(row.modality) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="examDate" label="检查日期" width="115" />
        <el-table-column label="AI 风险" width="120">
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <RiskLevelTag :risk-level="(row.riskLevel as RiskLevel) ?? null" />
              <span v-if="row.riskScore !== null" class="text-[12px] font-num text-sub">{{ row.riskScore }}分</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="审核状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag
              :type="STATUS_META[row.reviewStatus as ReviewStatus].type"
              effect="light"
              size="small"
              round
            >
              {{ STATUS_META[row.reviewStatus as ReviewStatus].text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="会签进度" width="170">
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <el-progress
                :percentage="progressPercent(row as ReviewListItem)"
                :stroke-width="8"
                :show-text="false"
                :color="progressColor(row as ReviewListItem)"
                class="flex-1"
              />
              <span class="text-[12px] font-num text-sub whitespace-nowrap">{{ progressText(row as ReviewListItem) }}</span>
              <el-tooltip v-if="!(row as ReviewListItem).hasRadiologist && (row as ReviewListItem).signedCount > 0" content="尚未有影像科医生签字，签发需至少 1 名影像科医生" placement="top">
                <el-icon :size="14" class="text-[#D99A2B]"><WarningFilled /></el-icon>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <div class="flex items-center gap-1.5 flex-wrap">
              <el-button link type="primary" size="small" :icon="'View'" @click="openDetail(row as ReviewListItem)">
                详情
              </el-button>
              <el-button
                v-if="canSubmit(row.reviewStatus)"
                link type="primary" size="small" :icon="'Upload'"
                @click="onSubmit(row as ReviewListItem)"
              >提交</el-button>
              <template v-if="canReview && canDecisionStatus(row.reviewStatus) && !canWithdrawRow(row as ReviewListItem)">
                <el-button link type="success" size="small" :icon="'Check'" @click="onDecision(row as ReviewListItem, 'approve')">同意</el-button>
                <el-button link type="success" size="small" :icon="'EditPen'" @click="onDecision(row as ReviewListItem, 'sign')">签字</el-button>
                <el-button link type="danger" size="small" :icon="'Close'" @click="onDecision(row as ReviewListItem, 'reject')">退回</el-button>
              </template>
              <el-button
                v-if="canWithdrawRow(row as ReviewListItem)"
                link type="warning" size="small" :icon="'RefreshLeft'"
                @click="onWithdraw(row as ReviewListItem)"
              >撤回</el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="当前筛选条件下无待审报告" :image-size="90" />
        </template>
      </el-table>

      <!-- 分页 -->
      <div class="flex justify-end px-4 py-3 border-t border-line">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </div>

    <!-- ==================== 审核详情弹窗 ==================== -->
    <el-dialog
      v-model="detailVisible"
      title="报告会签审核详情"
      width="720px"
      append-to-body
      :close-on-click-modal="false"
    >
      <div v-loading="detailLoading" class="space-y-4">
        <template v-if="detail">
          <!-- 基本信息 -->
          <div class="rounded-card border border-line p-4 bg-[#FAFCFE]">
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-2">
                <el-icon :size="18" class="text-primary"><DocumentChecked /></el-icon>
                <span class="text-[14px] font-medium text-ink">报告基本信息</span>
              </div>
              <el-tag
                :type="STATUS_META[detail.reviewStatus].type"
                effect="dark" size="small" round
              >{{ STATUS_META[detail.reviewStatus].text }}</el-tag>
            </div>
            <div class="grid grid-cols-2 gap-x-6 gap-y-2 text-[12.5px]">
              <div class="flex">
                <span class="text-hint w-20 shrink-0">病例编号</span>
                <span class="font-num text-ink">{{ detail.caseId }}</span>
              </div>
              <div class="flex">
                <span class="text-hint w-20 shrink-0">患者姓名</span>
                <span class="text-ink">{{ detail.patientName || '—' }}</span>
              </div>
              <div class="flex">
                <span class="text-hint w-20 shrink-0">检查模态</span>
                <span class="text-ink">{{ modalityText(detail.modality) }}</span>
              </div>
              <div class="flex">
                <span class="text-hint w-20 shrink-0">检查日期</span>
                <span class="font-num text-ink">{{ detail.examDate || '—' }}</span>
              </div>
              <div class="flex">
                <span class="text-hint w-20 shrink-0">科室</span>
                <span class="text-ink">{{ detail.department || '—' }}</span>
              </div>
              <div class="flex">
                <span class="text-hint w-20 shrink-0">诊断状态</span>
                <span class="text-ink">{{ detail.diagStatus || '—' }}</span>
              </div>
              <div class="flex items-center">
                <span class="text-hint w-20 shrink-0">AI 风险</span>
                <RiskLevelTag :risk-level="(detail.riskLevel as RiskLevel) ?? null" />
                <span v-if="detail.riskScore !== null" class="ml-2 text-[12px] font-num text-sub">{{ detail.riskScore }}分</span>
              </div>
              <div class="flex items-center">
                <span class="text-hint w-20 shrink-0">会签进度</span>
                <span class="font-num text-ink">{{ detail.signedCount }} / {{ detail.requiredCount }} 签</span>
                <el-tooltip
                  v-if="!detail.hasRadiologist && detail.signedCount > 0"
                  content="签发需至少 1 名影像科医生签字"
                  placement="top"
                >
                  <el-icon :size="14" class="ml-2 text-[#D99A2B]"><WarningFilled /></el-icon>
                </el-tooltip>
              </div>
            </div>

            <!-- 最新版本信息 -->
            <div v-if="detail.latestVersion" class="mt-3 pt-3 border-t border-line">
              <div class="text-[12px] text-hint mb-1.5">最新分析版本</div>
              <div class="flex flex-wrap gap-x-6 gap-y-1 text-[12px] text-sub">
                <span>版本 <b class="font-num text-ink">v{{ detail.latestVersion.version }}</b></span>
                <span>模型 <b class="text-ink">{{ detail.latestVersion.modelVersion || '—' }}</b></span>
                <span>融合策略 <b class="text-ink">{{ detail.latestVersion.fusionStrategy || '—' }}</b></span>
                <span>操作人 <b class="text-ink">{{ detail.latestVersion.operator || '—' }}</b></span>
                <span>时间 <b class="font-num text-ink">{{ detail.latestVersion.createdAt || '—' }}</b></span>
              </div>
            </div>

            <!-- 提交人 -->
            <div v-if="detail.submitter" class="mt-2 text-[12px] text-sub">
              <span>当前提交人：<b class="text-ink">{{ detail.submitter.name || detail.submitter.username }}</b></span>
              <span class="ml-2 font-num">{{ detail.submitter.at || '—' }}</span>
            </div>
          </div>

          <!-- 会签记录时间线 -->
          <div class="rounded-card border border-line p-4">
            <div class="flex items-center gap-2 mb-3">
              <el-icon :size="18" class="text-primary"><Clock /></el-icon>
              <span class="text-[14px] font-medium text-ink">会签记录</span>
              <el-tag size="small" effect="plain" round>{{ detail.reviews.length }} 条</el-tag>
            </div>
            <el-timeline v-if="detail.reviews.length > 0">
              <el-timeline-item
                v-for="r in detail.reviews"
                :key="r.id"
                :timestamp="r.signedAt || '—'"
                placement="top"
                :color="DECISION_META[r.decision]?.color || '#2F6DA3'"
              >
                <div class="rounded-card border border-line p-3">
                  <div class="flex flex-wrap items-center gap-2 mb-1.5">
                    <el-tag
                      size="small" effect="plain"
                      :style="{ color: DECISION_META[r.decision]?.color || '#2F6DA3', borderColor: (DECISION_META[r.decision]?.color || '#2F6DA3') + '66' }"
                    >
                      {{ DECISION_META[r.decision]?.text || r.decision }}
                    </el-tag>
                    <span class="text-[13px] font-medium text-ink">{{ r.reviewerName || r.reviewerUsername }}</span>
                    <el-tag
                      v-if="r.reviewerRole" size="small" effect="plain" round
                      :style="{ color: roleColor(r.reviewerRole), borderColor: roleColor(r.reviewerRole) + '66' }"
                    >{{ roleText(r.reviewerRole) }}</el-tag>
                  </div>
                  <p v-if="r.comment" class="text-[12.5px] text-sub leading-5 m-0">{{ r.comment }}</p>
                  <p v-else class="text-[12px] text-hint italic m-0">（无备注）</p>
                </div>
              </el-timeline-item>
            </el-timeline>
            <el-empty v-else description="该报告暂无会签记录" :image-size="80" />
          </div>
        </template>

        <el-empty v-else-if="!detailLoading" description="未加载到详情" :image-size="80" />
      </div>

      <!-- 弹窗底部操作按钮 -->
      <template #footer>
        <div class="flex items-center justify-between w-full">
          <span class="text-[12px] text-hint">
            <el-icon :size="13" class="align-middle"><InfoFilled /></el-icon>
            会签签发条件：≥ {{ detail?.requiredCount ?? 2 }} 名医生签字（含至少 1 名影像科医生）
          </span>
          <div class="flex items-center gap-2">
            <el-button @click="detailVisible = false">关闭</el-button>
            <el-button
              v-if="detailCanSubmit"
              type="primary" plain :icon="'Upload'"
              @click="detail && onSubmit({ caseId: detail.caseId, reviewStatus: detail.reviewStatus } as ReviewListItem)"
            >提交审核</el-button>
            <el-button
              v-if="detailCanWithdraw"
              type="warning" plain :icon="'RefreshLeft'"
              @click="detail && onWithdraw({ caseId: detail.caseId, reviewStatus: detail.reviewStatus, reviews: detail.reviews } as ReviewListItem)"
            >撤回审核</el-button>
            <template v-if="detailCanDecision">
              <el-button type="danger" plain :icon="'Close'" @click="detail && onDecision({ caseId: detail.caseId } as ReviewListItem, 'reject')">退回</el-button>
              <el-button type="success" plain :icon="'Check'" @click="detail && onDecision({ caseId: detail.caseId } as ReviewListItem, 'approve')">同意</el-button>
              <el-button type="success" :icon="'EditPen'" @click="detail && onDecision({ caseId: detail.caseId } as ReviewListItem, 'sign')">会签签字</el-button>
            </template>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>
