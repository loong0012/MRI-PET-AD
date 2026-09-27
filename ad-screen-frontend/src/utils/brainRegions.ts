/**
 * AD（阿尔茨海默病）重点病症脑区知识库
 * ------------------------------------------------------------------
 * 供阅片视口 2D 标注与 3D 体绘制标注共用：
 * - 中文名 / 英文名 / 标注色
 * - 功能简述与 AD 关联（面向医生与患者的示教说明）
 * - 轴位 / 冠状 / 矢状三个平面的标注锚点（归一化 0-1，影像空间）
 * - 3D 体数据锚点（归一化 i,j,k ∈ 0-1，NIfTI canonical 体空间：i=左→右, j=后→前(j 大为前), k=下→上）
 * 注意：所有坐标为标准 MNI 脑图谱近似位置（示教用途），非个体配准分割结果。
 */

export type Orientation = 'axial' | 'sagittal' | 'coronal'

/** 二维平面锚点（归一化影像空间坐标） */
export interface RegionAnchor2D {
  x: number
  y: number
}

/** 重点病症脑区定义 */
export interface BrainRegion {
  key: string
  /** 中文名（标注显示） */
  name: string
  /** 英文名 */
  en: string
  /** 标注色 */
  color: string
  /** 功能简述（患者友好） */
  fn: string
  /** 与 AD 的关联说明 */
  ad: string
  /** 双侧结构（左右各一个锚点，true 时 3D 生成两枚标记） */
  bilateral: boolean
  /** 三平面锚点：axial 影像空间 y 向后增大；coronal/sagittal y 向下增大 */
  anchors: Record<Orientation, RegionAnchor2D[]>
  /**
   * 三平面层位可见范围（切片序号占比 0-1，区间外不显示标注）：
   * axial 沿上下（0=颅顶）、coronal 沿前后（0=额极）、sagittal 沿左右（0.5=中线）
   */
  range: Record<Orientation, [number, number]>
  /** 3D 体空间锚点（i,j,k ∈ 0-1） */
  vol: Array<{ i: number; j: number; k: number }>
}

/**
 * 脑区在当前平面/层位的标注可见度（0-1）：区间内为 1，两端 18% 渐隐。
 * 让标注只出现在解剖学上可见该结构的层面，避免"全层满屏标注"造成的误导与拥挤。
 */
export function regionSliceFade(
  region: BrainRegion,
  o: Orientation,
  sliceIdx: number,
  sliceCount: number
): number {
  const [a, b] = region.range[o]
  if (b <= a) return 1
  const f = sliceCount <= 1 ? 0.5 : (sliceIdx + 1) / (sliceCount + 1)
  const ramp = (b - a) * 0.18
  if (f <= a - ramp || f >= b + ramp) return 0
  const lo = ramp > 0 ? Math.min(1, (f - (a - ramp)) / ramp) : 1
  const hi = ramp > 0 ? Math.min(1, (b + ramp - f) / ramp) : 1
  return Math.max(0, Math.min(lo, hi))
}

/**
 * AD 重点病症脑区（按病程受累顺序排列）
 * 锚点基于标准脑图谱近似，标注时叠加"图谱近似"说明。
 */
