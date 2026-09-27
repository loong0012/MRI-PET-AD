/**
 * 三平面联动共享状态（方案 B：模块级单例 reactive，绕开父组件高频重渲染）
 * ------------------------------------------------------------------
 * 体数据轴约定（与 ViewerView 原有联动公式一致）：
 *   X = 左右 → 矢状位切片索引；Y = 前后 → 冠状位切片索引；Z = 上下 → 轴位切片索引
 * 各平面视口显示轴（u=影像横 col，v=影像纵 row，均为 0..SLICE_SIZE 像素）：
 *   axial    (u=X, v=Y)
 *   sagittal (u=Z, v=Y)
 *   coronal  (u=X, v=Z)
 *
 * 职责：
 * 1. committed 三切片状态 + 单击/拖拽换算（纯函数，可单测）
 * 2. hover 预览点：某视口悬停时，另两视口显示琥珀色预览十字线
 * 3. rAF 合并的重绘订阅（一帧最多一次，避免 mousemove 高频重绘）
 * 4. canvas 注册表：三平面合成截图用
 */
import { computed, reactive, watch, type Ref } from 'vue'
import { SLICE_SIZE } from '@/utils/imaging'

/** 三个标准平面（api/imaging 的 Orientation 同值） */
export type Axis3 = 'axial' | 'sagittal' | 'coronal'
export type ScreenAxis = 'u' | 'v'

/** 悬停点（影像空间像素坐标，来自哪个视口） */
export interface TriplanarHover {
  ori: Axis3
  u: number
  v: number
}

interface SyncState {
  enabled: boolean
  /** 真实体数据层数（三轴同分辨率） */
  count: number
  /** 已提交切片：axial=Z，sagittal=X，coronal=Y */
  slices: Record<Axis3, number>
  hover: TriplanarHover | null
}

const state = reactive<SyncState>({
  enabled: false,
  count: 128,
  slices: { axial: 64, sagittal: 64, coronal: 64 },
  hover: null
})

function clampV(v: number, total: number = state.count): number {
  if (total <= 0) return 0
  return Math.max(0, Math.min(total - 1, Math.round(v)))
}

/** 影像像素 0..SLICE_SIZE → 体素索引（floor + clamp，与 useVoxelFromPixel 同公式） */
function voxelFromPx(px: number): number {
  const total = state.count
  if (total <= 0 || SLICE_SIZE <= 0) return 0
  return clampV(Math.floor((px / SLICE_SIZE) * total), total)
}

/**
 * 单击/落点提交：源视口影像坐标 → 更新另两视口切片。
 * axial(u,v)→sag=u(X),cor=v(Y)；sagittal(u,v)→axial=u(Z),cor=v(Y)；
 * coronal(u,v)→sag=u(X),axial=v(Z)
 */
function commitFrom(ori: Axis3, px: number, py: number): void {
  const u = voxelFromPx(px)
  const v = voxelFromPx(py)
  if (ori === 'axial') {
    state.slices.sagittal = u
    state.slices.coronal = v
  } else if (ori === 'sagittal') {
    state.slices.axial = u
    state.slices.coronal = v
  } else {
    state.slices.sagittal = u
    state.slices.axial = v
  }
}

/**
 * 拖动十字线单轴翻层：在 ori 视口拖 u(竖线)/v(横线)，
 * 按该视口显示轴映射更新对应的另一个切片。
 */
function scrubAxis(ori: Axis3, axis: ScreenAxis, p: number): void {
  const vx = voxelFromPx(p)
  if (ori === 'axial') {
    if (axis === 'u') state.slices.sagittal = vx
    else state.slices.coronal = vx
  } else if (ori === 'sagittal') {
    if (axis === 'u') state.slices.axial = vx
    else state.slices.coronal = vx
  } else {
    if (axis === 'u') state.slices.sagittal = vx
    else state.slices.axial = vx
  }
}

/** 悬停点上报（影像空间坐标）；离开影像区时传 null */
function hoverAt(ori: Axis3, u: number, v: number): void {
  state.hover = { ori, u, v }
}
function clearHover(ori: Axis3): void {
  if (state.hover?.ori === ori) state.hover = null
}

/**
 * 已提交十字线（影像空间 0..1 比例）：每个视口的 col/row 显示「另两轴」当前切片。
 * axial col=X(sag) row=Y(cor)；sagittal col=Z(ax) row=Y(cor)；coronal col=X(sag) row=Z(ax)
 */
