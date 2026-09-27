/**
 * 医学影像合成引擎（演示数据源）
 * ------------------------------------------------------------------
 * 说明：真实部署时由后端下发 DICOM 序列（可接入 cornerstone3D），
 * 本模块以参数化算法合成 256×256 脑部 MRI/PET 切片数据，
 * 提供与真实影像一致的窗宽窗位/ROI/融合渲染管线，
 * 保证阅片交互链路完整可用。
 */
import { mulberry32, strSeed } from './format'

export const SLICE_SIZE = 256

/** 单切片体数据 */
export interface SliceData {
  width: number
  height: number
  gray: Uint8Array // MRI 灰度（T1 加权语义：白质亮/灰质中/脑脊液暗）
  pet: Uint8Array // PET 代谢活性（0-255）
}

/** ROI 椭圆 */
export interface RoiEllipse {
  x: number
  y: number
  rx: number
  ry: number
}

/** ROI 脑区形状 */
export interface RoiShape {
  name: string
  color: string
  ellipses: RoiEllipse[]
}

/** 生成 2D 值噪声（种子稳定） */
function makeNoise(seed: number): (x: number, y: number) => number {
  const rand = mulberry32(seed)
  const perm = new Uint8Array(512)
  const base = new Uint8Array(256)
  for (let i = 0; i < 256; i++) base[i] = i
  for (let i = 255; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    const t = base[i]
    base[i] = base[j]
    base[j] = t
  }
  for (let i = 0; i < 512; i++) perm[i] = base[i & 255]

  const grad = (hash: number, x: number, y: number): number => {
    switch (hash & 3) {
      case 0:
        return x + y
      case 1:
        return -x + y
      case 2:
        return x - y
      default:
        return -x - y
    }
  }
  const fade = (t: number): number => t * t * t * (t * (t * 6 - 15) + 10)

  return function (x: number, y: number): number {
    const xi = Math.floor(x) & 255
    const yi = Math.floor(y) & 255
    const xf = x - Math.floor(x)
    const yf = y - Math.floor(y)
    const u = fade(xf)
    const v = fade(yf)
    const aa = perm[perm[xi] + yi]
    const ab = perm[perm[xi] + yi + 1]
    const ba = perm[perm[xi + 1] + yi]
    const bb = perm[perm[xi + 1] + yi + 1]
    const x1 = grad(aa, xf, yf) * (1 - u) + grad(ba, xf - 1, yf) * u
    const x2 = grad(ab, xf, yf - 1) * (1 - u) + grad(bb, xf - 1, yf - 1) * u
    return (x1 * (1 - v) + x2 * v + 1) / 2 // 0-1
  }
}

/** 多倍频噪声 */
function fbm(noise: (x: number, y: number) => number, x: number, y: number): number {
  return noise(x, y) * 0.55 + noise(x * 2.1, y * 2.1) * 0.28 + noise(x * 4.3, y * 4.3) * 0.17
}

/** 点是否在椭圆内 */
function inEllipse(x: number, y: number, cx: number, cy: number, rx: number, ry: number): boolean {
  const dx = (x - cx) / rx
  const dy = (y - cy) / ry
  return dx * dx + dy * dy <= 1
}

/**
 * 合成指定切片的 MRI + PET 体数据
 * @param sliceIdx 当前切片号（0 起）
 * @param sliceCount 序列总切片数
 * @param seed 病例种子（决定个体形态/萎缩程度）
 */