export const BRAIN_REGIONS: BrainRegion[] = [
  {
    key: 'hippocampus',
    name: '海马',
    en: 'Hippocampus',
    color: '#4FC3F7',
    fn: '记忆的形成与存储中枢，学习新事物的关键结构',
    ad: 'AD 最早、最显著萎缩的脑区，体积缩小与情景记忆减退直接相关',
    bilateral: true,
    anchors: {
      axial: [
        { x: 0.43, y: 0.58 },
        { x: 0.57, y: 0.58 }
      ],
      coronal: [
        { x: 0.42, y: 0.68 },
        { x: 0.58, y: 0.68 }
      ],
      sagittal: [{ x: 0.42, y: 0.66 }]
    },
    range: {
      axial: [0.42, 0.68],
      coronal: [0.5, 0.78],
      sagittal: [0.3, 0.5]
    },
    vol: [
      { i: 0.36, j: 0.45, k: 0.46 },
      { i: 0.64, j: 0.45, k: 0.46 }
    ]
  },
  {
    key: 'entorhinal',
    name: '内嗅皮层',
    en: 'Entorhinal Cortex',
    color: '#B39DDB',
    fn: '记忆信息的"中转站"，连接海马与新皮层',
    ad: '病理改变早于海马，是 AD 最早期病变位点之一',
    bilateral: true,
    anchors: {
      axial: [
        { x: 0.46, y: 0.54 },
        { x: 0.54, y: 0.54 }
      ],
      coronal: [
        { x: 0.44, y: 0.74 },
        { x: 0.56, y: 0.74 }
      ],
      sagittal: [{ x: 0.44, y: 0.7 }]
    },
    range: {
      axial: [0.46, 0.66],
      coronal: [0.55, 0.8],
      sagittal: [0.32, 0.52]
    },
    vol: [
      { i: 0.4, j: 0.6, k: 0.46 },
      { i: 0.6, j: 0.6, k: 0.46 }
    ]
  },
  {
    key: 'posterior-cingulate',
    name: '后扣带回',
    en: 'Posterior Cingulate',
    color: '#FFB74D',
    fn: '默认脑网络中枢，参与情景记忆提取与自我参照思维',
    ad: 'PET 代谢减低的经典早期指标，临床上静息态 fMRI 关注的核心区域',
    bilateral: false,
    anchors: {
      axial: [{ x: 0.5, y: 0.38 }],
      coronal: [{ x: 0.5, y: 0.44 }],
      sagittal: [{ x: 0.5, y: 0.4 }]
    },
    range: {
      axial: [0.32, 0.56],
      coronal: [0.3, 0.52],
      sagittal: [0.42, 0.58]
    },
    vol: [{ i: 0.5, j: 0.3, k: 0.6 }]
  },
  {
    key: 'precuneus',
    name: '楔前叶',
    en: 'Precuneus',
    color: '#FFD54F',
    fn: '参与情景记忆、注意与意识加工，是脑网络的重要枢纽',
    ad: '与后扣带回同属默认网络，AD 早期代谢显著减低',
    bilateral: false,
    anchors: {
      axial: [{ x: 0.5, y: 0.32 }],
      coronal: [{ x: 0.5, y: 0.3 }],
      sagittal: [{ x: 0.5, y: 0.3 }]
    },
    range: {
      axial: [0.18, 0.46],
      coronal: [0.24, 0.5],
      sagittal: [0.4, 0.6]
    },
    vol: [{ i: 0.5, j: 0.26, k: 0.68 }]
  },
  {
    key: 'temporoparietal',
    name: '颞顶联合皮层',
    en: 'Temporoparietal Association',
    color: '#FF8A65',
    fn: '语言理解与语义加工的高级联合区',
    ad: '中晚期受累明显，与命名、找词困难等语言症状相关',
    bilateral: true,
    anchors: {
      axial: [
        { x: 0.27, y: 0.45 },
        { x: 0.73, y: 0.45 }
      ],
      coronal: [
        { x: 0.28, y: 0.42 },
        { x: 0.72, y: 0.42 }
      ],
      sagittal: [{ x: 0.32, y: 0.44 }]
    },
    range: {
      axial: [0.3, 0.62],
      coronal: [0.34, 0.62],
      sagittal: [0.08, 0.42]
    },
    vol: [
      { i: 0.24, j: 0.38, k: 0.54 },
      { i: 0.76, j: 0.38, k: 0.54 }
    ]
  },
  {
    key: 'prefrontal',
    name: '前额叶皮层',
    en: 'Prefrontal Cortex',
    color: '#81C784',
    fn: '执行功能与工作记忆中枢，负责计划、判断与自控',
    ad: '病程中晚期受累，与执行功能下降、性格改变相关',
    bilateral: false,
    anchors: {
      axial: [{ x: 0.5, y: 0.2 }],
      coronal: [{ x: 0.5, y: 0.56 }],
      sagittal: [{ x: 0.5, y: 0.62 }]
    },
    range: {
      axial: [0.12, 0.5],
      coronal: [0.02, 0.32],
      sagittal: [0.4, 0.6]
    },
    vol: [{ i: 0.5, j: 0.8, k: 0.56 }]
  },
  {
    key: 'occipital',
    name: '枕叶皮层',
    en: 'Occipital Cortex',
    color: '#90A4AE',
    fn: '视觉加工皮层，一般不受 AD 早期累及',
    ad: 'AD 中通常相对保留，可作为对照参考区（路易体痴呆则早期受累）',
    bilateral: false,
    anchors: {
      axial: [{ x: 0.5, y: 0.78 }],
      coronal: [{ x: 0.5, y: 0.24 }],
      sagittal: [{ x: 0.5, y: 0.24 }]
    },
    range: {
      axial: [0.3, 0.6],
      coronal: [0.68, 0.95],
      sagittal: [0.4, 0.6]
    },
    vol: [{ i: 0.5, j: 0.18, k: 0.5 }]
  }
]

/** 按平面获取标注锚点（返回数组，双侧结构含左右两点） */
export function getRegionAnchors(o: Orientation): Array<{ region: BrainRegion; x: number; y: number }> {
  const out: Array<{ region: BrainRegion; x: number; y: number }> = []
  for (const r of BRAIN_REGIONS) {
    for (const a of r.anchors[o]) out.push({ region: r, x: a.x, y: a.y })
  }
  return out
}

/** 3D 标注位置 → three.js 空间（立方体 [-0.5,0.5]³）
 * canonical 体空间 i=左→右、j=后→前、k=下→上；
 * 渲染世界系 x=i, y=k, z=-j（右手系，患者前方朝 z-，放射学定侧） */
export function getRegion3DPositions(): Array<{ region: BrainRegion; pos: [number, number, number] }> {
  return BRAIN_REGIONS.flatMap((r) =>
    r.vol.map((v) => ({
      region: r,
      pos: [v.i - 0.5, v.k - 0.5, 0.5 - v.j] as [number, number, number]
    }))
  )
}

/** 归一化坐标 → 256 影像空间像素坐标 */
export function toSlicePx(x: number, y: number, size: number): { x: number; y: number } {
  return { x: x * size, y: y * size }
}
