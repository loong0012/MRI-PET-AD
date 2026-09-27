<script setup lang="ts">
/**
 * AI 推理加载弹窗（全站统一）
 * ------------------------------------------------------------------
 * 阅片页启动/重新推理时由父级控制 v-model 与进度数据；
 * 弹窗不可点击遮罩关闭、不显示右上角关闭按钮，
 * 保证推理事务在医疗操作流程中的原子性。
 */
import { computed } from 'vue'
import type { InferenceProgress } from '@/types/analysis'

const props = defineProps<{
  /** 弹窗可见性 */
  modelValue: boolean
  /** 推理进度（0-100 与阶段文案） */
  progress: InferenceProgress | null
  /** 病例编号（展示用） */
  caseId?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'cancel'): void
}>()

/** 弹窗内部可见性（el-dialog 双向绑定代理） */
const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})
</script>

<template>
  <el-dialog
    v-model="visible"
    width="480px"
    align-center
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
  >
    <div class="flex flex-col items-center py-2">
      <!-- 仪表盘进度环：医疗设备读数风格（等宽数字） -->
      <el-progress
        type="dashboard"
        :percentage="progress?.progress ?? 0"
        :width="132"
        :stroke-width="10"
        color="var(--ad-primary)"
      >
        <template #default>
          <div class="flex flex-col items-center">
            <span class="font-num text-[26px] font-semibold text-ink leading-none">
              {{ progress?.progress ?? 0 }}%
            </span>
            <span class="text-hint text-xs mt-1.5">多模态融合推理</span>
          </div>
        </template>
      </el-progress>

      <!-- 当前阶段文案 -->
      <div class="mt-5 text-[14px] text-ink-secondary min-h-[22px]">
        {{ progress?.stage ?? '正在初始化推理引擎…' }}
      </div>

      <!-- 病例上下文 -->
      <div v-if="caseId" class="mt-1.5 text-xs text-hint font-num">病例编号：{{ caseId }}</div>

      <!-- 提示语 -->
      <div class="mt-5 px-6 py-2.5 rounded bg-primary-light text-xs text-primary leading-5 text-center">
        推理过程中请勿关闭或刷新页面，避免任务中断
      </div>

      <!-- 取消入口：仅关闭前端进度订阅（后端在途任务不受影响），避免假死时遮罩永久锁页 -->
      <el-button class="mt-4" plain size="small" @click="emit('cancel')">取消推理</el-button>
    </div>
  </el-dialog>
</template>
