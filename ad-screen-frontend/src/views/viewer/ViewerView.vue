<script setup lang="ts">
/**
 * 核心多模态阅片 + AI 分析页（四分区固定布局）
 * ------------------------------------------------------------------
 * 左上：MRI 原始影像（T1WI 灰度窗）
 * 右上：PET 原始影像（FDG 代谢伪彩）
 * 左下：MRI-PET 配准融合 + 海马/颞叶 ROI 高亮标注
 * 右下：AI 量化结果面板（风险评分 / 病程分期 / 量化指标 / 异常脑区 / 置信度）
 * 三个影像视口切片位置联动（同一解剖层面），窗宽窗位与工具各自独立；
 * 推理过程使用全局 AI 加载弹窗；高风险结果强制触发预警确认弹窗。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiGetCase } from '@/api/case'
import {
  apiRunInference,
  apiRerunInference,
  apiGetAnalysis,
  apiSaveAnalysis,
  InferenceCanceledError,
  type CancellableInference
} from '@/api/analysis'
import { apiGetImagingMeta, type ImagingMeta } from '@/api/imaging'
import { caseSeed, buildMip, jetColor, SLICE_SIZE } from '@/utils/imaging'
import { useTriplanarSync } from '@/composables/useTriplanarSync'
import { useViewerPrefs, type FitMode } from '@/composables/useViewerPrefs'
import { useUserStore } from '@/stores/user'
import type { CaseRecord } from '@/types/case'
import type { AnalysisResult, InferenceProgress } from '@/types/analysis'
import MedicalViewport from '@/components/MedicalViewport.vue'
// 异步加载 3D 体绘制组件，避免 Three.js（544KB）进入首屏 bundle
// 用户点击"3D 体绘制"按钮时才下载该 chunk
import { defineAsyncComponent } from 'vue'
const VolumeRenderer = defineAsyncComponent(() => import('@/components/VolumeRenderer.vue'))
import AiLoadingDialog from '@/components/AiLoadingDialog.vue'
import RiskWarningDialog from '@/components/RiskWarningDialog.vue'
import DisclaimerBar from '@/components/DisclaimerBar.vue'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import RoiAnnotationTool from '@/components/RoiAnnotationTool.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const caseId = computed(() => String(route.params.caseId ?? ''))

// ---------- 页面数据 ----------
const caseInfo = ref<CaseRecord | null>(null)
const analysis = ref<AnalysisResult | null>(null)
const pageLoading = ref(true)
const saving = ref(false)

/** 三个视口联动的切片位置（0 起） */
const sliceIdx = ref(128)
/** 病例稳定影像种子 */
const seed = computed(() => caseSeed(caseId.value))

// ---------- 真实 NIfTI 影像数据源 ----------
const imagingMeta = ref<ImagingMeta | null>(null)
/** 是否使用真实切片（有真实 MRI/PET 文件） */
const realOn = computed(() => !!imagingMeta.value?.available)
/** 视口切片总数：真实 128³ 体数据用 meta.sliceCount，否则合成 256 */
const viewSliceCount = computed(() => imagingMeta.value?.sliceCount ?? 256)

// ---------- P13/P17 三平面联动（共享状态方案 B） ----------
/** 三平面模式开关（真实影像不可用时禁用） */
const triplePlaneMode = ref(false)
/** 三平面共享单例：切片/悬停/十字线换算/canvas 注册表（视口组件间直连，绕开本页高频重渲染） */
const triSync = useTriplanarSync()
const { state: triState, sliceRefs, getCanvases } = triSync
const triAxial = sliceRefs.axial
const triSagittal = sliceRefs.sagittal
const triCoronal = sliceRefs.coronal
/** 全局适配模式（适应/填满/拉伸，localStorage 记忆） */
const { fitMode } = useViewerPrefs()
const FIT_OPTIONS: Array<{ key: FitMode; label: string; tip: string }> = [
  { key: 'contain', label: '适应', tip: '适应（contain）：完整显示，长边留黑边' },
  { key: 'cover', label: '填满', tip: '填满（cover）：铺满画布，超出部分裁切' },
  { key: 'fill', label: '拉伸', tip: '拉伸（fill）：非等比铺满，允许变形' }
]

