import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

/**
 * 前端单元测试配置
 * ------------------------------------------------------------------
 * 背景：项目此前前端测试数为 0（无 vitest、无 @vue/test-utils），
 * 120 个 .vue/.ts 文件完全依赖人工点击验证。先建立最小可用的测试通道，
 * 从纯函数（utils）起步，后续再扩展到组件与 E2E。
 *
 * 环境用 happy-dom 而非 jsdom：更轻、启动快，且本项目测试对象以纯函数为主。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    include: ['src/**/*.spec.ts'],
    // 默认缓存落在系统临时目录，受限环境下会因 EPERM 中断整个测试运行
    cacheDir: 'node_modules/.vitest',
    coverage: {
      reporter: ['text'],
      include: ['src/utils/**']
    }
  }
})
