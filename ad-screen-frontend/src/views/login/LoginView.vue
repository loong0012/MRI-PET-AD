<script setup lang="ts">
/**
 * 登录 / 注册页
 * ------------------------------------------------------------------
 * 简约医用居中卡片布局：
 *  - 登录：账号 + 密码 + 图形验证码 + 登录；支持四类演示角色身份卡一键填充（角色由账号自动识别）
 *  - 注册：用户类型（放射科医师 / 神经内科医师 / 科研管理员）+ 姓名 + 用户名 + 密码 + 确认密码 + 科室 + 手机号 + 图形验证码
 * 底部展示系统版本号与隐私协议入口，样式克制、无花哨动效。
 */
import { onMounted, reactive, ref, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElNotification } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { apiDemoAccounts, apiGetCaptcha, apiRegister } from '@/api/auth'
import { drawCaptcha, drawCaptchaImage } from '@/utils/captcha'
import type { DemoAccount, RegisterPayload, RegisterableRole, RoleKey } from '@/types/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

// ---------- 卡片切换：登录 / 注册 ----------
type Mode = 'login' | 'register'
const mode = ref<Mode>('login')

function switchTo(m: Mode): void {
  mode.value = m
  // 切换后重置目标表单的验证状态与验证码
  registerFormRef.value?.resetFields()
  loginFormRef.value?.resetFields()
  nextTick(() => refreshCaptcha())
}

// ---------- 登录表单 ----------
const loginFormRef = ref<FormInstance | null>(null)
const loading = ref(false)
const form = reactive({
  username: '',
  password: '',
  captcha: ''
})
const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  captcha: [{ required: true, message: '请输入验证码', trigger: 'blur' }]
}

// ---------- 注册表单 ----------
const registerFormRef = ref<FormInstance | null>(null)
const registerLoading = ref(false)
// confirmPassword 仅用于前端二次校验，不提交后端（提交时剔除）。
// 注意：必须放在 registerForm 内——el-form 的 prop 校验从 :model 取值，
// 放在独立 ref 里会导致 el-form 读到 undefined，校验永远失败且无任何请求发出
const registerForm = reactive<RegisterPayload & { confirmPassword: string }>({
  username: '',
  password: '',
  realName: '',
  role: 'radiologist', // 默认放射科医师，可在下方三类业务角色中选择
  department: '',
  phone: '',
  captcha: '',
  captchaKey: '',
  confirmPassword: ''
})
const registerRules: FormRules = {
  realName: [
    { required: true, message: '请填写真实姓名', trigger: 'blur' },
    { min: 2, max: 20, message: '姓名长度 2-20 位', trigger: 'blur' }
  ],
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度 3-20 位', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_]+$/, message: '仅支持字母、数字、下划线', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 20, message: '密码长度 6-20 位', trigger: 'blur' }
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule: unknown, value: string, callback: (err?: Error) => void) => {
        if (value !== registerForm.password) callback(new Error('两次密码不一致'))
        else callback()
      },
      trigger: 'blur'
    }
  ],
  captcha: [{ required: true, message: '请输入验证码', trigger: 'blur' }]
}
// ---------- 验证码 ----------
// 登录/注册两个表单各有一个 canvas，必须使用独立 ref
// （同名 ref 会被后者覆盖，导致验证码实际绘制到隐藏的另一张表单上）
const loginCaptchaCanvas = ref<HTMLCanvasElement | null>(null)
const registerCaptchaCanvas = ref<HTMLCanvasElement | null>(null)
const captchaKey = ref('')
/** 真实后端模式下验证码服务是否不可用：不可用时禁止提交（本地验证码后端无法校验，提交必然失败死循环） */
const captchaFailed = ref(false)

/** 在 canvas 上绘制"验证码不可用"占位提示（失败态，点击重试） */
function drawCaptchaUnavailable(canvas: HTMLCanvasElement): void {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.fillStyle = '#FBE9E7'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.fillStyle = '#C94F4F'
  ctx.font = '600 12px sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText('点击重试', canvas.width / 2, canvas.height / 2)
}

/** 刷新图形验证码（Mock 模式本地生成，真实模式从后端获取服务端渲染图片） */
async function refreshCaptcha(): Promise<void> {
  const canvas = mode.value === 'login' ? loginCaptchaCanvas.value : registerCaptchaCanvas.value
  if (!canvas) return
  if (useMock) {
    // Mock 模式：本地生成验证码
    captchaFailed.value = false
    captchaKey.value = drawCaptcha(canvas)
  } else {
    // 真实后端模式：获取服务端渲染的 PNG（明文 code 不下发），绘制到 canvas
    try {
      const res = await apiGetCaptcha()
      captchaKey.value = res.captchaKey
      await drawCaptchaImage(canvas, res.image)
      captchaFailed.value = false
    } catch {
      // 不降级本地码：本地验证码后端无法校验，会造成"登录必然失败"死循环
      captchaKey.value = ''
      captchaFailed.value = true
      drawCaptchaUnavailable(canvas)
    }
  }
}

// ---------- 演示账号快捷填充 ----------
const demoAccounts = ref<DemoAccount[]>([])
const activeDemo = ref<string>('')

// ---------- 用户类型（角色） ----------
/** 角色展示元数据：注册角色卡片与登录页演示账号身份卡共用 */
interface RoleMeta {
  /** 角色中文名（与后端 ROLE_PERMISSIONS 文案一致） */
  name: string
  icon: string
  /** 职责简述（注册卡片辅助说明） */
  desc: string
  /** 图标底色 / 前景色（低饱和角色区分色，类名完整保留供 Tailwind 扫描） */
  toneBg: string
  toneFg: string
}
const ROLE_META: Record<RoleKey, RoleMeta> = {
  radiologist: { name: '放射科医师', icon: 'View', desc: '影像阅片 · AI 审核', toneBg: 'bg-[#eaf2fa]', toneFg: 'text-[#2f6da3]' },
  neurologist: { name: '神经内科医师', icon: 'FirstAidKit', desc: '临床诊断 · 干预随访', toneBg: 'bg-[#e5f4f2]', toneFg: 'text-[#0d8a7f]' },
  researcher: { name: '科研管理员', icon: 'DataAnalysis', desc: '数据分析 · 模型研究', toneBg: 'bg-[#efedf8]', toneFg: 'text-[#5b54a4]' },
  admin: { name: '超级管理员', icon: 'UserFilled', desc: '平台治理 · 权限审计', toneBg: 'bg-[#edf0f4]', toneFg: 'text-[#52616f]' }
}
/** 允许自助注册的用户类型（管理员不开放自助注册，仅能由既有管理员在系统管理中创建） */
const REGISTER_ROLES: readonly RegisterableRole[] = ['radiologist', 'neurologist', 'researcher']

