<script setup lang="ts">
/**
 * 系统权限管理页（超级管理员）
 * ------------------------------------------------------------------
 * Tab1 用户管理：多角色账号列表（放射科医师 / 神经内科医师 / 科研管理员 / 超级管理员），
 *               支持新增账号、启用/禁用（禁用后无法登录）；
 * Tab2 角色权限：四类角色的功能模块权限点自定义配置（勾选后保存下发）；
 * Tab3 审计日志：系统操作日志 / 病例操作记录 / AI 推理日志三类溯源查询，
 *               全量操作行为可追溯，支持关键词过滤与 CSV 导出。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  apiGetUsers, apiAddUser, apiToggleUser, apiReviewUser,
  apiGetRolePermissions, apiSaveRolePermissions, apiGetAllPermissions
} from '@/api/auth'
import { apiGetSystemLogs, apiGetCaseLogs, apiGetInferenceLogs as apiFetchInferenceLogs } from '@/api/model'
import { apiListAnnouncements, apiPublishAnnouncement, apiRevokeAnnouncement } from '@/api/notification'
import { apiAdminLoginLogs, type LoginLogItem } from '@/api/loginLog'
import { apiAuditOverview, type AuditOverview } from '@/api/auditBoard'
import ChartBase from '@/components/ChartBase.vue'
import type { EChartsOption } from 'echarts'
import {
  apiBackupInfo, apiBackupDownload, apiBackupFiles, apiBackupRestore,
  type BackupInfo, type BackupFile
} from '@/api/backup'
import { useUserStore } from '@/stores/user'
import { downloadCsv } from '@/utils/export'
import type { UserRecord, RoleKey, RolePermission, PermissionNode } from '@/types/user'
import type { SystemLog, CaseLog } from '@/types/system'
import type { InferenceLog } from '@/types/model'
import type { Announcement } from '@/types/notification'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const userStore = useUserStore()

// ---------- 待审核统计（Tab 标签徽标） ----------
const pendingReviewCount = computed(() => users.value.filter((u) => u.status === 2).length)

// ---------- Tab 状态 ----------
const activeTab = ref('users')

// ============================================================
// Tab1 用户管理
// ============================================================
const users = ref<UserRecord[]>([])
const usersLoading = ref(true)

/** 用户关键词搜索（账号 / 姓名 / 科室） */
const userKeyword = ref('')
const filteredUsers = computed<UserRecord[]>(() => {
  const k = userKeyword.value.trim().toLowerCase()
  if (!k) return users.value
  return users.value.filter(
    (u) => u.username.toLowerCase().includes(k) || u.realName.includes(k) || u.department.includes(k)
  )
})

/** 新增用户弹窗 */
const addVisible = ref(false)
const addFormRef = ref<FormInstance | null>(null)
const addLoading = ref(false)
const addForm = reactive({
  username: '',
  realName: '',
  role: 'radiologist' as RoleKey,
  department: '放射科',
  phone: '',
  password: ''
})
const addRules: FormRules = {
  username: [
    { required: true, message: '请输入登录账号', trigger: 'blur' },
    { pattern: /^[a-zA-Z0-9_]{3,20}$/, message: '3-20 位字母 / 数字 / 下划线', trigger: 'blur' }
  ],
  realName: [{ required: true, message: '请输入真实姓名', trigger: 'blur' }],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  department: [{ required: true, message: '请输入所属科室', trigger: 'blur' }],
  phone: [{ pattern: /^1\d{10}$/, message: '请输入 11 位手机号', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 6, max: 20, message: '密码长度 6-20 位', trigger: 'blur' }
  ]
}

const ROLE_OPTIONS: { value: RoleKey; label: string }[] = [
  { value: 'radiologist', label: '放射科医师' },
  { value: 'neurologist', label: '神经内科医师' },
  { value: 'researcher', label: '科研管理员' },
  { value: 'admin', label: '超级管理员' }
]

async function submitAdd(): Promise<void> {
  const valid = await addFormRef.value?.validate().catch(() => false)
  if (!valid) return
  addLoading.value = true
  try {
    await apiAddUser({
      username: addForm.username,
      realName: addForm.realName,
      role: addForm.role,
      roleName: ROLE_OPTIONS.find((r) => r.value === addForm.role)?.label ?? '',
      department: addForm.department,
      phone: addForm.phone,
      status: 1,
      // 初始密码必须透传（早期版本漏发，后端静默默认 123456）
      password: addForm.password
    })
    ElMessage.success('账号创建成功')
    addVisible.value = false
    // 重置表单
    addForm.username = ''
    addForm.realName = ''
    addForm.role = 'radiologist'
    addForm.department = '放射科'
    addForm.phone = ''
    addForm.password = ''
    users.value = await apiGetUsers()
  } finally {
    addLoading.value = false
  }
}

/** 启用 / 禁用用户 */
const toggleConfirmVisible = ref(false)
const toggleTarget = ref<UserRecord | null>(null)

function askToggle(u: UserRecord): void {
  toggleTarget.value = u
  toggleConfirmVisible.value = true
}

async function doToggle(): Promise<void> {
  const u = toggleTarget.value
  if (!u) return
  const next: 0 | 1 | 2 = u.status === 1 ? 0 : 1
  try {
    await apiToggleUser(u.id, next)
    ElMessage.success(next === 1 ? `已启用账号 ${u.username}` : `已禁用账号 ${u.username}`)
    toggleConfirmVisible.value = false
    // 强制刷新用户列表 + 系统日志（mock 模式原数组被修改但响应式未触发）
    users.value = await apiGetUsers()
    systemLogs.value = await apiGetSystemLogs()
  } catch {
    // 异常时关闭确认弹窗并提示，避免 ConfirmDialog 卡死无反馈
    toggleConfirmVisible.value = false
    ElMessage.error('账号状态切换失败，请稍后重试')
  }
}

