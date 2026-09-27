<script setup lang="ts">
/**
 * 导出脱敏审计对话框
 * ------------------------------------------------------------------
 * 在导出科研数据集前弹出，自动调用 /privacy/audit 进行 PHI 扫描与 k-匿名检查，
 * 展示合规评级、PHI 字段命中表、k-匿名分组明细、脱敏样例对比。
 * 用户确认后通过 export-approved 事件通知父组件执行实际导出（含/不含脱敏）。
 *
 * 评级语义：
 *   green  无 PHI 命中 + k-匿名满足 → 可直接导出
 *   yellow 有 PHI 命中但可脱敏 + k-匿名满足 → 需脱敏后导出
 *   red    k-匿名违反 → 去标识后仍可能被重识别，禁止导出
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiPrivacyAudit } from '@/api/privacy'
import type { AuditReport, ComplianceLevel, ExportApprovedPayload } from '@/types/privacy'

const props = defineProps<{
  /** v-model：控制弹窗显隐 */
  modelValue: boolean
  /** 已选中的病例 ID 列表 */
  selectedCaseIds: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  /** 用户确认导出（含是否脱敏）；父组件据此执行实际导出 */
  (e: 'export-approved', payload: ExportApprovedPayload): void
}>()

// v-model 双向绑定
const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

// ---------- 审计状态 ----------
const loading = ref(false)
const errorMsg = ref('')
const report = ref<AuditReport | null>(null)

/** 已选病例数 */
const selectedCount = computed(() => props.selectedCaseIds?.length ?? 0)

/** 当前合规等级（未加载时为 null） */
const level = computed<ComplianceLevel | null>(() => report.value?.compliance.level ?? null)

/** 命中 PHI 的字段键集合（用于脱敏时传给后端） */
const hitFieldKeys = computed<string[]>(() => {
  if (!report.value) return []
  return report.value.phiScan.byField.filter((f) => f.hits > 0).map((f) => f.field)
})

/** 等级 → el-tag type 与色值映射 */
const levelMeta: Record<ComplianceLevel, { type: 'success' | 'warning' | 'danger'; color: string; text: string }> = {
  green: { type: 'success', color: '#2E9E6B', text: '可直接导出' },
  yellow: { type: 'warning', color: '#D99A2B', text: '需脱敏后导出' },
  red: { type: 'danger', color: '#C94F4F', text: '禁止导出' }
}

/** "仍要导出"按钮是否可用：red 状态禁用（k-匿名违反） */
const canRawExport = computed(() => level.value === 'green')

/** "确认脱敏后导出"按钮是否可用：red 状态禁用（脱敏无法修复 k-匿名违反） */
const canMaskedExport = computed(() => level.value !== 'red')

/** 样例对比的键集合（取原始对象键并集，保留顺序） */
const sampleKeys = computed<string[]>(() => {
  if (!report.value) return []
  const orig = report.value.sampleCase.original
  const masked = report.value.sampleCase.masked
  return Array.from(new Set([...Object.keys(orig), ...Object.keys(masked)]))
})

/** 拉取脱敏审计报告 */
async function fetchAudit(): Promise<void> {
  if (selectedCount.value === 0) {
    errorMsg.value = '请先选择至少 1 例病例'
    report.value = null
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await apiPrivacyAudit({
      caseIds: props.selectedCaseIds,
      includeAnnotations: true,
      includeImaging: false
    })
    report.value = data
  } catch (e) {
    console.error('[PrivacyAudit] 审计失败', e)
    errorMsg.value = '脱敏审计失败，请稍后重试'
    report.value = null
  } finally {
    loading.value = false
  }
}

/** 弹窗打开时自动审计；关闭时清空状态避免残留 */
watch(
  () => props.modelValue,
  (val) => {
    if (val) {
      report.value = null
      errorMsg.value = ''
      fetchAudit()
    }
  }
)

/** 重新审计 */
function onRetry(): void {
  fetchAudit()
}

/** 仍要导出（不脱敏）—— yellow 状态需二次确认（PHI 会泄露） */
async function onRawExport(): Promise<void> {
  if (!canRawExport.value) {
    ElMessage.warning('当前合规等级不允许直接导出，请先脱敏')
    return
  }
  emit('export-approved', { applyMask: false })
  visible.value = false
}