// 进入三平面时注册层数（保持各轴当前位置，首次即中层）；退出时静默共享状态
watch(triplePlaneMode, (on) => {
  if (on) triSync.enter(viewSliceCount.value, false)
  else triSync.exit()
})
// 真实 meta 到达后层数由合成 256 切为真实 128：四分区定位中层，三平面同步重定位
watch(viewSliceCount, (n) => {
  sliceIdx.value = Math.floor(n / 2)
  if (triplePlaneMode.value) triSync.setCount(n)
})
/** 三平面合成截图：三个视口 canvas（含标注/十字线）+ 信息头合成一张 PNG */
function exportTriplanarPNG(): void {
  const map = getCanvases()
  const cells: Array<{ canvas: HTMLCanvasElement | undefined; label: string; layer: number }> = [
    { canvas: map.get('axial'), label: '轴位', layer: triState.slices.axial + 1 },
    { canvas: map.get('sagittal'), label: '矢状位', layer: triState.slices.sagittal + 1 },
    { canvas: map.get('coronal'), label: '冠状位', layer: triState.slices.coronal + 1 }
  ]
  if (cells.some((c) => !c.canvas)) {
    ElMessage.warning('视口尚未就绪，请稍后再试')
    return
  }
  const PAD = 16
  const GAP = 16
  const HEADER = 48
  const CELL = 400
  const W = PAD * 2 + CELL * 3 + GAP * 2
  const H = PAD + HEADER + CELL + PAD
  const out = document.createElement('canvas')
  out.width = W
  out.height = H
  const ctx = out.getContext('2d')!
  ctx.fillStyle = '#0b0f14'
  ctx.fillRect(0, 0, W, H)
  ctx.fillStyle = 'rgba(159,205,255,.92)'
  ctx.font = "600 15px Consolas, 'Microsoft YaHei', sans-serif"
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillText(`${caseId.value} · ${caseInfo.value?.patient.name ?? ''} · MRI 三平面联动`, PAD, PAD + 16)
  ctx.font = "12px Consolas, 'Microsoft YaHei', sans-serif"
  ctx.fillStyle = 'rgba(255,255,255,.6)'
  const layers = cells.map((c) => `${c.label} L${c.layer}`).join('  ·  ')
  const ts = new Date()
  const pad2 = (n: number): string => String(n).padStart(2, '0')
  const stamp = `${ts.getFullYear()}-${pad2(ts.getMonth() + 1)}-${pad2(ts.getDate())} ${pad2(ts.getHours())}:${pad2(ts.getMinutes())}`
  ctx.textAlign = 'right'
  ctx.fillText(`${layers}    ${stamp}`, W - PAD, PAD + 18)
  cells.forEach((c, i) => {
    const src = c.canvas!
    const x0 = PAD + i * (CELL + GAP)
    const y0 = PAD + HEADER
    ctx.fillStyle = '#000'
    ctx.fillRect(x0, y0, CELL, CELL)
    const k = Math.min(CELL / src.width, CELL / src.height)
    const dw = src.width * k
    const dh = src.height * k
    ctx.drawImage(src, x0 + (CELL - dw) / 2, y0 + (CELL - dh) / 2, dw, dh)
    ctx.strokeStyle = 'rgba(255,255,255,.14)'
    ctx.lineWidth = 1
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, CELL - 1, CELL - 1)
  })
  out.toBlob((blob) => {
    if (!blob) return
    const fname = `${caseId.value}_triplanar_${stamp.replace(/[-: ]/g, '').slice(0, 12)}.png`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fname
    a.click()
    URL.revokeObjectURL(url)
  }, 'image/png')
}

/** DICOM 元数据传给视口/ROI 的统一来源 */
const dicomMeta = computed(() => imagingMeta.value?.dicomMeta ?? null)

// ---------- 推理流程 ----------
const loadingVisible = ref(false)
const progress = ref<InferenceProgress | null>(null)
const warningVisible = ref(false)
/** 3D 体绘制弹窗 */
const volVisible = ref(false)
/** ROI 标注工具抽屉 */
const roiVisible = ref(false)
/** ROI 工具当前模态（默认 MRI，GradCAM 叠加仅 MRI 可用） */
const roiModality = ref<'MRI' | 'PET'>('MRI')

/** MIP 投影视窗（第 32 轮新增：调用 imaging.ts:buildMip 直接渲染投影结果） */
const mipVisible = ref(false)
/** MIP 投影模态（PET 默认：PET MIP 临床最常用，可整体观察脑代谢分布） */
const mipModality = ref<'PET' | 'MRI'>('PET')
/** MIP 投影层数（默认全部 256；真实模式由 viewSliceCount 决定） */
const mipSliceCount = computed(() => viewSliceCount.value)
/** MIP canvas 引用 */
const mipCanvas = ref<HTMLCanvasElement | null>(null)
/** MIP 是否使用伪彩（仅 PET 模式可用，沿用 jet 配色） */
const mipPseudoColor = ref(true)