// ---------- 注册账号审核流程 ----------
const reviewConfirmVisible = ref(false)
const reviewTarget = ref<UserRecord | null>(null)
const reviewAction = ref<'approve' | 'reject'>('approve')

/** 弹出审核确认弹窗 */
function askReview(u: UserRecord, action: 'approve' | 'reject'): void {
  reviewTarget.value = u
  reviewAction.value = action
  reviewConfirmVisible.value = true
}

/** 执行审核：通过 →1 / 拒绝 →0 */
async function doReview(): Promise<void> {
  const u = reviewTarget.value
  if (!u) return
  const approve = reviewAction.value === 'approve'
  try {
    await apiReviewUser({
      id: u.id,
      approve,
      operator: userStore.userInfo?.username ?? 'admin'
    })
    ElMessage.success(approve ? `已通过 ${u.username} 的注册申请` : `已拒绝 ${u.username} 的注册申请`)
    reviewConfirmVisible.value = false
    // 强制刷新用户列表 + 系统日志（mock 模式原数组被修改但响应式未触发）
    users.value = await apiGetUsers()
    systemLogs.value = await apiGetSystemLogs()
  } catch {
    // 异常时关闭确认弹窗并提示，避免 ConfirmDialog 卡死无反馈
    reviewConfirmVisible.value = false
    ElMessage.error('审核操作失败，请稍后重试')
  }
}

/** 切换到审计日志 Tab 时刷新日志（确保审核操作后的新日志可见） */
async function refreshLogs(): Promise<void> {
  const [sys, cl, inf] = await Promise.all([
    apiGetSystemLogs(),
    apiGetCaseLogs(),
    apiFetchInferenceLogs()
  ])
  systemLogs.value = sys
  caseLogs.value = cl
  inferenceLogs.value = inf
}

// ============================================================
// Tab2 角色权限
// ============================================================
const permissions = ref<PermissionNode[]>([])
const rolePerms = ref<RolePermission[]>([])
const activeRole = ref<RoleKey>('radiologist')
const permsLoading = ref(true)

/** 当前角色的权限勾选（本地编辑副本，保存后下发） */
const checkedKeys = ref<string[]>([])
const activeRoleInfo = computed(() => rolePerms.value.find((r) => r.role === activeRole.value))

/** 切换角色时加载对应权限 */
function switchRole(role: RoleKey): void {
  activeRole.value = role
  const rp = rolePerms.value.find((r) => r.role === role)
  checkedKeys.value = rp ? [...rp.permissionKeys] : []
}

/** 权限保存中（按钮 loading 反馈，避免用户连点无响应） */
const permsSaving = ref(false)

async function savePerms(): Promise<void> {
  const rp = rolePerms.value.find((r) => r.role === activeRole.value)
  if (!rp) return
  rp.permissionKeys = [...checkedKeys.value]
  permsSaving.value = true
  try {
    await apiSaveRolePermissions(rolePerms.value)
    ElMessage.success(`角色「${rp.roleName}」权限配置已保存`)
  } catch {
    ElMessage.error('权限保存失败，请稍后重试')
  } finally {
    permsSaving.value = false
  }
}

// ============================================================
// Tab3 审计日志
// ============================================================
type LogKind = 'system' | 'case' | 'inference'
const logKind = ref<LogKind>('system')
const logKeyword = ref('')

const systemLogs = ref<SystemLog[]>([])
const caseLogs = ref<CaseLog[]>([])
const inferenceLogs = ref<InferenceLog[]>([])
const logsLoading = ref(true)

/** 关键词过滤（各日志通用字段：编号 / 操作 / 操作人） */
const filteredSystemLogs = computed<SystemLog[]>(() => filterLogs(systemLogs.value))
const filteredCaseLogs = computed<CaseLog[]>(() => filterLogs(caseLogs.value))
const filteredInferenceLogs = computed<InferenceLog[]>(() => filterLogs(inferenceLogs.value))

/** 通用过滤辅助（对传入字段取值匹配） */
function filterLogs<T>(list: T[]): T[] {
  const k = logKeyword.value.trim().toLowerCase()
  if (!k) return list
  return list.filter((item) => JSON.stringify(item).toLowerCase().includes(k))
}

/** 日志 CSV 导出（按当前类别） */
function exportLogs(): void {
  if (logKind.value === 'system') {
    downloadCsv(
      '系统操作日志.csv',
      ['ID', '模块', '操作', '操作人', '角色', 'IP', '结果', '时间', '详情'],
      filteredSystemLogs.value.map((l) => [l.id, l.module, l.action, l.operator, l.role, l.ip, l.result, l.time, l.detail])
    )
  } else if (logKind.value === 'case') {
    downloadCsv(
      '病例操作记录.csv',
      ['ID', '病例编号', '患者', '操作', '操作人', '时间', '详情'],
      filteredCaseLogs.value.map((l) => [l.id, l.caseId, l.patientName, l.action, l.operator, l.time, l.detail])
    )
  } else {
    downloadCsv(
      'AI推理日志.csv',
      ['ID', '病例编号', '患者', '模型版本', '风险评分', '状态', '操作人', '时间'],
      filteredInferenceLogs.value.map((l) => [l.id, l.caseId, l.patientName, l.modelVersion, l.riskScore, l.status, l.operator, l.time])
    )
  }
}

// ---------- 系统公告 ----------
const announcements = ref<Announcement[]>([])
const announceVisible = ref(false)
const announcePublishing = ref(false)
const announceForm = reactive({ title: '', content: '' })

