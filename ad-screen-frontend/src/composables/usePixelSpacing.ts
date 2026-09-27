/**
 * 像素间距换算 composable
 * ------------------------------------------------------------------
 * 从 DicomMeta.pixelSpacing（[row_spacing, col_spacing] 单位 mm）
 * 派生"图像像素距离 → 物理毫米距离"的工具方法。
 *
 * pixelSpacing 缺失（历史病例 / 合成影像）→ hasScale=false，toMm 返回 null，
 * 由调用方决定回退显示"长度（px）· 无标尺信息"标签。
 */
import { computed, type Ref } from 'vue'
import type { DicomMeta } from '@/api/imaging'

export function usePixelSpacing(meta: Ref<DicomMeta | null>) {
  /** 当前像素间距（mm/px，[row, col]）；meta 为空或字段为 null 时为 null */
  const pixelSpacing = computed<[number, number] | null>(() => meta.value?.pixelSpacing ?? null)
  /** 是否有可用标尺信息 */
  const hasScale = computed<boolean>(() => pixelSpacing.value !== null)
  /** 像素距离 → 物理毫米距离；无标尺时返回 null */
  const toMm = (pixelDistance: number): number | null => {
    const ps = pixelSpacing.value
    return ps ? pixelDistance * ps[0] : null
  }
  return { pixelSpacing, hasScale, toMm }
}