/** 确认脱敏后导出 —— red 状态需二次确认（k-匿名违反仍存在） */
async function onMaskedExport(): Promise<void> {
  if (!canMaskedExport.value) {
    ElMessageBox.alert(
      'k-匿名检查未通过，去标识后仍可能被重识别。请先从少数分组中移除病例，再重新审计导出。',
      '禁止导出',
      { type: 'error', confirmButtonText: '我知道了' }
    )
    return
  }
  emit('export-approved', { applyMask: true, fields: hitFieldKeys.value })
  visible.value = false
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="导出脱敏审计"
    width="680px"
    top="14vh"
    :close-on-click-modal="false"
  >
    <!-- 加载中 -->
    <div v-if="loading" class="py-10 flex flex-col items-center gap-3">
      <el-icon class="is-loading" :size="28" color="#2F6DA3"><Loading /></el-icon>
      <span class="text-[13px] text-hint">正在扫描 PHI 字段并执行 k-匿名检查…</span>
    </div>

    <!-- 错误态 -->
    <div v-else-if="errorMsg" class="py-8 flex flex-col items-center gap-3">
      <el-icon :size="28" color="#C94F4F"><CircleCloseFilled /></el-icon>
      <span class="text-[13px] text-ink-secondary">{{ errorMsg }}</span>
      <el-button size="small" type="primary" plain @click="onRetry">重新审计</el-button>
    </div>

    <!-- 审计报告 -->
    <div v-else-if="report" class="space-y-4">
      <!-- ① 合规总览 -->
      <div
        class="card-ad p-4"
        :style="{ borderColor: level ? `${levelMeta[level].color}40` : undefined }"
      >
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            <span class="text-[13px] font-semibold text-ink">合规总览</span>
          </div>
          <div class="flex items-center gap-2">
            <el-tag
              v-if="level"
              :type="levelMeta[level].type"
              effect="dark"
              size="small"
            >
              {{ levelMeta[level].text }}
            </el-tag>
            <span class="text-[11.5px] text-hint">
              共 <span class="font-num text-ink-secondary font-semibold">{{ report.totalCases }}</span> 例
            </span>
          </div>
        </div>
        <p class="mt-2 text-[12.5px] text-ink-secondary leading-5">{{ report.compliance.summary }}</p>
        <!-- 警告列表 -->
        <div v-if="report.compliance.warnings.length" class="mt-2 space-y-1">
          <div
            v-for="(w, i) in report.compliance.warnings"
            :key="i"
            class="flex items-start gap-1.5 text-[11.5px]"
            :style="{ color: level === 'red' ? '#C94F4F' : '#D99A2B' }"
          >
            <el-icon class="mt-0.5" :size="11"><WarningFilled /></el-icon>
            <span class="leading-5">{{ w }}</span>
          </div>
        </div>
      </div>

      <!-- ② PHI 字段命中表 -->
      <div class="card-ad p-4">
        <div class="flex items-center gap-2 mb-3">
          <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
          <span class="text-[13px] font-semibold text-ink">PHI 字段命中</span>
          <span class="text-[11.5px] text-hint">
            命中 <span class="font-num text-ink-secondary font-semibold">{{ report.phiScan.totalHits }}</span> 处
          </span>
        </div>
        <el-table
          :data="report.phiScan.byField"
          size="small"
          :show-header="true"
          style="width: 100%"
        >
          <el-table-column prop="label" label="字段" min-width="90">
            <template #default="{ row }">
              <span class="text-[12px] text-ink">{{ row.label }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="hits" label="命中数" width="70" align="center">
            <template #default="{ row }">
              <span
                class="font-num text-[12px] font-semibold"
                :class="row.hits > 0 ? 'text-[#C94F4F]' : 'text-hint'"
              >{{ row.hits }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="actionLabel" label="建议动作" width="80" align="center">
            <template #default="{ row }">
              <el-tag
                v-if="row.hits > 0"
                :type="row.action === 'drop' ? 'danger' : 'warning'"
                size="small"
                effect="plain"
              >{{ row.actionLabel }}</el-tag>
              <span v-else class="text-[11.5px] text-hint">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="sample" label="脱敏样例" min-width="140">
            <template #default="{ row }">
              <span v-if="row.sample" class="font-num text-[11.5px] text-ink-secondary">{{ row.sample }}</span>
              <span v-else class="text-[11.5px] text-hint">（剔除）</span>
            </template>
          </el-table-column>
        </el-table>
        <p
          v-if="report.phiScan.annotationHits"
          class="mt-2 text-[11.5px] text-[#D99A2B] leading-5"
        >
          标注自由文本中检测到 {{ report.phiScan.annotationHits }} 条疑似 PHI（电话/身份证），请人工复核。
        </p>
      </div>

      <!-- ③ k-匿名检查 -->
      <div class="card-ad p-4">
        <div class="flex items-center gap-2 mb-3">
          <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
          <span class="text-[13px] font-semibold text-ink">k-匿名检查</span>
          <span class="text-[11.5px] text-hint">
            k = <span class="font-num text-ink-secondary font-semibold">{{ report.kAnonymity.k }}</span>
          </span>
        </div>
        <div class="flex items-center gap-1.5 mb-2 text-[11.5px] text-hint">
          <span>准标识符：</span>
          <el-tag
            v-for="qi in report.kAnonymity.quasiIdentifiers"
            :key="qi"
            size="small"
            type="info"
            effect="plain"
          >{{ qi }}</el-tag>
        </div>

        <!-- 分组明细 -->
        <div class="grid grid-cols-2 gap-2 mt-2">
          <div
            v-for="g in report.kAnonymity.groups"
            :key="g.group"
            class="flex items-center justify-between px-2.5 py-1.5 rounded border text-[11.5px]"
            :class="g.satisfied
              ? 'border-[#BFE3D2] bg-[#F0FAF5]'
              : 'border-[#F0CFCF] bg-[#FCF2F2]'"
          >
            <span class="text-ink-secondary truncate" :title="g.group">{{ g.group }}</span>
            <div class="flex items-center gap-1.5 flex-shrink-0">
              <span class="font-num text-ink font-semibold">{{ g.count }}</span>
              <el-tag
                :type="g.satisfied ? 'success' : 'danger'"
                size="small"
                effect="plain"
              >{{ g.satisfied ? '满足' : '不足' }}</el-tag>
            </div>
          </div>
        </div>

        <!-- 违反分组告警 -->
        <el-alert
          v-if="report.kAnonymity.violations.length"
          class="mt-3"
          type="warning"
          :closable="false"
          show-icon
        >
          <template #title>
            <span class="text-[12px]">
              {{ report.kAnonymity.violations.length }} 个分组病例数 &lt; {{ report.kAnonymity.k }}，存在重识别风险
            </span>
          </template>
          <div class="text-[11.5px] leading-5 mt-1">
            <div
              v-for="v in report.kAnonymity.violations"
              :key="v.group"
            >
              {{ v.group }}：{{ v.count }} 例（差 {{ v.deficit }} 例）
            </div>
          </div>
        </el-alert>
      </div>

      <!-- ④ 脱敏样例对比 -->
      <div class="card-ad p-4">
        <div class="flex items-center gap-2 mb-3">
          <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
          <span class="text-[13px] font-semibold text-ink">脱敏样例对比</span>
          <span class="text-[11.5px] text-hint font-num">{{ report.sampleCase.caseId }}</span>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <!-- 原始 -->
          <div class="rounded border border-line p-3">
            <div class="text-[11.5px] text-hint mb-2 flex items-center gap-1">
              <el-icon :size="11" color="#C94F4F"><Warning /></el-icon>
              原始（含 PHI）
            </div>
            <div class="space-y-1.5">
              <div
                v-for="key in sampleKeys"
                :key="`orig-${key}`"
                class="flex items-center justify-between text-[11.5px]"
              >
                <span class="text-hint">{{ key }}</span>
                <span class="font-num text-ink-secondary truncate ml-2 max-w-[140px]">{{ report.sampleCase.original[key] ?? '—' }}</span>
              </div>
            </div>
          </div>
          <!-- 脱敏后 -->
          <div class="rounded border border-[#BFE3D2] bg-[#F7FCF9] p-3">
            <div class="text-[11.5px] text-hint mb-2 flex items-center gap-1">
              <el-icon :size="11" color="#2E9E6B"><CircleCheck /></el-icon>
              脱敏后
            </div>
            <div class="space-y-1.5">
              <div
                v-for="key in sampleKeys"
                :key="`mask-${key}`"
                class="flex items-center justify-between text-[11.5px]"
              >
                <span class="text-hint">{{ key }}</span>
                <span class="font-num text-ink-secondary truncate ml-2 max-w-[140px]">{{ report.sampleCase.masked[key] ?? '—' }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 未选择病例 -->
    <div v-else class="py-10 flex flex-col items-center gap-3">
      <el-icon :size="28" color="#93A1AF"><InfoFilled /></el-icon>
      <span class="text-[13px] text-hint">请先选择需要导出的病例</span>
    </div>

    <template #footer>
      <div class="flex justify-between items-center gap-3">
        <span class="text-[11.5px] text-hint">
          导出前请确认已阅读上述合规审计结果
        </span>
        <div class="flex gap-3">
          <el-button @click="visible = false">取 消</el-button>
          <el-tooltip
            v-if="!canRawExport && report"
            content="当前存在 PHI 命中或 k-匿名违反，禁止直接导出"
            placement="top"
          >
            <span>
              <el-button :disabled="true" type="default">仍要导出</el-button>
            </span>
          </el-tooltip>
          <el-button
            v-else
            type="default"
            :disabled="!report"
            @click="onRawExport"
          >仍要导出</el-button>
          <el-button
            type="primary"
            :disabled="!report || !canMaskedExport"
            @click="onMaskedExport"
          >
            <el-icon class="mr-1"><Lock /></el-icon>
            确认脱敏后导出
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>
