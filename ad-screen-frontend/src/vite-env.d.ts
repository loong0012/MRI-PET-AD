/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

interface ImportMetaEnv {
  /** 后端接口基础路径 */
  readonly VITE_API_BASE: string
  /** 是否启用内置 Mock（true: 演示模式） */
  readonly VITE_USE_MOCK: string
  /** 应用版本号 */
  readonly VITE_APP_VERSION: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