/** 渲染 MIP 投影到 canvas */
function renderMip(): void {
  const canvas = mipCanvas.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  // 调用顶部已导入的 buildMip：仅在弹窗打开时计算（256³ 投影耗时约 50-100ms）
  const data = buildMip(seed.value, mipSliceCount.value, mipModality.value)
  // 内部离屏画布合成（避免直接操作主画布像素慢）
  const offscreen = document.createElement('canvas')
  offscreen.width = SLICE_SIZE
  offscreen.height = SLICE_SIZE
  const octx = offscreen.getContext('2d')!
  const imgData = octx.createImageData(SLICE_SIZE, SLICE_SIZE)
  const src = mipModality.value === 'PET' ? data.pet : data.gray
  if (mipModality.value === 'PET' && mipPseudoColor.value) {
    for (let i = 0; i < SLICE_SIZE * SLICE_SIZE; i++) {
      const v = src[i] / 255
      const [r, g, b] = jetColor(v)
      imgData.data[i * 4] = r
      imgData.data[i * 4 + 1] = g
      imgData.data[i * 4 + 2] = b
      imgData.data[i * 4 + 3] = 255
    }
  } else {
    for (let i = 0; i < SLICE_SIZE * SLICE_SIZE; i++) {
      imgData.data[i * 4] = src[i]
      imgData.data[i * 4 + 1] = src[i]
      imgData.data[i * 4 + 2] = src[i]
      imgData.data[i * 4 + 3] = 255
    }
  }
  octx.putImageData(imgData, 0, 0)
  // 缩放绘制到主 canvas
  canvas.width = SLICE_SIZE
  canvas.height = SLICE_SIZE
  ctx.imageSmoothingEnabled = true
  ctx.drawImage(offscreen, 0, 0)
}

/** 导出当前 MIP PNG（含模态+伪彩标识） */
function exportMipPNG(): void {
  const canvas = mipCanvas.value
  if (!canvas) return
  const a = document.createElement('a')
  a.download = `MIP_${mipModality.value}_${mipPseudoColor.value ? 'pseudo' : 'gray'}_${caseId.value}.png`
  a.href = canvas.toDataURL('image/png')
  a.click()
}

// 弹窗打开 / 模态切换 / 切片总数变化时重绘
watch([mipVisible, mipModality, mipPseudoColor, mipSliceCount], () => {
  if (mipVisible.value) {
    nextTick(renderMip)
  }
})

/** 键盘快捷键（仅在焦点不在输入控件时生效）
 * - ←/→：上一/下一层切片
 * - ↑/↓：跳到首尾层
 * - m：切换 MIP 弹窗
 * - v：切换 3D 体绘制弹窗
 */
function onKeyDown(e: KeyboardEvent): void {
  // 输入控件（input/textarea/contenteditable）聚焦时跳过，避免影响录入
  const tag = (e.target as HTMLElement)?.tagName?.toLowerCase() ?? ''
  if (tag === 'input' || tag === 'textarea' || (e.target as HTMLElement)?.isContentEditable) return
  // 弹窗打开时不响应切片导航，避免与 Element Plus 弹窗交互冲突
  if (volVisible.value || roiVisible.value || mipVisible.value) return
  // 三平面模式下切片位置由 useTriplanarSync 独立管理，跳过本页按键
  if (triplePlaneMode.value) return
  const total = viewSliceCount.value
  if (total <= 0) return
  switch (e.key) {
    case 'ArrowLeft':
      e.preventDefault()
      sliceIdx.value = Math.max(0, sliceIdx.value - 1)
      break
    case 'ArrowRight':
      e.preventDefault()
      sliceIdx.value = Math.min(total - 1, sliceIdx.value + 1)
      break
    case 'ArrowUp':
      e.preventDefault()
      sliceIdx.value = 0
      break
    case 'ArrowDown':
      e.preventDefault()
      sliceIdx.value = total - 1
      break
    case 'm':
    case 'M':
      e.preventDefault()
      mipVisible.value = true
      break
    case 'v':
    case 'V':
      e.preventDefault()
      if (realOn.value) volVisible.value = true
      break
  }
}

/** 启动/重新发起多模态 AI 融合分析（同一时刻仅允许一个在途推理） */
let inferenceHandle: CancellableInference | null = null

/** 推理异常提示：取消类按场景区分，axios 错误已由请求层统一弹过，不再重复 toast */
function handleInferenceError(e: unknown): void {
  if (e instanceof InferenceCanceledError) {
    if (e.reason === 'user') ElMessage.info('已取消本次推理（后端任务可能仍在执行，稍后可刷新查看结果）')
    return
  }
  if (e && typeof e === 'object' && 'response' in e) return
  ElMessage.error(e instanceof Error ? e.message : '推理失败，请重试')
}

