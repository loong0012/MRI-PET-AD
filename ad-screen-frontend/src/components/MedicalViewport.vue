<script setup lang="ts">
/**
 * 专业医学影像视口组件（阅片四分区共用）
 * ------------------------------------------------------------------
 * 对标神经影像工作站的视口交互，单模态自包含：
 * 1. 切片滚动：鼠标滚轮逐层浏览 + 底部切片滑杆定位（多视口联动由父级同步 sliceIdx）
 * 2. 窗宽窗位：激活"W/L"工具后拖拽调节（水平=窗宽 / 垂直=窗位）
 * 3. 缩放：Ctrl+滚轮 / 工具栏按钮；平移：激活"平移"工具拖拽
 * 4. 测量：激活"测量"工具拖拽画线，自动换算实际距离（mm）
 * 5. 视图重置：一键恢复窗宽窗位/缩放/平移
 * 6. ROI 叠加：海马 / 颞叶关键脑区椭圆高亮标注（融合视口启用）
 * 7. 医学角标：模态 / 层号 / WW-WC / 缩放倍率 / 方向标（R/L/A/P）
 * 渲染管线与真实 DICOM 一致：灰度窗宽窗位映射 + PET Jet 伪彩 + 融合 Alpha 叠加
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { applyWWWC, generateSliceData, getRoiShapes, getHeatmapBlobs, jetColor, SLICE_SIZE, type SliceData, type HeatmapRegion } from '@/utils/imaging'
import { loadSliceGray, loadGradcam, type Orientation, type DicomMeta } from '@/api/imaging'
import { BRAIN_REGIONS, getRegionAnchors, regionSliceFade, toSlicePx, type BrainRegion } from '@/utils/brainRegions'
import DicomMetaBar from '@/components/DicomMetaBar.vue'
import { useViewerPrefs } from '@/composables/useViewerPrefs'
import { useTriplanarSync, type Axis3, type ScreenAxis } from '@/composables/useTriplanarSync'

/** 全局阅片偏好（适配模式，所有视口共享 + localStorage 记忆） */
const { fitMode } = useViewerPrefs()
/** 三平面联动共享状态（仅 linkOnClick 视口接入） */
const triSync = useTriplanarSync()

const props = withDefaults(
  defineProps<{
    /** 视口模态：MRI 灰度 / PET 伪彩 / FUSION 融合叠加 */
    modality: 'MRI' | 'PET' | 'FUSION'
    /** 影像种子（病例级稳定，决定个体形态） */
    seed: number
    /** 序列切片总数 */
    sliceCount?: number
    /** 是否叠加 ROI 脑区标注 */
    showRoi?: boolean
    /** 视口左上标题 */
    label: string
    /** 当前联动切片号（0 起，父级同步） */
    sliceIdx: number
    /** 该模态影像缺失（显示占位） */
    disabled?: boolean
    /** 真实 NIfTI 影像病例 id（传入且 realAvailable 时启用真实切片数据源） */
    realCaseId?: string
    /** 本视口模态是否有真实影像（MRI 视口=mri 可用，PET 视口=pet 可用，FUSION=两者可用） */
    realAvailable?: boolean
    /** 成像平面方向（仅真实模式生效） */
    orientation?: Orientation
    /** AI 分析异常脑区列表（非空时可叠加热力图） */
    heatmapRegions?: HeatmapRegion[]
    /** 海马体积（左右，cm³），传入时可叠加海马定量标注 */
    hippocampusVolumes?: { left: number; right: number } | null
    /** DICOM 元数据（顶部信息条显示；真实影像有，合成/历史病例为 null） */
    dicomMeta?: DicomMeta | null
    /**
     * 三平面联动十字线（影像空间 0..1 比例坐标）。
     * null 时不显示；非 null 时在画布上叠加 col/row 两条细线（颜色 #3b82f6 50% 透明）。
     */
    crosshair?: { col: number; row: number } | null
    /**
     * 单击联动模式（三平面布局开启时由父组件置 true）。
     * 开启后无需切换测量工具：在画布上「单击」（位移 ≤ CLICK_THRESHOLD_PX）
     * 即向父组件 emit image-click 驱动另两视口切片联动；
     * 拖拽仍按当前工具处理（window=调窗 / pan=平移 / measure=测距），
     * click 与 drag 通过位移阈值分离，避免误触。
     */
    linkOnClick?: boolean
  }>(),
  { sliceCount: 256, showRoi: false, disabled: false, realCaseId: '', realAvailable: false, orientation: 'axial', heatmapRegions: () => [], hippocampusVolumes: null, dicomMeta: null, crosshair: null, linkOnClick: false }
)

const emit = defineEmits<{
  (e: 'update:sliceIdx', v: number): void
  /** 真实模式下用户在画布上点击（用于三平面联动）；payload 为影像空间像素坐标 */
  (e: 'image-click', px: number, py: number): void
}>()

/** 当前平面方向（真实模式可切换；合成模式固定轴位） */
const orientation = ref<Orientation>(props.orientation)
/** 平面方向中文标签 */
const ORIENT_LABEL: Record<Orientation, string> = {
  axial: '轴位 Axial',
  sagittal: '矢状位 Sagittal',
  coronal: '冠状位 Coronal'
}
/** 切换平面方向（真实模式下，重置到该方向中层） */
function setOrientation(o: Orientation) {
  if (!useReal.value) return
  if (orientation.value === o) return
  orientation.value = o
  // 切到新平面时定位中层
  emit('update:sliceIdx', Math.floor((props.sliceCount ?? 256) / 2))
  draw()
}

// ---------- 三平面十字线（共享状态） ----------
/** 三平面槽位固定身份（由 prop 决定，与父级联动映射一致） */
const triOri = computed<Axis3>(() => props.orientation as Axis3)
/** 已提交十字线：三平面联动走共享单例；其他场景保留 props.crosshair 通道 */
const committedCrosshair = computed<{ col: number; row: number } | null>(() => {
  if (props.linkOnClick && triSync.state.enabled) return triSync.crosshairFor(triOri.value)
  return props.crosshair
})
/** hover 预览十字线：其他视口悬停时本视口实时显示（琥珀色），自身悬停不显示 */
const previewCrosshair = computed<{ col: number; row: number } | null>(() =>
  props.linkOnClick && triSync.state.enabled ? triSync.previewFor(triOri.value) : null
)

// ---------- 视图状态 ----------
/** 默认窗宽窗位（MRI：T1 软组织窗；PET：伪彩全窗） */
const DEFAULT_WL: Record<'MRI' | 'PET' | 'FUSION', { ww: number; wc: number }> = {
  MRI: { ww: 200, wc: 110 },
  PET: { ww: 220, wc: 120 },
  FUSION: { ww: 200, wc: 110 }
}
const wl = reactive({ ...DEFAULT_WL[props.modality] })
const zoom = ref(1)
const pan = reactive({ x: 0, y: 0 })
type Tool = 'window' | 'pan' | 'measure' | 'roi'
const tool = ref<Tool>('window')

/** W/L 拖拽实时数值气泡（屏幕坐标相对画布区；visible 仅 window 拖拽期间） */
const wlBubble = reactive({ visible: false, x: 0, y: 0, ww: 200, wc: 110 })
/** 平移越界微光提示（影像被 pan 拉出、露出背景的那一侧发亮） */
const edgeGlow = reactive({ l: false, r: false, t: false, b: false })
/** 十字线热区光标样式（col-resize 竖线 / row-resize 横线 / move 交点；空串=默认） */
const crossCursor = ref('')
/** 平移越界判定阈值（CSS px）并在 pan/缩放后刷新四条边 */
function refreshEdgeGlow(): void {
  // 适配模式自带的居中黑边不算越界；仅当 pan 在此基础上又拉出 >4px 空隙时提示
  edgeGlow.l = pan.x > 4
  edgeGlow.r = pan.x < -4
  edgeGlow.t = pan.y > 4
  edgeGlow.b = pan.y < -4
}

// ---------- 窗宽窗位预设（P11 工具栏）----------
/** 窗宽窗位预设常量（参考放射学工作站常规预设） */
const WINDOW_PRESETS = {
  MRI_T1: { wc: 40, ww: 80 },
  MRI_T2: { wc: 80, ww: 200 },
  MRI_FLAIR: { wc: 85, ww: 120 },
  PET_FDG: { wc: 1.0, ww: 0.5 },
  PET_AMYLOID: { wc: 1.2, ww: 0.6 },
  CUSTOM: null
} as const
type PresetKey = keyof typeof WINDOW_PRESETS
/** 预设下拉选项（label 为中文友好显示） */
const presetOptions: Array<{ key: PresetKey; label: string }> = [
  { key: 'MRI_T1', label: 'MRI T1' },
  { key: 'MRI_T2', label: 'MRI T2' },
  { key: 'MRI_FLAIR', label: 'MRI FLAIR' },
  { key: 'PET_FDG', label: 'PET FDG' },
  { key: 'PET_AMYLOID', label: 'PET 淀粉样' },
  { key: 'CUSTOM', label: '自定义' }
]
/** 当前选择的预设（CUSTOM 表示用户滑块自定义） */
const presetChoice = ref<PresetKey>(props.modality === 'PET' ? 'PET_FDG' : 'MRI_T1')
/** 自定义 WC/WW（滑块控制；CUSTOM 预设时生效） */
const customWl = reactive({ wc: 40, ww: 80 })
/** 实际生效的 WC/WW：预设选定值或自定义值 */
const effectiveWl = computed<{ wc: number; ww: number }>(() => {
  const p = WINDOW_PRESETS[presetChoice.value]
  if (p !== null) return { wc: p.wc, ww: p.ww }
  return { wc: customWl.wc, ww: customWl.ww }
})
/** 工具栏窗宽窗位面板展开 */
const wlPanelOpen = ref(false)
/** 角标/截图文件名展示用窗值：真实模式以后端实际请求窗为准，合成模式以本地 wl 为准 */
const displayWl = computed<{ wc: number; ww: number }>(() => (useReal.value ? effectiveWl.value : wl))
/** 窗值紧凑格式：PET 小数量纲保留最多 2 位小数，MRI 整数显示 */
function fmtWl(v: number): string {
  if (Math.abs(v) < 10) return String(Math.round(v * 100) / 100)
  return String(Math.round(v))
}
/** 窗宽窗位切换后重新加载真实切片的 100ms 防抖句柄 */
let wlDebounceTimer: ReturnType<typeof setTimeout> | null = null

