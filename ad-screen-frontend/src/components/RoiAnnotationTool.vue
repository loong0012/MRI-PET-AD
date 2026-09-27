<script setup lang="ts">
/**
 * 影像 ROI 标注工具组件
 * ------------------------------------------------------------------
 * 嵌入阅片器（ViewerView）的标注工具面板：
 *  - 工具按钮组：球形 / 矩形 / 多边形 ROI 绘制 + 清除 + 撤销 + 导出
 *  - 标签下拉：海马体 / 内嗅皮层 / 杏仁核 / 脑室 / 自定义
 *  - 标注列表：显示已有标注，可切换显隐 / 跳转切片 / 删除
 *  - GradCAM 热图叠加对比开关（emit 事件通知父组件）
 *
 * 渲染：自包含 Canvas（256×256 影像空间，CSS 拉伸到展示尺寸），
 * 切片图通过 apiSliceUrl 加载，ROI 用 Canvas 2D API 叠加绘制。
 * 撤销用历史栈管理（仅限本会话内未保存草稿；已保存标注的撤销走删除接口）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiSliceUrl, loadGradcam, type DicomMeta } from '@/api/imaging'
import { ensureMediaToken } from '@/utils/mediaToken'
import { SLICE_SIZE } from '@/utils/imaging'
import { usePixelSpacing } from '@/composables/usePixelSpacing'
import {
  apiListAnnotations,
  apiCreateAnnotation,
  apiDeleteAnnotation,
  apiExportAnnotations
} from '@/api/annotation'
import type { AnnotationItem, RoiType } from '@/types/annotation'

const props = defineProps<{
  /** 病例编号 */
  caseId: string
  /** 当前切片索引（与阅片器联动） */
  sliceIndex: number
  /** 影像模态：MRI / PET */
  modality: string
  /** DICOM 元数据（含 pixelSpacing，用于距离测量的 mm 换算；可选） */
  dicomMeta?: DicomMeta | null
}>()

/** 测量模式：ROI 标注 / 距离 / 角度 */
type MeasureMode = 'roi' | 'distance' | 'angle'
const mode = ref<MeasureMode>('roi')

const emit = defineEmits<{
  /** 新标注创建成功 */
  (e: 'roi-created', item: AnnotationItem): void
  /** 标注删除成功 */
  (e: 'roi-deleted', id: number): void
  /** GradCAM 叠加对比开关切换 */
  (e: 'toggle-gradcam', on: boolean): void
  /** 请求跳转切片（列表项跳转） */
  (e: 'jump-slice', idx: number): void
}>()

// ---------- 常量 ----------
/** ROI 标签预设 */
const LABEL_OPTIONS = ['海马体', '内嗅皮层', '杏仁核', '脑室', '自定义'] as const
/** ROI 类型中文 */
const ROI_TYPE_LABEL: Record<RoiType, string> = {
  sphere: '球形',
  rect: '矩形',
  polygon: '多边形'
}
/** 256 像素影像空间 → 物理毫米（与 MedicalViewport 一致：250mm FOV） */
const MM_PER_PX = 250 / SLICE_SIZE

/**
 * 错误提示去重：请求拦截器已对业务错误统一 ElMessage.error 并打 __handled 标记，
 * 此处仅对纯本地异常补一次提示，避免同一错误弹两次 toast。
 */
function notifyLocalError(e: unknown, fallback: string): void {
  if ((e as { __handled?: boolean })?.__handled) return
  ElMessage.error(e instanceof Error ? e.message : fallback)
}

// ---------- 工具状态 ----------
/** 当前激活的绘制工具 */
const activeTool = ref<RoiType | null>('sphere')
/** 当前选择的标签 */
const labelChoice = ref<string>('海马体')
/** 自定义标签输入（labelChoice 为"自定义"时启用） */
const customLabel = ref<string>('')
/** GradCAM 叠加对比开关 */
const gradcamOn = ref<boolean>(false)
/** 切片图加载状态 */
const sliceLoading = ref<boolean>(false)
/** 标注列表加载状态 */
const listLoading = ref<boolean>(false)

/** 当前生效标签（合并预设与自定义） */
const currentLabel = computed<string>(() =>
  labelChoice.value === '自定义' ? (customLabel.value.trim() || '自定义') : labelChoice.value
)

// ---------- 像素间距换算（P12 距离测量 mm 显示）----------
const { hasScale, toMm } = usePixelSpacing(computed(() => props.dicomMeta ?? null))

// ---------- 测量数据（distance / angle 模式）----------
interface Point2D { x: number; y: number }
interface DistanceMeasure { p1: Point2D; p2: Point2D; mm: number | null }
interface AngleMeasure { p1: Point2D; vertex: Point2D; p2: Point2D; angleDeg: number }
/** 已完成的距离测量列表 */
const distMeasures = ref<DistanceMeasure[]>([])
/** 已完成的角度测量列表 */
const angleMeasures = ref<AngleMeasure[]>([])
/** 当前距离测量草稿（点击第一点后等第二点） */
const distDraft = ref<Point2D | null>(null)
/** 当前角度测量草稿（累积点击点，最多 3 个） */
const angleDraft = ref<Point2D[]>([])

