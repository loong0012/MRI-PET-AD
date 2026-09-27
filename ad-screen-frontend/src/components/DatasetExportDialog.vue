<script setup lang="ts">
/**
 * 科研数据集导出对话框
 * ------------------------------------------------------------------
 * 用于将已选中的病例批量导出为科研数据集（JSON / CSV）。
 * - JSON：结构化数组，便于后续程序处理与统计分析
 * - CSV：含 BOM 头，Excel 可直接打开，标注以 JSON 字符串列存储
 * 可选项：是否包含 ROI 标注、是否包含影像文件路径。
 * 事件：export-done（导出成功后通知父组件，可用于刷新列表 / 打审计日志）
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiExportDataset } from '@/api/dataset'
import PrivacyAuditDialog from '@/components/PrivacyAuditDialog.vue'
import type { DatasetFormat } from '@/types/dataset'
import { formatDate } from '@/utils/format'

const props = defineProps<{
  /** v-model：控制弹窗显隐 */
  modelValue: boolean
  /** 已选中的病例 ID 列表 */
  selectedCaseIds: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'export-done', payload: { count: number; format: DatasetFormat }): void
}>()

// v-model 双向绑定
const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

// ---------- 表单状态 ----------
const format = ref<DatasetFormat>('json')
const includeAnnotations = ref(true)
const includeImaging = ref(false)
const exporting = ref(false)

// ---------- 脱敏审计弹窗 ----------
const privacyAuditVisible = ref(false)

/** 已选病例数（空数组时给出友好提示） */
const selectedCount = computed(() => props.selectedCaseIds?.length ?? 0)

/**
 * 触发浏览器下载 Blob
 * 通过临时 <a> 标签的 download 属性保存文件；JSON 默认走 .json、CSV 走 .csv。
 */
