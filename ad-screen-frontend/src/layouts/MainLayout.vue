<script setup lang="ts">
/**
 * 主布局：顶部导航栏 + 左侧固定侧边菜单 + 主内容区
 * ------------------------------------------------------------------
 * 1. 顶栏：系统标识、当前页面标题（面包屑）、当前登录用户与角色、退出登录
 * 2. 侧边栏：深蓝灰医疗工作站配色，菜单项按角色权限过滤（与路由守卫一致）
 * 3. 主内容区：固定视口内滚动，保证阅片/表格页布局稳定
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { applyTheme, getStoredTheme, type ThemeMode } from '@/utils/theme'
import { ensureMediaToken } from '@/utils/mediaToken'
import {
  apiGetNotifications, apiGetUnreadCount, apiMarkRead, apiMarkAllRead
} from '@/api/notification'
import { apiSearchCases } from '@/api/case'
import ErrorBoundary from '@/components/ErrorBoundary.vue'
import type { NotificationItem } from '@/types/notification'
import type { SearchCaseItem } from '@/types/case'
import type { RoleKey } from '@/types/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// ---------- 主题切换 ----------
const themeMode = ref<ThemeMode>(getStoredTheme())
function toggleTheme(): void {
  themeMode.value = themeMode.value === 'dark' ? 'light' : 'dark'
  applyTheme(themeMode.value)
}

// ---------- 通知中心（顶栏铃铛，全角色可用） ----------
const notifyPopoverVisible = ref(false)
const notifications = ref<NotificationItem[]>([])
const unreadCount = ref(0)
const unreadHasFollowUp = ref(false)
const onlyUnread = ref(false)
const notifyLoading = ref(false)

/** 通知类型 → 图标/配色（CSS 变量适配深浅色） */
const TYPE_META: Record<string, { icon: string; color: string; bg: string }> = {
  followup: { icon: 'Calendar', color: 'var(--ad-risk-late)', bg: 'var(--ad-notify-danger-bg)' },
  review: { icon: 'DocumentChecked', color: 'var(--ad-primary)', bg: 'var(--ad-notify-info-bg)' },
  announce: { icon: 'BellFilled', color: 'var(--ad-risk-mci)', bg: 'var(--ad-notify-warn-bg)' },
  system: { icon: 'InfoFilled', color: 'var(--ad-ink-3)', bg: 'var(--ad-notify-neutral-bg)' }
}

/** 上一次未读数：用于检测"新增"通知并轻提醒（null=首次拉取不提醒） */
const lastUnread = ref<number | null>(null)

async function refreshUnreadBadge(): Promise<void> {
  try {
    const s = await apiGetUnreadCount()
    // 未读数增加且通知面板未打开时，弹一次轻提醒（审核结果/随访/公告可被及时感知）
    if (
      lastUnread.value !== null && s.count > lastUnread.value &&
      !notifyPopoverVisible.value && document.visibilityState === 'visible'
    ) {
      ElMessage({ message: `您有 ${s.count} 条未读通知，点击顶栏铃铛查看`, type: 'info', duration: 3000 })
    }
    lastUnread.value = s.count
    unreadCount.value = s.count
    unreadHasFollowUp.value = s.hasFollowUp
  } catch {
    // 静默：顶栏徽标不阻断页面
  }
}

async function refreshList(): Promise<void> {
  notifyLoading.value = true
  try {
    notifications.value = await apiGetNotifications(onlyUnread.value, 20)
  } catch {
    notifications.value = []
  } finally {
    notifyLoading.value = false
  }
}

function onPopoverVisibleChange(v: boolean): void {
  if (v) void refreshList()
}

/** 点击通知：未读则先标记已读，再跳转关联页面 */
async function onItemClick(n: NotificationItem): Promise<void> {
  if (!n.isRead) {
    try {
      await apiMarkRead(n.id)
      n.isRead = true
      unreadCount.value = Math.max(0, unreadCount.value - 1)
      if (n.type === 'followup') refreshUnreadBadge()
    } catch { /* 静默 */ }
  }
  if (n.link) {
    notifyPopoverVisible.value = false
    void router.push(n.link)
  } else {
    // 无跳转目标（如公告类）：刷新列表，使已读项从未读视图移除，避免 popover 卡在旧列表
    void refreshList()
  }
}