/** 计算两点像素距离 */
function pxDistance(a: Point2D, b: Point2D): number {
  return Math.hypot(b.x - a.x, b.y - a.y)
}
/** 计算角度（°）：以 vertex 为顶点的两条线段夹角，用向量点积公式 */
function angleAtVertex(p1: Point2D, vertex: Point2D, p2: Point2D): number {
  const v1 = { x: p1.x - vertex.x, y: p1.y - vertex.y }
  const v2 = { x: p2.x - vertex.x, y: p2.y - vertex.y }
  const dot = v1.x * v2.x + v1.y * v2.y
  const m1 = Math.hypot(v1.x, v1.y)
  const m2 = Math.hypot(v2.x, v2.y)
  if (m1 < 1e-6 || m2 < 1e-6) return 0
  const cos = Math.max(-1, Math.min(1, dot / (m1 * m2)))
  return (Math.acos(cos) * 180) / Math.PI
}

/** 清空测量草稿与已完成列表（仅清测量层，不影响 ROI 标注） */
function clearMeasures(): void {
  distMeasures.value = []
  angleMeasures.value = []
  distDraft.value = null
  angleDraft.value = []
  drawMeasureLayer()
}

// ---------- 标注数据 ----------
/** 已保存的标注列表 */
const annotations = ref<AnnotationItem[]>([])
/** 标注显隐映射（id → 是否显示） */
const hiddenSet = ref<Set<number>>(new Set())

/** 获取该切片下的标注（用于绘制） */
const visibleAnnotations = computed(() =>
  annotations.value.filter(
    (a) => a.sliceIndex === props.sliceIndex && !hiddenSet.value.has(a.id)
  )
)

// ---------- 草稿与历史栈 ----------
/** 正在绘制的草稿（球形/矩形为单形状；多边形为多点） */
interface DraftShape {
  type: RoiType
  coords: Record<string, unknown>
}
/** 当前草稿（绘制中） */
const draft = ref<DraftShape | null>(null)
/** 历史栈：每条为一个已提交草稿（可撤销删除） */
const history = ref<DraftShape[]>([])

// ---------- 切片图缓存 ----------
const sliceImg = ref<HTMLImageElement | null>(null)
const sliceCache = new Map<string, HTMLImageElement>()
const gradcamImg = ref<HTMLImageElement | null>(null)
const gradcamCache = new Map<string, HTMLImageElement>()
/** 单调请求序号：快速翻层时丢弃过期响应，避免晚到的旧层图片覆盖当前层 */
let sliceReqSeq = 0
let gradcamReqSeq = 0

/** 切片缓存键 */
function sliceKey(idx: number): string {
  return `${props.caseId}|${props.modality}|${idx}`
}
/** GradCAM 缓存键（仅 MRI 模态） */
function gradcamKey(idx: number): string {
  return `${props.caseId}|${idx}`
}

/** 加载切片图（带缓存；仅最后一次请求允许写入当前显示，防止乱序错层） */
async function ensureSlice(idx: number): Promise<void> {
  // 层号硬校验：仅接受非负整数（NaN/负数/小数无意义，上层未传层数时上限由后端 404 兜底）
  if (!Number.isInteger(idx) || idx < 0) return
  const seq = ++sliceReqSeq
  const key = sliceKey(idx)
  if (sliceCache.has(key)) {
    if (seq === sliceReqSeq && idx === props.sliceIndex) {
      sliceImg.value = sliceCache.get(key) ?? null
      draw()
    }
    return
  }
  if (seq === sliceReqSeq) sliceLoading.value = true
  try {
    await ensureMediaToken()
    const url = apiSliceUrl(props.caseId, props.modality as 'MRI' | 'PET', idx)
    const img = await loadImage(url)
    sliceCache.set(key, img)
    if (sliceCache.size > 24) {
      const first = sliceCache.keys().next().value
      if (first !== undefined) sliceCache.delete(first)
    }
    // 过期响应（用户已翻到别的层）：只入缓存，不覆盖当前显示
    if (seq === sliceReqSeq && idx === props.sliceIndex) {
      sliceImg.value = img
      draw()
    }
  } catch {
    if (seq === sliceReqSeq && idx === props.sliceIndex) {
      sliceImg.value = null
      ElMessage.warning('切片图加载失败，请检查影像是否可用')
    }
  } finally {
    if (seq === sliceReqSeq) sliceLoading.value = false
  }
}

/** 加载 GradCAM 热图（带缓存，同样用请求序号防乱序） */
async function ensureGradcam(idx: number): Promise<void> {
  if (props.modality !== 'MRI') return
  const seq = ++gradcamReqSeq
  const key = gradcamKey(idx)
  if (gradcamCache.has(key)) {
    if (seq === gradcamReqSeq && idx === props.sliceIndex) {
      gradcamImg.value = gradcamCache.get(key) ?? null
      draw()
    }
    return
  }
  try {
    const img = await loadGradcam(props.caseId, idx)
    gradcamCache.set(key, img)
    if (gradcamCache.size > 20) {
      const first = gradcamCache.keys().next().value
      if (first !== undefined) gradcamCache.delete(first)
    }
    if (seq === gradcamReqSeq && idx === props.sliceIndex) {
      gradcamImg.value = img
      draw()
    }
  } catch {
    if (seq === gradcamReqSeq && idx === props.sliceIndex) gradcamImg.value = null
  }
}