export function generateSliceData(sliceIdx: number, sliceCount: number, seed: number): SliceData {
  const W = SLICE_SIZE
  const H = SLICE_SIZE
  const gray = new Uint8Array(W * H)
  const pet = new Uint8Array(W * H)

  const noise = makeNoise(seed + 7717)
  const rand = mulberry32(seed + sliceIdx * 131)

  // 归一化切片位置：两端（颅顶/颅底）脑组织截面变小
  const z = Math.min(0.96, Math.max(0.04, (sliceIdx + 1) / (sliceCount + 1)))
  const zF = Math.sin(Math.PI * z) // 0-1 截面缩放
  const cx = W / 2 + (rand() - 0.5) * 4
  const cy = H / 2 + (rand() - 0.5) * 4

  // 个体萎缩程度（0-1，由种子决定；影响脑沟增宽/脑室扩大/代谢减低）
  const atrophy = 0.25 + mulberry32(seed)() * 0.55

  // 头部椭圆（头皮+颅骨）
  const headRx = 26 + 70 * zF
  const headRy = 22 + 66 * zF
  // 脑实质椭圆
  const brainRx = headRx - 9 - atrophy * 5
  const brainRy = headRy - 8 - atrophy * 4
  // 侧脑室（中层切片最大）
  const venScale = Math.max(0, zF - 0.35) * 1.6
  const venRx = (10 + atrophy * 10) * (0.5 + venScale)
  const venRy = (16 + atrophy * 14) * (0.5 + venScale)

  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x
      // 颅外背景（含微噪声）
      if (!inEllipse(x, y, cx, cy, headRx, headRy)) {
        gray[i] = Math.random() < 0.02 ? 6 : 2
        pet[i] = Math.random() < 0.03 ? 5 : 2
        continue
      }
      // 颅骨环（T1 低信号中的板障亮层 → 简化为亮环）
      if (!inEllipse(x, y, cx, cy, brainRx + 3, brainRy + 3)) {
        gray[i] = 168 + Math.floor(rand() * 30)
        pet[i] = 8
        continue
      }
      // 脑脊液边缘间隙
      if (!inEllipse(x, y, cx, cy, brainRx, brainRy)) {
        gray[i] = 46 + Math.floor(rand() * 18)
        pet[i] = 6
        continue
      }

      // ---------- 脑实质：脑沟脑回纹理 ----------
      // 以到中心距离构造皮层折叠的角向条纹 + 噪声扰动
      const ang = Math.atan2(y - cy, x - cx)
      const dist = Math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
      const foldWave =
        Math.sin(ang * 14 + fbm(noise, x / 21, y / 21) * 7.5) * 0.5 +
        Math.sin(ang * 23 + fbm(noise, x / 11, y / 11) * 5) * 0.28
      const isGrayMatter = foldWave + (fbm(noise, x / 15, y / 15) - 0.5) * 0.9 > (1 - dist / brainRx) * 0.35

      // 萎缩 → 脑沟（暗缝）更宽更明显
      const sulcus = foldWave > 0.62 && dist > brainRx * 0.42
      if (sulcus && atrophy > 0.3) {
        gray[i] = 40 + Math.floor(rand() * 16)
        pet[i] = 30 + Math.floor(rand() * 10)
        continue
      }

      if (isGrayMatter) {
        gray[i] = 116 + Math.floor(fbm(noise, x / 9, y / 9) * 26)
        pet[i] = 168 + Math.floor(fbm(noise, x / 13, y / 13) * 40)
      } else {
        gray[i] = 158 + Math.floor(fbm(noise, x / 7, y / 7) * 22)
        pet[i] = 118 + Math.floor(fbm(noise, x / 10, y / 10) * 26)
      }

      // 侧脑室（T1 低信号 / PET 无代谢）
      if (venScale > 0.05 && (inEllipse(x, y, cx - venRx * 1.15, cy - venRy * 0.1, venRx, venRy) ||
        inEllipse(x, y, cx + venRx * 1.15, cy - venRy * 0.1, venRx, venRy))) {
        gray[i] = 22 + Math.floor(rand() * 10)
        pet[i] = 12
      }

      // 正中矢状裂
      if (Math.abs(x - cx) < 2.2 && dist < brainRy * 0.92) {
        gray[i] = Math.min(gray[i], 48)
        pet[i] = Math.min(pet[i], 22)
      }
    }
  }

  // ---------- PET 代谢异常建模 ----------
  // 影像种子的高 4 位决定代谢减低偏侧与程度（模拟颞叶/海马低代谢）
  const hypoSide = mulberry32(seed)() > 0.5 ? 1 : -1
  const hypoPower = 0.35 + mulberry32(seed + 99)() * 0.5
  const roiShapes = getRoiShapes(sliceIdx, sliceCount)
  for (const shape of roiShapes) {
    if (shape.name.indexOf('海马') < 0 && shape.name.indexOf('颞叶') < 0) continue
    for (const e of shape.ellipses) {
      // 单侧减低：仅处理与 hypoSide 同侧的椭圆（x 相对中心判断）
      const side = e.x < SLICE_SIZE / 2 ? -1 : 1
      const factor = side === hypoSide ? 1 - hypoPower * 0.9 : 1 - hypoPower * 0.25
      for (let y = Math.max(0, Math.floor(e.y - e.ry)); y < Math.min(SLICE_SIZE, e.y + e.ry); y++) {
        for (let x = Math.max(0, Math.floor(e.x - e.rx)); x < Math.min(SLICE_SIZE, e.x + e.rx); x++) {
          if (!inEllipse(x, y, e.x, e.y, e.rx, e.ry)) continue
          const i = y * SLICE_SIZE + x
          pet[i] = Math.floor(pet[i] * factor)
        }
      }
    }
  }

  return { width: W, height: H, gray, pet }
}

