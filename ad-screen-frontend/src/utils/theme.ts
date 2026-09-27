/**
 * 主题切换工具（浅色 / 深色）
 * - 持久化到 localStorage（key: ad_theme）
 * - 通过给 <html> 添加/移除 dark class 切换
 * - Element Plus 暗色变量由 element-plus/theme-chalk/dark/css-vars.css 提供
 */
export type ThemeMode = 'light' | 'dark'

const STORAGE_KEY = 'ad_theme'

export function getStoredTheme(): ThemeMode {
  // 隐私模式等场景 localStorage 读取可能抛错，异常时按无存储值处理
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'light' || v === 'dark') return v
  } catch {
    /* 读取异常：继续走系统偏好兜底 */
  }
  // 首次跟随系统偏好
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function applyTheme(mode: ThemeMode): void {
  const root = document.documentElement
  if (mode === 'dark') root.classList.add('dark')
  else root.classList.remove('dark')
  // 持久化失败（配额/隐私模式）静默跳过：dark class 已切换，不影响当次主题
  try {
    localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    /* 忽略持久化异常 */
  }
}

export function initTheme(): void {
  applyTheme(getStoredTheme())
}
