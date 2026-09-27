/**
 * 像素 → 体素 换算 composable（三平面十字线联动）
 * ------------------------------------------------------------------
 * 影像空间（SLICE_SIZE 像素边长）→ 体数据轴索引（0..sliceCount-1）。
 * 越界由本工具做 clamp；线性映射后向下取整。
 *
 * 参数支持普通数值或响应式来源（Ref / getter）：
 * sliceCount 在真实影像元数据到达后会从合成默认值（256）切换为真实层数
 * （如 128），因此调用方必须传 getter（如 () => viewSliceCount.value），
 * 避免在 setup 早期以过期层数构造闭包导致联动层号整体偏移。
 *
 * 用法：
 *   const { toVoxel } = useVoxelFromPixel(256, () => viewSliceCount.value)
 *   const voxelX = toVoxel(128)  // 128 层时 → 64
 */
import { toValue, type MaybeRefOrGetter } from 'vue'

export function useVoxelFromPixel(
  canvasSize: MaybeRefOrGetter<number>,
  sliceCount: MaybeRefOrGetter<number>
) {
  /** 像素坐标 → 体素索引（向下取整，越界 clamp；每次调用读取最新层数） */
  const toVoxel = (px: number): number => {
    const size = toValue(canvasSize)
    const total = toValue(sliceCount)
    if (size <= 0 || total <= 0) return 0
    const v = Math.floor((px / size) * total)
    if (v < 0) return 0
    if (v >= total) return total - 1
    return v
  }
  return { toVoxel }
}
