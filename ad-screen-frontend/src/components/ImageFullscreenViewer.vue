<script setup lang="ts">
/**
 * 全屏专一影像查看器（分析详情 / 报告页缩略图点击进入）
 * ------------------------------------------------------------------
 * 复用 MedicalViewport 全部专业阅片能力（切片滚动 / 窗宽窗位 / 缩放平移 /
 * 测量 / ROI 标注 / 方向标），在大尺寸画布中专一观看 MRI / PET / MRI-PET 融合。
 * 顶部工具栏支持：模态切换（MRI/PET/融合）、成像平面切换（轴/矢/冠）、
 * 切片滑杆、视图重置；ESC 或关闭按钮退出。
 */
import { computed, ref, watch } from 'vue'
import { caseSeed } from '@/utils/imaging'
import type { Orientation } from '@/api/imaging'
import MedicalViewport from './MedicalViewport.vue'
import type { ImagingMeta } from '@/api/imaging'

const props = defineProps<{
  modelValue: boolean
  caseId: string
  /** 初始模态 */
  initialModality?: 'MRI' | 'PET' | 'FUSION'
  /** 真实影像元信息（决定可切换的模态与平面） */
  meta: ImagingMeta | null
}>()

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v)
})

const modality = ref<'MRI' | 'PET' | 'FUSION'>(props.initialModality ?? 'MRI')
const orientation = ref<Orientation>('axial')
const sliceIdx = ref(64)

/** 病例级种子（MedicalViewport 内部 fallback 用） */
const seed = computed(() => caseSeed(props.caseId))

/** 当前模态是否有真实影像 */
const realAvailable = computed(() => {
  if (!props.meta?.available) return false
  if (modality.value === 'MRI') return props.meta.mri
  if (modality.value === 'PET') return props.meta.pet
  return props.meta.mri && props.meta.pet
})

/** 该模态对应平面的切片数 */
const sliceCount = computed(() => {
  if (!props.meta?.available) return 256
  return props.meta[`sliceCount${orientation.value.charAt(0).toUpperCase() + orientation.value.slice(1)}` as keyof ImagingMeta] as number ?? 128
})

/** 可用平面列表 */
const orientations = computed<Orientation[]>(() => props.meta?.orientations ?? ['axial', 'sagittal', 'coronal'])

const ORIENT_LABEL: Record<Orientation, string> = {
  axial: '轴位',
  sagittal: '矢状位',
  coronal: '冠状位'
}

/** 模态中文标签 */
const MODALITY_LABEL: Record<'MRI' | 'PET' | 'FUSION', string> = {
  MRI: 'MRI T1WI',
  PET: 'PET FDG',
  FUSION: 'MRI-PET 融合'
}

// 切换模态时重置到中层
watch(modality, () => {
  sliceIdx.value = Math.floor(sliceCount.value / 2)
})
// 切换平面时重置到中层
watch(orientation, () => {
  sliceIdx.value = Math.floor(sliceCount.value / 2)
})
// 组件常驻不销毁：再次打开弹窗传入新的初始模态时必须同步，否则仍显示上一次模态
watch(
  () => props.initialModality,
  (m) => {
    if (m) {
      modality.value = m
      orientation.value = 'axial'
      sliceIdx.value = Math.floor(sliceCount.value / 2)
    }
  }
)

function handleClose(): void {
  visible.value = false
}

// ESC 退出
function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') handleClose()
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`专一影像查看 · ${MODALITY_LABEL[modality]}`"
    width="92vw"
    top="3vh"
    :close-on-click-modal="false"
    :close-on-press-escape="true"
    append-to-body
    class="img-fullscreen-dialog"
    @keydown="onKeydown"
  >
    <!-- 工具栏 -->
    <div class="img-fs-toolbar">
      <el-radio-group v-model="modality" size="small">
        <el-radio-button value="MRI" :disabled="meta?.available && !meta.mri">MRI</el-radio-button>
        <el-radio-button value="PET" :disabled="meta?.available && !meta.pet">PET</el-radio-button>
        <el-radio-button value="FUSION" :disabled="meta?.available && !(meta.mri && meta.pet)">融合</el-radio-button>
      </el-radio-group>

      <el-divider direction="vertical" />

      <el-radio-group v-model="orientation" size="small">
        <el-radio-button
          v-for="o in orientations"
          :key="o"
          :value="o"
        >
          {{ ORIENT_LABEL[o] }}
        </el-radio-button>
      </el-radio-group>

      <el-divider direction="vertical" />

      <span class="text-xs text-hint whitespace-nowrap">
        切片 {{ sliceIdx + 1 }} / {{ sliceCount }}
      </span>
      <el-slider
        v-model="sliceIdx"
        :min="0"
        :max="sliceCount - 1"
        :step="1"
        class="!w-64 mx-2"
        size="small"
      />
    </div>

    <!-- 大尺寸视口（专一观看） -->
    <div class="img-fs-viewport">
      <MedicalViewport
        :modality="modality"
        :seed="seed"
        :slice-count="sliceCount"
        :show-roi="true"
        :label="MODALITY_LABEL[modality]"
        :slice-idx="sliceIdx"
        :real-case-id="caseId"
        :real-available="realAvailable"
        :orientation="orientation"
        :dicom-meta="meta?.dicomMeta ?? null"
        @update:slice-idx="sliceIdx = $event"
      />
    </div>

    <!-- 底部提示 -->
    <div class="img-fs-footer text-xs text-hint text-center mt-2">
      滚轮切换切片 · 拖拽调节窗宽窗位 · Ctrl+滚轮缩放 · ESC 退出
      <span v-if="!meta?.available" class="ml-2 text-[#D99A2B]">（当前为演示合成影像，无真实 NIfTI 数据）</span>
    </div>
  </el-dialog>
</template>

<style scoped>
.img-fs-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 12px;
  background: #F7FAFD;
  border: 1px solid #E6EEF5;
  border-radius: 8px;
  margin-bottom: 12px;
}
.img-fs-viewport {
  height: 70vh;
  min-height: 480px;
  background: #0B0F14;
  border-radius: 8px;
  overflow: hidden;
}
.img-fullscreen-dialog :deep(.el-dialog__body) {
  padding: 12px 16px 16px;
}
.img-fullscreen-dialog :deep(.el-dialog__header) {
  padding: 14px 16px 10px;
}
</style>