/** 全部已读 */
async function onMarkAllRead(): Promise<void> {
  try {
    await apiMarkAllRead()
    ElMessage.success('已全部标记已读')
    await Promise.all([refreshUnreadBadge(), refreshList()])
  } catch {
    ElMessage.error('操作失败，请稍后重试')
  }
}

function switchOnlyUnread(v: boolean): void {
  if (onlyUnread.value === v) return
  onlyUnread.value = v
  void refreshList()
}

// ---------- 通知自动刷新：45s 轮询 + 页面重新可见时立即拉取 ----------
const NOTIFY_POLL_MS = 45000
let notifyTimer: ReturnType<typeof setInterval> | null = null

// ---------- 短期媒体票据：登录后预取，8 分钟自动续期（票据有效期 10 分钟） ----------
// 影像切片 <img>/SSE 只能以 URL query 带凭证，必须保证同步拼 URL 时缓存已就绪
const MEDIA_TOKEN_REFRESH_MS = 8 * 60 * 1000
let mediaTimer: ReturnType<typeof setInterval> | null = null

/** 从其他标签页/最小化切回本页时立即刷新（轮询在隐藏期间自动暂停） */
function onVisibilityChange(): void {
  if (document.visibilityState === 'visible') {
    void refreshUnreadBadge()
    // 票据可能在挂起期间过期，切回时立即续一张
    void ensureMediaToken()
  }
}

onMounted(() => {
  void refreshUnreadBadge()
  void ensureMediaToken()
  notifyTimer = setInterval(() => {
    // 后台标签页不请求，省资源并避免无意义并发
    if (document.visibilityState === 'visible') void refreshUnreadBadge()
  }, NOTIFY_POLL_MS)
  mediaTimer = setInterval(() => {
    if (document.visibilityState === 'visible') void ensureMediaToken()
  }, MEDIA_TOKEN_REFRESH_MS)
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  if (notifyTimer !== null) clearInterval(notifyTimer)
  if (mediaTimer !== null) clearInterval(mediaTimer)
  if (searchTimer !== null) clearTimeout(searchTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})

// 路由切换回主业务页时刷新未读数（如处理后返回）
watch(
  () => route.path,
  (p) => {
    if (p === '/dashboard' || p === '/followup' || p === '/cases' || p === '/analysis') refreshUnreadBadge()
  }
)

// ---------- 全局快捷搜索（顶栏，全角色） ----------
const searchVisible = ref(false)
const searchKeyword = ref('')
const searchResults = ref<SearchCaseItem[]>([])
const searchLoading = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null

const RISK_LABEL: Record<string, string> = {
  low: '低风险', mci: '轻度认知障碍', 'ad-early': 'AD 早期', 'ad-late': 'AD 中晚期'
}

async function fetchSearch(): Promise<void> {
  const kw = searchKeyword.value.trim()
  if (!kw) {
    searchResults.value = []
    return
  }
  searchLoading.value = true
  try {
    searchResults.value = await apiSearchCases(kw, 8)
  } catch {
    searchResults.value = []
  } finally {
    searchLoading.value = false
  }
}

/** 输入防抖（300ms） */
function onSearchInput(): void {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => void fetchSearch(), 300)
}

/** 回车：若只有一条结果直接跳详情，否则跳病例库带筛选 */
function onSearchEnter(): void {
  if (searchResults.value.length === 1) {
    goSearchCase(searchResults.value[0], 'detail')
  } else if (searchKeyword.value.trim()) {
    searchVisible.value = false
    void router.push({ path: '/cases', query: { keyword: searchKeyword.value.trim() } })
  }
}

function goSearchCase(item: SearchCaseItem, target: 'detail' | 'viewer' | 'analysis'): void {
  searchVisible.value = false
  if (target === 'detail') {
    void router.push({ path: '/cases', query: { keyword: item.id } })
  } else {
    void router.push(target === 'viewer' ? `/viewer/${item.id}` : `/analysis/${item.id}`)
  }
}

/** 侧边菜单配置（roles 为空 = 全角色可见） */
interface MenuItem {
  key: string
  path: string
  title: string
  icon: string
  roles?: RoleKey[]
}