/**
 * 获取当前切片的关键脑区 ROI 形状（海马/颞叶等，随切片位置变化）
 * 仅在中部切片显示（与真实解剖一致）
 */
export function getRoiShapes(sliceIdx: number, sliceCount: number): RoiShape[] {
  const z = (sliceIdx + 1) / (sliceCount + 1)
  if (z < 0.28 || z > 0.78) return []
  const cx = SLICE_SIZE / 2
  const cy = SLICE_SIZE / 2
  const t = Math.min(1, (z - 0.28) / 0.2) // 进入中部的过渡
  const s = 0.75 + t * 0.25

  return [
    {
      name: '左侧海马',
      color: '#4FC3F7',
      ellipses: [{ x: cx - 40 * s, y: cy + 8 * s, rx: 15 * s, ry: 9 * s }]
    },
    {
      name: '右侧海马',
      color: '#4FC3F7',
      ellipses: [{ x: cx + 40 * s, y: cy + 8 * s, rx: 15 * s, ry: 9 * s }]
    },
    {
      name: '左侧颞叶皮层',
      color: '#FFB74D',
      ellipses: [
        { x: cx - 66 * s, y: cy + 20 * s, rx: 16 * s, ry: 26 * s }
      ]
    },
    {
      name: '右侧颞叶皮层',
      color: '#FFB74D',
      ellipses: [
        { x: cx + 66 * s, y: cy + 20 * s, rx: 16 * s, ry: 26 * s }
      ]
    }
  ]
}

/** Jet 伪彩映射（PET 代谢展示，医疗后处理标准色表） */
export function jetColor(t: number): [number, number, number] {
  const v = Math.min(1, Math.max(0, t))
  const r = Math.min(255, Math.floor(255 * Math.min(4 * v - 1.5, 1)))
  const g = Math.min(255, Math.floor(255 * Math.min(4 * v - 0.5, 4 * (1 - v) - 0.5, 1) + 0) ) // 中段峰值
  const b = Math.min(255, Math.floor(255 * Math.min(1.5 - 4 * v + 1, 1)))
  return [Math.max(0, r), Math.max(0, g), Math.max(0, b)]
}

/** 窗宽窗位映射（0-255 → 0-255 显示值） */
export function applyWWWC(value: number, ww: number, wc: number): number {
  const lower = wc - ww / 2
  const out = ((value - lower) / Math.max(1, ww)) * 255
  return Math.min(255, Math.max(0, Math.round(out)))
}

/** 生成病例稳定性种子 */
export function caseSeed(caseId: string): number {
  return strSeed(caseId)
}

