<script setup lang="ts">
/**
 * AI 免责声明提示条（全站强制组件）
 * ------------------------------------------------------------------
 * 所有渲染 AI 结果的页面（阅片分析 / AI 详情 / 报告）必须内置本提示，
 * 文案固定："AI 辅助筛查，不可替代临床诊断"，符合医疗 AI 软件合规要求。
 * variant：inline（页内横条） | plain（报告内嵌浅样式，打印时保留）
 */
defineProps<{
  variant?: 'inline' | 'plain'
}>()
</script>

<template>
  <!-- 页内横条样式 -->
  <div
    v-if="(variant ?? 'inline') === 'inline'"
    class="flex items-center gap-2 px-3.5 py-2 rounded-card text-[13px]"
    style="background: var(--ad-notify-warn-bg); border: 1px solid color-mix(in srgb, var(--ad-risk-mci) 35%, transparent);"
  >
    <el-icon class="shrink-0" style="color: var(--ad-risk-mci)"><WarningFilled /></el-icon>
    <span style="color: var(--ad-risk-mci)">
      AI 辅助筛查，不可替代临床诊断。结果需由专科医师结合临床资料综合判定。
    </span>
  </div>
  <!-- 报告内嵌样式（打印可见，样式克制） -->
  <div
    v-else
    class="flex items-center gap-1.5 px-3 py-2 rounded"
    style="background: var(--ad-notify-warn-bg); border: 1px solid color-mix(in srgb, var(--ad-risk-mci) 35%, transparent);"
  >
    <span class="text-[13px]" style="color: var(--ad-risk-mci)">⚠</span>
    <span class="text-[12px]" style="color: var(--ad-risk-mci)">AI 辅助筛查，不可替代临床诊断</span>
  </div>
</template>
