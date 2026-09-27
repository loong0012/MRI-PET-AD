/**
 * 真实 NIfTI 影像切片 API
 * ------------------------------------------------------------------
 * 后端将病例关联的真实 MRI / PET NIfTI 体数据渲染为轴位切片 PNG
 * （8-bit 单通道，256×256）。前端解码后复用 MedicalViewport 既有的
 * 窗宽窗位 / Jet 伪彩 / 融合叠加渲染管线。
 * Mock 模式 / 无真实影像时 meta.available=false，调用方回退合成影像。
 */
import { httpGet } from '@/utils/request'
import { getToken } from '@/utils/auth'
import { getMediaToken, ensureMediaToken } from '@/utils/mediaToken'
import { applyWWWC, jetColor, SLICE_SIZE } from '@/utils/imaging'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'
const BASE = import.meta.env.VITE_API_BASE || '/api'

/**
 * 为无法自定义请求头的资源 URL（<img src> 加载切片/Grad-CAM）拼接短期媒体票据。
 * 票据由布局层登录后预取并自动续期；Mock 模式无后端鉴权，原样返回。
 */
function withToken(url: string): string {
  if (useMock) return url
  const token = getMediaToken()
  if (!token) return url
  return `${url}&token=${encodeURIComponent(token)}`
}

/** 影像平面方向 */
export type Orientation = 'axial' | 'sagittal' | 'coronal'

/**
 * DICOM 元数据（12 标准字段，camelCase）
 * 后端从真实 DICOM 抽取；历史病例 / 合成影像返回 null
 */
export interface DicomMeta {
  patientName: string | null
  patientId: string | null
  studyDate: string | null
  studyTime: string | null
  seriesDescription: string | null
  modality: string | null
  manufacturer: string | null
  fieldStrength: string | null
  windowCenter: number | null
  windowWidth: number | null
  sliceThickness: number | null
  pixelSpacing: [number, number] | null
}

/** 真实影像元信息 */
export interface ImagingMeta {
  available: boolean
  mri: boolean
  pet: boolean
  sliceCount: number
  sliceCountAxial: number
  sliceCountSagittal: number
  sliceCountCoronal: number
  sliceSize: number
  orientation: string
  orientations: Orientation[]
  /** DICOM 元数据（真实影像 12 字段；历史病例 / 合成影像为 null） */
  dicomMeta?: DicomMeta | null
}

/** 轴位中层索引（128³ 体数据默认 64） */
export const DEFAULT_MID_SLICE = 64

/** 获取病例真实影像元信息 */
export function apiGetImagingMeta(caseId: string): Promise<ImagingMeta> {
  if (useMock) {
    return Promise.resolve({
      available: false,
      mri: false,
      pet: false,
      sliceCount: 256,
      sliceCountAxial: 256,
      sliceCountSagittal: 256,
      sliceCountCoronal: 256,
      sliceSize: SLICE_SIZE,
      orientation: 'axial',
      orientations: ['axial', 'sagittal', 'coronal'],
      dicomMeta: null
    })
  }
  return httpGet<ImagingMeta>(`/case/${caseId}/imaging/meta`)
}

/**
 * 拼接单张切片 PNG 的 URL（供 <img>/canvas 直接加载，凭证经 query token 传递）
 * 可选 wc/ww（窗位/窗宽）覆盖后端默认显示窗；仅当二者均为有限数时拼接
 */
export function apiSliceUrl(
  caseId: string,
  modality: 'MRI' | 'PET',
  idx: number,
  orientation: Orientation = 'axial',
  wc?: number,
  ww?: number
): string {
  let url = `${BASE}/case/${caseId}/imaging/slice?modality=${modality}&idx=${idx}&orientation=${orientation}`
  if (wc !== undefined && ww !== undefined && Number.isFinite(wc) && Number.isFinite(ww)) {
    url += `&wc=${wc}&ww=${ww}`
  }
  return withToken(url)
}

/** Grad-CAM 代谢偏差热力图 URL（RGBA PNG，可叠加在 MRI 上，凭证经 query token 传递） */
export function apiGradcamUrl(
  caseId: string,
  idx: number,
  orientation: Orientation = 'axial'
): string {
  return withToken(`${BASE}/case/${caseId}/imaging/gradcam?idx=${idx}&orientation=${orientation}`)
}

/** 加载 Grad-CAM 热力图为 HTMLImageElement */
export async function loadGradcam(caseId: string, idx: number, orientation: Orientation = 'axial'): Promise<HTMLImageElement> {
  if (!useMock) await ensureMediaToken()
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(`Grad-CAM 加载失败: #${idx}`))
    img.src = apiGradcamUrl(caseId, idx, orientation)
  })
}

