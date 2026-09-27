import type { Config } from 'tailwindcss'

/**
 * Tailwind 主题扩展 —— 医疗低饱和度蓝灰色系
 * 颜色值通过 CSS 变量映射，支持浅色/深色双主题（html.dark 切换变量）
 */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  darkMode: 'class',
  corePlugins: {
    preflight: true
  },
  theme: {
    extend: {
      colors: {
        // 表面色（卡片/面板背景）—— 浅色 #fff，深色 #1e2937
        white: 'var(--ad-surface)',
        surface: 'var(--ad-surface)',
        // 主色：低饱和医疗蓝
        primary: {
          DEFAULT: 'var(--ad-primary)',
          dark: 'var(--ad-primary-dark)',
          light: 'var(--ad-primary-light)'
        },
        // 正文墨色（蓝灰）
        ink: {
          DEFAULT: 'var(--ad-ink)',
          secondary: 'var(--ad-ink-2)',
          muted: 'var(--ad-ink-3)'
        },
        // 页面背景 / 边框
        page: 'var(--ad-bg)',
        line: 'var(--ad-border)',
        // 侧边栏深蓝灰
        sidebar: 'var(--ad-sidebar)',
        sidebarHover: 'var(--ad-sidebar-hover)',
        // 四级风险色
        risk: {
          low: 'var(--ad-risk-low)',
          mci: 'var(--ad-risk-mci)',
          early: 'var(--ad-risk-early)',
          late: 'var(--ad-risk-late)'
        }
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'Consolas', 'monospace']
      },
      boxShadow: {
        card: 'var(--ad-shadow-card)',
        pop: '0 8px 28px rgba(0,0,0,.24)'
      },
      borderRadius: {
        card: '8px'
      }
    }
  },
  plugins: []
} satisfies Config
