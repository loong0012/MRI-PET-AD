---
name: "med-dashboard-ui-polish"
description: "Polish medical/admin Vue3 + Element Plus + Tailwind + ECharts dashboards: add a restrained motion layer, route/modal transitions, card hover/entrance animations, brand gradient tokens, and login split-screen branding without changing the existing design system or logic. Invoke when the user asks to 美化界面/优化UI/界面不够精致/丰富界面对本项目或同类医疗信息系统做视觉精修。"
---

# 医疗信息系统 UI 精致化精修（Vue3 + Element Plus + Tailwind + ECharts）

适用于 AD-Screen 及同类"低饱和医疗蓝灰 + 白色卡片"风格的企业/医疗信息系统。核心原则：**只加质感与动效，不改设计系统色值、不改业务逻辑**。

## 决策优先级
1. 不破坏既有设计令牌（CSS 变量 / tailwind.config 色板）、组件交互、路由结构。
2. 动效克制（医疗专业感）：短时、缓动自然、无弹跳夸张效果。
3. 所有动效必须支持 `prefers-reduced-motion` 降级。
4. 最小一致性改动：能用工具类就不写散点 CSS。

## 标准落地清单

### 1. 全局动效层（写入全局 CSS，如 src/styles/index.css）
- keyframes：`ad-fade-up`（opacity 0→1 + translateY 14px→0）、`ad-fade-in`、`ad-scale-in`(scale .96→1)。
- 工具类：`.anim-fade-up/.anim-fade-in/.anim-scale-in`，用 `animation-fill-mode: forwards` + `animation-delay: var(--anim-delay, 0ms)` 实现错落入场；元素上 `style="--anim-delay: 70ms"`。
- `.card-interactive`：`transition: transform .22s cubic-bezier(.22,.61,.36,1), box-shadow, border-color`；`:hover { transform: translateY(-3px); box-shadow 加深; border-color: 主色35%透明 }`。
- `.brand-gradient-text` / `.brand-gradient-bg`：基于既有主色 `--ad-primary` 派生的 120°/135° 渐变。
- 路由过渡 `.page-fade-enter/leave`（进入淡入+上移 10px，离开淡出-6px）；弹窗 `.dialog-fade`（进入 translateY 18px scale .98）。
- 末尾必须有 `@media (prefers-reduced-motion: reduce)` 把所有 animation/transition 时长压到 0.01ms。

### 2. 主框架（MainLayout）
- `<router-view>` 包 `<transition name="page-fade" mode="out-in"><component :is="Component" :key="route.fullPath"/></transition>`。
- 侧边栏激活项：主色线性渐变背景 + 左侧 3px 浅蓝指示条（`.el-menu-item.is-active::before`，绝对定位）+ 柔和主色投影；非激活 hover 用深色 hover 底。
- 顶栏 Logo：`brand-gradient-bg` + 主色发光投影；顶栏加 `shadow-[0_1px_8px_rgba(16,32,48,.05)]` 分层。

### 3. 仪表盘卡片
- 统计卡片网格：每张卡加 `card-interactive anim-fade-up`，`:style="{'--anim-delay': i*70+'ms'}"` 错落；`relative overflow-hidden`，右上角放一个 `-right-6 -top-8 w-24 h-24 rounded-full opacity-[0.08]` 的同卡片色装饰光斑。
- 图标容器用该指标的浅色底 + 同色图标 + `shadow-sm`。
- 各内容区块按行设置递增延迟（280/380/480ms）整行淡入。

### 4. 登录页分屏
- 左 `w-[46%] hidden lg:flex` 深色品牌面板：主色深色渐变底（`--ad-sidebar`→主色，155°）、`.brand-grid` 网格纹理用 radial-gradient mask 渐隐、两个 `.brand-glow` 大半径 blur 光斑、品牌标题+产品价值文案+3 个能力点（图标方块+标题+描述）+底部模型/技术栈信息。
- 右侧表单区保留全部原逻辑；Logo 用 `brand-gradient-bg`，标题中产品名套 `brand-gradient-text`，登录按钮可加 `tracking-[0.3em]`。
- 窄屏（<lg）自动隐藏品牌面板，右侧标识仍显示品牌 Logo。

## 验证
- `npm run build`（vue-tsc 严格，零 any、零类型错误）。
- 浏览器核对：卡片错落入场、悬停上浮、侧边栏激活指示条、路由淡入、登录分屏、控制台无报错。
- 注意：退出登录时旧页面被中断的 net::ERR_ABORTED 影像请求属正常现象，非缺陷。

## 本项目约定（AD-Screen）
- 色板：primary `#2F6DA3` / sidebar `#1C2B3A` / 风险四色 low `#2E9E6B` mci `#D99A2B` early `#D96B2B` late `#C94F4F`。
- 卡片：`.card-ad`（bg-white rounded-card shadow-card border-line），标题 `.card-ad__title`（自带主色竖条装饰）。
- 数字用 `.font-num`（IBM Plex Mono, tabular-nums）。
- npm registry 用 `--registry=https://registry.npmmirror.com`；后端无 --reload，改后端需手动重启 uvicorn。
- Element Plus 图标全局注册，直接 `<el-icon><Name/></el-icon>`，但需先确认图标在 `@element-plus/icons-vue` 中存在（无 `MagicStick`，用 `Sunny` 等）。