async function fetchAnnouncements(): Promise<void> {
  try {
    announcements.value = await apiListAnnouncements()
  } catch { /* 静默：公告加载失败不影响其他 tab */ }
}

function openAnnounce(): void {
  announceForm.title = ''
  announceForm.content = ''
  announceVisible.value = true
}

async function submitAnnounce(): Promise<void> {
  if (!announceForm.title.trim() || !announceForm.content.trim()) {
    ElMessage.warning('请填写公告标题与内容')
    return
  }
  announcePublishing.value = true
  try {
    const res = await apiPublishAnnouncement({
      title: announceForm.title.trim(),
      content: announceForm.content.trim()
    })
    ElMessage.success(`公告已发布，已通知 ${res.receivers} 人`)
    announceVisible.value = false
    await fetchAnnouncements()
  } catch {
    ElMessage.error('发布失败，请稍后重试')
  } finally {
    announcePublishing.value = false
  }
}

async function revokeAnnounce(row: Announcement): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定下线公告「${row.title}」吗？下线后将从所有用户通知中心移除。`,
      '下线确认',
      { type: 'warning', confirmButtonText: '确定下线', cancelButtonText: '取消' }
    )
  } catch { return }
  try {
    await apiRevokeAnnouncement(row.id)
    ElMessage.success('公告已下线')
    await fetchAnnouncements()
  } catch {
    ElMessage.error('下线失败，请稍后重试')
  }
}

// ---------- 数据备份 ----------
const backupInfo = ref<BackupInfo | null>(null)
const backupFiles = ref<BackupFile[]>([])
const backupDownloading = ref(false)
const backupLoading = ref(false)

/** 字节大小人类可读 */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function fetchBackupData(): Promise<void> {
  backupLoading.value = true
  try {
    const [info, files] = await Promise.all([apiBackupInfo(), apiBackupFiles()])
    backupInfo.value = info
    backupFiles.value = files
  } catch {
    ElMessage.error('备份信息加载失败')
  } finally {
    backupLoading.value = false
  }
}

async function onBackupDownload(): Promise<void> {
  backupDownloading.value = true
  try {
    await apiBackupDownload()
    ElMessage.success('备份文件已开始下载，同时已存档至服务器')
    await fetchBackupData()
  } catch {
    ElMessage.error('备份失败，请稍后重试')
  } finally {
    backupDownloading.value = false
  }
}

async function onBackupRestore(file: BackupFile): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定用备份「${file.filename}」恢复数据库吗？<br/>恢复前会自动备份当前数据库；恢复后建议重新登录。`,
      '恢复确认（高风险操作）',
      {
        type: 'warning',
        dangerouslyUseHTMLString: true,
        confirmButtonText: '确定恢复',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch { return }
  try {
    const res = await apiBackupRestore(file.filename)
    ElMessageBox.alert(
      `数据库已恢复。<br/>恢复前的自动备份：<b>${res.preRestoreFile}</b>（可用于回退）<br/>请刷新页面或重新登录。`,
      '恢复完成',
      { dangerouslyUseHTMLString: true, confirmButtonText: '我知道了' }
    )
    await fetchBackupData()
  } catch {
    ElMessage.error('恢复失败，请查看后端日志')
  }
}

// 切到数据备份 / 登录日志 / 运行审计 tab 时懒加载
watch(activeTab, (name) => {
  if (name === 'backup') void fetchBackupData()
  if (name === 'login-logs') void fetchLoginLogs()
  if (name === 'audit') void fetchAuditBoard()
})

// ---------- 登录日志（安全审计） ----------
const loginLogs = ref<LoginLogItem[]>([])
const loginLogsTotal = ref(0)
const failCount7d = ref(0)
const loginLogsLoading = ref(false)
const loginLogQuery = reactive({ page: 1, pageSize: 15, keyword: '', result: '' })

async function fetchLoginLogs(): Promise<void> {
  loginLogsLoading.value = true
  try {
    const res = await apiAdminLoginLogs({ ...loginLogQuery })
    loginLogs.value = res.list
    loginLogsTotal.value = res.total
    failCount7d.value = res.failCount7d
  } finally {
    loginLogsLoading.value = false
  }
}

function searchLoginLogs(): void {
  loginLogQuery.page = 1
  fetchLoginLogs()
}

function parseLoginUA(ua: string): string {
  if (!ua) return '—'
  const os = /Windows NT 10/.test(ua) ? 'Windows 10/11'
    : /Windows/.test(ua) ? 'Windows'
    : /Mac OS X/.test(ua) ? 'macOS'
    : /Android/.test(ua) ? 'Android'
    : /iPhone|iPad|iOS/.test(ua) ? 'iOS'
    : /Linux/.test(ua) ? 'Linux' : '未知系统'
  const browser = /Edg\//.test(ua) ? 'Edge'
    : /Chrome\//.test(ua) ? 'Chrome'
    : /Firefox\//.test(ua) ? 'Firefox'
    : /Safari\//.test(ua) ? 'Safari' : '未知浏览器'
  return `${os} · ${browser}`
}

// ---------- 运行审计看板 ----------
const auditLoading = ref(false)
const auditDays = ref(30)
const audit = ref<AuditOverview | null>(null)

async function fetchAuditBoard(): Promise<void> {
  auditLoading.value = true
  try {
    audit.value = await apiAuditOverview(auditDays.value)
  } finally {
    auditLoading.value = false
  }
}

function changeAuditDays(d: string | number | boolean | undefined): void {
  auditDays.value = Number(d)
  fetchAuditBoard()
}