/** 左侧品牌面板能力点（图标 + 短标题，弱化文字权重） */
const features = [
  { icon: 'DataAnalysis', title: '多模态融合' },
  { icon: 'View', title: '3D 重建' },
  { icon: 'FirstAidKit', title: '指南对齐' },
  { icon: 'Lock', title: '留痕审核' }
]

/** 填充演示账号（点击角色芯片） */
function fillDemo(acc: DemoAccount): void {
  form.username = acc.username
  form.password = acc.password
  activeDemo.value = acc.username
}

// ---------- 登录提交 ----------
async function handleLogin(): Promise<void> {
  // 在途锁：表单回车连点时，validate() 异步窗口内可能重入；验证码 key 单次有效，重复请求必然失败
  if (loading.value) return
  const valid = await loginFormRef.value?.validate().catch(() => false)
  if (!valid) return
  if (loading.value) return
  // 真实后端且验证码服务不可用时禁止提交（否则后端校验必然失败）
  if (!useMock && (captchaFailed.value || !captchaKey.value)) {
    ElMessage.warning('验证码不可用，请点击验证码图片刷新后重试')
    void refreshCaptcha()
    return
  }
  loading.value = true
  try {
    await userStore.login({ ...form, captchaKey: captchaKey.value })
    ElNotification.success({
      title: '登录成功',
      message: `欢迎，${userStore.realName}（${userStore.roleName}）`,
      duration: 2500
    })
    // 登录后回跳来源页（路由守卫记录），默认工作台
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.replace(redirect)
  } catch (e) {
    // 请求拦截器已统一提示过的错误（错误码/网络异常）不再重复弹 toast
    if (!(e as { __handled?: boolean } | null)?.__handled) {
      ElMessage.error(e instanceof Error ? e.message : '登录失败，请重试')
    }
    refreshCaptcha()
    form.captcha = ''
  } finally {
    loading.value = false
  }
}

// ---------- 注册提交 ----------
async function handleRegister(): Promise<void> {
  // 在途锁：与登录同理，防止回车连点发出重复注册请求
  if (registerLoading.value) return
  const valid = await registerFormRef.value?.validate().catch(() => false)
  if (!valid) return
  if (registerLoading.value) return
  if (!useMock && (captchaFailed.value || !captchaKey.value)) {
    ElMessage.warning('验证码不可用，请点击验证码图片刷新后重试')
    void refreshCaptcha()
    return
  }
  registerLoading.value = true
  // 先记录申请身份：注册成功后 switchTo 会 resetFields，role 将回到默认值
  const appliedRoleName = ROLE_META[registerForm.role].name
  try {
    // confirmPassword 仅前端校验用，不提交
    const { confirmPassword: _omit, ...payload } = { ...registerForm, captchaKey: captchaKey.value }
    await apiRegister(payload)
    ElNotification.success({
      title: '注册成功',
      message: `账号已以「${appliedRoleName}」身份提交，待管理员审核启用后方可登录`,
      duration: 3500
    })
    // 注册成功后自动切回登录视图并预填用户名。
    // switchTo 内 resetFields 会清空 registerForm，必须在切换前用局部变量保存用户名
    registerForm.confirmPassword = ''
    const registeredUsername = registerForm.username
    switchTo('login')
    form.username = registeredUsername
  } catch (e) {
    if (!(e as { __handled?: boolean } | null)?.__handled) {
      ElMessage.error(e instanceof Error ? e.message : '注册失败，请重试')
    }
    refreshCaptcha()
    registerForm.captcha = ''
  } finally {
    registerLoading.value = false
  }
}

onMounted(() => {
  demoAccounts.value = apiDemoAccounts()
  refreshCaptcha()
})
</script>

