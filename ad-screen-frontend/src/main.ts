import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import '@/styles/index.css'
import '@/styles/element-overrides.css'
import { initTheme } from '@/utils/theme'

initTheme()

const app = createApp(App)

// 全局注册 Pinia / 路由 / Element Plus（中文语言包，全量注册保证内网离线可用）
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn, size: 'default' })

// 全量注册 Element Plus 图标组件（模板中以 <el-icon><IconName /></el-icon> 使用）
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// ---------- 全局兜底：组件内异常与未捕获的 Promise 拒绝 ----------
// 只记录日志、不打断页面；请求层已统一 toast 的错误（__handled）不重复打扰用户
app.config.errorHandler = (err, _instance, info) => {
  if ((err as { __handled?: boolean } | null)?.__handled) return
  console.error('[全局异常]', info, err)
}

window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason as { __handled?: boolean } | undefined
  if (reason?.__handled) {
    // 已知业务/网络错误且已提示：阻止控制台未处理拒绝噪音
    event.preventDefault()
    return
  }
  console.warn('[未处理的 Promise 拒绝]', reason)
})

app.mount('#app')