const MENUS: MenuItem[] = [
  { key: 'dashboard', path: '/dashboard', title: '工作台仪表盘', icon: 'Odometer' },
  { key: 'cases', path: '/cases', title: '病例库管理', icon: 'Notebook' },
  {
    key: 'patient',
    path: '/patient',
    title: '患者档案',
    icon: 'UserFilled',
    roles: ['radiologist', 'neurologist', 'admin']
  },
  {
    key: 'followup',
    path: '/followup',
    title: '随访管理',
    icon: 'Calendar',
    roles: ['radiologist', 'neurologist', 'admin']
  },
  {
    key: 'warning',
    path: '/warning',
    title: '高危预警中心',
    icon: 'Warning',
    roles: ['radiologist', 'neurologist', 'admin']
  },
  {
    key: 'quality',
    path: '/quality',
    title: '数据质控中心',
    icon: 'CircleCheck',
    roles: ['radiologist', 'neurologist', 'admin']
  },
  {
    key: 'cdss',
    path: '/cdss',
    title: '临床决策支持',
    icon: 'Connection',
    roles: ['radiologist', 'neurologist', 'admin']
  },
  {
    key: 'model',
    path: '/model',
    title: '模型科研配置',
    icon: 'DataAnalysis',
    roles: ['researcher', 'admin']
  },
  {
    key: 'analytics',
    path: '/analytics',
    title: '科研统计分析',
    icon: 'TrendCharts',
    roles: ['researcher', 'admin']
  },
  {
    key: 'model-monitor',
    path: '/model-monitor',
    title: '模型性能监控',
    icon: 'Monitor',
    roles: ['researcher', 'admin']
  },
  {
    key: 'review',
    path: '/review',
    title: '报告审核工作流',
    icon: 'DocumentChecked',
    roles: ['radiologist', 'neurologist', 'admin']
  },
  {
    key: 'active-learning',
    path: '/active-learning',
    title: '主动学习队列',
    icon: 'Aim',
    roles: ['researcher', 'admin']
  },
  { key: 'system', path: '/system', title: '系统权限管理', icon: 'Setting', roles: ['admin'] },
  { key: 'case-log', path: '/case-log', title: '病例操作审计', icon: 'Tickets', roles: ['admin'] },
  { key: 'dictionary', path: '/dictionary', title: '数据字典中心', icon: 'Reading' },
  { key: 'knowledge', path: '/knowledge', title: '科普知识库', icon: 'Notebook' },
]

/** 按当前角色过滤后的可见菜单 */
const visibleMenus = computed<MenuItem[]>(() =>
  MENUS.filter((m) => userStore.hasRole(m.roles))
)

/** 当前激活菜单（详情页归并到所属模块高亮） */
const activeMenu = computed<string>(() => {
  if (route.path.startsWith('/viewer') || route.path.startsWith('/analysis') || route.path.startsWith('/report')) {
    return '/cases'
  }
  if (route.path.startsWith('/patient/')) {
    return '/patient'
  }
  return route.path
})

/** 当前页面标题（顶栏展示） */
const pageTitle = computed<string>(() => (route.meta.title as string) ?? '')

/** 退出登录（二次确认，医疗系统防误触） */
async function onLogout(): Promise<void> {
  try {
    await ElMessageBox.confirm('确定要退出当前登录吗？退出后需重新登录。', '退出确认', {
      confirmButtonText: '退出登录',
      cancelButtonText: '取消',
      type: 'warning',
      confirmButtonClass: 'el-button--danger'
    })
  } catch {
    return // 用户取消
  }
  await userStore.logout()
  ElMessage.success('已安全退出')
  router.replace('/login')
}
</script>