/**
 * MIP 最大密度投影重建
 * ------------------------------------------------------------------
 * 沿 Z 轴（切片方向）对每个像素取最大值，得到一张轴位投影图。
 * 临床意义：
 * - PET MIP：突出全脑代谢活跃区，便于整体评估低代谢范围与对称性
 * - MRI MIP：突出高信号结构（血管/钙化），常用于血管成像
 * 在 AD 诊断中，PET MIP 是评估颞顶叶低代谢范围与对称性的标准后处理。
 *
 * 性能：在合成数据下，256 切片 × 256×256 = 16.7M 次 max 运算，
 * V8 JIT 下约 80-150ms，可接受；真实 DICOM 体数据应迁至 Web Worker。
 *
 * @param seed 病例种子（决定个体形态）
 * @param sliceCount 投影层厚（默认 256，全层投影）
 * @param modality 'PET' | 'MRI'（默认 PET，因为 PET MIP 临床最常用）
 * @returns SliceData 结构，width/height=256，gray 或 pet 字段填充投影结果
 */
export function buildMip(
  seed: number,
  sliceCount = 256,
  modality: 'PET' | 'MRI' = 'PET'
): SliceData {
  const W = SLICE_SIZE
  const H = SLICE_SIZE
  // MIP 投影结果缓存（max 池化）
  const projection = new Uint8Array(W * H)
  // 另一模态占位（MIP 视口通常只展示投影模态）
  const placeholder = new Uint8Array(W * H)

  for (let z = 0; z < sliceCount; z++) {
    const slice = generateSliceData(z, sliceCount, seed)
    const src = modality === 'PET' ? slice.pet : slice.gray
    for (let i = 0; i < W * H; i++) {
      if (src[i] > projection[i]) {
        projection[i] = src[i]
      }
    }
  }

  return {
    width: W,
    height: H,
    gray: modality === 'MRI' ? projection : placeholder,
    pet: modality === 'PET' ? projection : placeholder,
  }
}

/**
 * 生成影像缩略图 dataURL（AI 详情页关键截图 / 报告影像区复用）
 * @param modality MRI | PET | FUSION
 */
export function makeThumbnail(
  modality: 'MRI' | 'PET' | 'FUSION',
  seed: number,
  sliceIdx = 128,
  sliceCount = 256
): string {
  const data = generateSliceData(sliceIdx, sliceCount, seed)
  const canvas = document.createElement('canvas')
  canvas.width = SLICE_SIZE
  canvas.height = SLICE_SIZE
  const ctx = canvas.getContext('2d')
  if (!ctx) return ''
  const img = ctx.createImageData(SLICE_SIZE, SLICE_SIZE)
  const ww = 200
  const wc = 110
  for (let i = 0; i < SLICE_SIZE * SLICE_SIZE; i++) {
    let r = 0
    let g = 0
    let b = 0
    if (modality === 'MRI') {
      const v = applyWWWC(data.gray[i], ww, wc)
      r = g = b = v
    } else if (modality === 'PET') {
      const t = applyWWWC(data.pet[i], 220, 120) / 255
      ;[r, g, b] = jetColor(t)
    } else {
      const v = applyWWWC(data.gray[i], ww, wc)
      const t = applyWWWC(data.pet[i], 220, 120) / 255
      const [pr, pg, pb] = jetColor(t)
      const alpha = Math.max(0, t - 0.35) * 1.5 // 高代谢区叠加
      r = v * (1 - alpha) + pr * alpha
      g = v * (1 - alpha) + pg * alpha
      b = v * (1 - alpha) + pb * alpha
    }
    img.data[i * 4] = r
    img.data[i * 4 + 1] = g
    img.data[i * 4 + 2] = b
    img.data[i * 4 + 3] = 255
  }
  ctx.putImageData(img, 0, 0)
  return canvas.toDataURL('image/jpeg', 0.92)
}

// =====================================================================
// 异常脑区热力图叠加（基于 AI 分析的 abnormalRegions）
// ---------------------------------------------------------------------
// 将分析报告中的脑区名称映射到轴位切片的近似解剖位置，
// 按萎缩程度 / z-score 渲染彩色病灶叠加层，辅助医生定位异常区域。
// 注意：这是基于标准脑图谱的近似坐标，非个体配准分割结果。
// =====================================================================