/** 通用图片加载 */
function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('图片加载失败'))
    img.src = url
  })
}

// ---------- Canvas 引用与绘制 ----------
/** ROI 层 Canvas（基础切片图 + ROI 标注） */
const canvasEl = ref<HTMLCanvasElement | null>(null)
/** 测量层 Canvas（距离/角度叠加；与 ROI 层同尺寸，绝对定位浮于上层，pointer-events: none） */
const measureLayerEl = ref<HTMLCanvasElement | null>(null)

/** 屏幕坐标 → 影像空间坐标（256×256） */
function toImageCoord(e: MouseEvent): { x: number; y: number } {
  const canvas = canvasEl.value
  if (!canvas) return { x: 0, y: 0 }
  const rect = canvas.getBoundingClientRect()
  const sx = (e.clientX - rect.left) / rect.width
  const sy = (e.clientY - rect.top) / rect.height
  return {
    x: Math.max(0, Math.min(SLICE_SIZE - 1, Math.round(sx * SLICE_SIZE))),
    y: Math.max(0, Math.min(SLICE_SIZE - 1, Math.round(sy * SLICE_SIZE)))
  }
}

/** 主绘制函数 */
function draw(): void {
  const canvas = canvasEl.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  // Canvas 内部固定为 256×256 影像空间，CSS 拉伸到容器尺寸
  if (canvas.width !== SLICE_SIZE) canvas.width = SLICE_SIZE
  if (canvas.height !== SLICE_SIZE) canvas.height = SLICE_SIZE
  // 黑底
  ctx.fillStyle = '#0b0f14'
  ctx.fillRect(0, 0, SLICE_SIZE, SLICE_SIZE)
  // 切片图
  if (sliceImg.value) {
    ctx.imageSmoothingEnabled = true
    ctx.drawImage(sliceImg.value, 0, 0, SLICE_SIZE, SLICE_SIZE)
  }
  // GradCAM 叠加（MRI + 开启）
  if (gradcamOn.value && gradcamImg.value && props.modality === 'MRI') {
    ctx.globalAlpha = 0.55
    ctx.drawImage(gradcamImg.value, 0, 0, SLICE_SIZE, SLICE_SIZE)
    ctx.globalAlpha = 1
  }
  // 已保存标注（按当前切片过滤）
  for (const a of visibleAnnotations.value) {
    drawAnnotation(ctx, a, false)
  }
  // 草稿（绘制中）
  if (draft.value) {
    drawDraftShape(ctx, draft.value)
  }
  // 同步刷新测量层
  drawMeasureLayer()
}

/**
 * 绘制测量层（距离 / 角度）
 * 在 measureLayer canvas 上独立绘制，与 ROI 层物理隔离；
 * distance 模式：每条线段 + 端点 + 长度标签（mm 或 px）；
 * angle 模式：两条线段 + 顶点 + 角度标签（°）；
 * 进行中的草稿也会绘制（提示用户当前点击进度）。
 */
function drawMeasureLayer(): void {
  const canvas = measureLayerEl.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  if (canvas.width !== SLICE_SIZE) canvas.width = SLICE_SIZE
  if (canvas.height !== SLICE_SIZE) canvas.height = SLICE_SIZE
  ctx.clearRect(0, 0, SLICE_SIZE, SLICE_SIZE)
  const stroke = '#FFD54F'
  const draftStroke = '#FFE082'
  // ----- 已完成距离测量 -----
  for (const d of distMeasures.value) {
    drawDistLine(ctx, d.p1, d.p2, stroke, formatDistLabel(d))
  }
  // ----- 已完成角度测量 -----
  for (const a of angleMeasures.value) {
    drawAngle(ctx, a.p1, a.vertex, a.p2, stroke, `${a.angleDeg.toFixed(1)}°`)
  }
  // ----- 距离草稿（已点击第一点，等待第二点） -----
  if (mode.value === 'distance' && distDraft.value) {
    ctx.beginPath()
    ctx.arc(distDraft.value.x, distDraft.value.y, 2.4, 0, Math.PI * 2)
    ctx.fillStyle = draftStroke
    ctx.fill()
    drawLabel(ctx, '点 1/2', distDraft.value.x, distDraft.value.y - 6, draftStroke)
  }
  // ----- 角度草稿（累积点击点 1~2 个） -----
  if (mode.value === 'angle' && angleDraft.value.length > 0) {
    for (let i = 0; i < angleDraft.value.length; i++) {
      const p = angleDraft.value[i]
      ctx.beginPath()
      ctx.arc(p.x, p.y, 2.4, 0, Math.PI * 2)
      ctx.fillStyle = draftStroke
      ctx.fill()
      drawLabel(ctx, `点 ${i + 1}/3`, p.x, p.y - 6, draftStroke)
    }
  }
}

/** 距离测量长度标签：有标尺→"X.X mm"，无标尺→"X px · 无标尺信息" */
function formatDistLabel(d: DistanceMeasure): string {
  const px = pxDistance(d.p1, d.p2)
  const mm = d.mm
  if (mm !== null) return `${mm.toFixed(2)} mm`
  return `${px.toFixed(0)} px · 无标尺信息`
}

