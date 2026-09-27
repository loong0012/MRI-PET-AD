<script setup lang="ts">
/**
 * 个人中心
 * ------------------------------------------------------------------
 * 1. 左侧档案卡：头像 / 姓名 / 角色 / 科室 / 职称 / 个人简介
 * 2. 右侧 Tab：基本资料编辑 / 账号安全（改密）/ 工作统计 / 活动记录
 *    - 资料保存后同步顶栏头像与姓名（userStore.patchUser + 新 token）
 *    - 改密成功后强制重新登录（医疗系统安全策略）
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { apiMyLoginLogs, type LoginLogItem } from '@/api/loginLog'
import {
  apiGetProfile, apiUpdateProfile, apiChangePassword,
  apiGetProfileStats, apiGetProfileActivities
} from '@/api/profile'
import { setToken } from '@/utils/auth'
import type { UserProfile, ProfileStats, ActivityItem } from '@/types/user'

const router = useRouter()
const userStore = useUserStore()

// ---------- 档案数据 ----------
const profile = ref<UserProfile | null>(null)
const stats = ref<ProfileStats | null>(null)
const activities = ref<ActivityItem[]>([])
const loading = ref(false)
const activeTab = ref('info')

// ---------- 登录记录（切到 tab 时懒加载） ----------
const loginLogs = ref<LoginLogItem[]>([])
const loginLogsLoaded = ref(false)
const loginLogsLoading = ref(false)

/** 从 User-Agent 提取浏览器/操作系统简描 */
function parseUA(ua: string): string {
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

async function loadLoginLogs(): Promise<void> {
  if (loginLogsLoaded.value) return
  loginLogsLoading.value = true
  try {
    loginLogs.value = await apiMyLoginLogs(20)
    loginLogsLoaded.value = true
  } catch {
    /* 拦截器已提示 */
  } finally {
    loginLogsLoading.value = false
  }
}

watch(activeTab, (v) => {
  if (v === 'login-log') loadLoginLogs()
})

/** 角色徽章配色（与系统管理页保持一致） */
const roleBadge = computed(() => {
  const map: Record<string, { text: string; cls: string }> = {
    admin: { text: '超级管理员', cls: 'role-admin' },
    neurologist: { text: '神经内科医师', cls: 'role-neuro' },
    radiologist: { text: '放射科医师', cls: 'role-radio' },
    researcher: { text: '科研人员', cls: 'role-research' }
  }
  const r = profile.value?.role ?? userStore.role
  return map[r] ?? { text: r, cls: 'role-radio' }
})

// ---------- 资料编辑表单 ----------
const formRef = ref<FormInstance>()
const form = reactive({
  realName: '',
  phone: '',
  email: '',
  department: '',
  title: '',
  signature: '',
  avatar: ''
})
const saving = ref(false)

const rules: FormRules = {
  realName: [{ required: true, message: '请填写真实姓名', trigger: 'blur' }],
  email: [
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' }
  ],
  phone: [
    { pattern: /^1[3-9]\d{9}$|^$/, message: '手机号格式不正确', trigger: 'blur' }
  ]
}

function syncForm(p: UserProfile): void {
  form.realName = p.realName
  form.phone = p.phone
  form.email = p.email
  form.department = p.department
  form.title = p.title
  form.signature = p.signature
  form.avatar = p.avatar
}

async function onSaveProfile(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  saving.value = true
  try {
    const res = await apiUpdateProfile({
      realName: form.realName,
      phone: form.phone,
      email: form.email,
      department: form.department,
      title: form.title,
      signature: form.signature,
      avatar: form.avatar
    })
    profile.value = res.profile
    // 同步顶栏：更新 token + 用户信息
    if (res.token) {
      setToken(res.token)
      userStore.token = res.token
    }
    userStore.patchUser({
      realName: res.profile.realName,
      department: res.profile.department,
      title: res.profile.title,
      avatar: res.profile.avatar,
      phone: res.profile.phone,
      email: res.profile.email,
      signature: res.profile.signature
    })
    ElMessage.success('资料已保存')
  } catch {
    /* 错误已由拦截器提示 */
  } finally {
    saving.value = false
  }
}

// ---------- 头像上传（本地裁剪为 base64，限制 200KB） ----------
const avatarFile = ref<HTMLInputElement | null>(null)
function onPickAvatar(): void {
  avatarFile.value?.click()
}
function onAvatarChange(e: Event): void {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('请选择图片文件')
    return
  }
  if (file.size > 2 * 1024 * 1024) {
    ElMessage.warning('图片大小不能超过 2MB')
    return
  }
  const reader = new FileReader()
  reader.onload = () => {
    const img = new Image()
    img.onload = () => {
      // 裁剪为 256x256 正方形并压缩
      const size = Math.min(img.width, img.height)
      const canvas = document.createElement('canvas')
      canvas.width = 256
      canvas.height = 256
      const ctx = canvas.getContext('2d')!
      ctx.drawImage(img, (img.width - size) / 2, (img.height - size) / 2, size, size, 0, 0, 256, 256)
      form.avatar = canvas.toDataURL('image/jpeg', 0.85)
    }
    img.src = reader.result as string
  }
  reader.readAsDataURL(file)
  input.value = ''
}