/** 加载切片 PNG 并解码为 0-255 灰度数组（256×256） */
async function loadSliceGray(
  caseId: string,
  modality: 'MRI' | 'PET',
  idx: number,
  orientation: Orientation = 'axial',
  wc?: number,
  ww?: number
): Promise<Uint8Array> {
  if (!useMock) await ensureMediaToken()
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = SLICE_SIZE
      canvas.height = SLICE_SIZE
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('canvas 不可用'))
        return
      }
      ctx.drawImage(img, 0, 0)
      const data = ctx.getImageData(0, 0, SLICE_SIZE, SLICE_SIZE).data
      const out = new Uint8Array(SLICE_SIZE * SLICE_SIZE)
      for (let i = 0; i < out.length; i++) out[i] = data[i * 4] // 单通道 PNG，R=G=B
      resolve(out)
    }
    img.onerror = () => reject(new Error(`切片加载失败: ${modality}#${idx}`))
    img.src = apiSliceUrl(caseId, modality, idx, orientation, wc, ww)
  })
}

/**
 * 生成真实影像关键层面缩略图 dataURL（供分析详情 / 报告页复用）
 * 渲染风格与合成缩略图一致：MRI 灰度 / PET Jet 伪彩 / 融合叠加。
 * 失败或无该模态时返回 null，调用方回退到 makeThumbnail。
 */
export async function apiGetRealThumbnail(
  caseId: string,
  modality: 'MRI' | 'PET' | 'FUSION',
  idx: number = DEFAULT_MID_SLICE,
  orientation: Orientation = 'axial'
): Promise<string | null> {
  try {
    let gray: Uint8Array | null = null
    let pet: Uint8Array | null = null
    if (modality === 'MRI' || modality === 'FUSION') gray = await loadSliceGray(caseId, 'MRI', idx, orientation)
    if (modality === 'PET' || modality === 'FUSION') pet = await loadSliceGray(caseId, 'PET', idx, orientation)

    const canvas = document.createElement('canvas')
    canvas.width = SLICE_SIZE
    canvas.height = SLICE_SIZE
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    const img = ctx.createImageData(SLICE_SIZE, SLICE_SIZE)
    const n = SLICE_SIZE * SLICE_SIZE
    // 真实数据后端已归一化到视觉窗，前端用全窗显示
    const ww = 255
    const wc = 128
    const zero = new Uint8Array(n)
    const g = gray ?? zero
    const p = pet ?? zero
    for (let i = 0; i < n; i++) {
      let r = 0
      let gg = 0
      let b = 0
      if (modality === 'MRI') {
        const v = applyWWWC(g[i], ww, wc)
        r = gg = b = v
      } else if (modality === 'PET') {
        const t = applyWWWC(p[i], ww, wc) / 255
        ;[r, gg, b] = jetColor(t)
      } else {
        const v = applyWWWC(g[i], ww, wc)
        const t = applyWWWC(p[i], ww, wc) / 255
        const [pr, pg, pb] = jetColor(t)
        const alpha = Math.max(0, t - 0.35) * 1.5
        r = v * (1 - alpha) + pr * alpha
        gg = v * (1 - alpha) + pg * alpha
        b = v * (1 - alpha) + pb * alpha
      }
      img.data[i * 4] = r
      img.data[i * 4 + 1] = gg
      img.data[i * 4 + 2] = b
      img.data[i * 4 + 3] = 255
    }
    ctx.putImageData(img, 0, 0)
    return canvas.toDataURL('image/jpeg', 0.92)
  } catch {
    return null
  }
}

export { loadSliceGray }

/** 3D 体数据（下采样 uint8，xyz 三维） */
export interface VolumeData3D {
  data: Uint8Array
  dims: [number, number, number]
}

/** 加载 3D 体绘制数据（原始字节流 + X-Vol-Dims 头） */
export async function loadVolume3D(
  caseId: string,
  modality: 'MRI' | 'PET',
  size = 64
): Promise<VolumeData3D> {
  // fetch 支持自定义请求头，体数据鉴权走 Authorization Bearer（token 不进 URL）
  const token = useMock ? '' : getToken()
  const resp = await fetch(
    `${BASE}/case/${caseId}/imaging/volume?modality=${modality}&size=${size}`,
    token ? { headers: { Authorization: `Bearer ${token}` } } : undefined
  )
  if (!resp.ok) throw new Error(`体数据加载失败: ${resp.status}`)
  const buf = await resp.arrayBuffer()
  const dimsHeader = resp.headers.get('X-Vol-Dims') ?? `${size},${size},${size}`
  const parts = dimsHeader.split(',').map((s) => parseInt(s.trim(), 10))
  const dims: [number, number, number] = [parts[0] || size, parts[1] || size, parts[2] || size]
  return { data: new Uint8Array(buf), dims }
}