/** 画一条带端点 + 文字标签的距离线 */
function drawDistLine(
  ctx: CanvasRenderingContext2D,
  p1: Point2D,
  p2: Point2D,
  stroke: string,
  label: string
): void {
  ctx.beginPath()
  ctx.moveTo(p1.x, p1.y)
  ctx.lineTo(p2.x, p2.y)
  ctx.strokeStyle = stroke
  ctx.lineWidth = 1.4
  ctx.setLineDash([])
  ctx.stroke()
  for (const p of [p1, p2]) {
    ctx.beginPath()
    ctx.arc(p.x, p.y, 2.4, 0, Math.PI * 2)
    ctx.fillStyle = stroke
    ctx.fill()
  }
  const mx = (p1.x + p2.x) / 2
  const my = (p1.y + p2.y) / 2
  drawLabel(ctx, label, mx, my - 6, stroke)
}

/** 画角度：p1-vertex-p2 两条线段 + 顶点 + 角度标签 */
function drawAngle(
  ctx: CanvasRenderingContext2D,
  p1: Point2D,
  vertex: Point2D,
  p2: Point2D,
  stroke: string,
  label: string
): void {
  ctx.beginPath()
  ctx.moveTo(p1.x, p1.y)
  ctx.lineTo(vertex.x, vertex.y)
  ctx.lineTo(p2.x, p2.y)
  ctx.strokeStyle = stroke
  ctx.lineWidth = 1.4
  ctx.setLineDash([])
  ctx.stroke()
  // 顶点强调
  ctx.beginPath()
  ctx.arc(vertex.x, vertex.y, 3, 0, Math.PI * 2)
  ctx.fillStyle = stroke
  ctx.fill()
  for (const p of [p1, p2]) {
    ctx.beginPath()
    ctx.arc(p.x, p.y, 2.2, 0, Math.PI * 2)
    ctx.fillStyle = stroke
    ctx.fill()
  }
  drawLabel(ctx, label, vertex.x, vertex.y - 8, stroke)
}

/** 绘制单条标注 */
function drawAnnotation(ctx: CanvasRenderingContext2D, a: AnnotationItem, isDraft: boolean): void {
  const stroke = isDraft ? '#FFC857' : '#2f6da3'
  const fill = isDraft ? 'rgba(255,200,87,0.18)' : 'rgba(47,109,163,0.18)'
  ctx.lineWidth = 1.5
  ctx.strokeStyle = stroke
  ctx.fillStyle = fill
  if (a.roiType === 'sphere') {
    const c = a.coords as { cx: number; cy: number; r: number }
    const r = Math.max(1, c.r ?? 0)
    ctx.beginPath()
    ctx.arc(c.cx ?? 0, c.cy ?? 0, r, 0, Math.PI * 2)
    ctx.fill()
    ctx.stroke()
    // 标签
    drawLabel(ctx, a.label || ROI_TYPE_LABEL[a.roiType], c.cx ?? 0, (c.cy ?? 0) - r - 4, stroke)
  } else if (a.roiType === 'rect') {
    const c = a.coords as { x: number; y: number; w: number; h: number }
    ctx.beginPath()
    ctx.rect(c.x ?? 0, c.y ?? 0, Math.max(1, c.w ?? 0), Math.max(1, c.h ?? 0))
    ctx.fill()
    ctx.stroke()
    drawLabel(ctx, a.label || ROI_TYPE_LABEL[a.roiType], c.x ?? 0, (c.y ?? 0) - 4, stroke)
  } else if (a.roiType === 'polygon') {
    const c = a.coords as { points: Array<{ x: number; y: number }> }
    const pts = c.points ?? []
    if (pts.length < 1) return
    ctx.beginPath()
    ctx.moveTo(pts[0].x, pts[0].y)
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y)
    if (pts.length >= 3) ctx.closePath()
    ctx.fill()
    ctx.stroke()
    // 顶点
    ctx.fillStyle = stroke
    for (const p of pts) {
      ctx.beginPath()
      ctx.arc(p.x, p.y, 1.6, 0, Math.PI * 2)
      ctx.fill()
    }
    drawLabel(ctx, a.label || ROI_TYPE_LABEL[a.roiType], pts[0].x, pts[0].y - 6, stroke)
  }
}

/** 草稿统一走 drawAnnotation（isDraft=true 高亮） */
function drawDraftShape(ctx: CanvasRenderingContext2D, d: DraftShape): void {
  const fake: AnnotationItem = {
    id: -1,
    caseId: props.caseId,
    user: '',
    sliceIndex: props.sliceIndex,
    modality: props.modality,
    roiType: d.type,
    coords: d.coords,
    label: currentLabel.value,
    area: 0,
    notes: '',
    createdAt: ''
  }
  drawAnnotation(ctx, fake, true)
}

/** 绘制文字标签（带深色底衬，确保在任意影像上都可读） */
function drawLabel(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, color: string): void {
  if (!text) return
  ctx.save()
  ctx.font = '10px "PingFang SC", "Microsoft YaHei", sans-serif'
  const tw = ctx.measureText(text).width + 6
  const ty = Math.max(0, Math.min(SLICE_SIZE - 12, y))
  ctx.fillStyle = 'rgba(8,12,18,0.82)'
  ctx.fillRect(x - 2, ty - 9, tw, 12)
  ctx.fillStyle = color
  ctx.fillText(text, x + 1, ty + 1)
  ctx.restore()
}

