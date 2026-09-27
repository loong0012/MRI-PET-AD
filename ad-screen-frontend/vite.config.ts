import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

/**
 * Vite 构建配置
 * - @ 别名指向 src 目录
 * - 开发环境将 /api 代理到本地后端服务（默认 8001 端口，可按需修改）
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 2400,
    rollupOptions: {
      output: {
        // 大依赖单独分包，利于医院内网环境下首屏缓存与长效缓存命中
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('echarts') || id.includes('zrender')) return 'echarts'
            if (id.includes('element-plus') || id.includes('@element-plus')) return 'element-plus'
            if (id.includes('three') || id.includes('@types/three')) return 'three'
            if (id.includes('vue') || id.includes('pinia') || id.includes('vue-router')) return 'vue-vendor'
            if (id.includes('axios')) return 'axios'
            return 'vendor'
          }
        }
      }
    }
  }
})