/** 异常脑区条目（与 types/analysis.d.ts AbnormalRegion 对齐） */
export interface HeatmapRegion {
  region: string
  side: '左侧' | '右侧' | '双侧'
  atrophy: '轻度萎缩' | '中度萎缩' | '重度萎缩'
  metabolism: number
  zScore: number
}

/** 单个热力图病灶（256 影像空间坐标） */
export interface HeatmapBlob {
  x: number
  y: number
  rx: number
  ry: number
  color: string
  alpha: number
  label: string
}

/**
 * 脑区 → 轴位中层近似坐标（归一化 0-1，影像空间：x=左右, y=前后，上=前）
 * 坐标基于标准 MNI 脑图谱在 128³ 体数据中的大致位置。
 */
const REGION_AXIAL_POS: Record<string, { x: number; y: number; rx: number; ry: number }> = {
  海马: { x: 0.43, y: 0.58, rx: 0.06, ry: 0.045 },
  内嗅皮层: { x: 0.46, y: 0.54, rx: 0.05, ry: 0.04 },
  颞叶皮层: { x: 0.27, y: 0.56, rx: 0.08, ry: 0.06 },
  后扣带回: { x: 0.5, y: 0.38, rx: 0.06, ry: 0.05 },
  楔前叶: { x: 0.5, y: 0.32, rx: 0.07, ry: 0.055 },
  顶叶皮层: { x: 0.31, y: 0.31, rx: 0.08, ry: 0.06 },
  额叶皮层: { x: 0.5, y: 0.2, rx: 0.1, ry: 0.07 },
  枕叶皮层: { x: 0.5, y: 0.78, rx: 0.08, ry: 0.06 }
}

/** 萎缩程度 → 颜色与基础透明度 */
const ATROPHY_STYLE: Record<string, { color: string; base: number }> = {
  轻度萎缩: { color: '#FFD54F', base: 0.35 },
  中度萎缩: { color: '#FF8A65', base: 0.5 },
  重度萎缩: { color: '#EF5350', base: 0.65 }
}

/**
 * 生成当前轴位切片的异常脑区热力图病灶列表。
 * @param regions 异常脑区列表
 * @param sliceIdx 当前层号
 * @param sliceCount 总层数
 */
export function getHeatmapBlobs(
  regions: HeatmapRegion[],
  sliceIdx: number,
  sliceCount: number
): HeatmapBlob[] {
  if (!regions || regions.length === 0) return []
  const blobs: HeatmapBlob[] = []
  const mid = sliceCount / 2
  // 仅在中层 ±30% 范围内显示（对应有这些脑区的轴位层面）
  const rel = Math.abs(sliceIdx - mid) / sliceCount
  const zFade = Math.max(0, 1 - rel / 0.3)
  if (zFade <= 0) return []

  for (const r of regions) {
    const pos = REGION_AXIAL_POS[r.region]
    if (!pos) continue
    const style = ATROPHY_STYLE[r.atrophy] ?? ATROPHY_STYLE['轻度萎缩']
    // z-score 越大异常越显著，透明度越高
    const zBoost = Math.min(0.3, Math.max(0, r.zScore) / 10)
    const alpha = Math.min(0.85, (style.base + zBoost) * zFade)

    // side: 右侧=患者右=影像左(x小)；左侧=患者左=影像右(x大)
    const sides: Array<'L' | 'R'> =
      r.side === '双侧' ? ['L', 'R'] : r.side === '左侧' ? ['L'] : ['R']

    for (const s of sides) {
      const x = (s === 'R' ? pos.x : 1 - pos.x) * SLICE_SIZE
      const y = pos.y * SLICE_SIZE
      blobs.push({
        x,
        y,
        rx: pos.rx * SLICE_SIZE,
        ry: pos.ry * SLICE_SIZE,
        color: style.color,
        alpha,
        label: `${s === 'L' ? '左' : '右'}${r.region}`
      })
    }
  }
  return blobs
}