function crosshairFor(ori: Axis3): { col: number; row: number } {
  const total = state.count || 1
  const { axial, sagittal, coronal } = state.slices
  if (ori === 'axial') return { col: sagittal / total, row: coronal / total }
  if (ori === 'sagittal') return { col: axial / total, row: coronal / total }
  return { col: sagittal / total, row: axial / total }
}

/**
 * 预览十字线：其他视口 hover 时，本视口对应轴显示其悬停位置（0..1 比例），
 * 未被 hover 覆盖的轴沿用已提交位置。本视口自身 hover 时返回 null。
 */
function previewFor(ori: Axis3): { col: number; row: number } | null {
  const hv = state.hover
  if (!hv || hv.ori === ori) return null
  const committed = crosshairFor(ori)
  // hover 源视口各屏幕轴对应的体素维度
  const hoverDim = {
    axial: { u: 'X', v: 'Y' },
    sagittal: { u: 'Z', v: 'Y' },
    coronal: { u: 'X', v: 'Z' }
  }[hv.ori]
  // 目标视口 col/row 对应的体素维度
  const targetDim = {
    axial: { col: 'X', row: 'Y' },
    sagittal: { col: 'Z', row: 'Y' },
    coronal: { col: 'X', row: 'Z' }
  }[ori]
  const frac = (dim: string): number =>
    dim === hoverDim.u ? hv.u / SLICE_SIZE : dim === hoverDim.v ? hv.v / SLICE_SIZE : Number.NaN
  const colF = frac(targetDim.col)
  const rowF = frac(targetDim.row)
  return {
    col: Number.isNaN(colF) ? committed.col : colF,
    row: Number.isNaN(rowF) ? committed.row : rowF
  }
}

// ---------- rAF 合并的重绘订阅 ----------
type SubCb = () => void
const subscribers = new Set<SubCb>()
let rafPending = false
function notify(): void {
  if (rafPending) return
  rafPending = true
  requestAnimationFrame(() => {
    rafPending = false
    subscribers.forEach((cb) => cb())
  })
}
watch(
  () => [state.slices.axial, state.slices.sagittal, state.slices.coronal, state.hover],
  () => {
    if (state.enabled) notify()
  },
  { flush: 'post' }
)

/** 组件订阅三平面状态变化（slices/hover）；返回取消函数 */
function subscribe(cb: SubCb): () => void {
  subscribers.add(cb)
  return () => subscribers.delete(cb)
}

// ---------- 切片双向绑定（供 v-model:slice-idx） ----------
const sliceRefs: Record<Axis3, Ref<number>> = {
  axial: computed({
    get: () => state.slices.axial,
    set: (v) => (state.slices.axial = clampV(v))
  }),
  sagittal: computed({
    get: () => state.slices.sagittal,
    set: (v) => (state.slices.sagittal = clampV(v))
  }),
  coronal: computed({
    get: () => state.slices.coronal,
    set: (v) => (state.slices.coronal = clampV(v))
  })
}

/** 进入三平面：注册层数并定位中层；退出时清 hover / 订阅静默 */
function enter(count: number, recenter: boolean): void {
  state.count = count > 0 ? count : 128
  if (recenter) {
    const mid = Math.floor(state.count / 2)
    state.slices.axial = mid
    state.slices.sagittal = mid
    state.slices.coronal = mid
  } else {
    state.slices.axial = clampV(state.slices.axial)
    state.slices.sagittal = clampV(state.slices.sagittal)
    state.slices.coronal = clampV(state.slices.coronal)
  }
  state.hover = null
  state.enabled = true
}
function setCount(count: number): void {
  if (count <= 0 || count === state.count) return
  state.count = count
  const mid = Math.floor(count / 2)
  state.slices.axial = mid
  state.slices.sagittal = mid
  state.slices.coronal = mid
}
function exit(): void {
  state.enabled = false
  state.hover = null
}

// ---------- canvas 注册表（三平面合成截图） ----------
const canvasRegistry = new Map<Axis3, HTMLCanvasElement>()
function registerCanvas(ori: Axis3, el: HTMLCanvasElement | null): void {
  if (el) canvasRegistry.set(ori, el)
  else canvasRegistry.delete(ori)
}
function getCanvases(): Map<Axis3, HTMLCanvasElement> {
  return canvasRegistry
}

export function useTriplanarSync() {
  return {
    state,
    voxelFromPx,
    commitFrom,
    scrubAxis,
    hoverAt,
    clearHover,
    crosshairFor,
    previewFor,
    subscribe,
    sliceRefs,
    enter,
    setCount,
    exit,
    registerCanvas,
    getCanvases
  }
}