// ---------- 修改密码 ----------
const pwdRef = ref<FormInstance>()
const pwdForm = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })
const pwdRules: FormRules = {
  oldPassword: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  newPassword: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, max: 20, message: '密码长度 6-20 位', trigger: 'blur' }
  ],
  confirmPassword: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_r, v, cb) => {
        if (v !== pwdForm.newPassword) cb(new Error('两次输入的新密码不一致'))
        else cb()
      },
      trigger: 'blur'
    }
  ]
}
const pwdSaving = ref(false)

async function onSavePassword(): Promise<void> {
  if (!pwdRef.value) return
  try {
    await pwdRef.value.validate()
  } catch {
    return
  }
  pwdSaving.value = true
  try {
    await apiChangePassword({
      oldPassword: pwdForm.oldPassword,
      newPassword: pwdForm.newPassword
    })
    await ElMessageBox.alert('密码已修改，请重新登录', '安全提示', { type: 'success' })
    userStore.logout()
    router.replace('/login')
  } catch {
    /* 拦截器已提示 */
  } finally {
    pwdSaving.value = false
  }
}

// ---------- 工作统计卡片 ----------
const statCards = computed(() => [
  { label: '参与病例', value: stats.value?.totalCases ?? 0, icon: 'Notebook', color: '#2F6DA3' },
  { label: '完成筛查', value: stats.value?.completedCases ?? 0, icon: 'CircleCheck', color: '#2E9E6B' },
  { label: 'AI 分析次数', value: stats.value?.inferenceCount ?? 0, icon: 'Cpu', color: '#D99A2B' },
  { label: '出具报告', value: stats.value?.reportedCount ?? 0, icon: 'Document', color: '#8E6BB3' },
  { label: '累计登录', value: stats.value?.loginCount ?? 0, icon: 'Key', color: '#5C6B7A' }
])