<template>
  <div class="login-page h-screen flex">
    <!-- ==================== 左侧品牌面板 ==================== -->
    <aside class="brand-panel hidden lg:flex flex-col w-[50%] p-10 text-white relative overflow-hidden">
      <!-- 装饰光斑 + 网格 -->
      <div class="brand-glow brand-glow--1" />
      <div class="brand-glow brand-glow--2" />
      <div class="brand-glow brand-glow--3" />
      <div class="brand-grid" />

      <!-- 大脑 3D 医学渲染主视觉 -->
      <div class="brain-3d-wrapper absolute inset-0 flex items-center justify-center pointer-events-none z-0">
        <svg class="brain-3d" viewBox="0 0 360 360" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <defs>
            <radialGradient id="brainBody" cx="50%" cy="42%" r="62%">
              <stop offset="0%" stop-color="rgba(155,220,255,0.30)" />
              <stop offset="48%" stop-color="rgba(95,165,220,0.16)" />
              <stop offset="100%" stop-color="rgba(36,86,127,0.04)" />
            </radialGradient>
            <radialGradient id="brainDepth" cx="48%" cy="40%" r="52%">
              <stop offset="0%" stop-color="rgba(155,220,255,0.13)" />
              <stop offset="100%" stop-color="rgba(155,220,255,0)" />
            </radialGradient>
            <linearGradient id="sulcusStroke" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="rgba(155,220,255,0.68)" />
              <stop offset="100%" stop-color="rgba(125,184,255,0.10)" />
            </linearGradient>
            <linearGradient id="scanRing" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="rgba(155,220,255,0.0)" />
              <stop offset="50%" stop-color="rgba(155,220,255,0.35)" />
              <stop offset="100%" stop-color="rgba(155,220,255,0.0)" />
            </linearGradient>
            <radialGradient id="petHeat" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stop-color="rgba(255,190,120,0.45)" />
              <stop offset="60%" stop-color="rgba(155,220,255,0.12)" />
              <stop offset="100%" stop-color="rgba(155,220,255,0)" />
            </radialGradient>
            <linearGradient id="suvScale" x1="0%" y1="100%" x2="0%" y2="0%">
              <stop offset="0%" stop-color="rgba(63,134,196,0.5)" />
              <stop offset="40%" stop-color="rgba(125,184,255,0.6)" />
              <stop offset="70%" stop-color="rgba(255,200,120,0.7)" />
              <stop offset="100%" stop-color="rgba(255,120,90,0.8)" />
            </linearGradient>
            <filter id="brainGlow" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="2.5" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <filter id="softGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="6" />
            </filter>
          </defs>

          <!-- 扫描定位环 -->
          <circle cx="180" cy="172" r="152" fill="none" stroke="rgba(155,220,255,0.07)" stroke-width="1" stroke-dasharray="2 8" />
          <circle cx="180" cy="172" r="138" fill="none" stroke="rgba(155,220,255,0.05)" stroke-width="0.8" />
          <g stroke="rgba(155,220,255,0.16)" stroke-width="1">
            <line x1="180" y1="24" x2="180" y2="34" /><line x1="180" y1="310" x2="180" y2="320" />
            <line x1="36" y1="172" x2="46" y2="172" /><line x1="314" y1="172" x2="324" y2="172" />
          </g>
          <text x="180" y="18" text-anchor="middle" fill="rgba(155,220,255,0.38)" font-size="9" font-family="monospace">A</text>
          <text x="180" y="332" text-anchor="middle" fill="rgba(155,220,255,0.38)" font-size="9" font-family="monospace">P</text>
          <text x="27" y="176" text-anchor="middle" fill="rgba(155,220,255,0.38)" font-size="9" font-family="monospace">L</text>
          <text x="333" y="176" text-anchor="middle" fill="rgba(155,220,255,0.38)" font-size="9" font-family="monospace">R</text>

          <!-- 医学坐标轴 -->
          <line x1="180" y1="34" x2="180" y2="310" stroke="rgba(155,220,255,0.09)" stroke-width="0.8" stroke-dasharray="4 6" />
          <line x1="46" y1="172" x2="314" y2="172" stroke="rgba(155,220,255,0.09)" stroke-width="0.8" stroke-dasharray="4 6" />

          <!-- 大脑主体 -->
          <path d="M180 48 C 122 48, 78 92, 82 150 C 84 184, 100 218, 130 234 C 144 240, 156 234, 164 224 C 174 234, 184 240, 198 236 C 226 228, 246 198, 250 164 C 254 92, 214 48, 180 48 Z"
                fill="url(#brainBody)" stroke="rgba(155,220,255,0.40)" stroke-width="1.6" filter="url(#brainGlow)" />
          <path d="M180 58 C 132 60, 94 98, 98 150 C 100 178, 114 204, 138 216 C 148 220, 156 216, 162 210 C 170 218, 180 222, 190 218 C 210 212, 226 188, 228 162 C 230 102, 198 56, 180 58 Z"
                fill="url(#brainDepth)" stroke="rgba(155,220,255,0.09)" stroke-width="1" />

          <!-- 半球间裂 + 胼胝体 -->
          <path d="M180 58 C 178 114, 176 178, 180 226" fill="none" stroke="rgba(155,220,255,0.40)" stroke-width="1.4" stroke-linecap="round" />
          <path d="M148 130 C 160 122, 200 122, 212 130" fill="none" stroke="rgba(155,220,255,0.32)" stroke-width="1.3" stroke-linecap="round" />
          <path d="M152 140 C 164 134, 196 134, 208 140" fill="none" stroke="rgba(155,220,255,0.20)" stroke-width="1" stroke-linecap="round" />

          <!-- 侧脑室 -->
          <path d="M158 118 C 150 128, 148 148, 156 163 C 162 173, 170 170, 172 160 C 170 143, 168 128, 158 118 Z"
                fill="rgba(155,220,255,0.11)" stroke="rgba(155,220,255,0.28)" stroke-width="0.9" />
          <path d="M202 118 C 210 128, 212 148, 204 163 C 198 173, 190 170, 188 160 C 190 143, 192 128, 202 118 Z"
                fill="rgba(155,220,255,0.11)" stroke="rgba(155,220,255,0.28)" stroke-width="0.9" />

          <!-- 小脑 + 脑干 -->
          <path d="M128 230 C 118 246, 126 266, 146 272 C 158 275, 166 268, 170 258 C 174 268, 182 275, 194 272 C 214 266, 222 246, 212 230"
                fill="rgba(155,220,255,0.09)" stroke="rgba(155,220,255,0.28)" stroke-width="1.2" stroke-linecap="round" />
          <g stroke="rgba(155,220,255,0.18)" stroke-width="0.8" stroke-linecap="round" fill="none">
            <path d="M136 242 C 154 238, 206 238, 208 242" />
            <path d="M138 252 C 156 248, 204 248, 206 252" />
            <path d="M144 262 C 158 259, 202 259, 200 262" />
          </g>
          <path d="M168 266 L 168 282 C 168 286, 192 286, 192 282 L 192 266" fill="rgba(155,220,255,0.07)" stroke="rgba(155,220,255,0.22)" stroke-width="1" stroke-linecap="round" />

          <!-- 脑沟回（左） -->
          <path d="M106 102 C 128 112, 138 130, 126 150" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.6" stroke-linecap="round" />
          <path d="M96 146 C 116 154, 126 170, 114 190" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.6" stroke-linecap="round" />
          <path d="M118 188 C 140 194, 154 202, 146 216" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.4" stroke-linecap="round" />
          <path d="M136 80 C 150 92, 152 110, 140 126" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.4" stroke-linecap="round" />
          <path d="M148 134 C 162 144, 164 164, 152 178" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.3" stroke-linecap="round" />
          <!-- 脑沟回（右） -->
          <path d="M254 102 C 232 112, 222 130, 234 150" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.6" stroke-linecap="round" />
          <path d="M264 146 C 244 154, 234 170, 246 190" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.6" stroke-linecap="round" />
          <path d="M242 188 C 220 194, 206 202, 214 216" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.4" stroke-linecap="round" />
          <path d="M224 80 C 210 92, 208 110, 220 126" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.4" stroke-linecap="round" />
          <path d="M212 134 C 198 144, 196 164, 208 178" fill="none" stroke="url(#sulcusStroke)" stroke-width="1.3" stroke-linecap="round" />

          <!-- PET 代谢热区（颞顶叶，AD 典型低代谢区） -->
          <g opacity="0.32">
            <ellipse cx="138" cy="138" rx="24" ry="18" fill="url(#petHeat)" />
            <ellipse cx="222" cy="138" rx="24" ry="18" fill="url(#petHeat)" />
          </g>

          <!-- 神经节点 + 连接 -->
          <g class="brain-nodes">
            <circle cx="126" cy="122" r="3.2" fill="rgba(155,220,255,0.9)" />
            <circle cx="110" cy="164" r="2.6" fill="rgba(155,220,255,0.7)" />
            <circle cx="234" cy="122" r="3.2" fill="rgba(155,220,255,0.9)" />
            <circle cx="250" cy="164" r="2.6" fill="rgba(155,220,255,0.7)" />
            <circle cx="180" cy="90" r="2.2" fill="rgba(125,184,255,0.80)" />
            <circle cx="152" cy="202" r="2.6" fill="rgba(155,220,255,0.6)" />
            <circle cx="208" cy="202" r="2.6" fill="rgba(155,220,255,0.6)" />
            <circle cx="180" cy="148" r="2" fill="rgba(95,165,220,0.65)" />
          </g>
          <path class="brain-nerve" d="M126 122 Q 152 106, 180 90 Q 208 106, 234 122" fill="none" stroke="rgba(155,220,255,0.5)" stroke-width="1" stroke-dasharray="3 6" stroke-linecap="round" />
          <path class="brain-nerve" d="M110 164 Q 132 180, 152 202" fill="none" stroke="rgba(155,220,255,0.4)" stroke-width="0.9" stroke-dasharray="2 5" stroke-linecap="round" />
          <path class="brain-nerve" d="M250 164 Q 228 180, 208 202" fill="none" stroke="rgba(155,220,255,0.4)" stroke-width="0.9" stroke-dasharray="2 5" stroke-linecap="round" />
          <path class="brain-nerve" d="M180 90 Q 180 118, 180 148" fill="none" stroke="rgba(125,184,255,0.45)" stroke-width="0.8" stroke-dasharray="2 5" stroke-linecap="round" />

          <!-- 信号脉冲 -->
          <g fill="#9bdcff">
            <circle cx="153" cy="106" r="1.8"><animate attributeName="opacity" values="0;1;0" dur="2.4s" repeatCount="indefinite" begin="0s" /></circle>
            <circle cx="207" cy="106" r="1.8"><animate attributeName="opacity" values="0;1;0" dur="2.4s" repeatCount="indefinite" begin="0.6s" /></circle>
            <circle cx="131" cy="180" r="1.5"><animate attributeName="opacity" values="0;1;0" dur="2.4s" repeatCount="indefinite" begin="1.2s" /></circle>
            <circle cx="229" cy="180" r="1.5"><animate attributeName="opacity" values="0;1;0" dur="2.4s" repeatCount="indefinite" begin="1.8s" /></circle>
          </g>

          <!-- ROI 感兴趣区（海马） -->
          <g fill="none" stroke="rgba(155,220,255,0.55)" stroke-width="0.9" stroke-dasharray="2 2">
            <circle cx="140" cy="156" r="15" /><circle cx="220" cy="156" r="15" />
            <circle cx="140" cy="156" r="2.4" fill="rgba(155,220,255,0.65)" stroke="none" />
            <circle cx="220" cy="156" r="2.4" fill="rgba(155,220,255,0.65)" stroke="none" />
          </g>
          <text x="140" y="138" text-anchor="middle" fill="rgba(155,220,255,0.45)" font-size="6.5" font-family="monospace">HIP-L</text>
          <text x="220" y="138" text-anchor="middle" fill="rgba(155,220,255,0.45)" font-size="6.5" font-family="monospace">HIP-R</text>

          <!-- 扫描进度弧 -->
          <circle class="scan-progress" cx="180" cy="172" r="134" fill="none" stroke="url(#scanRing)" stroke-width="2" stroke-dasharray="110 730" stroke-linecap="round" transform="rotate(-90 180 172)" />

          <!-- 扫描切片线 -->
          <g stroke="rgba(155,220,255,0.11)" stroke-width="0.6">
            <line x1="68" y1="108" x2="292" y2="108" />
            <line x1="58" y1="143" x2="302" y2="143" />
            <line x1="58" y1="178" x2="302" y2="178" />
            <line x1="68" y1="213" x2="292" y2="213" />
            <line x1="88" y1="248" x2="272" y2="248" />
          </g>

          <!-- HUD 四角 -->
          <g fill="none" stroke="rgba(155,220,255,0.40)" stroke-width="1.1" stroke-linecap="round">
            <path d="M46 46 L 46 36 L 56 36" /><path d="M314 46 L 314 36 L 304 36" />
            <path d="M46 298 L 46 308 L 56 308" /><path d="M314 298 L 314 308 L 304 308" />
          </g>

          <!-- 数据指标 -->
          <g font-size="8" font-family="monospace">
            <rect x="44" y="88" width="56" height="15" rx="3" fill="rgba(155,220,255,0.05)" stroke="rgba(155,220,255,0.18)" stroke-width="0.6" />
            <text x="50" y="99" fill="rgba(155,220,255,0.68)">SUVr 1.42</text>
            <rect x="260" y="88" width="56" height="15" rx="3" fill="rgba(155,220,255,0.05)" stroke="rgba(155,220,255,0.18)" stroke-width="0.6" />
            <text x="266" y="99" fill="rgba(155,220,255,0.68)">CTR 0.28</text>
            <rect x="42" y="218" width="60" height="15" rx="3" fill="rgba(155,220,255,0.05)" stroke="rgba(155,220,255,0.18)" stroke-width="0.6" />
            <text x="48" y="229" fill="rgba(155,220,255,0.68)">HV 3.1cm³</text>
            <rect x="258" y="218" width="60" height="15" rx="3" fill="rgba(155,220,255,0.05)" stroke="rgba(155,220,255,0.18)" stroke-width="0.6" />
            <text x="264" y="229" fill="rgba(155,220,255,0.68)">Braak III</text>
          </g>

          <!-- SUV 色阶 -->
          <g transform="translate(330, 76)">
            <rect x="0" y="0" width="7" height="96" rx="2" fill="url(#suvScale)" />
            <text x="12" y="5" fill="rgba(155,220,255,0.45)" font-size="6" font-family="monospace">SUV</text>
            <text x="12" y="96" fill="rgba(155,220,255,0.45)" font-size="6" font-family="monospace">0</text>
          </g>

          <!-- 切片定位 -->
          <g transform="translate(12, 106)">
            <line x1="4" y1="0" x2="4" y2="116" stroke="rgba(155,220,255,0.22)" stroke-width="1" />
            <circle cx="4" cy="28" r="3" fill="#9bdcff" />
            <line x1="4" y1="28" x2="14" y2="28" stroke="rgba(155,220,255,0.38)" stroke-width="0.8" />
            <text x="18" y="31" fill="rgba(155,220,255,0.55)" font-size="6.5" font-family="monospace">SLICE 42</text>
            <text x="0" y="-5" fill="rgba(155,220,255,0.38)" font-size="6" font-family="monospace">AXIAL</text>
          </g>

          <!-- 模态标识 -->
          <g transform="translate(16, 56)" font-family="monospace" font-size="7">
            <rect x="0" y="0" width="54" height="42" rx="4" fill="rgba(155,220,255,0.04)" stroke="rgba(155,220,255,0.16)" stroke-width="0.6" />
            <text x="8" y="11" fill="rgba(155,220,255,0.68)">T1 MPRAGE</text>
            <text x="8" y="21" fill="rgba(155,220,255,0.52)">FDG PET</text>
            <text x="8" y="31" fill="rgba(155,220,255,0.42)">DWI / ADC</text>
            <text x="8" y="39" fill="rgba(125,184,255,0.58)">Aβ PET</text>
          </g>

          <!-- EEG 波形 -->
          <g transform="translate(108, 276)">
            <rect x="0" y="0" width="144" height="26" rx="6" fill="rgba(155,220,255,0.04)" stroke="rgba(155,220,255,0.14)" stroke-width="0.8" />
            <path class="eeg-wave" d="M8 13 L 18 13 L 22 5 L 26 21 L 30 13 L 42 13 L 46 7 L 50 19 L 54 13 L 70 13 L 74 9 L 78 17 L 82 13 L 100 13 L 104 5 L 108 21 L 112 13 L 136 13"
                  fill="none" stroke="rgba(125,184,255,0.72)" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
            <text x="8" y="8" fill="rgba(155,220,255,0.45)" font-size="6.5" font-family="monospace">EEG α</text>
          </g>

          <!-- AI 状态 -->
          <g transform="translate(108, 326)">
            <rect x="0" y="0" width="144" height="17" rx="4" fill="rgba(155,220,255,0.04)" stroke="rgba(155,220,255,0.14)" stroke-width="0.6" />
            <circle cx="12" cy="8.5" r="2.4" fill="#9bdcff"><animate attributeName="opacity" values="1;0.3;1" dur="2s" repeatCount="indefinite" /></circle>
            <text x="22" y="11.5" fill="rgba(155,220,255,0.55)" font-size="7.5" font-family="monospace">AI ANALYSIS · ACTIVE</text>
          </g>
        </svg>
        <!-- 光晕流动层 -->
        <div class="brain-light-flow" />
      </div>

      <!-- 顶部品牌标识：精简徽标（仅图标 + 英文名，不抢主标题焦点） -->
      <div class="relative z-10 flex items-center gap-2.5">
        <div class="brand-badge-mini">
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
        <div class="brand-top-en">BrainImaging · Dementia Insight</div>
      </div>

      <!-- 主标题：艺术字 + 大号 + 渐变描边发光，作为视觉焦点 -->
      <div class="brand-hero relative z-10 mt-10">
        <div class="brand-eyebrow">AI · MEDICAL IMAGING · 2026</div>
        <h1 class="brand-title">
          <span class="brand-title__char">脑</span><span class="brand-title__char">影</span><span class="brand-accent-dot">·</span><span class="brand-title__char">明</span><span class="brand-title__char">衰</span>
        </h1>
        <!-- 艺术装饰：标题下方渐变双弧线 -->
        <svg class="brand-title-deco" viewBox="0 0 320 12" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <defs>
            <linearGradient id="titleDecoGrad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stop-color="rgba(155,220,255,0)" />
              <stop offset="50%" stop-color="rgba(155,220,255,0.75)" />
              <stop offset="100%" stop-color="rgba(201,163,255,0)" />
            </linearGradient>
          </defs>
          <path d="M10 6 Q 80 0, 160 6 T 310 6" fill="none" stroke="url(#titleDecoGrad)" stroke-width="1.5" stroke-linecap="round" />
          <path d="M60 9 Q 120 4, 180 9 T 270 9" fill="none" stroke="url(#titleDecoGrad)" stroke-width="0.8" stroke-linecap="round" opacity="0.6" />
          <circle cx="160" cy="6" r="2.2" fill="#9bdcff" />
          <circle cx="160" cy="6" r="4" fill="#9bdcff" opacity="0.25" />
        </svg>
        <p class="brand-subtitle">
          阿尔茨海默病一体化 <span class="brand-subtitle__hl">MRI/PET</span> 脑成像智能诊断系统
        </p>
      </div>

      <!-- 占位：让底部能力点 + 模型信息固定在下方 -->
      <div class="flex-1" />

      <!-- 4 个能力点：图标 + 短标题，居中排布 -->
      <div class="relative z-10 grid grid-cols-4 gap-3 max-w-[420px] mx-auto">
        <div v-for="f in features" :key="f.title" class="feature-chip">
          <el-icon :size="18"><component :is="f.icon" /></el-icon>
          <span class="text-[12px] mt-1">{{ f.title }}</span>
        </div>
      </div>

      <!-- 底部模型信息（居中） -->
      <div class="relative z-10 mt-6 flex items-center justify-center gap-4 text-[11px] text-white/45">
        <span>TransMF-15ens-v4</span>
        <span class="w-1 h-1 rounded-full bg-white/20" />
        <span>2026 版 PET/MRI 指南</span>
      </div>
    </aside>

    <!-- ==================== 右侧登录/注册区 ==================== -->
    <div class="flex-1 flex flex-col items-center justify-center px-6 relative bg-white">
      <div class="w-[400px] max-w-full">
        <!-- 标识 + 标题 -->
        <div class="flex flex-col items-center mb-8">
          <div class="w-14 h-14 rounded-2xl brand-gradient-bg flex items-center justify-center text-white shadow-[0_8px_24px_rgba(47,109,163,0.35)]">
            <svg width="30" height="30" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="16" cy="16" r="13" stroke="currentColor" stroke-width="1.4" opacity="0.35" />
              <path d="M16 6.5 C12 6.5 8.5 10 8.5 15 C8.5 19.5 11 23 15 24.5 C14 22.5 13.5 20.5 14 18.5 C12.8 17.3 12.5 15.5 13 14 C12.5 12.3 13.5 10.8 15.2 10.3 C15.2 9 15.5 7.7 16 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M16 6.5 C20 6.5 23.5 10 23.5 15 C23.5 19.5 21 23 17 24.5 C18 22.5 18.5 20.5 18 18.5 C19.2 17.3 19.5 15.5 19 14 C19.5 12.3 18.5 10.8 16.8 10.3 C16.8 9 16.5 7.7 16 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M16 7.5 L16 24" stroke="currentColor" stroke-width="1.2" opacity="0.45" stroke-linecap="round" />
              <circle cx="16" cy="16" r="1.8" fill="currentColor" />
              <circle cx="3.5" cy="16" r="1.1" fill="currentColor" opacity="0.5" />
              <circle cx="28.5" cy="16" r="1.1" fill="currentColor" opacity="0.5" />
            </svg>
          </div>
          <h1 class="mt-5 text-[26px] font-bold text-ink tracking-wide">
            欢迎使用 <span class="brand-gradient-text">脑影明衰</span>
          </h1>
          <p class="mt-2 text-[13px] text-hint">
            {{ mode === 'login' ? '请登录以进入神经影像筛查工作台' : '注册账号需管理员审核启用后登录' }}
          </p>
        </div>

        <!-- ==================== 登录卡片 ==================== -->
        <div v-show="mode === 'login'" class="login-card">
          <div class="text-[16px] font-semibold text-ink mb-6">账号登录</div>
          <el-form ref="loginFormRef" :model="form" :rules="rules" size="large" @keyup.enter="handleLogin">
            <el-form-item prop="username">
              <el-input v-model="form.username" placeholder="用户名" :prefix-icon="'User'" autocomplete="username" />
            </el-form-item>
            <el-form-item prop="password">
              <el-input
                v-model="form.password"
                type="password"
                show-password
                placeholder="密码"
                :prefix-icon="'Lock'"
                autocomplete="current-password"
              />
            </el-form-item>
            <el-form-item prop="captcha">
              <div class="w-full">
                <div class="flex gap-3 w-full">
                  <el-input v-model="form.captcha" placeholder="验证码" :prefix-icon="'Key'" maxlength="4" class="flex-1" />
                  <canvas
                    ref="loginCaptchaCanvas"
                    width="112"
                    height="40"
                    class="rounded-lg border cursor-pointer shrink-0 w-[112px] h-[40px]"
                    :class="captchaFailed ? 'border-[#C94F4F]' : 'border-line'"
                    :title="captchaFailed ? '验证码服务不可用，点击重试' : '点击刷新验证码'"
                    @click="refreshCaptcha"
                  />
                </div>
                <div v-if="captchaFailed" class="text-[11px] text-[#C94F4F] mt-1 leading-none">
                  验证码服务不可用，请点击右侧图片重试
                </div>
              </div>
            </el-form-item>
            <el-button type="primary" size="large" class="login-btn" :loading="loading" @click="handleLogin">
              {{ loading ? '登录中…' : '登 录' }}
            </el-button>
          </el-form>
          <div class="mt-4 text-center text-[13px] text-hint">
            还没有账号？
            <el-link type="primary" :underline="'never'" @click="switchTo('register')">立即注册</el-link>
          </div>

          <!-- 演示账号：缩小放在卡片底部，弱化视觉 -->
          <div class="demo-section mt-6 pt-5 border-t border-line/60">
            <div class="text-[11px] text-hint mb-2.5">演示账号（点击自动填充）</div>
            <div class="grid grid-cols-2 gap-2">
              <button
                v-for="acc in demoAccounts"
                :key="acc.username"
                type="button"
                class="demo-card"
                :class="{ 'demo-card--active': activeDemo === acc.username }"
                @click="fillDemo(acc)"
              >
                <span class="w-7 h-7 rounded-md flex items-center justify-center shrink-0" :class="[ROLE_META[acc.role].toneBg, ROLE_META[acc.role].toneFg]">
                  <el-icon :size="14"><component :is="ROLE_META[acc.role].icon" /></el-icon>
                </span>
                <span class="min-w-0 flex-1 text-left">
                  <span class="block text-[12px] font-medium text-ink leading-tight truncate">{{ acc.roleName }}</span>
                  <span class="block font-num text-[10px] text-hint leading-tight mt-0.5 truncate">{{ acc.username }}</span>
                </span>
                <el-icon v-if="activeDemo === acc.username" class="text-primary shrink-0" :size="12"><Check /></el-icon>
              </button>
            </div>
            <div class="mt-2 text-[10.5px] text-hint leading-5">
              医师密码：<span class="font-num">123456</span>　管理员：<span class="font-num">admin123</span>
            </div>
          </div>
        </div>

        <!-- ==================== 注册卡片 ==================== -->
        <div v-show="mode === 'register'" class="login-card">
          <div class="flex items-center justify-between mb-6">
            <div class="text-[16px] font-semibold text-ink">账号注册</div>
            <el-link type="info" :underline="'never'" @click="switchTo('login')">返回登录</el-link>
          </div>
          <el-form ref="registerFormRef" :model="registerForm" :rules="registerRules" size="large" @keyup.enter="handleRegister">
            <div class="mb-[18px]">
              <div class="text-[12.5px] font-medium text-ink-secondary mb-2">用户类型</div>
              <div class="grid grid-cols-3 gap-2" role="radiogroup" aria-label="选择注册用户类型">
                <button
                  v-for="r in REGISTER_ROLES"
                  :key="r"
                  type="button"
                  role="radio"
                  :aria-checked="registerForm.role === r"
                  class="role-card"
                  :class="{ 'role-card--active': registerForm.role === r }"
                  @click="registerForm.role = r"
                >
                  <span class="w-7 h-7 rounded-lg flex items-center justify-center shrink-0" :class="[ROLE_META[r].toneBg, ROLE_META[r].toneFg]">
                    <el-icon :size="15"><component :is="ROLE_META[r].icon" /></el-icon>
                  </span>
                  <span class="text-[12.5px] font-medium text-ink leading-tight mt-1.5">{{ ROLE_META[r].name }}</span>
                  <span class="text-[10px] text-hint leading-tight mt-0.5 whitespace-nowrap">{{ ROLE_META[r].desc }}</span>
                  <el-icon class="role-card__check" :size="12"><Check /></el-icon>
                </button>
              </div>
            </div>
            <el-form-item prop="realName">
              <el-input v-model="registerForm.realName" placeholder="真实姓名" :prefix-icon="'UserFilled'" autocomplete="name" />
            </el-form-item>
            <el-form-item prop="username">
              <el-input v-model="registerForm.username" placeholder="用户名（3-20位字母/数字/下划线）" :prefix-icon="'User'" autocomplete="username" />
            </el-form-item>
            <el-form-item prop="password">
              <el-input
                v-model="registerForm.password"
                type="password"
                show-password
                placeholder="密码（6-20位）"
                :prefix-icon="'Lock'"
                autocomplete="new-password"
              />
            </el-form-item>
            <el-form-item prop="confirmPassword">
              <el-input
                v-model="registerForm.confirmPassword"
                type="password"
                show-password
                placeholder="确认密码"
                :prefix-icon="'Lock'"
                autocomplete="new-password"
              />
            </el-form-item>
            <div class="grid grid-cols-2 gap-3">
              <el-form-item prop="department">
                <el-input v-model="registerForm.department" placeholder="所属科室（可选）" :prefix-icon="'OfficeBuilding'" />
              </el-form-item>
              <el-form-item prop="phone">
                <el-input v-model="registerForm.phone" placeholder="手机号（可选）" :prefix-icon="'Phone'" autocomplete="tel" />
              </el-form-item>
            </div>
            <el-form-item prop="captcha">
              <div class="w-full">
                <div class="flex gap-3 w-full">
                  <el-input v-model="registerForm.captcha" placeholder="验证码" :prefix-icon="'Key'" maxlength="4" class="flex-1" />
                  <canvas
                    ref="registerCaptchaCanvas"
                    width="112"
                    height="40"
                    class="rounded-lg border cursor-pointer shrink-0 w-[112px] h-[40px]"
                    :class="captchaFailed ? 'border-[#C94F4F]' : 'border-line'"
                    :title="captchaFailed ? '验证码服务不可用，点击重试' : '点击刷新验证码'"
                    @click="refreshCaptcha"
                  />
                </div>
                <div v-if="captchaFailed" class="text-[11px] text-[#C94F4F] mt-1 leading-none">
                  验证码服务不可用，请点击右侧图片重试
                </div>
              </div>
            </el-form-item>
            <el-button type="primary" size="large" class="login-btn" :loading="registerLoading" @click="handleRegister">
              {{ registerLoading ? '注册中…' : '注 册' }}
            </el-button>
          </el-form>
          <div class="mt-3 text-[11.5px] text-hint leading-5">
            提交后将以「{{ ROLE_META[registerForm.role].name }}」身份创建账号，需管理员在系统权限管理中启用后方可登录；管理员账号不开放自助注册。
          </div>
        </div>
      </div>

      <!-- ==================== 页脚 ==================== -->
      <footer class="absolute bottom-4 left-0 right-0 py-2 text-center text-xs text-hint space-x-4">
        <span>脑影明衰 v1.0.0 · Build 20260907</span>
        <span class="text-line">|</span>
        <el-link type="info" :underline="'never'" @click="ElMessage.info('隐私协议：本系统采集数据仅用于临床筛查与科研，遵循《个人信息保护法》与医疗数据安全管理规范。')">
          隐私协议
        </el-link>
        <span class="text-line">|</span>
        <el-link type="info" :underline="'never'" @click="ElMessage.info('使用条款：系统结论仅供执业医师临床参考，不可替代临床诊断。')">
          使用条款
        </el-link>
      </footer>
    </div>
  </div>