// ---------- 鼠标交互 ----------
/** 多边形点击点缓存（多边形为多点点击模式） */
const polyPoints = ref<Array<{ x: number; y: number }>>([])
/** 球形/矩形拖拽起点 */
const dragStart = ref<{ x: number; y: number } | null>(null)
/** 是否正在拖拽 */
const dragging = ref<boolean>(false)

function onMouseDown(e: MouseEvent): void {
  const p = toImageCoord(e)
  // ----- 测量模式：distance / angle -----
  if (mode.value === 'distance') {
    if (!distDraft.value) {
      // 第一点
      distDraft.value = p
      drawMeasureLayer()
      return
    }
    // 第二点 → 完成一条距离测量
    const px = pxDistance(distDraft.value, p)
    const mm = toMm(px)
    distMeasures.value.push({ p1: distDraft.value, p2: p, mm })
    distDraft.value = null
    drawMeasureLayer()
    ElMessage.success(mm !== null ? `已测距离 ${mm.toFixed(2)} mm` : `已测距离 ${px.toFixed(0)} px（无标尺信息）`)
    return
  }
  if (mode.value === 'angle') {
    angleDraft.value.push(p)
    if (angleDraft.value.length < 3) {
      // 等待下一个点
      drawMeasureLayer()
      return
    }
    // 第三点 → 完成角度测量（vertex 为第二点）
    const [p1, vertex, p2] = angleDraft.value
    const deg = angleAtVertex(p1, vertex, p2)
    angleMeasures.value.push({ p1, vertex, p2, angleDeg: deg })
    angleDraft.value = []
    drawMeasureLayer()
    ElMessage.success(`已测角度 ${deg.toFixed(1)}°`)
    return
  }
  // ----- ROI 模式 -----
  if (!activeTool.value) return
  if (activeTool.value === 'polygon') {
    polyPoints.value.push(p)
    draft.value = { type: 'polygon', coords: { points: [...polyPoints.value] } }
    draw()
    return
  }
  dragStart.value = p
  dragging.value = true
}

function onMouseMove(e: MouseEvent): void {
  if (!dragging.value || !dragStart.value || !activeTool.value) return
  const p = toImageCoord(e)
  if (activeTool.value === 'sphere') {
    const r = Math.hypot(p.x - dragStart.value.x, p.y - dragStart.value.y)
    draft.value = {
      type: 'sphere',
      coords: { cx: dragStart.value.x, cy: dragStart.value.y, r: Math.round(r) }
    }
  } else if (activeTool.value === 'rect') {
    draft.value = {
      type: 'rect',
      coords: {
        x: Math.min(dragStart.value.x, p.x),
        y: Math.min(dragStart.value.y, p.y),
        w: Math.abs(p.x - dragStart.value.x),
        h: Math.abs(p.y - dragStart.value.y)
      }
    }
  }
  draw()
}

/** 标注最小尺寸（px）：小于该值视为误点，不落库（半径或宽高） */
const MIN_ROI_PX = 3

/** 草稿是否达到最小可保存尺寸（过滤原地单击产生的 0 面积标注） */
function isDraftBigEnough(d: DraftShape): boolean {
  if (d.type === 'sphere') return (d.coords as { r?: number }).r !== undefined && (d.coords as { r: number }).r >= MIN_ROI_PX
  if (d.type === 'rect') {
    const c = d.coords as { w?: number; h?: number }
    return (c.w ?? 0) >= MIN_ROI_PX && (c.h ?? 0) >= MIN_ROI_PX
  }
  return true
}

function onMouseUp(): void {
  if (!dragging.value) return
  dragging.value = false
  if (draft.value && activeTool.value !== 'polygon') {
    if (isDraftBigEnough(draft.value)) {
      void commitDraft(draft.value)
    } else {
      draft.value = null
      ElMessage.info('拖动范围过小，未生成标注')
    }
  }
  dragStart.value = null
}

/** 指针离开画布：取消进行中的拖拽草稿（不在边界点提前落库）；测量点不回滚 */
function onCanvasLeave(): void {
  if (!dragging.value) return
  dragging.value = false
  draft.value = null
  dragStart.value = null
  draw()
}

/** 多边形双击完成（measure 模式下不响应） */
function onDblClick(): void {
  if (mode.value !== 'roi') return
  if (activeTool.value !== 'polygon' || polyPoints.value.length < 3) return
  commitDraft({ type: 'polygon', coords: { points: [...polyPoints.value] } })
  polyPoints.value = []
}

/** 右键：ROI 模式完成多边形；measure 模式取消当前草稿 */
function onContextMenu(e: MouseEvent): void {
  e.preventDefault()
  if (mode.value === 'distance') {
    distDraft.value = null
    drawMeasureLayer()
    return
  }
  if (mode.value === 'angle') {
    angleDraft.value = []
    drawMeasureLayer()
    return
  }
  if (activeTool.value === 'polygon' && polyPoints.value.length >= 3) {
    commitDraft({ type: 'polygon', coords: { points: [...polyPoints.value] } })
    polyPoints.value = []
  }
}

