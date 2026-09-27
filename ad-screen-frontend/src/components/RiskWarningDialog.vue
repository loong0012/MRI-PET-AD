<script setup lang="ts">
/**
 * 高风险警告弹窗（全站通用）
 * ------------------------------------------------------------------
 * AI 判定 AD 早期 / 中晚期高风险时触发，用于医疗安全兜底：
 * 医生必须勾选"已知晓并安排临床复核"后方可继续操作，
 * 防止高风险病例在流程中被遗漏处置。
 */
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: boolean
  /** 患者姓名 */
  patientName: string
  /** 病例编号 */
  caseId: string
  /** 风险等级展示文字（如"AD 早期"） */
  riskLabel: string
  /** 风险评分（0-100） */
  riskScore: number
}>()

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'acknowledged'): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

/** 医师知晓确认勾选（勾选后才能继续） */
const acknowledged = ref(false)

// 每次打开弹窗重置勾选状态，强制重新确认
watch(visible, (v) => {
  if (v) acknowledged.value = false
})
</script>

<template>
  <el-dialog v-model="visible" width="460px" align-center :show-close="false" :close-on-click-modal="false">
    <template #header>
      <div class="flex items-center gap-2 text-[#C94F4F] font-semibold">
        <el-icon :size="18"><CircleCloseFilled /></el-icon>
        <span>AI 高风险预警提示</span>
      </div>
    </template>

    <!-- 风险信息卡片 -->
    <div class="rounded-card border border-[#EFC6C6] bg-[#FAEDED] p-4">
      <div class="flex items-center justify-between">
        <span class="text-[15px] font-semibold text-ink">
          {{ patientName }}
          <span class="text-hint font-normal text-[13px] ml-2 font-num">{{ caseId }}</span>
        </span>
        <span class="font-num text-[22px] font-semibold text-[#C94F4F]">{{ riskScore }}<span class="text-xs ml-0.5">分</span></span>
      </div>
      <div class="mt-2 text-[13px] text-[#A04545] leading-6">
        AI 多模态融合模型判定该病例为
        <b>{{ riskLabel }}</b>
        ，建议尽快安排神经内科专科门诊复核，并完善脑脊液 / 血浆生物标志物检测。
      </div>
    </div>

    <!-- 强制知晓勾选 -->
    <el-checkbox v-model="acknowledged" class="mt-4">
      <span class="text-[13px] text-ink-secondary">我已知晓上述 AI 预警，并将安排临床复核处置</span>
    </el-checkbox>

    <template #footer>
      <div class="flex flex-col items-end gap-1.5">
        <div class="flex justify-end gap-3">
          <el-button :disabled="!acknowledged" @click="visible = false">关闭</el-button>
          <el-button type="danger" :disabled="!acknowledged" @click="emit('acknowledged'); visible = false">
            继续后续操作
          </el-button>
        </div>
        <div v-if="!acknowledged" class="text-[11px] text-[#A04545]">请先勾选上方知晓确认后，方可关闭此预警</div>
      </div>
    </template>
  </el-dialog>
</template>