</template>

<style scoped>
/* ==========================================================
   登录页：左右分栏，左侧深蓝品牌主视觉，右侧白色登录区
   风格：医疗科研、高级简约、科技感、深蓝主调、克制动效
   ========================================================== */

/* 整体背景：右侧白色纯净 */
.login-page {
  background: #ffffff;
}

/* 左侧品牌面板 —— 原深蓝紫色调 */
.brand-panel {
  background: linear-gradient(155deg, #0f1a30 0%, #1a2d52 50%, #2557a7 100%);
  position: relative;
}
.brand-panel::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse at 65% 75%, rgba(63, 134, 196, 0.28), transparent 60%),
    radial-gradient(ellipse at 30% 15%, rgba(47, 109, 163, 0.20), transparent 55%);
  pointer-events: none;
  z-index: 0;
}
.brand-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
  background-size: 40px 40px;
  mask-image: radial-gradient(ellipse at 50% 50%, black 0%, transparent 75%);
  -webkit-mask-image: radial-gradient(ellipse at 50% 50%, black 0%, transparent 75%);
  z-index: 0;
}
.brand-glow {
  position: absolute;
  border-radius: 9999px;
  filter: blur(70px);
  pointer-events: none;
  z-index: 0;
}
.brand-glow--1 {
  width: 380px;
  height: 380px;
  background: rgba(95, 165, 220, 0.26);
  top: -100px;
  right: -90px;
}
.brand-glow--2 {
  width: 320px;
  height: 320px;
  background: rgba(47, 109, 163, 0.22);
  bottom: -110px;
  left: -70px;
}
.brand-glow--3 {
  width: 240px;
  height: 240px;
  background: rgba(110, 90, 180, 0.18);
  top: 35%;
  right: -100px;
}

