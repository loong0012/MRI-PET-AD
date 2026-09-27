import { nextTick } from 'vue'
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import type { RoleKey } from '@/types/user'

/**
 * 路由表
 * meta.roles 为空 = 全角色可见；meta.title 用于顶栏面包屑
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/login/LoginView.vue'),
    meta: { title: '登录', public: true }
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/dashboard/DashboardView.vue'),
        meta: { title: '工作台仪表盘' }
      },
      {
        path: 'cases',
        name: 'cases',
        component: () => import('@/views/cases/CaseListView.vue'),
        meta: { title: '病例库管理' }
      },
      {
        path: 'patient',
        name: 'patient-list',
        component: () => import('@/views/patient/PatientListView.vue'),
        meta: { title: '患者档案', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'patient/:patientNo',
        name: 'patient-profile',
        component: () => import('@/views/patient/PatientProfileView.vue'),
        meta: { title: '患者全景档案', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'viewer/:caseId',
        name: 'viewer',
        component: () => import('@/views/viewer/ViewerView.vue'),
        meta: { title: '多模态阅片与AI分析', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'analysis/:caseId',
        name: 'analysis',
        component: () => import('@/views/analysis/AnalysisDetailView.vue'),
        meta: { title: 'AI分析详情与干预方案', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'report/:caseId',
        name: 'report',
        component: () => import('@/views/report/ReportView.vue'),
        meta: { title: '筛查报告', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'followup',
        name: 'followup',
        component: () => import('@/views/followup/FollowUpView.vue'),
        meta: { title: '随访管理', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'warning',
        name: 'warning',
        component: () => import('@/views/warning/WarningView.vue'),
        meta: { title: '高危预警中心', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'quality',
        name: 'quality',
        component: () => import('@/views/quality/QualityView.vue'),
        meta: { title: '数据质控中心', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'model',
        name: 'model',
        component: () => import('@/views/model/ModelConfigView.vue'),
        meta: { title: '模型科研配置', roles: ['researcher', 'admin'] as RoleKey[] }
      },
      {
        path: 'analytics',
        name: 'analytics',
        component: () => import('@/views/analytics/AnalyticsView.vue'),
        meta: { title: '科研统计分析', roles: ['researcher', 'admin'] as RoleKey[] }
      },
      {
        path: 'model-monitor',
        name: 'model-monitor',
        component: () => import('@/views/model_monitor/ModelMonitorView.vue'),
        meta: { title: '模型性能监控', roles: ['researcher', 'admin'] as RoleKey[] }
      },
      {
        path: 'review',
        name: 'review',
        component: () => import('@/views/review/ReviewWorkflowView.vue'),
        meta: { title: '报告审核工作流', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'active-learning',
        name: 'active-learning',
        component: () => import('@/views/active_learning/ActiveLearningView.vue'),
        meta: { title: '主动学习队列', roles: ['researcher', 'admin'] as RoleKey[] }
      },
      {
        path: 'cdss',
        name: 'cdss',
        component: () => import('@/views/cdss/CdssView.vue'),
        meta: { title: '临床决策支持', roles: ['radiologist', 'neurologist', 'admin'] as RoleKey[] }
      },
      {
        path: 'dictionary',
        name: 'dictionary',
        component: () => import('@/views/dictionary/DictionaryView.vue'),
        meta: { title: '数据字典中心' }
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/knowledge/KnowledgeView.vue'),
        meta: { title: '科普知识库' }
      },
      {
        path: 'system',
        name: 'system',
        component: () => import('@/views/system/SystemManageView.vue'),
        meta: { title: '系统权限管理', roles: ['admin'] as RoleKey[] }
      },
      {
        path: 'case-log',
        name: 'case-log',
        component: () => import('@/views/system/CaseLogView.vue'),
        meta: { title: '病例操作审计', roles: ['admin'] as RoleKey[] }
      },
      {
        path: 'profile',
        name: 'profile',
        component: () => import('@/views/profile/ProfileView.vue'),
        meta: { title: '个人中心' }
      }
    ]
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 主内容区是独立滚动容器（<main class="overflow-y-auto">，window 自身不滚动），
// scrollBehavior 的 el 只会对 window.scrollTo 生效，无法复位内部容器；
// 因此在 afterEach + nextTick（等 keyed router-view 子页面重建完成）后手动滚回顶部
router.afterEach(async () => {
  await nextTick()
  document.querySelector('main.flex-1.overflow-y-auto')?.scrollTo({ top: 0 })
})

// ---------- 全局守卫：登录态 + 角色权限 ----------
router.beforeEach((to) => {
  const userStore = useUserStore()
  // 公开页（登录）直接放行；已登录访问登录页则回工作台
  if (to.meta.public) {
    if (userStore.isLoggedIn && to.name === 'login') return { path: '/dashboard' }
    return true
  }
  if (!userStore.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  const roles = to.meta.roles as RoleKey[] | undefined
  if (!userStore.hasRole(roles)) {
    ElMessage.warning('当前角色无权访问该页面')
    return { path: '/dashboard' }
  }
  return true
})

router.afterEach((to) => {
  const base = '脑影明衰 · 阿尔茨海默病一体化 MRI/PET 脑成像智能诊断系统'
  document.title = to.meta.title ? `${to.meta.title} - ${base}` : base
})

export default router