const auditStatCards = computed(() => {
  const t = audit.value?.totals
  return [
    { label: '登录总次数', value: t?.loginCount ?? 0, unit: '次', color: '#2F6DA3' },
    { label: '登录失败率', value: t?.failRate ?? 0, unit: '%', color: (t?.failRate ?? 0) > 20 ? '#C94F4F' : '#D99A2B' },
    { label: '活跃用户', value: t?.activeUsers ?? 0, unit: '人', color: '#2E9E6B' },
    { label: '病例操作', value: t?.caseOps ?? 0, unit: '次', color: '#8E6BB3' },
    { label: '系统操作', value: t?.systemOps ?? 0, unit: '次', color: '#2F6DA3' },
    { label: 'AI 推理', value: t?.inferenceCount ?? 0, unit: '次', color: '#D99A2B' },
    { label: '推理失败', value: t?.inferenceFail ?? 0, unit: '次', color: '#C94F4F' },
    { label: '平均耗时', value: t?.avgDuration ?? 0, unit: 's', color: '#5A6577' }
  ]
})

const loginTrendOption = computed<EChartsOption>(() => {
  const data = audit.value?.loginTrend ?? []
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['登录成功', '登录失败'], right: 10, top: 4, textStyle: { color: '#5A6577', fontSize: 12 } },
    grid: { left: 40, right: 16, top: 38, bottom: 30 },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.date),
      axisLine: { lineStyle: { color: '#C8D1DC' } },
      axisLabel: { color: '#7A8699', fontSize: 10, interval: Math.max(0, Math.floor(data.length / 12)) }
    },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#EDF1F5' } }, axisLabel: { color: '#7A8699', fontSize: 11 } },
    series: [
      { name: '登录成功', type: 'bar', stack: 'login', data: data.map((d) => d.success), itemStyle: { color: '#2F6DA3', borderRadius: [0, 0, 0, 0] }, barMaxWidth: 14 },
      { name: '登录失败', type: 'bar', stack: 'login', data: data.map((d) => d.fail), itemStyle: { color: '#C94F4F', borderRadius: [3, 3, 0, 0] }, barMaxWidth: 14 }
    ]
  }
})

const activeUsersOption = computed<EChartsOption>(() => {
  const data = [...(audit.value?.activeUsers ?? [])].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['成功', '失败'], right: 10, top: 4, textStyle: { color: '#5A6577', fontSize: 12 } },
    grid: { left: 90, right: 24, top: 38, bottom: 26 },
    xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#EDF1F5' } }, axisLabel: { color: '#7A8699', fontSize: 11 } },
    yAxis: { type: 'category', data: data.map((u) => u.realName || u.username), axisLine: { lineStyle: { color: '#C8D1DC' } }, axisLabel: { color: '#5A6577', fontSize: 11 } },
    series: [
      { name: '成功', type: 'bar', stack: 'u', data: data.map((u) => u.success), itemStyle: { color: '#2F6DA3' }, barMaxWidth: 14 },
      { name: '失败', type: 'bar', stack: 'u', data: data.map((u) => u.fail), itemStyle: { color: '#C94F4F', borderRadius: [3, 3, 0, 0] }, barMaxWidth: 14 }
    ]
  }
})

const caseActionsOption = computed<EChartsOption>(() => {
  const data = audit.value?.caseActions ?? []
  return {
    tooltip: { trigger: 'item', formatter: '{b}：{c} 次（{d}%）' },
    legend: { type: 'scroll', orient: 'vertical', right: 6, top: 'middle', textStyle: { color: '#5A6577', fontSize: 11 } },
    color: ['#2F6DA3', '#4A8CC7', '#6FA8D6', '#2E9E6B', '#D99A2B', '#8E6BB3', '#C94F4F', '#5A6577', '#9AA8B8', '#D96B2B'],
    series: [{
      type: 'pie',
      radius: ['42%', '68%'],
      center: ['38%', '52%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data
    }]
  }
})

const failHoursOption = computed<EChartsOption>(() => {
  const data = audit.value?.failHours ?? []
  return {
    tooltip: { trigger: 'axis', formatter: (p: unknown) => {
      const arr = p as Array<{ axisValue: string; value: number }>
      return `${arr[0].axisValue} 失败登录 ${arr[0].value} 次`
    } },
    grid: { left: 36, right: 16, top: 24, bottom: 30 },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.hour.slice(0, 2)),
      axisLine: { lineStyle: { color: '#C8D1DC' } },
      axisLabel: { color: '#7A8699', fontSize: 10, interval: 1 }
    },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#EDF1F5' } }, axisLabel: { color: '#7A8699', fontSize: 11 } },
    series: [{
      type: 'bar',
      data: data.map((d) => ({
        value: d.count,
        itemStyle: { color: d.count > 0 ? '#C94F4F' : '#DCE3EB', borderRadius: [3, 3, 0, 0] }
      })),
      barMaxWidth: 12
    }]
  }
})

onMounted(async () => {
  // 三个 tab 数据独立容错：失败的 tab 仅自己空数据/报错，其余正常展示，loading 必定复位
  try {
    const [u, p, all, sys, cl, inf] = await Promise.allSettled([
      apiGetUsers(),
      apiGetRolePermissions(),
      apiGetAllPermissions(),
      apiGetSystemLogs(),
      apiGetCaseLogs(),
      apiFetchInferenceLogs()
    ])
    if (u.status === 'fulfilled') users.value = u.value
    if (p.status === 'fulfilled') rolePerms.value = p.value
    if (all.status === 'fulfilled') {
      permissions.value = all.value
      switchRole('radiologist')
    }
    if (sys.status === 'fulfilled') systemLogs.value = sys.value
    if (cl.status === 'fulfilled') caseLogs.value = cl.value
    if (inf.status === 'fulfilled') inferenceLogs.value = inf.value
  } finally {
    usersLoading.value = false
    permsLoading.value = false
    logsLoading.value = false
  }
  void fetchAnnouncements()
})
</script>