async function executeInference(rerun: boolean): Promise<void> {
  if (!caseInfo.value) return
  if (loadingVisible.value) return
  loadingVisible.value = true
  progress.value = null
  const runner = rerun ? apiRerunInference : apiRunInference
  const handle = runner(
    caseInfo.value,
    userStore.userInfo?.username ?? 'unknown',
    (p) => (progress.value = p)
  )
  inferenceHandle = handle
  try {
    const result = await handle
    analysis.value = result
    ElMessage.success(rerun ? '重新推理完成' : '多模态融合分析完成')
    // AD 早期 / 中晚期：强制高风险预警确认
    if (result.riskLevel === 'ad-early' || result.riskLevel === 'ad-late') {
      warningVisible.value = true
    }
  } catch (e) {
    handleInferenceError(e)
  } finally {
    loadingVisible.value = false
    inferenceHandle = null
  }
}

/** 启动多模态 AI 融合分析（首次） */
function runInference(): Promise<void> {
  return executeInference(false)
}

/** 重新推理（覆盖旧结果） */
function rerunInference(): Promise<void> {
  return executeInference(true)
}

/** 用户在加载弹窗中主动取消 */
function cancelInference(): void {
  inferenceHandle?.cancel('user')
}

// 推理中离开页面：二次确认，避免高危预警随组件卸载丢失
onBeforeRouteLeave(async () => {
  if (!loadingVisible.value) return true
  try {
    await ElMessageBox.confirm(
      'AI 推理仍在进行中，离开本页将取消进度显示且无法弹出高风险预警确认，确定离开吗？',
      '推理进行中',
      { confirmButtonText: '仍要离开', cancelButtonText: '继续等待', type: 'warning' }
    )
    return true
  } catch {
    return false
  }
})

// 关闭/刷新浏览器标签时同样拦截
function onBeforeUnload(e: BeforeUnloadEvent): void {
  if (!loadingVisible.value) return
  e.preventDefault()
  e.returnValue = ''
}
window.addEventListener('beforeunload', onBeforeUnload)
// 键盘快捷键：切片导航 + 弹窗切换（弹窗打开/输入聚焦时自动跳过）
window.addEventListener('keydown', onKeyDown)

onBeforeUnmount(() => {
  // 组件销毁（含同路由切病例）：静默取消 SSE、清理三平面模块单例与 beforeunload/keydown
  inferenceHandle?.cancel('unmount')
  inferenceHandle = null
  triSync.exit()
  window.removeEventListener('beforeunload', onBeforeUnload)
  window.removeEventListener('keydown', onKeyDown)
})

/** 保存 AI 分析结果 */
async function saveResult(): Promise<void> {
  if (!caseInfo.value || !analysis.value) return
  saving.value = true
  try {
    await apiSaveAnalysis(caseInfo.value, analysis.value, userStore.userInfo?.username ?? 'unknown')
    ElMessage.success('分析结果已保存')
  } finally {
    saving.value = false
  }
}

/** 生成报告（跳转报告页） */
function goReport(): void {
  if (!analysis.value) {
    ElMessage.warning('请先完成 AI 融合分析')
    return
  }
  router.push(`/report/${caseId.value}`)
}

/** 返回病例库 */
function goBack(): void {
  router.push('/cases')
}

// ---------- 风险分数量表颜色 ----------
const scoreColor = computed<string>(() => {
  const s = analysis.value?.riskScore ?? 0
  if (s >= 80) return '#C94F4F'
  if (s >= 62) return '#D96B2B'
  if (s >= 35) return '#D99A2B'
  return '#2E9E6B'
})

/** 关键量化指标（面板快捷卡片） */
const keyMetrics = computed(() => {
  const a = analysis.value
  if (!a) return []
  return [
    { label: '左侧海马体积', value: `${a.hippocampusVolumeL.toFixed(2)} cm³` },
    { label: '右侧海马体积', value: `${a.hippocampusVolumeR.toFixed(2)} cm³` },
    { label: '全脑代谢 SUV', value: a.meanSUV.toFixed(2) },
    { label: '皮层平均厚度', value: `${a.corticalThickness.toFixed(2)} mm` }
  ]
})

/** 指标状态色（与报告指标一致） */
function metricDot(status: 'normal' | 'warn' | 'abnormal'): string {
  return status === 'normal' ? '#2E9E6B' : status === 'warn' ? '#D99A2B' : '#C94F4F'
}