/** 选择预设：同步本地 wl（合成模式立即响应）+ 真实模式防抖重载 */
function selectPreset(key: PresetKey): void {
  presetChoice.value = key
  const p = WINDOW_PRESETS[key]
  if (p !== null) {
    wl.wc = p.wc
    wl.ww = p.ww
  }
  scheduleRealReload()
}
/** 自定义滑块改变 */
function onCustomWlChange(): void {
  presetChoice.value = 'CUSTOM'
  wl.wc = customWl.wc
  wl.ww = customWl.ww
  scheduleRealReload()
}
/** 真实模式下：清缓存 + 100ms 防抖后重新拉取当前切片 */
function scheduleRealReload(): void {
  if (!useReal.value) {
    draw()
    return
  }
  if (wlDebounceTimer) clearTimeout(wlDebounceTimer)
  wlDebounceTimer = setTimeout(() => {
    realCache.clear()
    realLoading.clear()
    void ensureRealSlice(props.sliceIdx)
  }, 100)
}

/** 是否显示异常脑区热力图（仅轴位 + 有分析结果时可用） */
const showHeatmap = ref(true)
/** 热力图是否可显示（轴位 + 有异常脑区） */
const heatmapEnabled = computed(
  () => orientation.value === 'axial' && (props.heatmapRegions?.length ?? 0) > 0
)

/** 海马定量叠加开关 */
const showHippocampus = ref(true)
/** 海马叠加是否可用（轴位 + 有体积数据） */
const hippocampusEnabled = computed(
  () => orientation.value === 'axial' && !!props.hippocampusVolumes
)

/** 重点病症脑区示教标注开关（标准图谱近似位置，三平面均可显示） */
const showRegions = ref(true)
/** 脑区图例弹层 */
const legendVisible = ref(false)
/** 当前异常脑区名集合（匹配到时标注点红色强调） */
const abnormalNames = computed(() => new Set((props.heatmapRegions ?? []).map((r) => r.region)))

// ---------- 标注标签（屏幕空间绘制，彻底避免缩放平移导致的重叠/看不清） ----------
/**
 * 右上悬浮工具栏横向安全区（px）。
 * 实测构成：right 偏移 10 + 按钮 32 + 容器 padding 4 + 内容溢出滚动条 10 = 56。
 * 右侧标注列 / 角标 / 方向标统一绘制在该安全区左侧，避免被工具栏遮挡。
 */
const TOOLBAR_RESERVE = 56

/** 屏幕空间标签项 */
interface ScreenTag {
  sx: number
  sy: number
  text: string
  color: string
  abnormal: boolean
  region: BrainRegion | null
  /** 影像空间锚点（判定左右侧分布） */
  ax: number
}
/** 本帧锚点屏幕坐标缓存（hover 命中检测用） */
let anchorScreenCache: Array<{ x: number; y: number; region: BrainRegion }> = []
/** 本帧标签矩形缓存（悬停标签本体也能触发信息条） */
let tagRectsCache: Array<{ x1: number; y1: number; x2: number; y2: number; region: BrainRegion | null }> = []
/** hover 命中的脑区（信息条显示） */
const hoverRegion = ref<BrainRegion | null>(null)

/**
 * 屏幕空间标签统一排布：左右分列贴边、垂直错开、深色底衬，
 * 引线从锚点屏幕坐标引到标签列（放射科工作站风格）。
 */
function drawScreenTags(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  items: ScreenTag[],
  startIdx = 0
): void {
  ctx.font = '11px "PingFang SC", "Microsoft YaHei", sans-serif'
  for (const side of ['left', 'right'] as const) {
    const list = items.filter((i) => (side === 'left' ? i.ax < SLICE_SIZE / 2 : i.ax >= SLICE_SIZE / 2))
    list.sort((a, b) => a.sy - b.sy)
    list.forEach((it, idx) => {
      const ty = Math.max(46, Math.min(h - 34, 46 + (startIdx + idx) * 17))
      // 右列贴边时为右上悬浮工具栏预留安全区，避免标签被按钮遮挡
      const textX = side === 'left' ? 10 : w - TOOLBAR_RESERVE
      const align = side === 'left' ? 'left' : 'right'
      // 引线：锚点 → 中间拐点 → 标签侧
      const elbowX = side === 'left' ? 74 : w - TOOLBAR_RESERVE - 64
      ctx.beginPath()
      ctx.moveTo(it.sx, it.sy)
      ctx.lineTo(elbowX, ty)
      ctx.lineTo(side === 'left' ? 70 : w - TOOLBAR_RESERVE - 60, ty)
      ctx.strokeStyle = it.color + '77'
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.beginPath()
      ctx.arc(it.sx, it.sy, 2.2, 0, Math.PI * 2)
      ctx.fillStyle = it.color
      ctx.fill()
      // 深色底衬（任何影像灰度上都可读）
      const tw = ctx.measureText(it.text).width + 10
      const bx = side === 'left' ? textX - 5 : textX - tw + 5
      tagRectsCache.push({ x1: bx, y1: ty - 9.5, x2: bx + tw, y2: ty + 7.5, region: it.region })
      ctx.fillStyle = 'rgba(8,12,18,0.82)'
      ctx.beginPath()
      ctx.roundRect(bx, ty - 9.5, tw, 17, 4)
      ctx.fill()
      ctx.strokeStyle = it.color + '77'
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.fillStyle = it.abnormal ? '#FF8A80' : it.color
      ctx.textAlign = align
      ctx.fillText(it.text, textX, ty + 3.5)
    })
  }
}

/** 悬停命中检测：距离任一锚点 16px 内，或落在标签矩形上 → 信息条显示该脑区说明 */
function onHoverMove(e: MouseEvent): void {
  if (!canvasEl.value) return
  const rect = canvasEl.value.getBoundingClientRect()
  const mx = e.clientX - rect.left
  const my = e.clientY - rect.top
  // 三平面：悬停点上报共享状态（另两视口显示预览十字线）+ 十字线热区光标
  if (!drag.active && props.linkOnClick && useReal.value && triSync.state.enabled) {
    const p = imageFromScreen(mx, my, rect.width, rect.height)
    if (isInsideImage(p.x, p.y)) {
      triSync.hoverAt(triOri.value, p.x, p.y)
      const hit = hitCrosshair(p, rect.width, rect.height)
      crossCursor.value = hit === 'both' ? 'move' : hit === 'u' ? 'col-resize' : hit === 'v' ? 'row-resize' : ''
    } else {
      triSync.clearHover(triOri.value)
      crossCursor.value = ''
    }
  }
  if (drag.active) return
  let best: BrainRegion | null = null
  let bd = 16
  for (const s of anchorScreenCache) {
    const d = Math.hypot(s.x - mx, s.y - my)
    if (d < bd) {
      bd = d
      best = s.region
    }
  }
  if (!best) {
    for (const r of tagRectsCache) {
      if (!r.region) continue
      if (mx >= r.x1 && mx <= r.x2 && my >= r.y1 && my <= r.y2) {
        best = r.region
        break
      }
    }
  }
  if (best !== hoverRegion.value) {
    hoverRegion.value = best
    draw()
  }
}

/** 离开画布：清除脑区悬停与三平面预览点 */
function onCanvasLeave(): void {
  hoverRegion.value = null
  crossCursor.value = ''
  if (props.linkOnClick) triSync.clearHover(triOri.value)
}

/** Grad-CAM 代谢偏差热力图开关 */
const showGradcam = ref(false)
/** Grad-CAM 热力图是否可用（真实模式 + 有 PET） */
const gradcamAvailable = computed(
  () => useReal.value && props.modality === 'MRI'
)
/** 已加载的 Grad-CAM 热力图（按「层号|平面」缓存，防止切平面后在途旧图回灌） */
const gradcamCache = new Map<string, HTMLImageElement>()
const gradcamLoading = new Set<string>()
function gradcamKey(idx: number, o: Orientation): string {
  return `${idx}|${o}`
}

async function ensureGradcam(idx: number): Promise<void> {
  const o = orientation.value
  const key = gradcamKey(idx, o)
  if (!gradcamAvailable.value || gradcamCache.has(key) || gradcamLoading.has(key)) return
  gradcamLoading.add(key)
  try {
    const img = await loadGradcam(props.realCaseId as string, idx, o)
    // 返回时平面已变：丢弃这张在途旧图（键已含平面，绝不写入新平面缓存）
    if (orientation.value !== o) return
    gradcamCache.set(key, img)
    if (gradcamCache.size > 20) {
      const oldest = gradcamCache.keys().next().value
      if (oldest !== undefined) gradcamCache.delete(oldest)
    }
    if (idx === props.sliceIdx && orientation.value === o) draw()
  } catch {
    // 加载失败不影响主影像
  } finally {
    gradcamLoading.delete(key)
  }
}

watch(
  () => [props.sliceIdx, orientation.value, showGradcam.value] as const,
  ([idx]) => {
    if (gradcamAvailable.value && showGradcam.value) ensureGradcam(idx)
  }
)
watch(orientation, () => {
  gradcamCache.clear()
  gradcamLoading.clear()
})

/** 测量记录（256 像素坐标空间，渲染时随视图变换） */
interface MeasureLine {
  x1: number
  y1: number
  x2: number
  y2: number
  mm: number
}
const measures = ref<MeasureLine[]>([])

/** ROI 矩形测量（面积测量，与直线测量共享 250mm FOV 标定） */
interface RoiRect {
  x1: number
  y1: number
  x2: number
  y2: number
  /** 面积 mm²，由像素面积 × MM_PER_PX² 计算 */
  area: number
}
const rois = ref<RoiRect[]>([])

/** FOV 标定：250mm 视野 / 256 像素（演示序列按标准头 FOV 标定） */
const MM_PER_PX = 250 / SLICE_SIZE

/**
 * 单击/拖拽判定阈值（屏幕像素）。
 * mousedown→mouseup 位移 ≤ 该值视为「单击」（三平面联动）；
 * 超过则视为拖拽（调窗 / 平移 / 测距），不触发联动。
 */
const CLICK_THRESHOLD_PX = 5