/* ---------- 大脑 3D 主视觉 ---------- */
.brain-3d-wrapper {
  z-index: 0;
  padding-top: 90px;
}
.brain-3d {
  width: 520px;
  height: 520px;
  opacity: 0.92;
  filter: drop-shadow(0 0 50px rgba(63, 134, 196, 0.22));
  transition: filter 0.4s ease;
}
/* 光点呼吸（微弱，不花哨） */
.brain-nodes circle {
  animation: node-pulse 3.6s ease-in-out infinite;
  transform-origin: center;
}
.brain-nodes circle:nth-child(1) { animation-delay: 0s; }
.brain-nodes circle:nth-child(2) { animation-delay: 0.6s; }
.brain-nodes circle:nth-child(3) { animation-delay: 1.2s; }
.brain-nodes circle:nth-child(4) { animation-delay: 1.8s; }
.brain-nodes circle:nth-child(5) { animation-delay: 0.3s; }
.brain-nodes circle:nth-child(6) { animation-delay: 2.4s; }
.brain-nodes circle:nth-child(7) { animation-delay: 3.0s; }
.brain-nodes circle:nth-child(8) { animation-delay: 1.5s; }

@keyframes node-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
}
/* 神经线流动 */
.brain-nerve {
  stroke-dashoffset: 0;
  animation: nerve-flow 6s linear infinite;
}
@keyframes nerve-flow {
  to { stroke-dashoffset: -30; }
}
/* 扫描进度环旋转 */
.scan-progress {
  animation: scan-rotate 12s linear infinite;
  transform-origin: 180px 175px;
}
@keyframes scan-rotate {
  to { transform: rotate(270deg); }
}
/* EEG 波形流动 */
.eeg-wave {
  stroke-dasharray: 260;
  stroke-dashoffset: 260;
  animation: eeg-draw 4s ease-in-out infinite;
}
@keyframes eeg-draw {
  0% { stroke-dashoffset: 260; }
  40%, 60% { stroke-dashoffset: 0; }
  100% { stroke-dashoffset: -260; }
}
/* 光影流动层（极弱扫光） */
.brain-light-flow {
  position: absolute;
  width: 60%;
  height: 100%;
  top: 0;
  left: 20%;
  background: linear-gradient(
    105deg,
    transparent 40%,
    rgba(155, 220, 255, 0.06) 50%,
    transparent 60%
  );
  transform: skewX(-12deg);
  animation: light-sweep 8s ease-in-out infinite;
}
@keyframes light-sweep {
  0%, 100% { opacity: 0; transform: translateX(-30%) skewX(-12deg); }
  45%, 55% { opacity: 1; }
  50% { transform: translateX(30%) skewX(-12deg); }
}