onMounted(async () => {
  try {
    caseInfo.value = (await apiGetCase(caseId.value)) ?? null
    if (!caseInfo.value) {
      ElMessage.error('病例不存在')
      goBack()
      return
    }
    // 已有结果则直接载入展示
    apiGetAnalysis(caseId.value)
      .then((r) => (analysis.value = r))
      .catch(() => (analysis.value = null))

    // 拉取真实 NIfTI 影像元信息（失败/无文件则回退合成影像）；
    // 层数变化（256→128）的中层定位由上方 viewSliceCount watcher 统一处理
    apiGetImagingMeta(caseId.value)
      .then((meta) => {
        imagingMeta.value = meta
      })
      .catch(() => (imagingMeta.value = null))
  } finally {
    pageLoading.value = false
  }
})
</script>

<template>
  <div v-loading="pageLoading" class="h-full flex flex-col p-3 gap-3 bg-page">
    <!-- ==================== 顶部病例信息条 ==================== -->
    <div class="card-ad h-12 shrink-0 flex items-center px-4 gap-4">
      <el-button :icon="'Back'" circle @click="goBack" />
      <div class="flex items-center gap-3">
        <span class="font-num text-[15px] font-semibold text-ink">{{ caseInfo?.id ?? '—' }}</span>
        <span class="text-[14px] text-ink font-medium">{{ caseInfo?.patient.name }}</span>
        <span class="text-[13px] text-sub">
          {{ caseInfo?.patient.gender === 'M' ? '男' : '女' }} / {{ caseInfo?.patient.age }} 岁
        </span>
        <span class="text-[12px] text-hint font-num">P号 {{ caseInfo?.patient.patientNo }}</span>
      </div>
      <el-divider direction="vertical" />
      <span class="text-[12px] text-sub">检查时间：<span class="font-num">{{ caseInfo?.examDate }}</span></span>
      <span class="text-[12px] text-sub">申请科室：{{ caseInfo?.department }}</span>
      <span v-if="analysis" class="text-[12px] text-hint">模型 {{ analysis.modelVersion }} · 推理耗时 {{ analysis.inferenceTime }}s</span>
      <div class="flex-1" />
      <!-- 影像适配模式（全局生效 + localStorage 记忆）：适应 / 填满 / 拉伸 -->
      <div class="flex items-center rounded border border-line overflow-hidden">
        <el-tooltip
          v-for="opt in FIT_OPTIONS"
          :key="opt.key"
          :content="opt.tip"
          placement="bottom"
        >
          <button
            class="px-2.5 h-7 text-[12px] leading-none transition-colors"
            :class="fitMode === opt.key ? 'bg-primary text-white' : 'bg-white text-sub hover:bg-fill-2'"
            @click="fitMode = opt.key"
          >
            {{ opt.label }}
          </button>
        </el-tooltip>
      </div>
      <!-- 三平面合成截图：仅三平面模式显示 -->
      <el-tooltip content="导出三平面合成截图 PNG" placement="bottom">
        <el-button v-if="triplePlaneMode" plain :icon="'Camera'" @click="exportTriplanarPNG">截图</el-button>
      </el-tooltip>
      <!-- 三平面模式开关：仅真实影像可用，否则灰显禁用 -->
      <el-tooltip
        :content="realOn ? '切换三平面（轴位/矢状位/冠状位）联动阅片' : '真实影像不可用，三平面模式禁用'"
        placement="bottom"
      >
        <div class="flex items-center gap-1.5 px-2 py-1 rounded border border-line">
          <span class="text-[12px]" :class="realOn ? 'text-ink' : 'text-hint'">三平面</span>
          <el-switch v-model="triplePlaneMode" :disabled="!realOn" size="small" />
        </div>
      </el-tooltip>
      <el-button v-if="realOn" plain :icon="'View'" @click="volVisible = true">3D 体绘制</el-button>
      <el-tooltip content="最大密度投影 MIP（PET 默认；可切 MRI；快捷键 M）" placement="bottom">
        <el-button plain :icon="'Histogram'" @click="mipVisible = true">MIP 投影</el-button>
      </el-tooltip>
      <el-button plain :icon="'Aim'" @click="roiVisible = true">ROI 标注</el-button>
      <RiskLevelTag v-if="analysis" :risk-level="analysis.riskLevel" />
    </div>

    <!-- ==================== 主工作区（三平面 / 四分区切换） ==================== -->
    <!-- 三平面模式：三视口并排（轴位/矢状位/冠状位） -->
    <div v-if="triplePlaneMode" class="flex-1 min-h-0 grid grid-cols-3 grid-rows-1 gap-3">
      <MedicalViewport
        v-model:slice-idx="triAxial"
        modality="MRI"
        label="真实 MRI · 轴位 Axial"
        :seed="seed"
        :disabled="!caseInfo?.hasMRI"
        :slice-count="viewSliceCount"
        :real-case-id="realOn ? caseId : ''"
        :real-available="!!imagingMeta?.mri"
        orientation="axial"
        :dicom-meta="dicomMeta"
        link-on-click
      />
      <MedicalViewport
        v-model:slice-idx="triSagittal"
        modality="MRI"
        label="真实 MRI · 矢状位 Sagittal"
        :seed="seed"
        :disabled="!caseInfo?.hasMRI"
        :slice-count="viewSliceCount"
        :real-case-id="realOn ? caseId : ''"
        :real-available="!!imagingMeta?.mri"
        orientation="sagittal"
        :dicom-meta="dicomMeta"
        link-on-click
      />
      <MedicalViewport
        v-model:slice-idx="triCoronal"
        modality="MRI"
        label="真实 MRI · 冠状位 Coronal"
        :seed="seed"
        :disabled="!caseInfo?.hasMRI"
        :slice-count="viewSliceCount"
        :real-case-id="realOn ? caseId : ''"
        :real-available="!!imagingMeta?.mri"
        orientation="coronal"
        :dicom-meta="dicomMeta"
        link-on-click
      />
    </div>

    <!-- 四分区主工作区 -->
    <div v-else class="flex-1 min-h-0 grid grid-cols-2 grid-rows-2 gap-3">
      <!-- 左上：MRI -->
      <MedicalViewport
        v-model:slice-idx="sliceIdx"
        modality="MRI"
        :label="realOn ? '真实 MRI · T1WI 轴位' : 'MRI 原始影像 · T1WI'"
        :seed="seed"
        :disabled="!caseInfo?.hasMRI"
        :slice-count="viewSliceCount"
        :real-case-id="realOn ? caseId : ''"
        :real-available="!!imagingMeta?.mri"
        :dicom-meta="dicomMeta"
        :hippocampus-volumes="analysis ? { left: analysis.hippocampusVolumeL, right: analysis.hippocampusVolumeR } : null"
      />

      <!-- 右上：PET -->
      <MedicalViewport
        v-model:slice-idx="sliceIdx"
        modality="PET"
        :label="realOn ? '真实 PET · FDG 代谢轴位' : 'PET 原始影像 · FDG 代谢'"
        :seed="seed"
        :disabled="!caseInfo?.hasPET"
        :slice-count="viewSliceCount"
        :real-case-id="realOn ? caseId : ''"
        :real-available="!!imagingMeta?.pet"
        :dicom-meta="dicomMeta"
      />

      <!-- 左下：MRI-PET 融合 + ROI（真实影像不叠加合成 ROI；异常脑区热力图叠加） -->
      <MedicalViewport
        v-model:slice-idx="sliceIdx"
        modality="FUSION"
        :label="realOn ? '真实 MRI-PET 配准融合' : 'MRI-PET 配准融合 · ROI 脑区分割'"
        :seed="seed"
        :show-roi="!realOn"
        :disabled="!caseInfo?.hasMRI || !caseInfo?.hasPET"
        :slice-count="viewSliceCount"
        :real-case-id="realOn ? caseId : ''"
        :real-available="!!imagingMeta?.mri && !!imagingMeta?.pet"
        :dicom-meta="dicomMeta"
        :heatmap-regions="analysis?.abnormalRegions ?? []"
      />

      <!-- 右下：AI 量化结果面板 -->
      <div class="card-ad flex flex-col min-h-0">
        <div class="card-ad__header shrink-0">
          <span class="card-ad__title">AI 量化分析结果</span>
          <span class="text-[11px] text-hint font-num">{{ analysis?.fusionStrategy === 'feature' ? '特征级融合' : '像素级融合' }}</span>
        </div>

        <div class="flex-1 min-h-0 overflow-y-auto p-4 space-y-3.5">
          <!-- ===== 未分析状态 ===== -->
          <div v-if="!analysis" class="h-full flex flex-col items-center justify-center gap-4 text-center">
            <div class="w-14 h-14 rounded-full bg-primary-light flex items-center justify-center">
              <el-icon :size="28" class="text-primary"><Cpu /></el-icon>
            </div>
            <div>
              <div class="text-[15px] text-ink font-medium">尚未进行多模态融合分析</div>
              <div class="mt-1 text-[12px] text-hint leading-5">将 MRI 形态学特征与 PET 代谢特征<br />输入 TransMF 融合模型生成 AD 风险评估</div>
            </div>
            <el-button type="primary" size="large" :icon="'Cpu'" :loading="loadingVisible" :disabled="!caseInfo?.hasMRI && !caseInfo?.hasPET" @click="runInference">
              启动多模态 AI 融合分析
            </el-button>
            <div v-if="!caseInfo?.hasMRI && !caseInfo?.hasPET" class="text-xs text-hint">该病例暂无影像数据，无法分析</div>
          </div>

          <!-- ===== 已分析结果 ===== -->
          <template v-else>
            <!-- 风险评分 + 分期 -->
            <div class="flex items-center gap-4">
              <div class="shrink-0 text-center">
                <div class="font-num text-[40px] leading-none font-semibold" :style="{ color: scoreColor }">
                  {{ analysis.riskScore }}
                </div>
                <div class="mt-1 text-[11px] text-hint">AD 风险评分 / 100</div>
              </div>
              <el-divider direction="vertical" class="!h-14" />
              <div class="flex-1 space-y-1.5">
                <div class="flex items-center gap-2">
                  <RiskLevelTag :risk-level="analysis.riskLevel" size="large" />
                </div>
                <div class="text-[13px] text-ink font-medium">{{ analysis.stage }}</div>
                <div class="flex items-center gap-2 text-[12px] text-sub">
                  置信度
                  <el-progress
                    :percentage="Number((analysis.confidence * 100).toFixed(1))"
                    :stroke-width="8"
                    :show-text="false"
                    class="!flex-1"
                  />
                  <span class="font-num">{{ (analysis.confidence * 100).toFixed(1) }}%</span>
                </div>
              </div>
            </div>

            <!-- 病程描述 -->
            <div class="text-[12.5px] text-sub leading-6 px-3 py-2.5 rounded-lg bg-page border border-line">
              {{ analysis.stageDesc }}
            </div>

            <!-- 关键量化指标 -->
            <div>
              <div class="text-[12px] text-hint mb-2 font-medium">关键量化指标</div>
              <div class="grid grid-cols-2 gap-2">
                <div v-for="m in keyMetrics" :key="m.label" class="px-3 py-2 rounded-lg bg-page border border-line">
                  <div class="text-[11px] text-hint">{{ m.label }}</div>
                  <div class="font-num text-[15px] text-ink font-medium mt-0.5">{{ m.value }}</div>
                </div>
              </div>
            </div>

            <!-- 全量指标明细表 -->
            <div>
              <div class="text-[12px] text-hint mb-2 font-medium">指标明细（含参考范围）</div>
              <table class="w-full text-[12px]">
                <thead>
                  <tr class="text-left text-hint border-b border-line">
                    <th class="py-1.5 font-medium">指标</th>
                    <th class="py-1.5 font-medium text-right">数值</th>
                    <th class="py-1.5 font-medium text-right">参考范围</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="m in analysis.metrics" :key="m.key" class="border-b border-line/60">
                    <td class="py-1.5 text-ink flex items-center gap-1.5">
                      <span class="w-1.5 h-1.5 rounded-full shrink-0" :style="{ background: metricDot(m.status) }" />
                      {{ m.label }}
                    </td>
                    <td class="py-1.5 text-right font-num text-ink">{{ m.value }} {{ m.unit }}</td>
                    <td class="py-1.5 text-right font-num text-hint">{{ m.refRange }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 异常脑区明细 -->
            <div>
              <div class="text-[12px] text-hint mb-2 font-medium">
                异常脑区明细（{{ analysis.abnormalRegions.length }} 处）
              </div>
              <div v-if="analysis.abnormalRegions.length === 0" class="text-[12px] text-hint py-2">
                未见显著异常脑区
              </div>
              <el-table v-else :data="analysis.abnormalRegions" size="small" class="!w-full" max-height="180">
                <el-table-column prop="region" label="脑区" min-width="86" />
                <el-table-column prop="side" label="侧别" width="58" />
                <el-table-column prop="atrophy" label="萎缩程度" width="86" />
                <el-table-column label="代谢(rCMRglc)" width="108" align="right">
                  <template #default="{ row }">
                    <span class="font-num">{{ row.metabolism.toFixed(2) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="Z 值" width="64" align="right">
                  <template #default="{ row }">
                    <span class="font-num text-[#C94F4F]">{{ row.zScore.toFixed(1) }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <!-- 免责提示（强制内置） -->
            <DisclaimerBar />
          </template>
        </div>

        <!-- ===== 操作按钮区 ===== -->
        <div v-if="analysis" class="shrink-0 px-4 py-3 border-t border-line flex items-center gap-2.5">
          <el-button :icon="'RefreshRight'" :disabled="loadingVisible" @click="rerunInference">重新推理</el-button>
          <el-button type="primary" plain :icon="'FolderChecked'" :loading="saving" @click="saveResult">保存结果</el-button>
          <div class="flex-1" />
          <el-button type="primary" :icon="'Document'" @click="goReport">生成报告</el-button>
        </div>
      </div>
    </div>

    <!-- ==================== 全局弹窗 ==================== -->
    <el-dialog v-model="volVisible" title="3D 体绘制 · MRI-PET 融合与重点脑区标注" width="860px" destroy-on-close>
      <div style="height: 540px">
        <VolumeRenderer
          v-if="volVisible"
          :case-id="caseId"
          initial-modality="MRI"
          :abnormal-regions="analysis?.abnormalRegions ?? []"
        />
      </div>
      <template #footer>
        <span class="text-[11px] text-hint">
          脑区标注基于标准脑图谱近似位置，仅供示教理解；融合视图叠加 PET 代谢信息，红色 ⚠ 标记为 AI 提示异常的重点脑区
        </span>
      </template>
    </el-dialog>

    <!-- ==================== MIP 投影视窗（第 32 轮新增） ==================== -->
    <el-dialog
      v-model="mipVisible"
      title="MIP 最大密度投影 · 多层最大池化投影"
      width="640px"
      destroy-on-close
      @opened="renderMip"
    >
      <div class="space-y-3">
        <!-- 控件栏 -->
        <div class="flex items-center gap-3 flex-wrap">
          <div class="flex items-center gap-2">
            <span class="text-[12px] text-hint">模态</span>
            <el-radio-group v-model="mipModality" size="small">
              <el-radio-button label="PET">PET · FDG 代谢</el-radio-button>
              <el-radio-button label="MRI">MRI · T1 结构</el-radio-button>
            </el-radio-group>
          </div>
          <el-checkbox v-model="mipPseudoColor" :disabled="mipModality === 'MRI'">
            PET 伪彩（jet 配色）
          </el-checkbox>
          <div class="flex-1" />
          <el-button size="small" :icon="'Download'" @click="exportMipPNG">导出 PNG</el-button>
        </div>
        <!-- MIP canvas -->
        <div class="flex items-center justify-center bg-black rounded-lg p-4" style="height: 460px">
          <canvas ref="mipCanvas" class="max-w-full max-h-full" />
        </div>
        <!-- 说明 -->
        <div class="text-[11px] text-hint leading-5">
          MIP（Maximum Intensity Projection）：沿 Z 轴对全部切片做最大池化投影，整体观察代谢/结构分布。
          PET MIP 临床最常用，可整体观察双侧皮层代谢对称性与异常浓聚/减低区。
          <span class="text-[var(--ad-warn,#D97A2B)]">注：演示数据投影结果仅供视口验证；真实 NIfTI 数据需后端体积渲染支持。</span>
        </div>
      </div>
    </el-dialog>
    <el-drawer
      v-model="roiVisible"
      title="影像 ROI 标注工具"
      direction="rtl"
      size="780px"
      destroy-on-close
    >
      <div style="height: calc(100vh - 120px)">
        <RoiAnnotationTool
          v-if="roiVisible"
          :case-id="caseId"
          :slice-index="sliceIdx"
          :modality="roiModality"
          :dicom-meta="dicomMeta"
          @roi-created="(item) => ElMessage.success(`标注已保存：${item.label || item.roiType}`)"
          @roi-deleted="() => {}"
          @toggle-gradcam="(on) => ElMessage.info(on ? '已叠加 GradCAM 热图' : '已关闭 GradCAM 叠加')"
          @jump-slice="(idx) => (sliceIdx = Math.max(0, Math.min(viewSliceCount - 1, idx)))"
        />
      </div>
      <template #footer>
        <span class="text-[11px] text-hint">
          支持 球形/矩形/多边形 ROI；与 GradCAM 热图对比用于病灶定位；可导出 JSON 用于训练数据
        </span>
      </template>
    </el-drawer>
    <AiLoadingDialog v-model="loadingVisible" :progress="progress" :case-id="caseId" @cancel="cancelInference" />
    <RiskWarningDialog
      v-if="analysis"
      v-model="warningVisible"
      :patient-name="caseInfo?.patient.name ?? ''"
      :case-id="caseId"
      :risk-label="analysis.riskLevel === 'ad-late' ? 'AD 中晚期' : 'AD 早期'"
      :risk-score="analysis.riskScore"
      @acknowledged="ElMessage.success('已确认预警，请及时安排临床复核')"
    />
  </div>
</template>