<template>
  <div class="page-wrap">
    <div class="card-ad anim-fade-up">
      <!-- ==================== 页内 Tab 导航 ==================== -->
      <el-tabs v-model="activeTab" class="px-5 pt-1">
        <!-- ============ Tab1 用户管理 ============ -->
        <el-tab-pane name="users">
          <template #label>
            <span>用户管理</span>
            <el-badge
              v-if="pendingReviewCount > 0"
              :value="pendingReviewCount"
              class="!ml-1.5"
              type="warning"
            />
          </template>
          <div class="flex items-center justify-between mb-4">
            <el-input
              v-model="userKeyword"
              placeholder="搜索账号 / 姓名 / 科室"
              clearable
              class="!w-64"
              :prefix-icon="'Search'"
            />
            <el-button type="primary" :icon="'Plus'" @click="addVisible = true">新增用户</el-button>
          </div>
          <el-table v-loading="usersLoading" :data="filteredUsers" class="w-full">
            <el-table-column prop="username" label="登录账号" width="110">
              <template #default="{ row }"><span class="font-num text-[13px]">{{ row.username }}</span></template>
            </el-table-column>
            <el-table-column prop="realName" label="姓名" width="100" />
            <el-table-column label="角色" width="130">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" round>{{ row.roleName }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="department" label="所属科室" width="110" />
            <el-table-column prop="phone" label="手机号" width="130">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ row.phone }}</span></template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  round
                  effect="light"
                  :type="row.status === 1 ? 'success' : row.status === 2 ? 'warning' : 'info'"
                >
                  {{ row.status === 1 ? '启用' : row.status === 2 ? '待审核' : '禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="createTime" label="创建时间" width="160">
              <template #default="{ row }"><span class="font-num text-[12px] text-sub">{{ row.createTime }}</span></template>
            </el-table-column>
            <el-table-column prop="lastLoginTime" label="最后登录" width="160">
              <template #default="{ row }"><span class="font-num text-[12px] text-sub">{{ row.lastLoginTime }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <!-- 待审核：通过 / 拒绝 -->
                <template v-if="row.status === 2">
                  <el-button
                    link
                    type="success"
                    :icon="'CircleCheck'"
                    @click="askReview(row, 'approve')"
                  >通过</el-button>
                  <el-button
                    link
                    type="danger"
                    :icon="'CircleClose'"
                    @click="askReview(row, 'reject')"
                  >拒绝</el-button>
                </template>
                <!-- 已启用/禁用：禁用/启用 -->
                <el-button
                  v-else
                  link
                  :type="row.status === 1 ? 'danger' : 'success'"
                  :icon="row.status === 1 ? 'CircleClose' : 'CircleCheck'"
                  @click="askToggle(row)"
                >
                  {{ row.status === 1 ? '禁用' : '启用' }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- ============ Tab2 角色权限 ============ -->
        <el-tab-pane label="角色权限" name="roles">
          <div class="flex gap-5 pb-5">
            <!-- 角色选择列 -->
            <div class="w-52 shrink-0 space-y-2">
              <div
                v-for="rp in rolePerms"
                :key="rp.role"
                class="px-4 py-3 rounded-card border cursor-pointer transition-colors"
                :class="activeRole === rp.role ? 'border-primary bg-primary-light' : 'border-line hover:border-primary/50'"
                @click="switchRole(rp.role)"
              >
                <div class="text-[13.5px] font-medium text-ink">{{ rp.roleName }}</div>
                <div class="mt-0.5 text-[11px] text-hint">{{ rp.permissionKeys.length }} 个功能模块</div>
              </div>
            </div>

            <!-- 权限勾选区 -->
            <div v-loading="permsLoading" class="flex-1 min-w-0">
              <div class="flex items-center justify-between mb-3">
                <div class="text-[13px] text-sub">
                  配置角色「<span class="font-medium text-ink">{{ activeRoleInfo?.roleName }}</span>」可访问的功能模块
                </div>
                <el-button type="primary" size="small" :icon="'FolderChecked'" :loading="permsSaving" @click="savePerms">保存权限</el-button>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div
                  v-for="p in permissions"
                  :key="p.key"
                  class="px-4 py-3 rounded-card border flex items-start gap-3 cursor-pointer transition-colors"
                  :class="checkedKeys.includes(p.key) ? 'border-primary bg-primary-light' : 'border-line'"
                  @click="checkedKeys.includes(p.key) ? checkedKeys.splice(checkedKeys.indexOf(p.key), 1) : checkedKeys.push(p.key)"
                >
                  <el-checkbox :model-value="checkedKeys.includes(p.key)" class="pointer-events-none" />
                  <div>
                    <div class="text-[13px] font-medium text-ink">{{ p.label }}</div>
                    <div class="mt-0.5 text-[11.5px] text-hint">{{ p.description }}</div>
                  </div>
                </div>
              </div>
              <!-- 风险提示 -->
              <el-alert type="warning" :closable="false" class="!mt-4">
                <template #title>权限变更即时生效：被移除模块的用户刷新后即失去访问能力，请谨慎操作</template>
              </el-alert>
            </div>
          </div>
        </el-tab-pane>

        <!-- ============ Tab3 审计日志 ============ -->
        <el-tab-pane label="审计日志" name="logs" @click="refreshLogs">
          <div class="flex items-center gap-3 mb-4">
            <el-radio-group v-model="logKind">
              <el-radio-button value="system">系统操作日志</el-radio-button>
              <el-radio-button value="case">病例操作记录</el-radio-button>
              <el-radio-button value="inference">AI 推理日志</el-radio-button>
            </el-radio-group>
            <el-input
              v-model="logKeyword"
              placeholder="关键词过滤"
              clearable
              class="!w-56"
              :prefix-icon="'Search'"
            />
            <div class="flex-1" />
            <el-button :icon="'Download'" @click="exportLogs">导出 CSV</el-button>
          </div>

          <!-- 系统操作日志 -->
          <el-table v-if="logKind === 'system'" v-loading="logsLoading" :data="filteredSystemLogs" class="w-full" max-height="520">
            <el-table-column prop="id" label="ID" width="70">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ row.id }}</span></template>
            </el-table-column>
            <el-table-column prop="module" label="模块" width="110" />
            <el-table-column prop="action" label="操作" min-width="120" />
            <el-table-column prop="operator" label="操作人" width="90" />
            <el-table-column prop="role" label="角色" width="110" />
            <el-table-column prop="ip" label="IP 地址" width="120">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ row.ip }}</span></template>
            </el-table-column>
            <el-table-column label="结果" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" round :type="row.result === '成功' ? 'success' : 'danger'" effect="light">{{ row.result }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="time" label="时间" width="160">
              <template #default="{ row }"><span class="font-num text-[12px] text-sub">{{ row.time }}</span></template>
            </el-table-column>
            <el-table-column prop="detail" label="详情" min-width="220" show-overflow-tooltip />
          </el-table>

          <!-- 病例操作记录 -->
          <el-table v-else-if="logKind === 'case'" v-loading="logsLoading" :data="filteredCaseLogs" class="w-full" max-height="520">
            <el-table-column prop="id" label="ID" width="90">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ row.id }}</span></template>
            </el-table-column>
            <el-table-column prop="caseId" label="病例编号" width="100">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ row.caseId }}</span></template>
            </el-table-column>
            <el-table-column prop="patientName" label="患者" width="90" />
            <el-table-column prop="action" label="操作" width="140" />
            <el-table-column prop="operator" label="操作人" width="90" />
            <el-table-column prop="time" label="时间" width="160">
              <template #default="{ row }"><span class="font-num text-[12px] text-sub">{{ row.time }}</span></template>
            </el-table-column>
            <el-table-column prop="detail" label="操作详情" min-width="320" show-overflow-tooltip />
          </el-table>

          <!-- AI 推理日志 -->
          <el-table v-else v-loading="logsLoading" :data="filteredInferenceLogs" class="w-full" max-height="520">
            <el-table-column prop="id" label="ID" width="90">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ row.id }}</span></template>
            </el-table-column>
            <el-table-column prop="caseId" label="病例编号" width="100">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ row.caseId }}</span></template>
            </el-table-column>
            <el-table-column prop="patientName" label="患者" width="90" />
            <el-table-column prop="modelVersion" label="模型版本" width="140">
              <template #default="{ row }"><span class="font-num text-[12px]">{{ row.modelVersion }}</span></template>
            </el-table-column>
            <el-table-column label="风险评分" width="90" align="right">
              <template #default="{ row }">
                <span class="font-num font-medium" :class="row.riskScore >= 62 ? 'text-risk-late' : 'text-ink'">{{ row.riskScore }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" round :type="row.status === '成功' ? 'success' : 'danger'" effect="light">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="operator" label="操作人" width="90" />
            <el-table-column prop="time" label="时间" min-width="160">
              <template #default="{ row }"><span class="font-num text-[12px] text-sub">{{ row.time }}</span></template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- ============ Tab4 系统公告 ============ -->
        <el-tab-pane label="系统公告" name="announce">
          <div class="flex items-center justify-between mb-4">
            <div class="text-[13px] text-sub">发布后将在全部启用用户的通知中心生成消息；下线后从所有用户通知中移除</div>
            <el-button type="primary" :icon="'BellFilled'" @click="openAnnounce">发布公告</el-button>
          </div>
          <el-table :data="announcements" class="w-full" max-height="520">
            <el-table-column prop="title" label="公告标题" min-width="160" show-overflow-tooltip />
            <el-table-column prop="content" label="公告内容" min-width="260" show-overflow-tooltip />
            <el-table-column prop="publisher" label="发布人" width="100" />
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.isActive" type="warning" size="small" effect="plain">生效中</el-tag>
                <el-tag v-else size="small" type="info" effect="plain">已下线</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="createdAt" label="发布时间" width="160" />
            <el-table-column prop="revokedAt" label="下线时间" width="160">
              <template #default="{ row }">
                <span v-if="row.revokedAt" class="text-hint">{{ row.revokedAt }}</span>
                <span v-else class="text-hint">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.isActive" link type="danger" @click="revokeAnnounce(row)">下线</el-button>
                <span v-else class="text-hint text-xs">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- ============ Tab5 数据备份 ============ -->
        <el-tab-pane label="数据备份" name="backup">
          <div v-loading="backupLoading">
            <!-- 当前库信息 + 备份操作 -->
            <div class="grid grid-cols-3 gap-4 mb-5">
              <div class="rounded-card bg-page border border-line p-4">
                <div class="text-[12px] text-sub">当前数据库大小</div>
                <div class="mt-1 font-num text-[24px] font-semibold text-primary">
                  {{ backupInfo ? formatSize(backupInfo.dbSize) : '—' }}
                </div>
                <div class="mt-1 text-[11px] text-hint">SQLite · ad_screen.db</div>
              </div>
              <div class="rounded-card bg-page border border-line p-4">
                <div class="text-[12px] text-sub">服务器存档数量</div>
                <div class="mt-1 font-num text-[24px] font-semibold text-[#2E9E6B]">
                  {{ backupInfo?.backupCount ?? 0 }}
                  <span class="text-[13px] text-hint font-sans font-normal">份</span>
                </div>
                <div class="mt-1 text-[11px] text-hint">位于后端 backups/ 目录</div>
              </div>
              <div class="rounded-card bg-page border border-line p-4 flex flex-col justify-center">
                <el-button
                  type="primary"
                  :icon="'Download'"
                  :loading="backupDownloading"
                  @click="onBackupDownload"
                >立即备份并下载</el-button>
                <div class="mt-2 text-[11px] text-hint text-center">在线一致性快照，不影响业务运行</div>
              </div>
            </div>

            <!-- 各表记录数 -->
            <div v-if="backupInfo" class="mb-5">
              <div class="text-[13px] font-medium text-ink mb-2">数据表记录数</div>
              <div class="flex flex-wrap gap-2">
                <el-tag
                  v-for="(count, table) in backupInfo.tableCounts"
                  :key="table"
                  effect="plain"
                  class="!mr-0"
                >
                  {{ table }}：{{ count }} 行
                </el-tag>
              </div>
            </div>

            <!-- 存档列表 -->
            <div class="flex items-center justify-between mb-3">
              <span class="text-[13px] font-medium text-ink">服务器存档备份</span>
              <el-button text :icon="'Refresh'" @click="fetchBackupData">刷新</el-button>
            </div>
            <el-table :data="backupFiles" class="w-full" max-height="360">
              <el-table-column prop="filename" label="备份文件名" min-width="280">
                <template #default="{ row }">
                  <span class="font-num text-[13px]">{{ row.filename }}</span>
                </template>
              </el-table-column>
              <el-table-column label="大小" width="120">
                <template #default="{ row }">{{ formatSize(row.size) }}</template>
              </el-table-column>
              <el-table-column prop="createdAt" label="生成时间" width="180" />
              <el-table-column label="操作" width="100" fixed="right">
                <template #default="{ row }">
                  <el-button link type="danger" @click="onBackupRestore(row)">恢复</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="backupFiles.length === 0" class="py-8 text-center text-xs text-hint">
              暂无存档备份，点击「立即备份并下载」生成第一份
            </div>
          </div>
        </el-tab-pane>

        <!-- ============ Tab6 登录日志（安全审计） ============ -->
        <el-tab-pane name="login-logs">
          <template #label>
            <span>登录日志</span>
            <el-badge
              v-if="failCount7d > 0"
              :value="failCount7d"
              :max="99"
              class="!ml-1.5"
              type="danger"
            />
          </template>

          <el-alert
            v-if="failCount7d > 0"
            class="mb-3"
            type="error"
            :closable="false"
            show-icon
          >
            <template #title>
              <span class="text-[13px]">近 7 天共检测到 <b>{{ failCount7d }}</b> 次登录失败（含验证码错误/密码错误/禁用账号），请关注是否存在暴力破解尝试</span>
            </template>
          </el-alert>

          <div class="flex items-center gap-3 mb-4">
            <el-input
              v-model="loginLogQuery.keyword"
              placeholder="搜索账号 / 姓名"
              clearable
              class="!w-56"
              :prefix-icon="'Search'"
              @keyup.enter="searchLoginLogs"
              @clear="searchLoginLogs"
            />
            <el-select v-model="loginLogQuery.result" placeholder="全部结果" clearable class="!w-36" @change="searchLoginLogs">
              <el-option label="登录成功" value="success" />
              <el-option label="登录失败" value="fail" />
            </el-select>
            <el-button type="primary" :icon="'Search'" @click="searchLoginLogs">查询</el-button>
          </div>

          <el-table :data="loginLogs" v-loading="loginLogsLoading" class="w-full" size="default">
            <el-table-column label="登录时间" width="170">
              <template #default="{ row }">
                <span class="font-num text-[13px]">{{ row.loginAt }}</span>
              </template>
            </el-table-column>
            <el-table-column label="账号" prop="username" width="130">
              <template #default="{ row }">
                <div class="leading-tight">
                  <div class="text-[13px] font-num">{{ row.username }}</div>
                  <div class="text-[11px] text-hint">{{ row.realName || '—' }}</div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="结果" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="row.success ? 'success' : 'danger'" size="small" effect="light" round>
                  {{ row.success ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="失败原因" width="130">
              <template #default="{ row }">
                <span :class="row.success ? 'text-hint' : 'text-[#C94F4F]'" class="text-[12px]">
                  {{ row.success ? '—' : row.failReason }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="IP 地址" width="140">
              <template #default="{ row }">
                <span class="font-num text-[12px]">{{ row.ip || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="登录设备" min-width="200">
              <template #default="{ row }">
                <span class="text-[12px] text-sub" :title="row.userAgent">{{ parseLoginUA(row.userAgent) }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无登录日志" :image-size="80" />
            </template>
          </el-table>

          <div class="flex justify-end mt-4">
            <el-pagination
              v-model:current-page="loginLogQuery.page"
              v-model:page-size="loginLogQuery.pageSize"
              :total="loginLogsTotal"
              :page-sizes="[15, 30, 50]"
              layout="total, sizes, prev, pager, next"
              background
              @current-change="fetchLoginLogs"
              @size-change="searchLoginLogs"
            />
          </div>
        </el-tab-pane>

        <!-- ============ Tab7 运行审计看板 ============ -->
        <el-tab-pane label="运行审计" name="audit">
          <div v-loading="auditLoading" class="pt-2">
            <div class="flex items-center justify-between mb-4">
              <div class="text-[13px] text-hint">基于登录日志、病例操作、系统操作与 AI 推理日志聚合，仅统计近 {{ auditDays }} 天数据</div>
              <el-radio-group :model-value="auditDays" size="small" @change="changeAuditDays">
                <el-radio-button :value="7">近 7 天</el-radio-button>
                <el-radio-button :value="30">近 30 天</el-radio-button>
                <el-radio-button :value="90">近 90 天</el-radio-button>
              </el-radio-group>
            </div>

            <!-- 指标卡 -->
            <div class="grid grid-cols-8 gap-3 mb-4">
              <div v-for="card in auditStatCards" :key="card.label" class="border border-line rounded-xl px-3 py-3 bg-white">
                <div class="text-[11.5px] text-hint">{{ card.label }}</div>
                <div class="mt-1.5 font-num text-[20px] leading-none font-semibold" :style="{ color: card.color }">
                  {{ card.value }}<span class="text-[11px] text-hint font-sans font-normal ml-0.5">{{ card.unit }}</span>
                </div>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
              <div class="border border-line rounded-xl bg-white p-3">
                <div class="text-[13px] font-medium text-ink px-1 pb-1">登录趋势（成功 / 失败）</div>
                <ChartBase :option="loginTrendOption" :height="270" />
              </div>
              <div class="border border-line rounded-xl bg-white p-3">
                <div class="text-[13px] font-medium text-ink px-1 pb-1">活跃用户 Top 8</div>
                <ChartBase :option="activeUsersOption" :height="270" />
              </div>
              <div class="border border-line rounded-xl bg-white p-3">
                <div class="text-[13px] font-medium text-ink px-1 pb-1">病例操作类型分布</div>
                <ChartBase :option="caseActionsOption" :height="270" :empty="(audit?.caseActions.length ?? 0) === 0" />
              </div>
              <div class="border border-line rounded-xl bg-white p-3">
                <div class="text-[13px] font-medium text-ink px-1 pb-1">失败登录时段分布（24 小时）</div>
                <ChartBase :option="failHoursOption" :height="270" />
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 发布系统公告 -->
    <el-dialog v-model="announceVisible" title="发布系统公告" width="520px">
      <el-form label-width="80px">
        <el-form-item label="公告标题" required>
          <el-input v-model="announceForm.title" maxlength="50" show-word-limit placeholder="一句话概括公告内容" />
        </el-form-item>
        <el-form-item label="公告内容" required>
          <el-input
            v-model="announceForm.content"
            type="textarea"
            :rows="5"
            maxlength="500"
            show-word-limit
            placeholder="填写公告详情，将展示在每位用户的通知中心"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="announceVisible = false">取消</el-button>
        <el-button type="primary" :loading="announcePublishing" @click="submitAnnounce">确认发布</el-button>
      </template>
    </el-dialog>

    <!-- ==================== 弹窗组 ==================== -->
    <!-- 新增用户 -->
    <el-dialog v-model="addVisible" title="新增系统用户" width="520px">
      <el-form ref="addFormRef" :model="addForm" :rules="addRules" label-width="90px">
        <div class="grid grid-cols-2 gap-x-5">
          <el-form-item label="登录账号" prop="username">
            <el-input v-model="addForm.username" placeholder="字母 / 数字 / 下划线" />
          </el-form-item>
          <el-form-item label="真实姓名" prop="realName">
            <el-input v-model="addForm.realName" />
          </el-form-item>
          <el-form-item label="系统角色" prop="role">
            <el-select v-model="addForm.role" class="!w-full">
              <el-option v-for="r in ROLE_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="所属科室" prop="department">
            <el-input v-model="addForm.department" />
          </el-form-item>
          <el-form-item label="手机号" prop="phone">
            <el-input v-model="addForm.phone" maxlength="11" />
          </el-form-item>
          <el-form-item label="初始密码" prop="password">
            <el-input v-model="addForm.password" type="password" show-password />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="addLoading" @click="submitAdd">创建账号</el-button>
      </template>
    </el-dialog>

    <!-- 禁用/启用确认 -->
    <ConfirmDialog
      v-model="toggleConfirmVisible"
      :title="toggleTarget?.status === 1 ? '禁用用户账号' : '启用用户账号'"
      :type="toggleTarget?.status === 1 ? 'danger' : 'info'"
      :content="
        toggleTarget?.status === 1
          ? `禁用后账号 ${toggleTarget?.username} 将无法登录本系统，已登录会话将被终止。是否确认禁用？`
          : `启用后账号 ${toggleTarget?.username} 可重新登录本系统。是否确认启用？`
      "
      :confirm-text="toggleTarget?.status === 1 ? '确认禁用' : '确认启用'"
      @confirm="doToggle"
    />

    <!-- 注册账号审核确认 -->
    <ConfirmDialog
      v-model="reviewConfirmVisible"
      :title="reviewAction === 'approve' ? '通过注册申请' : '拒绝注册申请'"
      :type="reviewAction === 'approve' ? 'info' : 'danger'"
      :content="
        reviewAction === 'approve'
          ? `通过后账号 ${reviewTarget?.username}（${reviewTarget?.realName} / ${reviewTarget?.roleName}）将激活为启用状态，可正常登录。是否确认通过？`
          : `拒绝后账号 ${reviewTarget?.username}（${reviewTarget?.realName}）将置为禁用状态，无法登录本系统。如需恢复需管理员重新启用。是否确认拒绝？`
      "
      :confirm-text="reviewAction === 'approve' ? '确认通过' : '确认拒绝'"
      @confirm="doReview"
    />
  </div>
</template>
