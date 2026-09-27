<script setup lang="ts">
/**
 * AI 四级风险等级标签（全站统一）
 * ------------------------------------------------------------------
 * 颜色语义与全站风险色一致：
 * low → 绿（低风险） / mci → 黄（轻度认知障碍）
 * ad-early → 橙（AD 早期） / ad-late → 红（AD 中晚期）
 * riskLevel 为 null 时展示"未分析"灰色标签（待 AI 分析病例）
 */
import { computed } from 'vue'
import type { RiskLevel } from '@/types/case'

const props = defineProps<{
  /** AI 风险等级（null = 尚未分析） */
  riskLevel: RiskLevel | null
  /** 展示尺寸（默认 default，阅片面板可用 large） */
  size?: 'default' | 'large'
}>()

interface LevelStyle {
  label: string
  color: string
  bg: string
  border: string
}

const LEVEL_MAP: Record<RiskLevel, LevelStyle> = {
  low: { label: '低风险', color: '#2E9E6B', bg: '#EDF7F2', border: '#BFE3D2' },
  mci: { label: '轻度认知障碍', color: '#B97E14', bg: '#FBF4E6', border: '#EAD5A8' },
  'ad-early': { label: 'AD 早期', color: '#C4581F', bg: '#FBF0E8', border: '#EFCDB4' },
  'ad-late': { label: 'AD 中晚期', color: '#C94F4F', bg: '#FAEDED', border: '#EFC6C6' }
}

const style = computed<LevelStyle | null>(() =>
  props.riskLevel ? LEVEL_MAP[props.riskLevel] : null
)
</script>

<template>
  <span
    v-if="style"
    class="inline-flex items-center rounded-full font-medium whitespace-nowrap"
    :class="size === 'large' ? 'px-3.5 h-7 text-[13px]' : 'px-2.5 h-6 text-xs'"
    :style="{ color: style.color, background: style.bg, border: `1px solid ${style.border}` }"
  >
    <span class="w-1.5 h-1.5 rounded-full mr-1.5" :style="{ background: style.color }" />
    {{ style.label }}
  </span>
  <span
    v-else
    class="inline-flex items-center rounded-full px-2.5 h-6 text-xs text-ink-muted bg-[#F1F3F6] border border-line font-medium whitespace-nowrap"
  >
    未分析
  </span>
</template>