// ---------- 提交与保存 ----------
/** 计算草稿面积（像素） */
function calcArea(d: DraftShape): number {
  if (d.type === 'sphere') {
    const c = d.coords as { r: number }
    return Math.PI * Math.pow(c.r ?? 0, 2)
  }
  if (d.type === 'rect') {
    const c = d.coords as { w: number; h: number }
    return (c.w ?? 0) * (c.h ?? 0)
  }
  if (d.type === 'polygon') {
    const c = d.coords as { points: Array<{ x: number; y: number }> }
    const pts = c.points ?? []
    if (pts.length < 3) return 0
    // 鞋带公式
    let s = 0
    for (let i = 0; i < pts.length; i++) {
      const j = (i + 1) % pts.length
      s += pts[i].x * pts[j].y - pts[j].x * pts[i].y
    }
    return Math.abs(s) / 2
  }
  return 0
}

/** 提交草稿：先入历史栈，再调用后端保存 */
async function commitDraft(d: DraftShape): Promise<void> {
  const area = calcArea(d)
  draft.value = null
  listLoading.value = true
  try {
    const result = await apiCreateAnnotation(props.caseId, {
      sliceIndex: props.sliceIndex,
      modality: props.modality,
      roiType: d.type,
      coords: d.coords,
      label: currentLabel.value,
      area,
      notes: ''
    })
    // 拉取最新列表（保证排序与字段一致）
    await loadAnnotations()
    const created = annotations.value.find((a) => a.id === result.id)
    if (created) emit('roi-created', created)
    ElMessage.success(`已保存 ${ROI_TYPE_LABEL[d.type]} 标注（面积 ${(area * MM_PER_PX * MM_PER_PX).toFixed(1)} mm²）`)
  } catch (e) {
    notifyLocalError(e, '保存失败')
  } finally {
    listLoading.value = false
    draw()
  }
}

// ---------- 撤销与清除 ----------
/** 撤销：清空当前草稿 / 多边形点；如有未保存历史则弹出 */
function undo(): void {
  if (draft.value) {
    draft.value = null
    polyPoints.value = []
    dragging.value = false
    draw()
    return
  }
  if (history.value.length > 0) {
    history.value.pop()
    draw()
    return
  }
  // 没有草稿与历史 → 删除最近一条已保存标注（按 createdAt 倒序）
  const latest = annotations.value[0]
  if (!latest) {
    ElMessage.info('无可撤销的标注')
    return
  }
  ElMessageBox.confirm(
    `将删除最近一条标注（${ROI_TYPE_LABEL[latest.roiType]} - ${latest.label || '无标签'}），是否继续？`,
    '撤销最近标注',
    { type: 'warning' }
  )
    .then(async () => {
      await removeAnnotation(latest.id)
    })
    .catch(() => {
      /* 用户取消 */
    })
}

/** 清除：清空当前草稿与多边形点 */
function clearDraft(): void {
  draft.value = null
  polyPoints.value = []
  dragging.value = false
  dragStart.value = null
  draw()
}

// ---------- 标注列表操作 ----------
/** 加载该病例的所有标注 */
async function loadAnnotations(): Promise<void> {
  if (!props.caseId) return
  listLoading.value = true
  try {
    annotations.value = await apiListAnnotations(props.caseId)
  } catch (e) {
    notifyLocalError(e, '标注列表加载失败')
  } finally {
    listLoading.value = false
    draw()
  }
}

/** 切换标注显隐 */
function toggleVisibility(id: number): void {
  if (hiddenSet.value.has(id)) hiddenSet.value.delete(id)
  else hiddenSet.value.add(id)
  draw()
}

/** 跳转到该标注所在切片 */
function jumpToSlice(idx: number): void {
  emit('jump-slice', idx)
}

/** 删除标注 */
async function removeAnnotation(id: number): Promise<void> {
  try {
    await apiDeleteAnnotation(id)
    annotations.value = annotations.value.filter((a) => a.id !== id)
    hiddenSet.value.delete(id)
    emit('roi-deleted', id)
    ElMessage.success('标注已删除')
    draw()
  } catch (e) {
    notifyLocalError(e, '删除失败')
  }
}