/* ---------- 顶部迷你徽标（仅图标 + 英文，弱化） ---------- */
.brand-badge-mini {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.16), rgba(255, 255, 255, 0.05));
  border: 1px solid rgba(255, 255, 255, 0.22);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}
.brand-top-en {
  font-size: 10px;
  letter-spacing: 0.16em;
  color: rgba(255, 255, 255, 0.4);
  text-transform: uppercase;
}

/* ---------- 品牌艺术标题区（居中对齐） ---------- */
.brand-hero {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  animation: hero-fade-in 1s ease-out both;
}
@keyframes hero-fade-in {
  0% { opacity: 0; transform: translateY(16px); }
  100% { opacity: 1; transform: translateY(0); }
}
.brand-eyebrow {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 10px;
  letter-spacing: 0.4em;
  color: rgba(155, 220, 255, 0.62);
  font-weight: 500;
  margin-bottom: 16px;
}
.brand-eyebrow::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #9bdcff;
  box-shadow: 0 0 6px #9bdcff;
  flex-shrink: 0;
}
/* 主标题：艺术字 —— 大字号 + 渐变填充 + 描边 + 发光 */
.brand-title {
  font-size: 78px;
  font-weight: 800;
  letter-spacing: 0.08em;
  line-height: 1;
  margin: 0;
  font-family: "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", system-ui, sans-serif;
  background: linear-gradient(165deg, #ffffff 0%, #d6ecff 30%, #9bdcff 60%, #c9a3ff 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  -webkit-text-stroke: 1.2px rgba(155, 220, 255, 0.2);
  filter:
    drop-shadow(0 4px 16px rgba(0, 0, 0, 0.42))
    drop-shadow(0 0 30px rgba(63, 134, 196, 0.45));
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  z-index: 1;
}
/* 标题背后柔光，增强存在感 */
.brand-title::before {
  content: "";
  position: absolute;
  inset: -24px -36px;
  background: radial-gradient(ellipse at center, rgba(155, 220, 255, 0.16), rgba(63, 134, 196, 0.06) 50%, transparent 72%);
  filter: blur(24px);
  z-index: -1;
  pointer-events: none;
}
/* 单字：逐个轻微错落，增加艺术感 */
.brand-title__char {
  display: inline-block;
  transition: transform 0.3s ease, filter 0.3s ease;
}
.brand-title__char:nth-child(1) { transform: translateY(-1px); }
.brand-title__char:nth-child(3) { transform: translateY(1px); }
.brand-title__char:nth-child(4) { transform: translateY(-0.5px); }
.brand-title:hover .brand-title__char:nth-child(odd) { transform: translateY(-3px); filter: drop-shadow(0 0 8px rgba(155, 220, 255, 0.5)); }
.brand-title:hover .brand-title__char:nth-child(even) { transform: translateY(2px); filter: drop-shadow(0 0 8px rgba(155, 220, 255, 0.5)); }

/* 中间"·"：发光光点 —— 与 Logo 蓝系一致 */
.brand-accent-dot {
  display: inline-block;
  width: 16px;
  height: 16px;
  margin: 0 12px;
  border-radius: 50%;
  background: radial-gradient(circle, #ffffff 0%, #9bdcff 35%, #7b8cff 100%);
  box-shadow:
    0 0 10px #9bdcff,
    0 0 18px rgba(155, 220, 255, 0.65),
    0 0 32px rgba(123, 140, 255, 0.55);
  animation: dot-pulse 2.8s ease-in-out infinite;
}
@keyframes dot-pulse {
  0%, 100% { transform: scale(1); box-shadow: 0 0 10px #9bdcff, 0 0 18px rgba(155, 220, 255, 0.65), 0 0 32px rgba(123, 140, 255, 0.55); }
  50% { transform: scale(1.18); box-shadow: 0 0 14px #7db8ff, 0 0 26px rgba(155, 220, 255, 0.85), 0 0 40px rgba(123, 140, 255, 0.75); }
}

/* 标题下方艺术装饰：双弧线（居中） */
.brand-title-deco {
  width: 260px;
  height: 12px;
  margin: 10px auto 0;
  display: block;
}

.brand-subtitle {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  font-size: 18px;
  color: rgba(255, 255, 255, 0.82);
  font-weight: 400;
  line-height: 1.6;
  letter-spacing: 0.05em;
  margin-top: 24px;
  text-shadow: 0 1px 6px rgba(0, 0, 0, 0.25);
}
.brand-subtitle__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: rgba(155, 220, 255, 0.15);
  border: 1px solid rgba(155, 220, 255, 0.28);
  color: #9bdcff;
  flex-shrink: 0;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.1);
}
.brand-subtitle__hl {
  font-weight: 700;
  background: linear-gradient(135deg, #ffffff 0%, #9bdcff 45%, #c9a3ff 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  padding: 0 4px;
  filter: drop-shadow(0 0 8px rgba(155, 220, 255, 0.45));
}

/* ---------- 能力点：图标 + 短标题 ---------- */
.feature-chip {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 14px 6px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(6px);
  transition: all 0.25s ease;
}
.feature-chip:hover {
  background: rgba(155, 220, 255, 0.10);
  border-color: rgba(155, 220, 255, 0.35);
  color: #9bdcff;
  transform: translateY(-2px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18), 0 0 12px rgba(155, 220, 255, 0.15);
}

/* ---------- 登录卡片 ---------- */
.login-card {
  background: #fff;
  border: 1px solid var(--ad-border);
  border-radius: 14px;
  padding: 30px 28px;
  box-shadow: 0 4px 24px rgba(16, 32, 48, 0.06);
}
.login-btn {
  width: 100%;
  height: 46px;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: 0.3em;
  border-radius: 10px;
  box-shadow: 0 4px 14px rgba(47, 109, 163, 0.28);
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}
.login-btn:hover {
  box-shadow: 0 6px 20px rgba(47, 109, 163, 0.38);
  transform: translateY(-1px);
}

/* ---------- 演示账号区：缩小、弱化 ---------- */
.demo-section {
  opacity: 0.88;
}
.demo-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border-radius: 9px;
  border: 1px solid var(--ad-border);
  background: #fff;
  cursor: pointer;
  font: inherit;
  transition: border-color 0.18s ease, background-color 0.18s ease;
}
.demo-card:hover {
  border-color: rgba(47, 109, 163, 0.5);
  background: #f7fafd;
}
.demo-card--active,
.demo-card--active:hover {
  border-color: var(--ad-primary);
  background: var(--ad-primary-light);
}

/* ==================== 注册页：用户类型选择卡 ==================== */
.role-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 10px 4px 9px;
  border-radius: 10px;
  border: 1px solid var(--ad-border);
  background: #fff;
  cursor: pointer;
  font: inherit;
  transition: border-color 0.18s ease, background-color 0.18s ease, box-shadow 0.18s ease;
}
.role-card:hover {
  border-color: rgba(47, 109, 163, 0.5);
  background: #f7fafd;
}
.role-card--active,
.role-card--active:hover {
  border-color: var(--ad-primary);
  background: var(--ad-primary-light);
  box-shadow: 0 0 0 1px var(--ad-primary) inset;
}
.role-card:focus-visible,
.demo-card:focus-visible {
  outline: 2px solid rgba(47, 109, 163, 0.45);
  outline-offset: 1px;
}
.role-card__check {
  position: absolute;
  top: 4px;
  right: 5px;
  color: var(--ad-primary);
  opacity: 0;
  transform: scale(0.6);
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.role-card--active .role-card__check {
  opacity: 1;
  transform: scale(1);
}
</style>