/** 导出测量结果为 CSV 文件 */
function exportMeasures(): void {
  if (measures.value.length === 0 && rois.value.length === 0) {
    return
  }
  const lines: string[] = []
  // 直线测量段
  if (measures.value.length > 0) {
    lines.push('# 直线测量')
    lines.push('序号,起点X,起点Y,终点X,终点Y,距离(mm),平面,层号')
    measures.value.forEach((m, i) => {
      lines.push(
        `${i + 1},${m.x1.toFixed(1)},${m.y1.toFixed(1)},${m.x2.toFixed(1)},${m.y2.toFixed(1)},${m.mm.toFixed(2)},${orientation.value},${props.sliceIdx + 1}`
      )
    })
  }
  // ROI 面积段
  if (rois.value.length > 0) {
    if (lines.length > 0) lines.push('')
    lines.push('# ROI 矩形面积测量')
    lines.push('序号,左上X,左上Y,右下X,右下Y,宽(mm),高(mm),面积(mm²),平面,层号')
    rois.value.forEach((r, i) => {
      const w = Math.abs(r.x2 - r.x1) * MM_PER_PX
      const h = Math.abs(r.y2 - r.y1) * MM_PER_PX
      lines.push(
        `${i + 1},${Math.min(r.x1, r.x2).toFixed(1)},${Math.min(r.y1, r.y2).toFixed(1)},${Math.max(r.x1, r.x2).toFixed(1)},${Math.max(r.y1, r.y2).toFixed(1)},${w.toFixed(2)},${h.toFixed(2)},${r.area.toFixed(2)},${orientation.value},${props.sliceIdx + 1}`
      )
    })
  }
  const blob = new Blob(['\uFEFF' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `测量结果_${props.modality}_${orientation.value}_L${props.sliceIdx + 1}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// ---------- 切片数据缓存（限制容量防止内存膨胀） ----------
const sliceCache = new Map<number, SliceData>()
function getSlice(idx: number): SliceData {
  const hit = sliceCache.get(idx)
  if (hit) return hit
  const data = generateSliceData(idx, props.sliceCount, props.seed)
  if (sliceCache.size > 48) sliceCache.clear()
  sliceCache.set(idx, data)
  return data
}

// ---------- 真实 NIfTI 切片数据源 ----------
/** 是否启用真实切片（有病例 id、本模态可用且未发生加载失败） */
const realFailed = ref(false)
const useReal = computed(() => !!props.realCaseId && props.realAvailable && !realFailed.value)
/** 切换病例时复位失败锁与缓存，避免上一个病例的一次网络抖动永久锁到新病例 */
watch(
  () => props.realCaseId,
  () => {
    realFailed.value = false
    realCache.clear()
    realLoading.clear()
    gradcamCache.clear()
    gradcamLoading.clear()
  }
)
/** 手动重试真实影像（失败浮层按钮）：清失败锁后下一帧自然触发加载 */
function retryRealSlice(): void {
  realFailed.value = false
  realCache.clear()
  realLoading.clear()
  draw()
}
/** 已解码的真实切片（gray=MRI 灰度，pet=PET 强度，256×256） */
interface RealSlice {
  gray?: Uint8Array
  pet?: Uint8Array
}
/** 缓存 key：层号 + 各模态各自的 WC/WW（MRI 与 PET 原始量纲不同，不可共用窗参数）+ 方向 */
function realWindowFor(m: 'MRI' | 'PET'): { wc: number; ww: number } {
  const k = presetChoice.value
  if (m === 'PET') {
    if (k === 'PET_FDG') return { wc: WINDOW_PRESETS.PET_FDG.wc, ww: WINDOW_PRESETS.PET_FDG.ww }
    if (k === 'PET_AMYLOID') return { wc: WINDOW_PRESETS.PET_AMYLOID.wc, ww: WINDOW_PRESETS.PET_AMYLOID.ww }
    if (k === 'CUSTOM') return { wc: customWl.wc, ww: customWl.ww }
    // PET 视口选了 MRI 预设：仍按 PET FDG 默认窗请求，避免把 40/80 套到 SUV 量纲
    return { wc: WINDOW_PRESETS.PET_FDG.wc, ww: WINDOW_PRESETS.PET_FDG.ww }
  }
  if (k === 'MRI_T1') return { wc: WINDOW_PRESETS.MRI_T1.wc, ww: WINDOW_PRESETS.MRI_T1.ww }
  if (k === 'MRI_T2') return { wc: WINDOW_PRESETS.MRI_T2.wc, ww: WINDOW_PRESETS.MRI_T2.ww }
  if (k === 'MRI_FLAIR') return { wc: WINDOW_PRESETS.MRI_FLAIR.wc, ww: WINDOW_PRESETS.MRI_FLAIR.ww }
  if (k === 'CUSTOM') return { wc: customWl.wc, ww: customWl.ww }
  return { wc: WINDOW_PRESETS.MRI_T1.wc, ww: WINDOW_PRESETS.MRI_T1.ww }
}
function realCacheKey(idx: number, o: Orientation): string {
  const parts = needModalities()
    .map((m) => {
      const { wc, ww } = realWindowFor(m)
      return `${m[0]}${wc.toFixed(3)},${ww.toFixed(3)}`
    })
    .join(';')
  return `${idx}|${parts}|${o}`
}
const realCache = new Map<string, RealSlice>()
const realLoading = new Set<string>()

/** 本视口需要请求的模态 */
function needModalities(): Array<'MRI' | 'PET'> {
  if (props.modality === 'MRI') return ['MRI']
  if (props.modality === 'PET') return ['PET']
  return ['MRI', 'PET']
}

/**
 * 异步拉取并解码一张真实切片，完成后重绘
 * @param idx 层号（硬边界 [0, sliceCount) 外的请求一律拒绝）
 * @param prefetchNeighbors 是否预取相邻层；仅用户当前层允许预取一层，
 *   预取层自身不再继续扩散，杜绝递归请求洪水
 * WC/WW 取自 effectiveWl，缓存按 (idx, wc, ww, orientation) 隔离
 */
async function ensureRealSlice(idx: number, prefetchNeighbors = true): Promise<void> {
  const total = props.sliceCount
  if (!useReal.value || !Number.isFinite(idx) || idx < 0 || idx >= total) return
  const key = realCacheKey(idx, orientation.value)
  if (realCache.has(key) || realLoading.has(key)) return
  realLoading.add(key)
  try {
    const caseId = props.realCaseId as string
    const entry: RealSlice = {}
    await Promise.all(
      needModalities().map(async (m) => {
        const { wc, ww } = realWindowFor(m)
        const arr = await loadSliceGray(caseId, m, idx, orientation.value, wc, ww)
        if (m === 'MRI') entry.gray = arr
        else entry.pet = arr
      })
    )
    realCache.set(key, entry)
    if (realCache.size > 40) {
      const oldest = realCache.keys().next().value
      if (oldest !== undefined) realCache.delete(oldest)
    }
    if (idx === props.sliceIdx) draw()
    // 仅从当前层预取一层相邻切片（滚轮浏览更顺滑）；预取层不再递归扩散
    if (prefetchNeighbors) {
      if (idx - 1 >= 0) void ensureRealSlice(idx - 1, false)
      if (idx + 1 < total) void ensureRealSlice(idx + 1, false)
    }
  } catch {
    // 仅当前显示层失败时才整体回退合成影像；预取层失败静默，
    // 避免后端瞬时重启等偶发错误让整个阅片会话永久离开真实模式
    if (idx === props.sliceIdx) {
      realFailed.value = true
      draw()
    }
  } finally {
    realLoading.delete(key)
  }
}

/** 真实模式切换时清空缓存（WC/WW 跟随预设） */
watch(
  () => useReal.value,
  (real) => {
    realCache.clear()
    realLoading.clear()
    if (real) {
      // 真实模式：用预设 WC/WW 重新加载（后端按 wc/ww 重渲 PNG）
      const p = WINDOW_PRESETS[presetChoice.value]
      if (p !== null) {
        wl.wc = p.wc
        wl.ww = p.ww
      } else {
        wl.wc = customWl.wc
        wl.ww = customWl.ww
      }
      ensureRealSlice(props.sliceIdx)
    }
    draw()
  }
)

/** 平面方向切换时清空已解码切片（不同平面数据不通用） */
watch(orientation, () => {
  realCache.clear()
  realLoading.clear()
  if (useReal.value) ensureRealSlice(props.sliceIdx)
  draw()
})

// ---------- 画布与渲染 ----------
const wrapEl = ref<HTMLDivElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const offscreen = document.createElement('canvas')
offscreen.width = SLICE_SIZE
offscreen.height = SLICE_SIZE
const offCtx = offscreen.getContext('2d')

/** 将当前切片按模态渲染到离屏画布 */
function renderOffscreen(): void {
  const ctx = offCtx
  if (!ctx) return
  const img = ctx.createImageData(SLICE_SIZE, SLICE_SIZE)
  const n = SLICE_SIZE * SLICE_SIZE

  // 真实 NIfTI 数据源（后端已按请求的 wc/ww 完成加窗，8-bit 字节即显示值，前端不再二次加窗）
  if (useReal.value) {
    const hit = realCache.get(realCacheKey(props.sliceIdx, orientation.value))
    if (!hit) {
      // 当前层尚未解码完成：触发加载，本帧先清黑（加载完成后 draw 重绘）
      void ensureRealSlice(props.sliceIdx)
      ctx.fillStyle = '#05080c'
      ctx.fillRect(0, 0, SLICE_SIZE, SLICE_SIZE)
      return
    }
    paintRealSlice(img, hit, n)
    ctx.putImageData(img, 0, 0)
    return
  }

  // 合成演示数据源
  paintSlice(img, getSlice(props.sliceIdx))
  ctx.putImageData(img, 0, 0)
}

/**
 * 真实切片着色：后端返回的 0-255 字节已是最终显示窗结果——
 * MRI 直接当灰度；PET 直接映射 Jet；融合 PET 同一条高代谢凸显曲线。
 * 此处绝不允许再做 applyWWWC，否则等于双重加窗（PET 默认窗会整体饱和变白）。
 */
function paintRealSlice(img: ImageData, hit: RealSlice, n: number): void {
  const zero = new Uint8Array(n)
  const gray = hit.gray ?? zero
  const pet = hit.pet ?? zero
  if (props.modality === 'MRI') {
    for (let i = 0; i < n; i++) {
      const v = gray[i]
      img.data[i * 4] = v
      img.data[i * 4 + 1] = v
      img.data[i * 4 + 2] = v
      img.data[i * 4 + 3] = 255
    }
  } else if (props.modality === 'PET') {
    for (let i = 0; i < n; i++) {
      const [r, g, b] = jetColor(pet[i] / 255)
      img.data[i * 4] = r
      img.data[i * 4 + 1] = g
      img.data[i * 4 + 2] = b
      img.data[i * 4 + 3] = 255
    }
  } else {
    // 融合：MRI 灰度打底 + PET 高代谢区 Jet 伪彩 Alpha 叠加（配准空间 1:1）
    for (let i = 0; i < n; i++) {
      const v = gray[i]
      const t = pet[i] / 255
      const [pr, pg, pb] = jetColor(t)
      const alpha = Math.max(0, t - 0.35) * 1.5
      img.data[i * 4] = v * (1 - alpha) + pr * alpha
      img.data[i * 4 + 1] = v * (1 - alpha) + pg * alpha
      img.data[i * 4 + 2] = v * (1 - alpha) + pb * alpha
      img.data[i * 4 + 3] = 255
    }
  }
}

/** 按模态把 SliceData 着色写入 ImageData（MRI 灰度 / PET 伪彩 / 融合叠加） */
function paintSlice(img: ImageData, data: SliceData): void {
  const n = SLICE_SIZE * SLICE_SIZE
  if (props.modality === 'MRI') {
    for (let i = 0; i < n; i++) {
      const v = applyWWWC(data.gray[i], wl.ww, wl.wc)
      img.data[i * 4] = v
      img.data[i * 4 + 1] = v
      img.data[i * 4 + 2] = v
      img.data[i * 4 + 3] = 255
    }
  } else if (props.modality === 'PET') {
    for (let i = 0; i < n; i++) {
      const t = applyWWWC(data.pet[i], wl.ww, wl.wc) / 255
      const [r, g, b] = jetColor(t)
      img.data[i * 4] = r
      img.data[i * 4 + 1] = g
      img.data[i * 4 + 2] = b
      img.data[i * 4 + 3] = 255
    }
  } else {
    // 融合：MRI 灰度打底 + PET 高代谢区 Jet 伪彩 Alpha 叠加（配准空间 1:1）
    for (let i = 0; i < n; i++) {
      const v = applyWWWC(data.gray[i], wl.ww, wl.wc)
      const t = applyWWWC(data.pet[i], 220, 120) / 255
      const [pr, pg, pb] = jetColor(t)
      const alpha = Math.max(0, t - 0.35) * 1.5
      img.data[i * 4] = v * (1 - alpha) + pr * alpha
      img.data[i * 4 + 1] = v * (1 - alpha) + pg * alpha
      img.data[i * 4 + 2] = v * (1 - alpha) + pb * alpha
      img.data[i * 4 + 3] = 255
    }
  }
}

/**
 * 画布→影像适配基准缩放（双轴，支持三种全局适配模式 fitMode）：
 *   contain 适应：min(w,h) 等比，长边留黑边，影像完整（医学阅片默认）
 *   cover   填满：max(w,h) 等比铺满，超出部分裁切，无黑边
 *   fill    拉伸：w/h 各自铺满，非等比（允许变形）
 * 影像为 SLICE_SIZE×SLICE_SIZE 方形，画布为任意宽高比（如三平面 261×169）。
 */
function baseScale(w: number, h: number): { sx: number; sy: number } {
  if (fitMode.value === 'fill') return { sx: w / SLICE_SIZE, sy: h / SLICE_SIZE }
  const k = fitMode.value === 'cover' ? Math.max(w, h) : Math.min(w, h)
  return { sx: k / SLICE_SIZE, sy: k / SLICE_SIZE }
}
/** 当前视图总缩放（适配基准 × 用户 zoom），x/y 双轴 */
function viewScaleAt(w: number, h: number): { sx: number; sy: number } {
  const b = baseScale(w, h)
  return { sx: b.sx * zoom.value, sy: b.sy * zoom.value }
}

function viewTransform(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const { sx, sy } = viewScaleAt(w, h)
  ctx.translate(w / 2 + pan.x, h / 2 + pan.y)
  ctx.scale(sx, sy)
  ctx.translate(-SLICE_SIZE / 2, -SLICE_SIZE / 2)
}

/** 屏幕点（相对画布的 CSS 像素）→ 影像空间坐标（双轴逆变换） */
function imageFromScreen(sxPx: number, syPx: number, w: number, h: number): { x: number; y: number } {
  const { sx, sy } = viewScaleAt(w, h)
  const ix = (sxPx - (w / 2 + pan.x)) / sx + SLICE_SIZE / 2
  const iy = (syPx - (h / 2 + pan.y)) / sy + SLICE_SIZE / 2
  return { x: ix, y: iy }
}
/** 鼠标事件 → 影像空间坐标（测量取点用；含适配逆变换） */
function toImageSpace(e: MouseEvent): { x: number; y: number } {
  const rect = canvasEl.value!.getBoundingClientRect()
  return imageFromScreen(e.clientX - rect.left, e.clientY - rect.top, rect.width, rect.height)
}

/** 影像空间坐标是否落在 SLICE_SIZE 方形影像区内（黑边区域返回 false） */
function isInsideImage(x: number, y: number): boolean {
  return x >= 0 && x <= SLICE_SIZE && y >= 0 && y <= SLICE_SIZE
}

/** 主渲染：黑底 + 影像 + ROI + 测量 + 医学角标 */
function draw(): void {
  const canvas = canvasEl.value
  const wrap = wrapEl.value
  const ctx = canvas?.getContext('2d')
  if (!canvas || !wrap || !ctx) return
  // 物理像素对齐（高清阅片屏）
  const dpr = window.devicePixelRatio || 1
  const w = wrap.clientWidth
  const h = wrap.clientHeight
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr)
    canvas.height = Math.round(h * dpr)
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = '#0b0f14'
  ctx.fillRect(0, 0, w, h)
  anchorScreenCache = []
  tagRectsCache = []
  // 当前双轴总缩放（适配基准 × zoom）；s 取双轴较小值用于线宽/字号（fill 模式下不变细）
  const ss = viewScaleAt(w, h)
  const s = Math.min(ss.sx, ss.sy)

  renderOffscreen()
  // 缩小（适配缩放）时双线性平滑避免锯齿；放大到原始像素以上用近邻采样呈真实像素
  ctx.imageSmoothingEnabled = s < 1
  ctx.save()
  viewTransform(ctx, w, h)
  ctx.drawImage(offscreen, 0, 0)
  // Grad-CAM 代谢偏差热力图叠加（MRI 视口真实模式下可开启）
  if (gradcamAvailable.value && showGradcam.value) {
    const cam = gradcamCache.get(gradcamKey(props.sliceIdx, orientation.value))
    if (cam) {
      ctx.globalAlpha = 0.55
      ctx.drawImage(cam, 0, 0, SLICE_SIZE, SLICE_SIZE)
      ctx.globalAlpha = 1
    } else {
      void ensureGradcam(props.sliceIdx)
    }
  }
  ctx.restore()

  // ----- ROI 脑区叠加（随视图变换同步缩放平移；真实影像不叠加合成 ROI，避免误导标注） -----
  if (props.showRoi && !useReal.value) {
    const shapes = getRoiShapes(props.sliceIdx, props.sliceCount)
    ctx.save()
    viewTransform(ctx, w, h)
    for (const shape of shapes) {
      for (const e of shape.ellipses) {
        ctx.beginPath()
        ctx.ellipse(e.x, e.y, e.rx, e.ry, 0, 0, Math.PI * 2)
        ctx.fillStyle = shape.color + '26' // 15% 透明填充
        ctx.fill()
        ctx.strokeStyle = shape.color
        ctx.lineWidth = 1.2 / s
        ctx.setLineDash([4 / s, 3 / s])
        ctx.stroke()
        ctx.setLineDash([])
      }
    }
    ctx.restore()
  }

  // ----- 异常脑区热力图叠加（仅轴位 + 有分析结果；标签统一屏幕空间绘制） -----
  const tagItems: ScreenTag[] = []
  if (heatmapEnabled.value && showHeatmap.value) {
    const blobs = getHeatmapBlobs(props.heatmapRegions ?? [], props.sliceIdx, props.sliceCount)
    ctx.save()
    viewTransform(ctx, w, h)
    for (const b of blobs) {
      // 高斯模糊感的多层椭圆叠加
      ctx.beginPath()
      ctx.ellipse(b.x, b.y, b.rx, b.ry, 0, 0, Math.PI * 2)
      ctx.fillStyle = b.color + Math.round(b.alpha * 255).toString(16).padStart(2, '0')
      ctx.fill()
      ctx.beginPath()
      ctx.ellipse(b.x, b.y, b.rx * 0.6, b.ry * 0.6, 0, 0, Math.PI * 2)
      ctx.fillStyle = b.color + Math.round(b.alpha * 1.4 * 255).toString(16).padStart(2, '0')
      ctx.fill()
    }
    ctx.restore()
    // 标签投到屏幕空间，纳入统一排布
    for (const b of blobs) {
      const region = BRAIN_REGIONS.find((r) => b.label.endsWith(r.name)) ?? null
      tagItems.push({
        sx: w / 2 + pan.x + (b.x - SLICE_SIZE / 2) * ss.sx,
        sy: h / 2 + pan.y + (b.y - SLICE_SIZE / 2) * ss.sy,
        text: b.label,
        color: b.color,
        abnormal: true,
        region,
        ax: b.x
      })
      if (region) anchorScreenCache.push({ x: w / 2 + pan.x + (b.x - SLICE_SIZE / 2) * ss.sx, y: h / 2 + pan.y + (b.y - SLICE_SIZE / 2) * ss.sy, region })
    }
  }

  // ----- 海马体积定量标注（仅轴位 + 有分析结果） -----
  if (hippocampusEnabled.value && showHippocampus.value && props.hippocampusVolumes) {
    const hv = props.hippocampusVolumes
    // 海马位于轴位中层附近（z ≈ 55-65/128），仅在中层 ±15 层显示
    const mid = props.sliceCount / 2
    const zRel = Math.abs(props.sliceIdx - mid) / props.sliceCount
    const zFade = Math.max(0, 1 - zRel / 0.12)
    if (zFade > 0) {
      const hipColor = '#4FC3F7'
      const alpha = Math.round(0.5 * zFade * 255).toString(16).padStart(2, '0')
      ctx.save()
      viewTransform(ctx, w, h)
      // 左右海马椭圆（基于标准图谱近似位置）
      const positions = [
        { x: 0.43, y: 0.58, rx: 0.06, ry: 0.045, vol: hv.right, label: 'R 海马' },
        { x: 0.57, y: 0.58, rx: 0.06, ry: 0.045, vol: hv.left, label: 'L 海马' }
      ]
      for (const p of positions) {
        const px = p.x * SLICE_SIZE
        const py = p.y * SLICE_SIZE
        const rx = p.rx * SLICE_SIZE
        const ry = p.ry * SLICE_SIZE
        ctx.beginPath()
        ctx.ellipse(px, py, rx, ry, 0, 0, Math.PI * 2)
        ctx.strokeStyle = hipColor + alpha
        ctx.lineWidth = 1.5 / s
        ctx.setLineDash([5 / s, 3 / s])
        ctx.stroke()
        ctx.setLineDash([])
        // 体积标签
        ctx.font = `${10 / s}px Consolas, 'Courier New', monospace`
        ctx.fillStyle = hipColor
        ctx.textAlign = 'center'
        ctx.fillText(`${p.label} ${p.vol.toFixed(2)}cm³`, px, py - ry - 5 / s)
      }
      ctx.restore()
    }
  }

  // ----- 重点病症脑区示教标注（按层位显隐 + 左右定侧；异常脑区红色强调；标签屏幕空间统一排布） -----
  if (showRegions.value && !props.disabled) {
    const anchors = getRegionAnchors(orientation.value)
    ctx.save()
    viewTransform(ctx, w, h)
    for (const a of anchors) {
      // 解剖层位门控：该结构不在当前层面附近时不显示（渐隐）
      const fade = regionSliceFade(a.region, orientation.value, props.sliceIdx, props.sliceCount)
      if (fade <= 0.35) continue
      const { x, y } = toSlicePx(a.x, a.y, SLICE_SIZE)
      const isAbn = abnormalNames.value.has(a.region.name)
      const c = isAbn ? '#EF5350' : a.region.color
      // 锚点圆点（影像空间，随缩放平移；带深色描边保证亮暗背景都可辨）
      ctx.beginPath()
      ctx.arc(x, y, 3.6 / s, 0, Math.PI * 2)
      ctx.fillStyle = '#0b0f14'
      ctx.globalAlpha = 0.9 * fade
      ctx.fill()
      ctx.beginPath()
      ctx.arc(x, y, 2.6 / s, 0, Math.PI * 2)
      ctx.fillStyle = c
      ctx.fill()
      ctx.globalAlpha = 1
      const sx = w / 2 + pan.x + (x - SLICE_SIZE / 2) * ss.sx
      const sy = h / 2 + pan.y + (y - SLICE_SIZE / 2) * ss.sy
      anchorScreenCache.push({ x: sx, y: sy, region: a.region })
      // 左右定侧（放射学惯例：轴位/冠状位图像左侧 = 患者 R 侧）
      const side =
        a.region.bilateral && orientation.value !== 'sagittal' ? (a.x < 0.5 ? 'R·' : 'L·') : ''
      tagItems.push({
        sx,
        sy,
        text: (isAbn ? '⚠ ' : '') + side + a.region.name,
        color: c,
        abnormal: isAbn,
        region: a.region,
        ax: x
      })
    }
    ctx.restore()
    // hover 命中高亮圈
    if (hoverRegion.value) {
      const hit = anchorScreenCache.find((s) => s.region === hoverRegion.value)
      if (hit) {
        ctx.beginPath()
        ctx.arc(hit.x, hit.y, 8, 0, Math.PI * 2)
        ctx.strokeStyle = '#FFFFFF'
        ctx.lineWidth = 1.6
        ctx.stroke()
      }
    }
  }

  // ----- 标签统一屏幕空间绘制（最上层，保证任何影像上都清晰可读） -----
  if (tagItems.length > 0) drawScreenTags(ctx, w, h, tagItems)

  // ----- 测量线叠加 -----
  const drawLine = (l: { x1: number; y1: number; x2: number; y2: number }): void => {
    ctx.beginPath()
    ctx.moveTo(l.x1, l.y1)
    ctx.lineTo(l.x2, l.y2)
    ctx.strokeStyle = '#FFD54F'
    ctx.lineWidth = 1.4 / s
    ctx.setLineDash([])
    ctx.stroke()
    // 端点标记
    for (const p of [l, { x1: l.x2, y1: l.y2, x2: l.x1, y2: l.y1 }]) {
      ctx.beginPath()
      ctx.arc(p.x1, p.y1, 2.4 / s, 0, Math.PI * 2)
      ctx.fillStyle = '#FFD54F'
      ctx.fill()
    }
  }

  /** ROI 矩形绘制：虚线框 + 半透明填充 + 四角端点 + 面积文本 */
  const drawRoi = (r: { x1: number; y1: number; x2: number; y2: number; area?: number }): void => {
    const x = Math.min(r.x1, r.x2)
    const y = Math.min(r.y1, r.y2)
    const rw = Math.abs(r.x2 - r.x1)
    const rh = Math.abs(r.y2 - r.y1)
    // 半透明填充（用青绿色区分直线测量的黄色）
    ctx.fillStyle = 'rgba(46, 158, 167, 0.18)'
    ctx.fillRect(x, y, rw, rh)
    // 虚线边框
    ctx.beginPath()
    ctx.strokeStyle = '#2E9EA7'
    ctx.lineWidth = 1.4 / s
    ctx.setLineDash([6 / s, 4 / s])
    ctx.rect(x, y, rw, rh)
    ctx.stroke()
    ctx.setLineDash([])
    // 四角端点
    const corners = [
      { px: x, py: y },
      { px: x + rw, py: y },
      { px: x, py: y + rh },
      { px: x + rw, py: y + rh },
    ]
    for (const c of corners) {
      ctx.beginPath()
      ctx.arc(c.px, c.py, 2.4 / s, 0, Math.PI * 2)
      ctx.fillStyle = '#2E9EA7'
      ctx.fill()
    }
    // 面积文本（如有）
    if (typeof r.area === 'number') {
      ctx.font = `${10 / s}px Consolas, 'Courier New', monospace`
      ctx.fillStyle = '#2E9EA7'
      ctx.textAlign = 'center'
      ctx.fillText(`${r.area.toFixed(1)} mm²`, x + rw / 2, y + rh / 2 - 6 / s)
    }
  }

  ctx.save()
  viewTransform(ctx, w, h)
  // 已完成测量（直线）
  for (const m of measures.value) {
    drawLine(m)
    ctx.font = `${10 / s}px Consolas, 'Courier New', monospace`
    ctx.fillStyle = '#FFD54F'
    ctx.textAlign = 'center'
    ctx.fillText(`${m.mm.toFixed(1)} mm`, (m.x1 + m.x2) / 2, (m.y1 + m.y2) / 2 - 6 / s)
  }
  // 已完成 ROI 矩形
  for (const r of rois.value) {
    drawRoi(r)
  }
  // 拖拽中的测量（直线）
  if (drag.active && drag.tool === 'measure') {
    const mm = Math.sqrt((drag.cx - drag.sx) ** 2 + (drag.cy - drag.sy) ** 2) * MM_PER_PX
    drawLine({ x1: drag.sx, y1: drag.sy, x2: drag.cx, y2: drag.cy })
    ctx.font = `${10 / s}px Consolas, 'Courier New', monospace`
    ctx.fillStyle = '#FFD54F'
    ctx.textAlign = 'center'
    ctx.fillText(`${mm.toFixed(1)} mm`, (drag.sx + drag.cx) / 2, (drag.sy + drag.cy) / 2 - 6 / s)
  }
  // 拖拽中的 ROI 矩形
  if (drag.active && drag.tool === 'roi') {
    const pxArea = Math.abs(drag.cx - drag.sx) * Math.abs(drag.cy - drag.sy)
    drawRoi({
      x1: drag.sx,
      y1: drag.sy,
      x2: drag.cx,
      y2: drag.cy,
      area: pxArea * MM_PER_PX * MM_PER_PX,
    })
  }
  ctx.restore()

  // ----- 医学角标（屏幕空间固定位置） -----
  ctx.font = "11px Consolas, 'Courier New', monospace"
  ctx.fillStyle = 'rgba(159,205,255,.9)'
  ctx.textAlign = 'left'
  ctx.fillText(`[${props.modality}]`, 10, 18)
  ctx.fillText(`WW ${fmtWl(displayWl.value.ww)} / WC ${fmtWl(displayWl.value.wc)}`, 10, 34)
  ctx.textAlign = 'right'
  // 右侧角标避开悬浮工具栏安全区
  ctx.fillText(`${props.sliceIdx + 1} / ${props.sliceCount}`, w - TOOLBAR_RESERVE, 18)
  ctx.fillText(`×${zoom.value.toFixed(1)}`, w - TOOLBAR_RESERVE, 34)
  // 方向标：随平面方向变化（放射学惯例）
  // axial: 上A 下P 左R 右L
  // sagittal: 上S 下I 左A 右P
  // coronal: 上S 下I 左R 右L
  const dirs =
    orientation.value === 'sagittal'
      ? { t: 'S', b: 'I', l: 'A', r: 'P' }
      : orientation.value === 'coronal'
        ? { t: 'S', b: 'I', l: 'R', r: 'L' }
        : { t: 'A', b: 'P', l: 'R', r: 'L' }
  // 方向标贴在适配影像方区的四条边内侧（随 pan/zoom 移动，并限制在可见画布内）
  const imgL = w / 2 + pan.x - (ss.sx * SLICE_SIZE) / 2
  const imgR = w / 2 + pan.x + (ss.sx * SLICE_SIZE) / 2
  const imgT = h / 2 + pan.y - (ss.sy * SLICE_SIZE) / 2
  const imgB = h / 2 + pan.y + (ss.sy * SLICE_SIZE) / 2
  ctx.textAlign = 'center'
  ctx.fillStyle = 'rgba(255,213,79,.85)'
  ctx.fillText(dirs.t, w / 2 + pan.x, Math.max(14, imgT + 12))
  ctx.fillText(dirs.b, w / 2 + pan.x, Math.min(h - 8, imgB - 8))
  ctx.fillText(dirs.l, Math.max(14, imgL + 12), h / 2 + pan.y + 4)
  // 居中字形右缘含抗锯齿，需在工具栏安全区基础上再预留 8px
  ctx.fillText(dirs.r, Math.min(w - TOOLBAR_RESERVE - 8, imgR - 10), h / 2 + pan.y + 4)

  // ----- 真实切片加载中提示 -----
  if (useReal.value) {
    if (!realCache.has(realCacheKey(props.sliceIdx, orientation.value))) {
      ctx.font = '12px sans-serif'
      ctx.fillStyle = 'rgba(159,205,255,.8)'
      ctx.textAlign = 'center'
      ctx.fillText('真实影像加载中…', w / 2, h / 2)
    }
  }

  // ----- 三平面联动十字线（影像空间，随视图变换同步缩放平移）-----
  // 已提交：蓝色虚线（另两视口当前切片）；hover 预览：琥珀色实线（其他视口悬停位置）
  const drawCrossLines = (ch: { col: number; row: number }, color: string, dashed: boolean): void => {
    const cx = ch.col * SLICE_SIZE
    const cy = ch.row * SLICE_SIZE
    ctx.save()
    viewTransform(ctx, w, h)
    ctx.strokeStyle = color
    ctx.lineWidth = 1.2 / s
    ctx.setLineDash(dashed ? [4 / s, 3 / s] : [])
    ctx.beginPath()
    ctx.moveTo(cx, 0)
    ctx.lineTo(cx, SLICE_SIZE)
    ctx.moveTo(0, cy)
    ctx.lineTo(SLICE_SIZE, cy)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.restore()
  }
  if (committedCrosshair.value) drawCrossLines(committedCrosshair.value, 'rgba(59,130,246,0.55)', true)
  if (previewCrosshair.value) drawCrossLines(previewCrosshair.value, 'rgba(255,193,7,0.85)', false)
}

// ---------- 交互：滚轮切片 / Ctrl+滚轮缩放 / 拖拽（W-L、平移、测量） ----------
/** 十字线拖拽热区半径（CSS px） */
const SCRUB_HIT_PX = 8
interface DragState {
  active: boolean
  tool: Tool | null
  startX: number
  startY: number
  startWl: { ww: number; wc: number }
  startPan: { x: number; y: number }
  sx: number
  sy: number
  cx: number
  cy: number
  /** 三平面十字线拖拽：u=拖竖线翻列层 / v=拖横线翻行层 / both=交点两轴同翻；null=普通拖拽 */
  scrub: ScreenAxis | 'both' | null
}
const drag = reactive<DragState>({
  active: false,
  tool: null,
  startX: 0,
  startY: 0,
  startWl: { ww: 200, wc: 110 },
  startPan: { x: 0, y: 0 },
  sx: 0,
  sy: 0,
  cx: 0,
  cy: 0,
  scrub: null
})

function onWheel(e: WheelEvent): void {
  e.preventDefault()
  if (e.ctrlKey || e.metaKey) {
    // 缩放以光标下影像点为锚点（缩放前后该点保持在光标下不动）
    const rect = canvasEl.value!.getBoundingClientRect()
    const fx = e.clientX - rect.left
    const fy = e.clientY - rect.top
    const before = imageFromScreen(fx, fy, rect.width, rect.height)
    zoom.value = Math.min(8, Math.max(0.5, zoom.value * (e.deltaY < 0 ? 1.12 : 0.89)))
    const { sx, sy } = viewScaleAt(rect.width, rect.height)
    pan.x = fx - rect.width / 2 - (before.x - SLICE_SIZE / 2) * sx
    pan.y = fy - rect.height / 2 - (before.y - SLICE_SIZE / 2) * sy
    refreshEdgeGlow()
  } else {
    // 切片滚动：向下滚 = 下一层（sliceCount 异常为 0 时禁止变更）
    const total = props.sliceCount
    if (total <= 0) return
    const next = Math.min(total - 1, Math.max(0, props.sliceIdx + (e.deltaY > 0 ? 1 : -1)))
    emit('update:sliceIdx', next)
  }
  draw()
}

/** 三平面模式下十字线命中测试：返回可拖拽的轴（window 工具 + 影像区内） */
function hitCrosshair(p: { x: number; y: number }, w: number, h: number): ScreenAxis | 'both' | null {
  if (!props.linkOnClick || !useReal.value || !triSync.state.enabled || tool.value !== 'window') return null
  const { sx, sy } = viewScaleAt(w, h)
  const ch = triSync.crosshairFor(triOri.value)
  const nearU = Math.abs(p.x - ch.col * SLICE_SIZE) * sx <= SCRUB_HIT_PX
  const nearV = Math.abs(p.y - ch.row * SLICE_SIZE) * sy <= SCRUB_HIT_PX
  if (nearU && nearV) return 'both'
  if (nearU) return 'u'
  if (nearV) return 'v'
  return null
}

function onMouseDown(e: MouseEvent): void {
  if (e.button !== 0) return
  // 测量/ROI 工具：起点必须落在影像方区内（适配黑边区域不开始测距）
  if (tool.value === 'measure' || tool.value === 'roi') {
    const p0 = toImageSpace(e)
    if (!isInsideImage(p0.x, p0.y)) return
  }
  // 三平面十字线拖拽优先于 W/L 拖拽（仅 window 工具 + 命中热区）
  const rect = canvasEl.value!.getBoundingClientRect()
  const pDown = imageFromScreen(e.clientX - rect.left, e.clientY - rect.top, rect.width, rect.height)
  const scrub = isInsideImage(pDown.x, pDown.y) ? hitCrosshair(pDown, rect.width, rect.height) : null
  drag.active = true
  drag.tool = tool.value
  drag.startX = e.clientX
  drag.startY = e.clientY
  drag.startWl = { ...wl }
  drag.startPan = { ...pan }
  drag.scrub = scrub
  if (tool.value === 'measure' || tool.value === 'roi') {
    drag.sx = pDown.x
    drag.sy = pDown.y
    drag.cx = pDown.x
    drag.cy = pDown.y
  }
  // 三平面单击联动改为 mouseup 时按「单击/拖拽位移阈值」判定（见 onMouseUp）
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
}

function onMouseMove(e: MouseEvent): void {
  if (!drag.active) return
  if (drag.scrub) {
    // 拖动十字线连续翻动目标视口切片（坐标经共享状态映射 + clamp）
    const rect = canvasEl.value!.getBoundingClientRect()
    const p = imageFromScreen(e.clientX - rect.left, e.clientY - rect.top, rect.width, rect.height)
    if (drag.scrub === 'u' || drag.scrub === 'both') triSync.scrubAxis(triOri.value, 'u', p.x)
    if (drag.scrub === 'v' || drag.scrub === 'both') triSync.scrubAxis(triOri.value, 'v', p.y)
    draw()
    return
  }
  const rect = canvasEl.value!.getBoundingClientRect()
  if (drag.tool === 'window') {
    // 水平拖 = 窗宽，垂直拖 = 窗位；光标旁实时数值气泡
    // PET 原始量纲为 SUV 级小数，下限与 MRI 不同
    const minWw = props.modality === 'PET' ? 0.05 : 20
    const minWc = props.modality === 'PET' ? 0.01 : 10
    const newWw = Math.max(minWw, drag.startWl.ww + (e.clientX - drag.startX) * 1.2)
    const newWc = Math.max(minWc, drag.startWl.wc + (e.clientY - drag.startY) * 1.2)
    if (useReal.value) {
      // 真实模式加窗在后端完成：拖拽转 CUSTOM 自定义窗，防抖重拉当前层
      presetChoice.value = 'CUSTOM'
      customWl.ww = newWw
      customWl.wc = newWc
      scheduleRealReload()
    }
    wl.ww = newWw
    wl.wc = newWc
    wlBubble.visible = true
    wlBubble.x = Math.min(rect.width - 86, Math.max(8, e.clientX - rect.left + 14))
    wlBubble.y = Math.min(rect.height - 30, Math.max(8, e.clientY - rect.top + 14))
    wlBubble.ww = newWw
    wlBubble.wc = newWc
  } else if (drag.tool === 'pan') {
    pan.x = drag.startPan.x + (e.clientX - drag.startX)
    pan.y = drag.startPan.y + (e.clientY - drag.startY)
    refreshEdgeGlow()
  } else if (drag.tool === 'measure' || drag.tool === 'roi') {
    const p = toImageSpace(e)
    // 拖出影像方区（适配黑边）时端点贴边，测量线不画到黑边上
    drag.cx = Math.max(0, Math.min(SLICE_SIZE, p.x))
    drag.cy = Math.max(0, Math.min(SLICE_SIZE, p.y))
  }
  draw()
}

function onMouseUp(e: MouseEvent): void {
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
  if (drag.active && drag.tool === 'measure') {
    const len = Math.sqrt((drag.cx - drag.sx) ** 2 + (drag.cy - drag.sy) ** 2)
    if (len > 4) {
      measures.value.push({ x1: drag.sx, y1: drag.sy, x2: drag.cx, y2: drag.cy, mm: len * MM_PER_PX })
    }
  } else if (drag.active && drag.tool === 'roi') {
    // ROI 矩形：仅当拖出 ≥4 像素的对角线才入库（避免误点产生 0 面积矩形）
    const diag = Math.sqrt((drag.cx - drag.sx) ** 2 + (drag.cy - drag.sy) ** 2)
    if (diag > 4) {
      const pxArea = Math.abs(drag.cx - drag.sx) * Math.abs(drag.cy - drag.sy)
      rois.value.push({
        x1: drag.sx,
        y1: drag.sy,
        x2: drag.cx,
        y2: drag.cy,
        area: pxArea * MM_PER_PX * MM_PER_PX,
      })
    }
  }
  // 三平面单击联动：linkOnClick + 真实影像，mouseup 位移 ≤ 阈值（单击，非拖拽）
  // 且落点在影像方区内（非黑边）时直接提交共享状态；拖十字线翻层不重复提交。
  if (drag.active && !drag.scrub && props.linkOnClick && useReal.value) {
    const moved = Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY)
    if (moved <= CLICK_THRESHOLD_PX) {
      const p = toImageSpace(e)
      if (isInsideImage(p.x, p.y)) {
        triSync.commitFrom(triOri.value, p.x, p.y)
        emit('image-click', p.x, p.y)
      }
    }
  }
  wlBubble.visible = false
  drag.active = false
  drag.tool = null
  drag.scrub = null
  draw()
}

// ---------- 工具栏动作 ----------
function setTool(t: Tool): void {
  tool.value = t
}
function zoomIn(): void {
  zoom.value = Math.min(8, zoom.value * 1.25)
  draw()
}
function zoomOut(): void {
  zoom.value = Math.max(0.5, zoom.value / 1.25)
  draw()
}
/** 视图重置：恢复默认窗宽窗位 / 缩放 / 平移（测量记录保留，由「清除测量」单独管理；适配模式为全局偏好，不重置） */
function resetView(): void {
  const isPet = props.modality === 'PET'
  presetChoice.value = isPet ? 'PET_FDG' : 'MRI_T1'
  const p = isPet ? WINDOW_PRESETS.PET_FDG : WINDOW_PRESETS.MRI_T1
  Object.assign(customWl, { wc: p.wc, ww: p.ww })
  Object.assign(wl, DEFAULT_WL[props.modality])
  zoom.value = 1
  pan.x = 0
  pan.y = 0
  wlBubble.visible = false
  refreshEdgeGlow()
  if (useReal.value) scheduleRealReload()
  draw()
}

/** 清除全部测量记录（双击复位不再连带清空，避免误删测量） */
function clearMeasures(): void {
  measures.value = []
  rois.value = []
  draw()
}

/** 当前视口导出 PNG（含影像/标注/十字线/角标；HTML 浮层不在画布内，天然干净） */
function snapshotPNG(): void {
  const canvas = canvasEl.value
  if (!canvas) return
  const ts = new Date()
  const pad = (n: number): string => String(n).padStart(2, '0')
  const stamp = `${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}-${pad(ts.getHours())}${pad(ts.getMinutes())}`
  const name = `${props.realCaseId || props.modality}_${orientation.value}_L${props.sliceIdx + 1}_WC${fmtWl(displayWl.value.wc)}_WW${fmtWl(displayWl.value.ww)}_${stamp}.png`
  canvas.toBlob((blob) => {
    if (!blob) return
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
  }, 'image/png')
}

/** 跳转切片（滑杆 / 父级联动） */
watch(
  () => props.sliceIdx,
  () => draw()
)
// ROI 开关变化重绘
watch(
  () => props.showRoi,
  () => draw()
)
// 三平面十字线变化重绘（props 通道：非共享状态场景）
watch(
  () => props.crosshair,
  () => draw()
)
// 全局适配模式切换：所有视口即时重绘
watch(fitMode, () => draw())

// ---------- 尺寸自适应 + 三平面共享状态注册 ----------
let resizeObserver: ResizeObserver | null = null
let unsubscribeSync: (() => void) | null = null
/** 注册三平面 canvas 与订阅（canvas 在 disabled 期间不渲染，启用后需补注册） */
function ensureSyncRegister(): void {
  if (props.linkOnClick && canvasEl.value && !unsubscribeSync) {
    triSync.registerCanvas(triOri.value, canvasEl.value)
    unsubscribeSync = triSync.subscribe(() => draw())
  }
}
onMounted(() => {
  draw()
  resizeObserver = new ResizeObserver(() => draw())
  if (wrapEl.value) resizeObserver.observe(wrapEl.value)
  ensureSyncRegister()
})
// disabled（影像缺失占位）由 true 变 false 时 canvas 才被 v-if 插入：
// 必须在下一帧补一次尺寸对齐+首帧绘制，否则视口会稳定黑屏直到用户交互
watch(
  () => props.disabled,
  (disabled) => {
    if (!disabled) {
      nextTick(() => {
        ensureSyncRegister()
        draw()
      })
    }
  }
)
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  unsubscribeSync?.()
  unsubscribeSync = null
  // 拖拽可能在 window 上仍挂着监听（拖到画布外卸载等），显式解绑防止卸载后持续报错
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
  drag.active = false
  if (props.linkOnClick) {
    triSync.registerCanvas(triOri.value, null)
    triSync.clearHover(triOri.value)
  }
  sliceCache.clear()
  if (wlDebounceTimer) {
    clearTimeout(wlDebounceTimer)
    wlDebounceTimer = null
  }
})

/** 工具按钮激活样式 */
const toolBtnClass = (t: Tool): string =>
  tool.value === t ? 'bg-primary text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'

/** 模态徽标颜色 */
const badge = computed<string>(() =>
  props.modality === 'MRI' ? '#90CAF9' : props.modality === 'PET' ? '#FFB74D' : '#A5D6A7'
)
</script>

<template>
  <div class="viewport-dark relative rounded-card overflow-hidden border border-[#1f2b38] h-full flex flex-col">
    <!-- 视口标题条 -->
    <div class="h-9 shrink-0 flex items-center justify-between px-3 bg-[#101820] border-b border-[#1f2b38]">
      <span class="text-[12px] text-white/85 font-medium flex items-center gap-2">
        <span class="w-1.5 h-1.5 rounded-full" :style="{ background: badge }" />
        {{ label }}
      </span>
      <div class="flex items-center gap-2">
        <span class="text-[11px] text-white/40 font-num">FOV 250mm · {{ ORIENT_LABEL[orientation] }}</span>
        <!-- 窗宽窗位面板开关按钮 -->
        <el-tooltip content="窗宽窗位预设" placement="bottom" :show-after="400">
          <button
            class="w-6 h-6 rounded flex items-center justify-center text-[10px] font-semibold"
            :class="wlPanelOpen ? 'bg-primary text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
            @click="wlPanelOpen = !wlPanelOpen"
          >W/L</button>
        </el-tooltip>
      </div>
    </div>

    <!-- DICOM 元数据信息条（真实影像显示，合成/历史病例灰显占位） -->
    <div class="shrink-0 px-2 pt-2 bg-[#0d141c]">
      <DicomMetaBar :meta="dicomMeta ?? null" />
    </div>

    <!-- 窗宽窗位预设面板（展开时显示） -->
    <transition name="fade">
      <div v-if="wlPanelOpen" class="wl-panel shrink-0 px-3 py-2 bg-[#101820] border-b border-[#1f2b38]">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-[11px] text-white/60 shrink-0">预设</span>
          <el-select
            v-model="presetChoice"
            size="small"
            style="width: 130px"
            @change="(k: string | number | boolean | object) => selectPreset(k as PresetKey)"
          >
            <el-option v-for="item in presetOptions" :key="item.key" :label="item.label" :value="item.key" />
          </el-select>
          <span class="text-[11px] text-white/40 font-num ml-1">
            WC {{ effectiveWl.wc.toFixed(1) }} / WW {{ effectiveWl.ww.toFixed(1) }}
          </span>
        </div>
        <div class="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
          <div class="flex items-center gap-2">
            <span class="text-[10px] text-white/60 w-8 shrink-0">WC</span>
            <el-slider
              v-model="customWl.wc"
              :min="-200"
              :max="400"
              :step="1"
              :show-tooltip="false"
              size="small"
              class="flex-1"
              @change="onCustomWlChange"
            />
            <span class="text-[10px] text-white/40 font-num w-10 text-right">{{ customWl.wc.toFixed(0) }}</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-[10px] text-white/60 w-8 shrink-0">WW</span>
            <el-slider
              v-model="customWl.ww"
              :min="1"
              :max="800"
              :step="1"
              :show-tooltip="false"
              size="small"
              class="flex-1"
              @change="onCustomWlChange"
            />
            <span class="text-[10px] text-white/40 font-num w-10 text-right">{{ customWl.ww.toFixed(0) }}</span>
          </div>
        </div>
      </div>
    </transition>

    <!-- 画布区 -->
    <div ref="wrapEl" class="relative flex-1 min-h-0">
      <canvas
        v-if="!disabled"
        ref="canvasEl"
        class="absolute inset-0 w-full h-full select-none"
        :class="crossCursor ? '' : hoverRegion ? 'cursor-pointer' : 'cursor-crosshair'"
        :style="crossCursor ? { cursor: crossCursor } : undefined"
        @wheel="onWheel"
        @mousedown="onMouseDown"
        @mousemove="onHoverMove"
        @mouseleave="onCanvasLeave"
        @dblclick="resetView"
        @contextmenu.prevent
      />
      <!-- 影像缺失占位 -->
      <div v-else class="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/35">
        <el-icon :size="34"><PictureRounded /></el-icon>
        <span class="text-[13px]">该病例缺少本模态影像序列</span>
      </div>

      <!-- 真实影像加载失败：明确告知当前为合成示意，提供手动重试，不再静默锁死 -->
      <div
        v-if="!disabled && realFailed"
        class="absolute left-1/2 bottom-3 -translate-x-1/2 z-20 flex items-center gap-2 rounded-md bg-[#2b1a1f]/92 border border-[#C94F4F]/50 px-3 py-1.5 shadow-lg"
      >
        <span class="text-[11px] text-[#e8a5a5] whitespace-nowrap">真实影像加载失败，当前显示合成示意</span>
        <el-button size="small" type="danger" plain @click="retryRealSlice">重试</el-button>
      </div>

      <!-- 平移越界微光（pan 把影像拉出边界的那一侧发亮，回位自动消失） -->
      <div class="vp-edge-glow vp-edge-l" :class="{ 'vp-edge-on': edgeGlow.l }" />
      <div class="vp-edge-glow vp-edge-r" :class="{ 'vp-edge-on': edgeGlow.r }" />
      <div class="vp-edge-glow vp-edge-t" :class="{ 'vp-edge-on': edgeGlow.t }" />
      <div class="vp-edge-glow vp-edge-b" :class="{ 'vp-edge-on': edgeGlow.b }" />

      <!-- W/L 拖拽实时数值气泡 -->
      <transition name="fade">
        <div
          v-if="wlBubble.visible"
          class="absolute z-30 pointer-events-none rounded-md bg-[#0d141c]/92 border border-[#3b82f6]/50 px-2 py-1 shadow-lg"
          :style="{ left: wlBubble.x + 'px', top: wlBubble.y + 'px' }"
        >
          <span class="text-[11px] font-num text-[#90CAF9]">WC {{ fmtWl(wlBubble.wc) }} / WW {{ fmtWl(wlBubble.ww) }}</span>
        </div>
      </transition>

      <!-- 三平面联动提示（纯展示层，pointer-events-none 不拦截画布点击） -->
      <div
        v-if="linkOnClick && !disabled"
        class="absolute top-2 left-2 z-10 pointer-events-none flex items-center gap-1 rounded bg-[#3b82f6]/25 border border-[#3b82f6]/50 px-1.5 py-0.5"
      >
        <span class="w-1.5 h-1.5 rounded-full bg-[#60a5fa]" />
        <span class="text-[10px] text-[#bfdbfe]">单击定位联动</span>
      </div>

      <!-- 悬停脑区信息条（医患理解：功能 + AD 关联） -->
      <transition name="fade">
        <div
          v-if="hoverRegion"
          class="absolute bottom-2.5 left-1/2 -translate-x-1/2 z-20 w-[86%] max-w-[360px] rounded-lg bg-[#0d141c]/95 border border-[#2a3b4d] px-3 py-1.5 shadow-xl pointer-events-none"
        >
          <div class="flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full shrink-0" :style="{ background: abnormalNames.has(hoverRegion.name) ? '#EF5350' : hoverRegion.color }" />
            <span class="text-[12px] text-white/90 font-medium">{{ hoverRegion.name }}</span>
            <span class="text-[10px] text-white/40 font-num">{{ hoverRegion.en }}</span>
            <span v-if="abnormalNames.has(hoverRegion.name)" class="text-[10px] text-[#EF5350] ml-auto shrink-0">⚠ AI 异常</span>
          </div>
          <div class="text-[10.5px] text-white/60 leading-4 mt-0.5">{{ hoverRegion.fn }}</div>
          <div class="text-[10.5px] text-[#FFB74D]/80 leading-4">AD 关联：{{ hoverRegion.ad }}</div>
        </div>
      </transition>

      <!-- 重点脑区图例与说明弹层（医患友好） -->
      <transition name="fade">
        <div
          v-if="legendVisible"
          class="absolute left-2.5 bottom-2.5 z-10 w-[280px] max-h-[calc(100%-24px)] overflow-y-auto rounded-lg bg-[#0d141c]/95 border border-[#2a3b4d] shadow-xl p-3 vp-legend"
        >
          <div class="text-[12px] text-white/90 font-medium mb-1 flex items-center justify-between">
            AD 重点病症脑区图例
            <button class="text-white/40 hover:text-white/80" @click="legendVisible = false">
              <el-icon :size="13"><Close /></el-icon>
            </button>
          </div>
          <div class="text-[10px] text-white/35 leading-4 mb-2">标注为标准脑图谱近似位置，仅供示教理解，不作为定量诊断依据；⚠ 为 AI 提示异常的脑区</div>
          <div v-for="r in BRAIN_REGIONS" :key="r.key" class="py-1.5 border-t border-white/5 first:border-t-0">
            <div class="flex items-center gap-1.5">
              <span class="w-2 h-2 rounded-full shrink-0" :style="{ background: r.color, outline: abnormalNames.has(r.name) ? '1.5px solid #EF5350' : 'none' }" />
              <span class="text-[11.5px] text-white/85 font-medium">{{ r.name }}</span>
              <span class="text-[10px] text-white/35 font-num">{{ r.en }}</span>
              <span v-if="abnormalNames.has(r.name)" class="text-[9.5px] text-[#EF5350] ml-auto shrink-0">AI 异常</span>
            </div>
            <div class="text-[10.5px] text-white/55 leading-4 mt-0.5">功能：{{ r.fn }}</div>
            <div class="text-[10.5px] text-[#FFB74D]/75 leading-4">AD 关联：{{ r.ad }}</div>
          </div>
        </div>
      </transition>

      <!-- 悬浮工具栏（工作站风格：右上角竖排小按钮，超出可滚动） -->
      <div v-if="!disabled" class="vp-toolbar absolute top-2.5 right-2.5 flex flex-col gap-1 max-h-[calc(100%-20px)] overflow-y-auto p-0.5">
        <!-- 平面方向切换（仅真实影像模式、且非三平面固定槽位；槽位内切换会破坏联动身份） -->
        <template v-if="useReal && !linkOnClick">
          <el-tooltip content="轴位" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md flex items-center justify-center text-[10px] font-semibold"
              :class="orientation === 'axial' ? 'bg-primary text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
              @click="setOrientation('axial')">AX</button>
          </el-tooltip>
          <el-tooltip content="矢状位" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md flex items-center justify-center text-[10px] font-semibold"
              :class="orientation === 'sagittal' ? 'bg-primary text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
              @click="setOrientation('sagittal')">SG</button>
          </el-tooltip>
          <el-tooltip content="冠状位" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md flex items-center justify-center text-[10px] font-semibold"
              :class="orientation === 'coronal' ? 'bg-primary text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
              @click="setOrientation('coronal')">CR</button>
          </el-tooltip>
          <div class="h-px bg-white/15 mx-1" />
        </template>
        <el-tooltip content="窗宽窗位（拖拽调节）" placement="left" :show-after="400">
          <button class="w-8 h-8 rounded-md flex items-center justify-center" :class="toolBtnClass('window')" @click="setTool('window')">
            <el-icon :size="15"><Operation /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="平移" placement="left" :show-after="400">
          <button class="w-8 h-8 rounded-md flex items-center justify-center" :class="toolBtnClass('pan')" @click="setTool('pan')">
            <el-icon :size="15"><Rank /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="测量距离" placement="left" :show-after="400">
          <button class="w-8 h-8 rounded-md flex items-center justify-center" :class="toolBtnClass('measure')" @click="setTool('measure')">
            <el-icon :size="15"><Aim /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="ROI 矩形面积测量（青绿色框；导出 CSV 含直线+ROI 两段）" placement="left" :show-after="400">
          <button class="w-8 h-8 rounded-md flex items-center justify-center" :class="toolBtnClass('roi')" @click="setTool('roi')">
            <el-icon :size="15"><FullScreen /></el-icon>
          </button>
        </el-tooltip>
        <div class="h-px bg-white/15 mx-1" />
        <el-tooltip content="放大" placement="left" :show-after="400">
          <button class="w-8 h-8 rounded-md bg-black/45 text-white/85 hover:bg-black/70 flex items-center justify-center" @click="zoomIn">
            <el-icon :size="15"><ZoomIn /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="缩小" placement="left" :show-after="400">
          <button class="w-8 h-8 rounded-md bg-black/45 text-white/85 hover:bg-black/70 flex items-center justify-center" @click="zoomOut">
            <el-icon :size="15"><ZoomOut /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="视图重置（双击画布亦可）" placement="left" :show-after="400">
          <button class="w-8 h-8 rounded-md bg-black/45 text-white/85 hover:bg-black/70 flex items-center justify-center" @click="resetView">
            <el-icon :size="15"><RefreshLeft /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="导出当前视口 PNG" placement="left" :show-after="400">
          <button class="w-8 h-8 rounded-md bg-black/45 text-white/85 hover:bg-black/70 flex items-center justify-center" @click="snapshotPNG">
            <el-icon :size="15"><Camera /></el-icon>
          </button>
        </el-tooltip>
        <!-- 异常脑区热力图开关（仅轴位+有分析结果可用） -->
        <template v-if="heatmapEnabled">
          <div class="h-px bg-white/15 mx-1" />
          <el-tooltip :content="showHeatmap ? '隐藏异常脑区热力图' : '显示异常脑区热力图'" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md flex items-center justify-center"
              :class="showHeatmap ? 'bg-[#EF5350] text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
              @click="showHeatmap = !showHeatmap">
              <el-icon :size="15"><HotWater /></el-icon>
            </button>
          </el-tooltip>
        </template>
        <!-- 海马体积定量标注开关（仅轴位+有体积数据可用） -->
        <template v-if="hippocampusEnabled">
          <div class="h-px bg-white/15 mx-1" />
          <el-tooltip :content="showHippocampus ? '隐藏海马定量标注' : '显示海马体积标注'" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md flex items-center justify-center"
              :class="showHippocampus ? 'bg-[#4FC3F7] text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
              @click="showHippocampus = !showHippocampus">
              <el-icon :size="15"><Coin /></el-icon>
            </button>
          </el-tooltip>
        </template>
        <!-- 重点病症脑区示教标注开关（三平面可用） -->
        <template v-if="!disabled">
          <div class="h-px bg-white/15 mx-1" />
          <el-tooltip :content="showRegions ? '隐藏重点脑区标注' : '显示重点病症脑区标注'" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md flex items-center justify-center"
              :class="showRegions ? 'bg-[#7E57C2] text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
              @click="showRegions = !showRegions">
              <el-icon :size="15"><Location /></el-icon>
            </button>
          </el-tooltip>
          <el-tooltip content="重点脑区图例与说明" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md bg-black/45 text-white/85 hover:bg-black/70 flex items-center justify-center"
              @click="legendVisible = !legendVisible">
              <el-icon :size="15"><InfoFilled /></el-icon>
            </button>
          </el-tooltip>
        </template>
        <!-- Grad-CAM 代谢偏差热力图（仅 MRI 真实模式可用） -->
        <template v-if="gradcamAvailable">
          <div class="h-px bg-white/15 mx-1" />
          <el-tooltip :content="showGradcam ? '隐藏 Grad-CAM 热力图' : '显示 Grad-CAM 代谢偏差热力图'" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md flex items-center justify-center"
              :class="showGradcam ? 'bg-[#AB47BC] text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
              @click="showGradcam = !showGradcam">
              <el-icon :size="15"><Sunny /></el-icon>
            </button>
          </el-tooltip>
        </template>
        <!-- 测量记录存在时：清除全部 + 导出 CSV -->
        <template v-if="measures.length > 0">
          <div class="h-px bg-white/15 mx-1" />
          <el-tooltip content="清除全部测量" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md bg-black/45 text-white/85 hover:bg-black/70 flex items-center justify-center" @click="clearMeasures">
              <el-icon :size="15"><Delete /></el-icon>
            </button>
          </el-tooltip>
          <el-tooltip content="导出测量结果 (CSV)" placement="left" :show-after="400">
            <button class="w-8 h-8 rounded-md bg-black/45 text-white/85 hover:bg-black/70 flex items-center justify-center" @click="exportMeasures">
              <el-icon :size="15"><Download /></el-icon>
            </button>
          </el-tooltip>
        </template>
      </div>
    </div>

    <!-- 切片滑杆 -->
    <div v-if="!disabled" class="h-11 shrink-0 flex items-center gap-3 px-4 bg-[#101820] border-t border-[#1f2b38]">
      <el-button
        size="small"
        text
        class="!text-white/70"
        :icon="'ArrowLeft'"
        :disabled="sliceIdx <= 0"
        @click="emit('update:sliceIdx', sliceIdx - 1)"
      />
      <el-slider
        :model-value="sliceIdx"
        :min="0"
        :max="sliceCount - 1"
        :show-tooltip="false"
        size="small"
        class="flex-1"
        @update:model-value="(v: number | number[]) => emit('update:sliceIdx', v as number)"
      />
      <el-button
        size="small"
        text
        class="!text-white/70"
        :icon="'ArrowRight'"
        :disabled="sliceIdx >= sliceCount - 1"
        @click="emit('update:sliceIdx', sliceIdx + 1)"
      />
      <span class="font-num text-[12px] text-white/60 w-16 text-right">L{{ sliceIdx + 1 }}</span>
    </div>
  </div>
</template>

<style scoped>
/* 脑区图例弹层过渡 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
/* 悬浮工具栏超出时显示细滚动条，保证底部按钮（热力图/海马/Grad-CAM/导出）可到达 */
.vp-toolbar {
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.35) transparent;
}
.vp-toolbar::-webkit-scrollbar {
  width: 4px;
}
.vp-toolbar::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.3);
  border-radius: 2px;
}
.vp-toolbar::-webkit-scrollbar-track {
  background: transparent;
}
/* 平移越界微光：默认透明，影像被拖出该侧时淡入一条蓝色柔光辉 */
.vp-edge-glow {
  position: absolute;
  z-index: 5;
  pointer-events: none;
  opacity: 0;
  transition: opacity 200ms ease;
}
.vp-edge-l,
.vp-edge-r {
  top: 0;
  bottom: 0;
  width: 10px;
}
.vp-edge-l {
  left: 0;
  background: linear-gradient(to right, rgba(59, 130, 246, 0.35), transparent);
}
.vp-edge-r {
  right: 0;
  background: linear-gradient(to left, rgba(59, 130, 246, 0.35), transparent);
}
.vp-edge-t,
.vp-edge-b {
  left: 0;
  right: 0;
  height: 10px;
}
.vp-edge-t {
  top: 0;
  background: linear-gradient(to bottom, rgba(59, 130, 246, 0.35), transparent);
}
.vp-edge-b {
  bottom: 0;
  background: linear-gradient(to top, rgba(59, 130, 246, 0.35), transparent);
}
.vp-edge-on {
  opacity: 1;
}
</style>