/** 导出该病例的标注 JSON */
async function exportAnnotations(): Promise<void> {
  if (annotations.value.length === 0) {
    ElMessage.warning('暂无标注可导出')
    return
  }
  try {
    const data = await apiExportAnnotations(props.caseId)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `标注_${props.caseId}.json`
    // 部分 Firefox 要求节点挂载到 DOM 后 click() 才触发下载
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${data.annotations.length} 条标注`)
  } catch (e) {
    notifyLocalError(e, '导出失败')
  }
}

// ---------- GradCAM 开关 ----------
function onGradcamToggle(val: boolean): void {
  gradcamOn.value = val
  emit('toggle-gradcam', val)
  if (val && props.modality === 'MRI') {
    void ensureGradcam(props.sliceIndex)
  } else {
    draw()
  }
}

// ---------- 工具切换 ----------
function selectTool(t: RoiType | null): void {
  activeTool.value = t
  // 切换到 ROI 工具自动切回 ROI 模式
  if (mode.value !== 'roi') mode.value = 'roi'
  // 切换工具时清空进行中的草稿
  draft.value = null
  polyPoints.value = []
  dragging.value = false
  draw()
}

/** 模式切换：清空对应模式的草稿，避免残留绘制 */
watch(mode, () => {
  draft.value = null
  polyPoints.value = []
  dragging.value = false
  dragStart.value = null
  distDraft.value = null
  angleDraft.value = []
  draw()
})

// ---------- 监听切片变化 ----------
watch(
  () => [props.caseId, props.sliceIndex, props.modality] as const,
  async ([cid, idx, mod]) => {
    if (!cid) return
    await ensureSlice(idx as number)
    if (gradcamOn.value && mod === 'MRI') {
      void ensureGradcam(idx as number)
    }
    draw()
  },
  { immediate: false }
)

watch(
  () => props.caseId,
  async (cid) => {
    if (!cid) return
    sliceCache.clear()
    gradcamCache.clear()
    sliceImg.value = null
    gradcamImg.value = null
    await loadAnnotations()
    await ensureSlice(props.sliceIndex)
    if (gradcamOn.value && props.modality === 'MRI') {
      void ensureGradcam(props.sliceIndex)
    }
    draw()
  },
  { immediate: true }
)

// ---------- 生命周期 ----------
onMounted(() => {
  // caseId 的 immediate watcher 已在 setup 阶段发起标注/切片请求；
  // 这里仅在 canvas DOM 就绪后补一帧绘制，避免与 watcher 重复请求（旧实现会各拉一遍）
  draw()
})

onBeforeUnmount(() => {
  sliceCache.clear()
  gradcamCache.clear()
})

// 暴露给父组件的刷新方法（外部切片或模态变化时调用）
defineExpose({
  refresh: loadAnnotations
})
</script>

<template>
  <div class="flex h-full gap-3">
    <!-- ==================== 左侧：影像 + ROI 画布 ==================== -->
    <div class="flex-1 min-w-0 flex flex-col gap-2">
      <!-- 工具栏 -->
      <div class="card-ad px-3 py-2 flex items-center gap-2 flex-wrap shrink-0">
        <!-- 模式切换：ROI / 距离 / 角度 -->
        <el-radio-group v-model="mode" size="small">
          <el-radio-button label="roi">ROI</el-radio-button>
          <el-radio-button label="distance">距离</el-radio-button>
          <el-radio-button label="angle">角度</el-radio-button>
        </el-radio-group>
        <el-divider direction="vertical" />
        <template v-if="mode === 'roi'">
          <el-tooltip content="球形 ROI：按下确定圆心，拖拽确定半径" placement="top">
            <el-button
              size="small"
              :type="activeTool === 'sphere' ? 'primary' : 'default'"
              @click="selectTool('sphere')"
            >
              球形 ROI
            </el-button>
          </el-tooltip>
          <el-tooltip content="矩形 ROI：按下拖出矩形" placement="top">
            <el-button
              size="small"
              :type="activeTool === 'rect' ? 'primary' : 'default'"
              @click="selectTool('rect')"
            >
              矩形 ROI
            </el-button>
          </el-tooltip>
          <el-tooltip content="多边形 ROI：多点点击，双击/右键完成" placement="top">
            <el-button
              size="small"
              :type="activeTool === 'polygon' ? 'primary' : 'default'"
              @click="selectTool('polygon')"
            >
              多边形 ROI
            </el-button>
          </el-tooltip>
          <el-divider direction="vertical" />
          <el-tooltip content="撤销当前草稿 / 最近一条标注" placement="top">
            <el-button size="small" :icon="'Back'" :disabled="listLoading" @click="undo">撤销</el-button>
          </el-tooltip>
          <el-tooltip content="清除当前进行中的绘制" placement="top">
            <el-button size="small" :icon="'Delete'" @click="clearDraft">清除</el-button>
          </el-tooltip>
          <el-divider direction="vertical" />
          <!-- 标签选择 -->
          <span class="text-[12px] text-hint">标签</span>
          <el-select v-model="labelChoice" size="small" style="width: 120px">
            <el-option v-for="lb in LABEL_OPTIONS" :key="lb" :label="lb" :value="lb" />
          </el-select>
          <el-input
            v-if="labelChoice === '自定义'"
            v-model="customLabel"
            size="small"
            placeholder="自定义标签名"
            style="width: 130px"
          />
        </template>
        <template v-else>
          <span class="text-[11.5px] text-hint">
            {{ mode === 'distance' ? '两点定线，自动换算 mm' : '三点定角，第二点为顶点' }}
            <span v-if="!hasScale" class="ml-1 text-[#FFB74D]">（无 pixelSpacing，仅 px）</span>
            <span v-else class="ml-1 text-[#7ECB8A]">（有标尺，已换算 mm）</span>
          </span>
          <el-divider direction="vertical" />
          <el-tooltip content="清空所有测量（不影响 ROI 标注）" placement="top">
            <el-button size="small" :icon="'Delete'" @click="clearMeasures">清除测量</el-button>
          </el-tooltip>
        </template>
        <div class="flex-1" />
        <el-tooltip content="导出该病例全部标注为 JSON（训练数据）" placement="top">
          <el-button size="small" :icon="'Download'" @click="exportAnnotations">导出</el-button>
        </el-tooltip>
      </div>

      <!-- 画布区 -->
      <div
        class="card-ad flex-1 min-h-0 flex items-center justify-center viewport-dark p-2 relative"
        v-loading="sliceLoading"
      >
        <div class="relative">
          <canvas
            ref="canvasEl"
            class="roi-canvas"
            :style="{ cursor: mode === 'roi' ? (activeTool ? 'crosshair' : 'default') : 'crosshair' }"
            @mousedown="onMouseDown"
            @mousemove="onMouseMove"
            @mouseup="onMouseUp"
            @mouseleave="onCanvasLeave"
            @dblclick="onDblClick"
            @contextmenu="onContextMenu"
          />
          <!-- 测量层 Canvas：与 ROI 层同尺寸，绝对定位浮于上层，pointer-events: none -->
          <canvas
            ref="measureLayerEl"
            class="measure-layer-canvas"
            aria-hidden="true"
          />
        </div>
        <!-- 角标：切片/模态/层号 -->
        <div class="absolute top-2 left-2 text-[11px] text-white/70 font-num leading-tight">
          {{ modality }} · L{{ sliceIndex + 1 }}
          <span v-if="mode === 'roi' && activeTool" class="ml-2 text-[#FFC857]">绘制中：{{ ROI_TYPE_LABEL[activeTool] }}</span>
          <span v-if="mode === 'roi' && activeTool === 'polygon' && polyPoints.length" class="ml-2 text-[#FFC857]">
            已点 {{ polyPoints.length }} 点（双击完成）
          </span>
          <span v-if="mode === 'distance'" class="ml-2 text-[#FFD54F]">
            距离模式 · {{ distDraft ? '已点 1/2' : '点击起点' }}
          </span>
          <span v-if="mode === 'angle'" class="ml-2 text-[#FFD54F]">
            角度模式 · 已点 {{ angleDraft.length }}/3
          </span>
        </div>
      </div>
    </div>

    <!-- ==================== 右侧：标注列表 + GradCAM 开关 ==================== -->
    <div class="w-[280px] shrink-0 flex flex-col gap-2">
      <!-- GradCAM 对比开关 -->
      <div class="card-ad px-3 py-2 shrink-0">
        <div class="text-[12px] text-hint mb-1.5">AI 可解释性对比</div>
        <div class="flex items-center justify-between">
          <span class="text-[13px] text-ink">GradCAM 热图叠加</span>
          <el-switch
            :model-value="gradcamOn"
            :disabled="modality !== 'MRI'"
            @update:model-value="onGradcamToggle"
          />
        </div>
        <div v-if="modality !== 'MRI'" class="mt-1 text-[11px] text-hint">
          仅 MRI 模态可用
        </div>
      </div>

      <!-- 标注列表 -->
      <div class="card-ad flex-1 min-h-0 flex flex-col">
        <div class="card-ad__header !h-10 !px-3">
          <span class="card-ad__title !text-[13px]">标注列表</span>
          <span class="text-[11px] text-hint font-num">{{ annotations.length }} 条</span>
        </div>
        <div class="flex-1 min-h-0 overflow-y-auto" v-loading="listLoading">
          <div v-if="annotations.length === 0" class="p-4 text-center text-[12px] text-hint">
            暂无标注<br />在左侧画布上使用工具开始绘制
          </div>
          <div
            v-for="a in annotations"
            :key="a.id"
            class="px-3 py-2 border-b border-line/60 hover:bg-page/60 transition-colors"
          >
            <div class="flex items-center gap-2">
              <el-tag size="small" :type="a.roiType === 'sphere' ? 'primary' : a.roiType === 'rect' ? 'success' : 'warning'">
                {{ ROI_TYPE_LABEL[a.roiType] }}
              </el-tag>
              <span class="text-[12.5px] text-ink font-medium truncate flex-1">
                {{ a.label || '未分类' }}
              </span>
              <el-icon
                :size="14"
                class="cursor-pointer text-hint hover:text-primary"
                :title="hiddenSet.has(a.id) ? '显示' : '隐藏'"
                @click="toggleVisibility(a.id)"
              >
                <View v-if="!hiddenSet.has(a.id)" />
                <Hide v-else />
              </el-icon>
            </div>
            <div class="mt-1 flex items-center gap-2 text-[11px] text-hint font-num">
              <span>L{{ a.sliceIndex + 1 }}</span>
              <span>· {{ a.modality }}</span>
              <span>· {{ (a.area * MM_PER_PX * MM_PER_PX).toFixed(1) }} mm²</span>
              <span v-if="a.user" class="truncate">· {{ a.user }}</span>
            </div>
            <div class="mt-1 flex items-center gap-2">
              <el-button size="small" text :icon="'Position'" @click="jumpToSlice(a.sliceIndex)">
                跳转切片
              </el-button>
              <el-button size="small" text type="danger" :icon="'Delete'" @click="removeAnnotation(a.id)">
                删除
              </el-button>
              <span class="flex-1 text-right text-[10px] text-hint truncate">{{ a.createdAt }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 画布展示尺寸：等比占满容器，最大 540×540 */
.roi-canvas {
  display: block;
  width: 100%;
  height: 100%;
  max-width: 540px;
  max-height: 540px;
  object-fit: contain;
  image-rendering: pixelated;
}
/* 测量层 Canvas：与 ROI 层完全重合并浮于上层，不接收鼠标事件 */
.measure-layer-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  max-width: 540px;
  max-height: 540px;
  pointer-events: none;
  image-rendering: pixelated;
}
</style>