// ---------- 加载 ----------
async function loadAll(): Promise<void> {
  loading.value = true
  try {
    const [p, s, a] = await Promise.all([
      apiGetProfile(),
      apiGetProfileStats(),
      apiGetProfileActivities()
    ])
    profile.value = p
    stats.value = s
    activities.value = a
    syncForm(p)
    // 同步顶栏头像/职称等扩展字段
    userStore.patchUser({
      avatar: p.avatar, title: p.title, signature: p.signature,
      phone: p.phone, email: p.email
    })
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="profile-page p-6" v-loading="loading">
    <div class="flex gap-6">
      <!-- ==================== 左侧档案卡 ==================== -->
      <aside class="w-72 shrink-0">
        <div class="bg-white rounded-xl border border-line shadow-sm overflow-hidden">
          <div class="profile-cover" />
          <div class="px-5 pb-5 -mt-12">
            <div class="flex justify-center">
              <el-avatar :size="92" :src="profile?.avatar" class="profile-avatar border-4 border-white shadow-lg">
                {{ profile?.realName?.slice(0, 1) || userStore.realName.slice(0, 1) }}
              </el-avatar>
            </div>
            <div class="text-center mt-3">
              <div class="text-lg font-semibold text-ink">{{ profile?.realName || userStore.realName }}</div>
              <div class="mt-1">
                <span class="inline-block px-2 py-0.5 rounded text-[11px] font-medium text-white" :class="roleBadge.cls">
                  {{ profile?.roleName || userStore.roleName }}
                </span>
              </div>
              <div class="text-sm text-hint mt-1.5">
                {{ profile?.department || userStore.userInfo?.department || '—' }}
                <span v-if="profile?.title"> · {{ profile.title }}</span>
              </div>
              <p v-if="profile?.signature" class="text-xs text-hint mt-2 leading-5 px-2">
                {{ profile.signature }}
              </p>
              <p v-else class="text-xs text-hint/60 mt-2 italic px-2">
                在「基本资料」中添加个人简介与临床专长
              </p>
            </div>

            <el-divider class="my-4" />

            <div class="space-y-2.5 text-sm">
              <div class="flex items-center gap-2 text-hint">
                <el-icon><User /></el-icon>
                <span class="text-ink/80">{{ profile?.username }}</span>
              </div>
              <div class="flex items-center gap-2 text-hint">
                <el-icon><Phone /></el-icon>
                <span class="text-ink/80">{{ profile?.phone || '—' }}</span>
              </div>
              <div class="flex items-center gap-2 text-hint">
                <el-icon><Message /></el-icon>
                <span class="text-ink/80 truncate">{{ profile?.email || '—' }}</span>
              </div>
              <div class="flex items-center gap-2 text-hint">
                <el-icon><Clock /></el-icon>
                <span class="text-ink/80">最近登录 {{ profile?.lastLoginTime || '—' }}</span>
              </div>
            </div>
          </div>
        </div>
      </aside>

      <!-- ==================== 右侧 Tab 区 ==================== -->
      <section class="flex-1 min-w-0">
        <div class="bg-white rounded-xl border border-line shadow-sm">
          <el-tabs v-model="activeTab" class="profile-tabs">
            <!-- 基本资料 -->
            <el-tab-pane label="基本资料" name="info">
              <el-form
                ref="formRef"
                :model="form"
                :rules="rules"
                label-width="90px"
                label-position="right"
                class="px-6 py-5 max-w-2xl"
              >
                <el-form-item label="头像">
                  <div class="flex items-center gap-4">
                    <el-avatar :size="64" :src="form.avatar" class="bg-primary-light text-primary text-lg">
                      {{ form.realName?.slice(0, 1) }}
                    </el-avatar>
                    <div>
                      <el-button @click="onPickAvatar">更换头像</el-button>
                      <div class="text-xs text-hint mt-1">支持 JPG/PNG，将自动裁剪为 256×256</div>
                      <input ref="avatarFile" type="file" accept="image/*" class="hidden" @change="onAvatarChange" />
                    </div>
                  </div>
                </el-form-item>
                <el-form-item label="真实姓名" prop="realName">
                  <el-input v-model="form.realName" maxlength="20" show-word-limit />
                </el-form-item>
                <el-form-item label="手机号" prop="phone">
                  <el-input v-model="form.phone" placeholder="11 位手机号" maxlength="11" />
                </el-form-item>
                <el-form-item label="邮箱" prop="email">
                  <el-input v-model="form.email" placeholder="用于报告推送与协作通知" />
                </el-form-item>
                <el-form-item label="科室">
                  <el-input v-model="form.department" placeholder="如：放射科 / 神经内科" />
                </el-form-item>
                <el-form-item label="职称">
                  <el-input v-model="form.title" placeholder="如：主任医师 / 主治医师" />
                </el-form-item>
                <el-form-item label="个人简介">
                  <el-input
                    v-model="form.signature"
                    type="textarea"
                    :rows="3"
                    maxlength="120"
                    show-word-limit
                    placeholder="临床专长、研究方向等（最多 120 字）"
                  />
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" :loading="saving" @click="onSaveProfile">保存资料</el-button>
                </el-form-item>
              </el-form>
            </el-tab-pane>

            <!-- 账号安全 -->
            <el-tab-pane label="账号安全" name="security">
              <el-form
                ref="pwdRef"
                :model="pwdForm"
                :rules="pwdRules"
                label-width="100px"
                label-position="right"
                class="px-6 py-5 max-w-xl"
              >
                <el-alert
                  type="info"
                  :closable="false"
                  class="mb-5"
                  title="医疗系统安全策略：建议每 90 天更换一次密码；密码不得与原密码相同，修改成功后需重新登录。"
                />
                <el-form-item label="原密码" prop="oldPassword">
                  <el-input v-model="pwdForm.oldPassword" type="password" show-password />
                </el-form-item>
                <el-form-item label="新密码" prop="newPassword">
                  <el-input v-model="pwdForm.newPassword" type="password" show-password placeholder="6-20 位" />
                </el-form-item>
                <el-form-item label="确认新密码" prop="confirmPassword">
                  <el-input v-model="pwdForm.confirmPassword" type="password" show-password />
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" :loading="pwdSaving" @click="onSavePassword">修改密码</el-button>
                </el-form-item>
              </el-form>
            </el-tab-pane>

            <!-- 工作统计 -->
            <el-tab-pane label="工作统计" name="stats">
              <div class="px-6 py-5">
                <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
                  <div v-for="c in statCards" :key="c.label" class="stat-card">
                    <el-icon :size="22" :color="c.color"><component :is="c.icon" /></el-icon>
                    <div class="text-2xl font-bold text-ink mt-1 tabular-nums">{{ c.value }}</div>
                    <div class="text-xs text-hint mt-0.5">{{ c.label }}</div>
                  </div>
                </div>
                <el-divider />
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                  <div class="flex justify-between py-2 border-b border-line">
                    <span class="text-hint">注册时间</span>
                    <span class="text-ink">{{ stats?.registerTime || '—' }}</span>
                  </div>
                  <div class="flex justify-between py-2 border-b border-line">
                    <span class="text-hint">最近登录</span>
                    <span class="text-ink">{{ stats?.lastLoginTime || '—' }}</span>
                  </div>
                </div>
              </div>
            </el-tab-pane>

            <!-- 活动记录 -->
            <el-tab-pane label="活动记录" name="activity">
              <div class="px-6 py-5">
                <el-timeline v-if="activities.length" class="activity-timeline">
                  <el-timeline-item
                    v-for="(a, i) in activities"
                    :key="i"
                    :timestamp="a.time"
                    placement="top"
                    :color="a.module === 'AI 分析' ? '#D99A2B' : '#2F6DA3'"
                  >
                    <div class="text-[13px] text-ink font-medium">{{ a.action }}</div>
                    <div class="text-xs text-hint mt-0.5">{{ a.module }} · {{ a.detail }}</div>
                  </el-timeline-item>
                </el-timeline>
                <el-empty v-else description="暂无活动记录" :image-size="80" />
              </div>
            </el-tab-pane>

            <el-tab-pane label="登录记录" name="login-log">
              <div class="px-6 py-5" v-loading="loginLogsLoading">
                <el-table :data="loginLogs" size="default" class="w-full">
                  <el-table-column label="登录时间" prop="loginAt" width="170">
                    <template #default="{ row }">
                      <span class="font-num text-[13px]">{{ row.loginAt }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="结果" width="90" align="center">
                    <template #default="{ row }">
                      <el-tag :type="row.success ? 'success' : 'danger'" size="small" effect="light" round>
                        {{ row.success ? '成功' : '失败' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="IP 地址" prop="ip" width="140">
                    <template #default="{ row }">
                      <span class="font-num text-[12px]">{{ row.ip || '—' }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="登录设备" min-width="180">
                    <template #default="{ row }">
                      <span class="text-[12px] text-sub">{{ parseUA(row.userAgent) }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="失败原因" min-width="130">
                    <template #default="{ row }">
                      <span :class="row.success ? 'text-hint' : 'text-[#C94F4F]'" class="text-[12px]">
                        {{ row.success ? '—' : row.failReason }}
                      </span>
                    </template>
                  </el-table-column>
                  <template #empty>
                    <el-empty description="暂无登录记录" :image-size="80" />
                  </template>
                </el-table>
                <div class="mt-3 text-[12px] text-hint">仅展示最近 20 条登录记录；如发现非本人操作的登录，请立即修改密码并联系管理员。</div>
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.profile-page { min-height: 100%; }
.profile-cover {
  height: 96px;
  background: linear-gradient(135deg, #2f6da3 0%, #4a8cc7 60%, #6fa8d6 100%);
}
.profile-avatar {
  background: linear-gradient(135deg, #2f6da3, #4a8cc7);
  color: #fff;
  font-size: 36px;
  font-weight: 600;
}
.role-admin { background: #C94F4F; }
.role-neuro { background: #8E6BB3; }
.role-radio { background: #2F6DA3; }
.role-research { background: #2E9E6B; }
.profile-tabs :deep(.el-tabs__header) { margin: 0 24px; }
.profile-tabs :deep(.el-tabs__content) { padding: 0; }
.stat-card {
  background: #F7FAFD;
  border: 1px solid #E6EEF5;
  border-radius: 10px;
  padding: 16px;
  transition: box-shadow 0.2s;
}
.stat-card:hover { box-shadow: 0 4px 14px rgba(47, 109, 163, 0.12); }
.activity-timeline :deep(.el-timeline-item__timestamp) { color: #94A3B4; font-size: 11px; }
</style>
