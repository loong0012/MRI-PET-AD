<script setup lang="ts">
/**
 * 统一操作确认弹窗（全站通用）
 * ------------------------------------------------------------------
 * 替代零散的 ElMessageBox，保证全站确认交互视觉与行为一致：
 * - type=info（普通操作确认，蓝色问号）
 * - type=warning（业务警告，黄色叹号）
 * - type=danger（不可逆操作，红色叹号）
 * content 支持默认插槽自定义富内容（如病例上下文信息）
 */
import { computed } from 'vue'

const props = defineProps<{
  modelValue: boolean
  title: string
  /** 确认类型：info | warning | danger */
  type?: 'info' | 'warning' | 'danger'
  /** 正文说明文字（简单场景直接传文字） */
  content?: string
  /** 确认按钮文字 */
  confirmText?: string
  /** 取消按钮文字 */
  cancelText?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

/** 图标与主按钮配色映射（使用 CSS 变量以适配深浅色模式） */
const THEME = {
  info: { icon: 'QuestionFilled', color: 'var(--ad-primary)' },
  warning: { icon: 'WarningFilled', color: 'var(--ad-risk-mci)' },
  danger: { icon: 'CircleCloseFilled', color: 'var(--ad-risk-late)' }
} as const

const theme = computed(() => THEME[props.type ?? 'info'])
const btnType = computed(() => (props.type === 'danger' ? 'danger' : 'primary'))

function onConfirm(): void {
  visible.value = false
  emit('confirm')
}
function onCancel(): void {
  visible.value = false
  emit('cancel')
}
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="420px" align-center>
    <div class="flex gap-3 items-start">
      <el-icon :size="22" class="mt-0.5 shrink-0" :style="{ color: theme.color }">
        <component :is="theme.icon" />
      </el-icon>
      <div class="flex-1 text-[14px] leading-6 text-ink-secondary">
        <div>{{ content }}</div>
        <!-- 富内容插槽（病例信息 / 表单确认摘要等） -->
        <slot />
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-3">
        <el-button @click="onCancel">{{ cancelText ?? '取 消' }}</el-button>
        <el-button :type="btnType" @click="onConfirm">{{ confirmText ?? '确 认' }}</el-button>
      </div>
    </template>
  </el-dialog>
</template>
