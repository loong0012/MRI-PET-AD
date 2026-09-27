/**
 * 影像 ROI 标注类型定义
 * 用于阅片器中球形/矩形/多边形 ROI 的绘制、测量与导出
 */

/** ROI 类型 */
export type RoiType = 'sphere' | 'rect' | 'polygon'

/** 球形 ROI 坐标（圆心 + 半径，像素空间） */
export interface SphereCoords {
  cx: number
  cy: number
  r: number
}

/** 矩形 ROI 坐标（左上 + 宽高，像素空间） */
export interface RectCoords {
  x: number
  y: number
  w: number
  h: number
}

/** 多边形 ROI 点集（像素空间） */
export interface PolygonCoords {
  points: Array<{ x: number; y: number }>
}

/** ROI 坐标联合（运行期为 dict，按 roiType 解析） */
export type RoiCoords = SphereCoords | RectCoords | PolygonCoords | Record<string, unknown>

/** 单条标注记录（与后端 ORM 一一对应） */
export interface AnnotationItem {
  id: number
  caseId: string
  user: string
  sliceIndex: number
  modality: string
  roiType: RoiType
  coords: RoiCoords
  label: string
  area: number
  notes: string
  createdAt: string
}

/** 新增标注请求体 */
export interface AnnotationCreateBody {
  sliceIndex: number
  modality: string
  roiType: RoiType
  coords: Record<string, unknown>
  label: string
  area: number
  notes: string
}

/** 病例标注导出包（训练数据导出格式） */
export interface AnnotationExport {
  caseId: string
  annotations: AnnotationItem[]
}
