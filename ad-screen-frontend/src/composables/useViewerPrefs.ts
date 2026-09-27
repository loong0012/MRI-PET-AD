/**
 * 阅片器全局偏好（模块级单例，跨视口/跨布局共享）
 * ------------------------------------------------------------------
 * 适配模式 fitMode：
 *   - contain 适应（默认）：等比缩放，长边留黑边，影像完整不裁切（医学阅片默认）
 *   - cover   填满：等比缩放铺满画布，超出部分裁切，无黑边
 *   - fill    拉伸：非等比缩放铺满（允许轻微变形）
 * 选择持久化到 localStorage，下次打开自动恢复；所有 MedicalViewport 直接读取，
 * 无需 props 透传，四分区/三平面/分析详情页全局统一。
 */
import { ref, watch } from 'vue'

export type FitMode = 'contain' | 'cover' | 'fill'

const STORAGE_KEY = 'adscreen_viewer_fit_mode'
const VALID_MODES: readonly FitMode[] = ['contain', 'cover', 'fill']

function readInitial(): FitMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v && (VALID_MODES as readonly string[]).includes(v)) return v as FitMode
  } catch {
    /* localStorage 不可用（隐私模式等）时静默回退默认值 */
  }
  return 'contain'
}

/** 模块级单例状态：所有组件实例共享同一份 */
const fitMode = ref<FitMode>(readInitial())

watch(fitMode, (v) => {
  try {
    localStorage.setItem(STORAGE_KEY, v)
  } catch {
    /* 持久化失败不影响内存态功能 */
  }
})

export function useViewerPrefs() {
  return { fitMode }
}