<template>
  <div class="h-screen flex flex-col overflow-hidden bg-page">
    <!-- ==================== 顶部导航栏 ==================== -->
    <header class="h-14 shrink-0 bg-white border-b border-line flex items-center px-5 z-20 shadow-[0_1px_8px_rgba(16,32,48,0.05)]">
      <!-- 系统标识 -->
      <div class="flex items-center gap-2.5 w-56 shrink-0">
        <div class="w-8 h-8 rounded-lg brand-gradient-bg flex items-center justify-center text-white shadow-[0_3px_10px_rgba(47,109,163,0.35)]">
          <svg width="18" height="18" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="16" cy="16" r="13" stroke="currentColor" stroke-width="1.4" opacity="0.35" />
            <path d="M16 6.5 C12 6.5 8.5 10 8.5 15 C8.5 19.5 11 23 15 24.5 C14 22.5 13.5 20.5 14 18.5 C12.8 17.3 12.5 15.5 13 14 C12.5 12.3 13.5 10.8 15.2 10.3 C15.2 9 15.5 7.7 16 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M16 6.5 C20 6.5 23.5 10 23.5 15 C23.5 19.5 21 23 17 24.5 C18 22.5 18.5 20.5 18 18.5 C19.2 17.3 19.5 15.5 19 14 C19.5 12.3 18.5 10.8 16.8 10.3 C16.8 9 16.5 7.7 16 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M16 7.5 L16 24" stroke="currentColor" stroke-width="1.2" opacity="0.45" stroke-linecap="round" />
            <circle cx="16" cy="16" r="1.8" fill="currentColor" />
            <circle cx="3.5" cy="16" r="1.1" fill="currentColor" opacity="0.5" />
            <circle cx="28.5" cy="16" r="1.1" fill="currentColor" opacity="0.5" />
          </svg>
        </div>
        <div class="leading-tight">
          <div class="text-[15px] font-semibold text-ink tracking-[0.16em]">脑影明衰</div>
          <div class="text-[10.5px] text-hint tracking-wide">阿尔茨海默病一体化 MRI/PET 脑成像智能诊断系统</div>
        </div>
      </div>

      <!-- 面包屑（当前业务页面位置） -->
      <el-breadcrumb separator="/" class="shrink-0">
        <el-breadcrumb-item>神经影像筛查中心</el-breadcrumb-item>
        <el-breadcrumb-item>{{ pageTitle }}</el-breadcrumb-item>
      </el-breadcrumb>

      <!-- 全局快捷搜索 -->
      <div class="flex-1 flex justify-center px-6">
        <el-popover
          v-model:visible="searchVisible"
          placement="bottom"
          :width="420"
          trigger="focus"
          popper-class="search-popper"
        >
          <template #reference>
            <el-input
              v-model="searchKeyword"
              placeholder="搜索病例 ID / 患者姓名…"
              clearable
              class="!w-72"
              :prefix-icon="'Search'"
              @input="onSearchInput"
              @keyup.enter="onSearchEnter"
              @focus="searchVisible = true"
            />
          </template>
          <div v-loading="searchLoading">
            <div v-if="searchResults.length > 0" class="max-h-[360px] overflow-y-auto">
              <div
                v-for="item in searchResults"
                :key="item.id"
                class="group py-2 px-3 rounded cursor-pointer hover:bg-page transition-colors"
                @click="goSearchCase(item, 'detail')"
              >
                <div class="flex items-center justify-between gap-2">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="font-num text-[13px] text-primary font-medium">{{ item.id }}</span>
                    <span class="text-[13px] text-ink truncate">{{ item.patientName }}</span>
                  </div>
                  <el-tag v-if="item.riskLevel" size="small" effect="plain" class="!mr-0">
                    {{ RISK_LABEL[item.riskLevel] ?? item.riskLevel }}
                  </el-tag>
                </div>
                <div class="mt-1 flex items-center gap-3 text-[11px] text-hint">
                  <span>{{ item.modality }}</span>
                  <span>检查：{{ item.examDate || '—' }}</span>
                  <span>评分：{{ item.riskScore ?? '—' }}</span>
                </div>
                <div class="mt-1 flex items-center gap-2 opacity-0 group-hover:opacity-100 search-item-actions">
                  <el-button link type="primary" size="small" @click.stop="goSearchCase(item, 'viewer')">阅片</el-button>
                  <el-button v-if="item.hasAnalysis" link type="primary" size="small" @click.stop="goSearchCase(item, 'analysis')">AI详情</el-button>
                </div>
              </div>
            </div>
            <div v-else-if="!searchLoading && searchKeyword.trim()" class="py-8 text-center text-xs text-hint">
              未找到匹配病例，回车按关键字筛选病例库
            </div>
            <div v-else-if="!searchLoading" class="py-8 text-center text-xs text-hint">
              输入病例 ID 或患者姓名开始搜索
            </div>
          </div>
        </el-popover>
      </div>

      <!-- 用户信息与退出 -->
      <div class="flex items-center gap-4 shrink-0">
        <!-- 主题切换（浅色/深色，医疗阅片护眼） -->
        <el-tooltip :content="themeMode === 'dark' ? '切换浅色模式' : '切换深色模式'" placement="bottom">
          <div
            class="w-9 h-9 rounded-lg flex items-center justify-center cursor-pointer transition-colors hover:bg-[var(--ad-notify-neutral-bg)]"
            @click="toggleTheme"
          >
            <el-icon :size="19" class="text-sub">
              <component :is="themeMode === 'dark' ? 'Sunny' : 'Moon'" />
            </el-icon>
          </div>
        </el-tooltip>

        <!-- 通知中心铃铛（全角色；随访/审核提醒懒生成，点击跳转关联页面） -->
      <el-popover
        v-model:visible="notifyPopoverVisible"
        placement="bottom-end"
        :width="384"
        trigger="click"
        popper-class="notify-popper"
        @show="onPopoverVisibleChange(true)"
      >
        <template #reference>
          <!-- 注意：el-popover 的 reference 插槽要求单一元素根节点，
               外层再套 el-tooltip 会触发 "Runtime directive used on component
               with non-element root node" 警告，故用原生 title 提供悬浮提示 -->
          <div
            class="relative w-9 h-9 rounded-lg flex items-center justify-center cursor-pointer transition-colors hover:bg-[var(--ad-notify-neutral-bg)]"
            :title="unreadCount > 0 ? `${unreadCount} 条未读通知` : '暂无未读通知'"
          >
            <el-icon :size="19" :style="{ color: unreadHasFollowUp ? 'var(--ad-risk-late)' : unreadCount > 0 ? 'var(--ad-risk-mci)' : 'var(--ad-ink-2)' }"><BellFilled /></el-icon>
            <span
              v-if="unreadCount > 0"
              class="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full text-white text-[10px] leading-4 text-center font-num"
              :style="{ background: unreadHasFollowUp ? 'var(--ad-risk-late)' : 'var(--ad-risk-mci)' }"
            >{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
          </div>
        </template>

        <div v-loading="notifyLoading">
          <!-- 面板头：全部/未读切换 + 全部已读 -->
          <div class="flex items-center justify-between pb-2 border-b border-line">
            <div class="flex items-center gap-3 text-[13px]">
              <span
                class="cursor-pointer font-medium"
                :class="onlyUnread ? 'text-sub' : 'text-primary'"
                @click="switchOnlyUnread(false)"
              >全部</span>
              <span
                class="cursor-pointer"
                :class="onlyUnread ? 'text-primary' : 'text-sub'"
                @click="switchOnlyUnread(true)"
              >未读{{ unreadCount > 0 ? `(${unreadCount})` : '' }}</span>
            </div>
            <el-link type="primary" underline="never" class="!text-xs" @click="onMarkAllRead">全部已读</el-link>
          </div>

          <!-- 通知列表 -->
          <div v-if="notifications.length > 0" class="max-h-[380px] overflow-y-auto -mx-2 px-2">
            <div
              v-for="n in notifications"
              :key="n.id"
              class="flex gap-2.5 py-2.5 border-b border-line/60 last:border-0 cursor-pointer rounded px-2 -mx-2 transition-colors hover:bg-page"
              @click="onItemClick(n)"
            >
              <div class="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5" :style="{ background: TYPE_META[n.type]?.bg, color: TYPE_META[n.type]?.color }">
                <el-icon :size="16"><component :is="TYPE_META[n.type]?.icon ?? 'InfoFilled'" /></el-icon>
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-1.5">
                  <span class="text-[13px] truncate" :class="n.isRead ? 'text-sub' : 'text-ink font-medium'">{{ n.title }}</span>
                  <span v-if="!n.isRead" class="w-1.5 h-1.5 rounded-full bg-[var(--ad-risk-late)] shrink-0" />
                </div>
                <div class="text-xs text-hint mt-0.5 line-clamp-2">{{ n.content }}</div>
                <div class="text-[11px] text-hint/70 mt-1">{{ n.createdAt }}</div>
              </div>
            </div>
          </div>
          <div v-else class="py-10 flex flex-col items-center gap-2">
            <el-icon :size="36" class="text-hint/40"><Bell /></el-icon>
            <div class="text-xs text-hint">{{ onlyUnread ? '没有未读通知' : '暂无通知' }}</div>
          </div>
        </div>
      </el-popover>
        <span class="text-xs text-hint">v1.0.0</span>
        <el-dropdown trigger="hover">
          <div class="flex items-center gap-2 cursor-pointer select-none">
            <el-avatar :size="30" :src="userStore.avatar" class="bg-primary-light text-primary text-sm font-semibold">
              {{ userStore.realName.slice(0, 1) }}
            </el-avatar>
            <div class="leading-tight text-left">
              <div class="text-[13px] text-ink font-medium">{{ userStore.realName }}</div>
              <div class="text-[11px] text-hint">{{ userStore.roleName }} · {{ userStore.userInfo?.department }}</div>
            </div>
            <el-icon class="text-hint"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>
                <el-icon><User /></el-icon>账号：{{ userStore.userInfo?.username }}
              </el-dropdown-item>
              <el-dropdown-item @click="router.push('/profile')">
                <el-icon><UserFilled /></el-icon>个人中心
              </el-dropdown-item>
              <el-dropdown-item divided @click="onLogout">
                <el-icon><SwitchButton /></el-icon>退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <div class="flex-1 flex overflow-hidden">
      <!-- ==================== 左侧固定侧边菜单 ==================== -->
      <aside class="w-56 shrink-0 bg-sidebar flex flex-col">
        <el-menu
          :default-active="activeMenu"
          router
          class="flex-1 !border-none pt-2"
          background-color="var(--ad-sidebar)"
          text-color="var(--ad-ink-3)"
          active-text-color="#FFFFFF"
        >
          <el-menu-item
            v-for="m in visibleMenus"
            :key="m.key"
            :index="m.path"
            class="h-12 mx-2 !rounded-lg !mb-1"
          >
            <el-icon :size="17"><component :is="m.icon" /></el-icon>
            <span>{{ m.title }}</span>
          </el-menu-item>
        </el-menu>
        <!-- 侧边栏底部版本信息 -->
        <div class="px-5 py-4 text-[11px] text-[var(--ad-ink-3)] leading-5 border-t border-[var(--ad-sidebar-hover)]">
          脑影明衰 v1.0.0<br />模型：TransMF-15ens-v4
        </div>
      </aside>

      <!-- ==================== 主内容区 ==================== -->
      <main class="flex-1 overflow-y-auto">
        <!--
          错误边界：包裹路由出口。
          页面级组件（图表 / 阅片视口 / 3D 体绘制）渲染异常时展示可恢复兜底 UI，
          避免整站白屏；key 用 route.fullPath，切页即重建边界状态。
        -->
        <ErrorBoundary :key="route.fullPath" :component-name="String(route.name || route.path)">
          <router-view v-slot="{ Component }">
            <transition name="page-fade" mode="out-in">
              <component :is="Component" :key="route.fullPath" />
            </transition>
          </router-view>
        </ErrorBoundary>
      </main>
    </div>
  </div>
</template>

<style scoped>
/* 侧边菜单激活态：医疗工作站统一的渐变高亮 + 左侧指示条 */
:deep(.el-menu-item) {
  position: relative;
  transition: background 0.2s ease, color 0.2s ease;
}
:deep(.el-menu-item.is-active) {
  background: linear-gradient(90deg, var(--ad-primary) 0%, var(--ad-primary-dark) 100%) !important;
  color: #fff !important;
  box-shadow: 0 4px 14px rgba(47, 109, 163, 0.4);
}
:deep(.el-menu-item.is-active::before) {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  border-radius: 0 3px 3px 0;
  background: #9fd0f5;
}
:deep(.el-menu-item:not(.is-active):hover) {
  background: var(--ad-sidebar-hover) !important;
  color: #fff !important;
}
</style>