function downloadBlob(blob: Blob, fmt: DatasetFormat): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const stamp = formatDate(new Date())
  a.download = `科研数据集_${stamp}.${fmt}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** 确认导出；成功返回 true，未选病例或导出失败返回 false（供脱敏审批流程判断是否提示成功） */
async function onConfirm(): Promise<boolean> {
  if (selectedCount.value === 0) {
    ElMessage.warning('请先选择至少 1 例病例')
    return false
  }
  exporting.value = true
  try {
    const blob = await apiExportDataset({
      caseIds: props.selectedCaseIds,
      includeAnnotations: includeAnnotations.value,
      includeImaging: includeImaging.value,
      format: format.value
    })
    downloadBlob(blob, format.value)
    ElMessage.success(`已导出 ${selectedCount.value} 例病例的科研数据集（${format.value.toUpperCase()}）`)
    emit('export-done', { count: selectedCount.value, format: format.value })
    visible.value = false
    return true
  } catch (e) {
    // 错误已由 request.ts 统一弹窗，这里仅兜底防止 loading 卡死并告知调用方失败
    console.error('[DatasetExport] 导出失败', e)
    return false
  } finally {
    exporting.value = false
  }
}

/** 关闭弹窗时重置表单状态，避免下次打开残留上次选项 */
function onClosed(): void {
  format.value = 'json'
  includeAnnotations.value = true
  includeImaging.value = false
  privacyAuditVisible.value = false
}

/** 打开脱敏审计弹窗（需先选择病例） */
function onOpenPrivacyAudit(): void {
  if (selectedCount.value === 0) {
    ElMessage.warning('请先选择至少 1 例病例')
    return
  }
  privacyAuditVisible.value = true
}

/** 脱敏审计通过后执行导出；仅导出成功且启用掩码时才提示脱敏成功，避免失败误报 */
async function onPrivacyExportApproved(payload: { applyMask: boolean }): Promise<void> {
  privacyAuditVisible.value = false
  const ok = await onConfirm()
  // 普通成功 toast 已在 onConfirm 内弹出，此处仅补充脱敏专属提示，避免重复弹窗
  if (ok && payload.applyMask) {
    ElMessage.success('已按脱敏策略导出（PHI 字段已掩码/剔除）')
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="导出科研数据集"
    width="520px"
    top="18vh"
    :close-on-click-modal="false"
    @closed="onClosed"
  >
    <div class="space-y-5">
      <!-- 已选病例概览 -->
      <div class="card-ad p-4">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            <span class="text-[13px] font-semibold text-ink">已选病例</span>
          </div>
          <span class="text-[13px] text-sub">
            共 <span class="text-primary font-semibold font-num">{{ selectedCount }}</span> 例
          </span>
        </div>
        <p class="mt-2 text-[11.5px] text-hint leading-5">
          仅导出未删除的病例元数据、AI 风险结果；患者标识字段保持原始入库值，科研使用请遵守伦理与脱敏规范。
        </p>
      </div>

      <!-- 导出格式 -->
      <div class="card-ad p-4">
        <div class="flex items-center gap-2 mb-3">
          <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
          <span class="text-[13px] font-semibold text-ink">导出格式</span>
        </div>
        <el-radio-group v-model="format" class="w-full">
          <div class="grid grid-cols-2 gap-3">
            <label
              class="flex items-start gap-2 p-3 rounded-card border cursor-pointer transition-colors"
              :class="format === 'json' ? 'border-primary bg-primary-light' : 'border-line hover:border-primary/50'"
            >
              <el-radio value="json" class="!mr-0 mt-0.5" />
              <div class="min-w-0">
                <div class="text-[13px] font-medium text-ink">JSON</div>
                <div class="text-[11.5px] text-hint mt-0.5 leading-5">结构化数组，标注嵌套完整，适合程序处理</div>
              </div>
            </label>
            <label
              class="flex items-start gap-2 p-3 rounded-card border cursor-pointer transition-colors"
              :class="format === 'csv' ? 'border-primary bg-primary-light' : 'border-line hover:border-primary/50'"
            >
              <el-radio value="csv" class="!mr-0 mt-0.5" />
              <div class="min-w-0">
                <div class="text-[13px] font-medium text-ink">CSV</div>
                <div class="text-[11.5px] text-hint mt-0.5 leading-5">含 BOM 头，Excel 直开，标注为 JSON 字符串列</div>
              </div>
            </label>
          </div>
        </el-radio-group>
      </div>

      <!-- 导出内容选项 -->
      <div class="card-ad p-4">
        <div class="flex items-center gap-2 mb-3">
          <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
          <span class="text-[13px] font-semibold text-ink">导出内容</span>
        </div>
        <div class="space-y-3">
          <el-checkbox v-model="includeAnnotations">
            <span class="text-[12.5px] text-ink">包含 ROI 标注</span>
          </el-checkbox>
          <p class="text-[11.5px] text-hint leading-5 pl-6">
            导出每例病例的 ROI 标注记录（海马体 / 内嗅皮层等区域坐标、面积、备注）。
          </p>
          <el-checkbox v-model="includeImaging">
            <span class="text-[12.5px] text-ink">包含影像路径</span>
          </el-checkbox>
          <p class="text-[11.5px] text-hint leading-5 pl-6">
            附带 MRI / PET 影像文件的相对存储路径，便于回溯原始影像数据。
          </p>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end gap-3">
        <el-button @click="visible = false">取 消</el-button>
        <el-button type="warning" plain :icon="'Lock'" :disabled="selectedCount === 0" @click="onOpenPrivacyAudit">
          脱敏审计
        </el-button>
        <el-button type="primary" :loading="exporting" :disabled="selectedCount === 0" @click="onConfirm">
          <el-icon class="mr-1"><Download /></el-icon>
          导出数据集
        </el-button>
      </div>
    </template>

    <!-- 脱敏审计弹窗（嵌套） -->
    <PrivacyAuditDialog
      v-model="privacyAuditVisible"
      :selected-case-ids="props.selectedCaseIds"
      @export-approved="onPrivacyExportApproved"
    />
  </el-dialog>
</template>
