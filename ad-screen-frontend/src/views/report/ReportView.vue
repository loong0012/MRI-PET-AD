<script setup lang="ts">
/**
 * 筛查报告预览导出页（左右分栏）
 * ------------------------------------------------------------------
 * 左侧：A4 纵向 PDF 实时预览（含"AI 辅助筛查，不可替代临床诊断"医疗免责水印），
 *       完整呈现患者信息 / 多模态影像截图 / AI 量化数据 / 风险结论 / 干预建议；
 *       报告模板支持后端动态配置（简版 / 详版 / 科研版），字段可勾选、抬头可配置。
 * 右侧：操作栏 —— PDF 导出、在线打印、病例数据云端保存。
 * 顶部：模板选择下拉框 + 模板管理弹窗入口。
 * 打印管线：全局 @media print 仅输出 .print-area（A4 预览区）。
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiGetCase } from '@/api/case'
import { apiGetReportData, apiSaveReportCloud, interventionToSummary } from '@/api/report'
import { apiListReportTemplates } from '@/api/reportTemplate'
import { apiListVersions, apiReviewVersion, type AnalysisVersion } from '@/api/analysis'
import { caseSeed, makeThumbnail } from '@/utils/imaging'
import { apiGetImagingMeta, apiGetRealThumbnail, apiGradcamUrl, DEFAULT_MID_SLICE } from '@/api/imaging'
import { ensureMediaToken } from '@/utils/mediaToken'
import { useUserStore } from '@/stores/user'
import { printSection, exportElementToPdf } from '@/utils/export'
import { formatDate } from '@/utils/format'
import type { ReportData } from '@/types/report'
import type { CaseRecord } from '@/types/case'
import type { ReportTemplate } from '@/types/reportTemplate.d'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ReportTemplateDialog from '@/components/ReportTemplateDialog.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const caseId = computed(() => String(route.params.caseId ?? ''))

// ---------- 页面数据 ----------
const data = ref<ReportData | null>(null)
const thumbs = ref<{ mri: string; pet: string; fusion: string }>({ mri: '', pet: '', fusion: '' })
const loading = ref(true)

// ---------- 报告模板（后端可配置） ----------
const templates = ref<ReportTemplate[]>([])
const activeTemplateId = ref<number | null>(null)
const activeTemplateInfo = computed(() => templates.value.find((t) => t.id === activeTemplateId.value) ?? null)
const templateDialogVisible = ref(false)

// ---------- 患者版 / 医生版切换 ----------
// 患者版：仅显示通俗科普正文（patientFriendly.summary + lifestyle + followUp），无技术细节
// 医生版：完整医生报告 + NIA-AA 框架 + 矛盾证据 + 患者通俗版预览
const patientMode = ref(false)

/** 模板类型 → 中文标签（含患者科普版） */
const TEMPLATE_TYPE_LABEL: Record<string, string> = {
  brief: '简版',
  detailed: '详版',
  research: '科研版',
  patient: '患者科普版'
}

async function loadTemplates(): Promise<void> {
  try {
    const res = await apiListReportTemplates()
    templates.value = res.list
    // 优先使用默认模板；否则取第一个
    const def = res.list.find((t) => t.isDefault)
    if (def) activeTemplateId.value = def.id
    else if (res.list.length > 0) activeTemplateId.value = res.list[0].id
  } catch {
    templates.value = []
  }
}

function onTemplateChanged(t: ReportTemplate | null): void {
  // 模板新增/编辑/删除/设为默认后刷新当前列表；保持选中已选模板
  void loadTemplates().then(() => {
    if (t && templates.value.some((x) => x.id === t.id)) {
      activeTemplateId.value = t.id
    } else if (templates.value.length > 0 && !templates.value.some((x) => x.id === activeTemplateId.value)) {
      const def = templates.value.find((x) => x.isDefault)
      activeTemplateId.value = def?.id ?? templates.value[0].id
    }
  })
}

/** 当前模板字段显隐配置（13 个可勾选字段；缺失键按默认值处理） */
const activeContent = computed(() => {
  const cs = activeTemplateInfo.value?.contentStructure ?? {}
  return {
    patientInfo: cs.patientInfo === true || cs.patientInfo === undefined,
    examInfo: cs.examInfo === true || cs.examInfo === undefined,
    aiResult: cs.aiResult === true || cs.aiResult === undefined,
    imagingDesc: cs.imagingDesc === true || cs.imagingDesc === undefined,
    riskStratification: cs.riskStratification === true || cs.riskStratification === undefined,
    interventionAdvice: cs.interventionAdvice === true || cs.interventionAdvice === undefined,
    followupPlan: cs.followupPlan === true || cs.followupPlan === undefined,
    gradcamImage: cs.gradcamImage === true,
    brainMetrics: cs.brainMetrics === true,
    disclaimer: cs.disclaimer === true || cs.disclaimer === undefined,
    // 3 个新增字段（默认 False，需模板显式开启）
    niaaAlignment: cs.niaaAlignment === true,
    conflictEvidence: cs.conflictEvidence === true,
    patientFriendly: cs.patientFriendly === true
  }
})

/** 报告抬头配置（医院 / 科室 / 标题 / Logo URL） */
const activeHeader = computed(() => activeTemplateInfo.value?.headerConfig ?? null)

/** 媒体票据版本号：导出前刷新票据后自增，驱动热图 URL 与 <img> 用新票据重新加载 */
const mediaTick = ref(0)

/** GradCAM 热图 URL（仅当字段开启时使用；依赖 mediaTick 以便票据续期后重建） */
const gradcamUrl = computed(() => {
  if (!activeContent.value.gradcamImage || !data.value) return ''
  const url = apiGradcamUrl(caseId.value, DEFAULT_MID_SLICE, 'axial')
  // 读取 mediaTick 建立响应式依赖（恒为非负），票据刷新后本 computed 重建 URL
  return mediaTick.value >= 0 ? url : ''
})

// ---------- 云端保存 ----------
const cloudSaving = ref(false)
const cloudConfirmVisible = ref(false)
async function saveCloud(): Promise<void> {
  if (!data.value) return
  cloudSaving.value = true
  try {
    await apiSaveReportCloud(data.value, userStore.userInfo?.username ?? 'unknown')
    ElMessage.success('报告与病例数据已云端保存归档')
  } finally {
    cloudSaving.value = false
    cloudConfirmVisible.value = false
  }
}

// ---------- PDF 导出 / 打印 ----------
const reportRef = ref<HTMLElement | null>(null)
const exporting = ref(false)

async function exportPdf(): Promise<void> {
  if (!reportRef.value) {
    ElMessage.warning('报告内容尚未加载')
    return
  }
  exporting.value = true
  try {
    // 媒体票据 10 分钟有效：页面久留后热图 URL 内的票据可能已过期，
    // html2canvas 会按 URL 重新请求，必须先续票并等 <img> 用新 URL 加载完成，否则 PDF 缺热图
    if (activeContent.value.gradcamImage && gradcamUrl.value) {
      await ensureMediaToken()
      mediaTick.value += 1
      await nextTick()
      const img = reportRef.value.querySelector('img[alt="GradCAM"]') as HTMLImageElement | null
      if (img && !img.complete) {
        await Promise.race([
          new Promise<void>((resolve) => {
            img.onload = () => resolve()
            img.onerror = () => resolve()
          }),
          new Promise<void>((resolve) => setTimeout(resolve, 5000)) // 最多等 5s，避免卡住导出
        ])
      }
    }
    await exportElementToPdf(reportRef.value, `AD筛查报告_${caseId.value}_${formatDate(new Date())}`)
    ElMessage.success('PDF 已导出')
  } catch (e) {
    console.error(e)
    ElMessage.error('PDF 导出失败，请尝试在线打印另存为 PDF')
  } finally {
    exporting.value = false
  }
}
function printReport(): void {
  printSection()
}

// ---------- 历史版本与协作审核 ----------
const versions = ref<AnalysisVersion[]>([])
const versionDialogVisible = ref(false)
/** 对比选择的两个版本 id（旧 → 新） */
const compareA = ref<number | null>(null)
const compareB = ref<number | null>(null)

const REVIEW_TAG: Record<string, { type: 'info' | 'success' | 'danger'; text: string }> = {
  pending: { type: 'info', text: '待审核' },
  approved: { type: 'success', text: '已通过' },
  rejected: { type: 'danger', text: '已驳回' }
}

async function loadVersions(): Promise<void> {
  try {
    versions.value = await apiListVersions(caseId.value)
    // 默认选中最近两个版本对比
    if (versions.value.length >= 2) {
      compareB.value = versions.value[0].id
      compareA.value = versions.value[1].id
    }
  } catch {
    versions.value = []
  }
}

function openVersionDialog(): void {
  versionDialogVisible.value = true
  void loadVersions()
}

/** 版本对比行 */
interface DiffRow {
  label: string
  oldVal: string
  newVal: string
  changed: boolean
}

const diffRows = computed<DiffRow[]>(() => {
  const a = versions.value.find((v) => v.id === compareA.value)
  const b = versions.value.find((v) => v.id === compareB.value)
  if (!a?.result || !b?.result) return []
  const ra = a.result
  const rb = b.result
  const num = (v: number, d = 2): string => v.toFixed(d)
  const level = (k: string): string => LEVEL_TEXT[k] ?? k
  const rows: DiffRow[] = [
    { label: 'AD 风险评分', oldVal: String(ra.riskScore), newVal: String(rb.riskScore), changed: ra.riskScore !== rb.riskScore },
    { label: '风险等级', oldVal: level(ra.riskLevel), newVal: level(rb.riskLevel), changed: ra.riskLevel !== rb.riskLevel },
    { label: '病程分期', oldVal: ra.stage, newVal: rb.stage, changed: ra.stage !== rb.stage },
    { label: '推理置信度', oldVal: `${num(ra.confidence * 100, 1)}%`, newVal: `${num(rb.confidence * 100, 1)}%`, changed: ra.confidence !== rb.confidence },
    { label: '左侧海马体积', oldVal: `${num(ra.hippocampusVolumeL)} cm³`, newVal: `${num(rb.hippocampusVolumeL)} cm³`, changed: ra.hippocampusVolumeL !== rb.hippocampusVolumeL },
    { label: '右侧海马体积', oldVal: `${num(ra.hippocampusVolumeR)} cm³`, newVal: `${num(rb.hippocampusVolumeR)} cm³`, changed: ra.hippocampusVolumeR !== rb.hippocampusVolumeR },
    { label: '全脑代谢 SUV', oldVal: num(ra.meanSUV), newVal: num(rb.meanSUV), changed: ra.meanSUV !== rb.meanSUV },
    { label: '皮层平均厚度', oldVal: `${num(ra.corticalThickness)} mm`, newVal: `${num(rb.corticalThickness)} mm`, changed: ra.corticalThickness !== rb.corticalThickness },
    { label: '异常脑区数', oldVal: `${ra.abnormalRegions.length} 处`, newVal: `${rb.abnormalRegions.length} 处`, changed: ra.abnormalRegions.length !== rb.abnormalRegions.length },
    { label: '融合策略', oldVal: ra.fusionStrategy === 'feature' ? '特征级' : '像素级', newVal: rb.fusionStrategy === 'feature' ? '特征级' : '像素级', changed: ra.fusionStrategy !== rb.fusionStrategy }
  ]
  return rows
})

/** 提交审核（通过 / 驳回，带备注） */
async function review(ver: AnalysisVersion, action: 'approve' | 'reject'): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt(
      action === 'approve' ? '请输入审核意见（可选）' : '请输入驳回原因',
      action === 'approve' ? '审核通过' : '审核驳回',
      {
        confirmButtonText: action === 'approve' ? '确认通过' : '确认驳回',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: action === 'approve' ? '审核意见…' : '驳回原因（必填）',
        inputValidator: (val: string) => (action === 'reject' && !val?.trim() ? '驳回必须填写原因' : true)
      }
    )
    const reviewer = userStore.realName || userStore.userInfo?.username || 'rad01'
    await apiReviewVersion(ver.id, action, reviewer, value || '')
    ElMessage.success(action === 'approve' ? '已审核通过' : '已驳回')
    await loadVersions()
  } catch (e) {
    if (e !== 'cancel' && e instanceof Error) ElMessage.error('审核提交失败')
  }
}

// ---------- 报告结构化内容 ----------
const LEVEL_TEXT: Record<string, string> = {
  low: '低风险',
  mci: '轻度认知障碍（MCI）',
  'ad-early': 'AD 早期',
  'ad-late': 'AD 中晚期'
}

/** 干预建议摘要（按模块聚合） */
const interventionSummary = computed<string[]>(() =>
  data.value ? interventionToSummary(data.value.interventions, data.value.followUp) : []
)

/** 模板差异化展示开关（向后兼容模板内的 show 引用） */
const show = computed(() => ({
  metricsDetail: activeContent.value.brainMetrics,        // 脑区结构指标开启时显示量化数据
  interventions: activeContent.value.interventionAdvice || activeContent.value.followupPlan,
  researchExtra: activeContent.value.brainMetrics         // 脑区结构指标同时控制科研版块
}))

/** 报告扩展字段（null 安全访问，便于模板类型收窄） */
const niaaData = computed(() => data.value?.niaaAlignment ?? null)
const conflictData = computed(() => data.value?.conflictEvidence ?? null)
const patientData = computed(() => data.value?.patientFriendly ?? null)

/** NIA-AA A/T/N chip 颜色映射（A+/A-/A_unknown、T+/T-、N+/N- → el-tag type） */
const NIAA_TAG_TYPE: Record<string, 'success' | 'warning' | 'info' | 'danger'> = {
  'A+': 'warning',
  'A-': 'success',
  'A_unknown': 'info',
  'T+': 'warning',
  'T-': 'success',
  'N+': 'danger',
  'N-': 'success'
}

/** 2026 版指南：配准质量等级 → 中文标签（报告页展示） */
function reportRegLabel(q: 'good' | 'acceptable' | 'poor'): string {
  return q === 'good' ? '配准良好' : q === 'acceptable' ? '配准可接受' : '配准较差'
}

/** 加载失败错误态（网络/服务异常时停留本页并允许重试，而不是误报"未分析"强跳阅片页） */
const loadError = ref(false)

/** 判断错误是否表示"病例不存在"（后端 fail() 走 HTTP 200 + code:404；mock 为消息文本） */
function isCaseNotFound(e: unknown): boolean {
  const err = e as { code?: number; message?: string } | null
  return err?.code === 404 || err?.message === '病例不存在'
}

async function loadReport(): Promise<void> {
  // 预取短期媒体票据，保证 Grad-CAM <img> 渲染时 URL 凭证已就绪
  void ensureMediaToken()
  // 加载报告模板（后端首次访问自动初始化 3 套默认模板）
  void loadTemplates()
  loading.value = true
  loadError.value = false
  try {
    let c: CaseRecord | null | undefined = null
    try {
      c = await apiGetCase(caseId.value)
    } catch (e) {
      if (isCaseNotFound(e)) {
        // 提示已由请求层统一给出，直接回病例库
        router.push('/cases')
        return
      }
      throw e
    }
    if (!c) {
      router.push('/cases')
      return
    }
    let report: ReportData | null = null
    try {
      report = await apiGetReportData(caseId.value, userStore.realName || userStore.roleName)
    } catch (e) {
      // 仅"病例不存在（404）"才离开；网络抖动/服务 500 等保留在错误态，避免误导去阅片页
      if (isCaseNotFound(e)) {
        router.push('/cases')
        return
      }
      throw e
    }
    if (!report) {
      ElMessage.warning('请先完成 AI 分析后再生成报告')
      router.push(`/viewer/${caseId.value}`)
      return
    }
    data.value = report
    // 三模态关键层面截图：真实病例优先真实切片，失败回退合成
    const seed = caseSeed(caseId.value)
    const fallback = {
      mri: makeThumbnail('MRI', seed),
      pet: makeThumbnail('PET', seed),
      fusion: makeThumbnail('FUSION', seed)
    }
    thumbs.value = fallback
    try {
      const meta = await apiGetImagingMeta(caseId.value)
      if (meta.available) {
        const mid = Math.floor(meta.sliceCount / 2)
        const [mri, pet, fusion] = await Promise.all([
          meta.mri ? apiGetRealThumbnail(caseId.value, 'MRI', mid) : Promise.resolve(null),
          meta.pet ? apiGetRealThumbnail(caseId.value, 'PET', mid) : Promise.resolve(null),
          meta.mri && meta.pet ? apiGetRealThumbnail(caseId.value, 'FUSION', mid) : Promise.resolve(null)
        ])
        thumbs.value = {
          mri: mri ?? fallback.mri,
          pet: pet ?? fallback.pet,
          fusion: fusion ?? fallback.fusion
        }
      }
    } catch {
      // 真实影像不可用，沿用合成缩略图
    }
  } catch {
    // 请求层已给出具体错误提示，这里仅切换到可重试的错误态视图（不再白屏/误跳转）
    loadError.value = true
  } finally {
    loading.value = false
  }
}

onMounted(loadReport)
</script>

<template>
  <div v-loading="loading" class="relative h-full flex flex-col gap-3 p-4 bg-page overflow-hidden">
    <!-- 加载失败错误态：网络/服务异常时停留并允许重试，不白屏、不误跳转 -->
    <div
      v-if="loadError"
      class="absolute inset-0 z-20 bg-page flex flex-col items-center justify-center gap-4"
    >
      <el-icon :size="40" class="text-hint"><WarningFilled /></el-icon>
      <div class="text-[15px] text-ink-secondary">报告数据加载失败，请检查网络连接后重试</div>
      <div class="flex gap-3">
        <el-button @click="router.push('/cases')">返回病例库</el-button>
        <el-button type="primary" :loading="loading" @click="loadReport">重新加载</el-button>
      </div>
    </div>
    <!-- ==================== 顶部工具栏：模板选择 + 模板管理 ==================== -->
    <div class="card-ad px-4 py-2.5 flex items-center gap-3 shrink-0">
      <div class="flex items-center gap-2 text-[13px] font-semibold text-ink">
        <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
        报告模板
      </div>
      <el-select
        v-model="activeTemplateId"
        placeholder="选择报告模板"
        size="default"
        class="!w-[240px]"
        :disabled="templates.length === 0"
      >
        <el-option
          v-for="t in templates"
          :key="t.id"
          :value="t.id"
          :label="t.name"
        >
          <span class="flex items-center gap-2">
            <el-tag v-if="t.isDefault" size="small" type="success" effect="light" round>默认</el-tag>
            <span>{{ t.name }}</span>
            <span class="text-[11px] text-hint ml-1">
              {{ TEMPLATE_TYPE_LABEL[t.templateType] ?? t.templateType }}
            </span>
          </span>
        </el-option>
      </el-select>
      <el-button :icon="'Setting'" @click="templateDialogVisible = true">模板管理</el-button>
      <div v-if="activeTemplateInfo" class="ml-2 text-[12px] text-hint">
        共 {{ Object.values(activeTemplateInfo.contentStructure).filter(Boolean).length }} 个字段
        <span class="ml-1">·</span>
        <span class="ml-1">{{ activeTemplateInfo.headerConfig.hospitalName || '默认抬头' }}</span>
      </div>
      <div class="flex-1" />
      <!-- 医生版 / 患者版切换（患者版仅显示通俗科普正文，便于患者沟通） -->
      <el-switch
        v-model="patientMode"
        active-text="患者版"
        inactive-text="医生版"
        inline-prompt
        class="mr-2"
      />
      <div v-if="data" class="text-[11px] text-hint">
        病例 <span class="font-num text-ink">{{ data.caseInfo.id }}</span>
      </div>
    </div>

    <!-- ==================== 主体：左 + 右 ==================== -->
    <div class="flex-1 flex gap-4 overflow-hidden">
      <!-- ==================== 左侧：A4 报告实时预览 ==================== -->
      <div class="flex-1 min-w-0 overflow-y-auto">
        <div
          v-if="data"
          ref="reportRef"
          class="print-area report-a4 bg-white mx-auto shadow-pop relative overflow-hidden"
        >
          <!-- 医疗免责水印层（斜向平铺，打印保留） -->
          <div class="watermark-layer" aria-hidden="true">
            <span v-for="i in 12" :key="i" class="watermark-item">AI 辅助筛查 · 不可替代临床诊断</span>
          </div>

          <div class="relative px-12 py-10 min-h-[1123px] flex flex-col">
            <!-- 报告头（使用模板抬头配置 + 项目品牌） -->
            <div class="flex items-start justify-between pb-4 border-b-2 border-primary">
              <div class="flex items-start gap-3">
                <!-- 项目 Logo：脑部扫描标识 -->
                <div class="w-12 h-12 rounded-xl brand-gradient-bg flex items-center justify-center text-white shrink-0 shadow-[0_4px_12px_rgba(47,109,163,0.25)]">
                  <svg width="26" height="26" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <circle cx="16" cy="16" r="13" stroke="currentColor" stroke-width="1.4" opacity="0.35" />
                    <path d="M16 6.5 C12 6.5 8.5 10 8.5 15 C8.5 19.5 11 23 15 24.5 C14 22.5 13.5 20.5 14 18.5 C12.8 17.3 12.5 15.5 13 14 C12.5 12.3 13.5 10.8 15.2 10.3 C15.2 9 15.5 7.7 16 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
                    <path d="M16 6.5 C20 6.5 23.5 10 23.5 15 C23.5 19.5 21 23 17 24.5 C18 22.5 18.5 20.5 18 18.5 C19.2 17.3 19.5 15.5 19 14 C19.5 12.3 18.5 10.8 16.8 10.3 C16.8 9 16.5 7.7 16 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
                    <path d="M16 7.5 L16 24" stroke="currentColor" stroke-width="1.2" opacity="0.45" stroke-linecap="round" />
                    <circle cx="16" cy="16" r="1.8" fill="currentColor" />
                    <circle cx="3.5" cy="16" r="1.1" fill="currentColor" opacity="0.5" />
                    <circle cx="28.5" cy="16" r="1.1" fill="currentColor" opacity="0.5" />
                  </svg>
                </div>
                <div>
                  <div class="text-[19px] font-bold text-ink tracking-wide">
                    {{ activeHeader?.title || '脑影·明衰  阿尔茨海默病多模态影像筛查报告' }}
                  </div>
                  <div class="mt-1 text-[11px] text-hint tracking-[1.5px]">
                    {{ activeHeader?.subtitle || 'BrainImaging · Dementia Insight — MRI/PET 融合智能诊断' }}
                  </div>
                  <div v-if="activeHeader?.department" class="mt-1 text-[12px] text-sub">
                    {{ activeHeader.department }}
                  </div>
                </div>
              </div>
              <div class="text-right text-[11px] text-sub leading-5">
                <div class="font-num">报告编号：{{ data.reportNo }}</div>
                <div class="font-num">报告日期：{{ data.reportDate }}</div>
                <div>{{ activeHeader?.hospitalName || data.hospital }}</div>
              </div>
            </div>

            <!-- ==================== 医生版正文（!patientMode） ==================== -->
            <template v-if="!patientMode">
              <!-- 患者信息（patientInfo + examInfo 字段控制） -->
              <section v-if="activeContent.patientInfo || activeContent.examInfo" class="mt-5">
                <h3 class="report-sec-title">一、患者基本信息</h3>
                <div class="grid grid-cols-3 gap-y-2 gap-x-6 text-[13px] mt-3">
                  <div v-if="activeContent.patientInfo" class="info-item"><label>姓名</label><span>{{ data.caseInfo.patient.name }}</span></div>
                  <div v-if="activeContent.patientInfo" class="info-item"><label>性别</label><span>{{ data.caseInfo.patient.gender === 'M' ? '男' : '女' }}</span></div>
                  <div v-if="activeContent.patientInfo" class="info-item"><label>年龄</label><span>{{ data.caseInfo.patient.age }} 岁</span></div>
                  <div v-if="activeContent.patientInfo" class="info-item"><label>病例编号</label><span class="font-num">{{ data.caseInfo.id }}</span></div>
                  <div v-if="activeContent.patientInfo" class="info-item"><label>患者编号</label><span class="font-num">{{ data.caseInfo.patient.patientNo }}</span></div>
                  <div v-if="activeContent.examInfo" class="info-item"><label>影像模态</label><span class="font-num">{{ data.caseInfo.modality }}</span></div>
                  <div v-if="activeContent.examInfo" class="info-item"><label>检查时间</label><span class="font-num">{{ data.caseInfo.examDate }}</span></div>
                  <div v-if="activeContent.examInfo" class="info-item"><label>申请科室</label><span>{{ data.caseInfo.department }}</span></div>
                  <div v-if="activeContent.examInfo" class="info-item"><label>检查项目</label><span>头颅 MRI + FDG-PET 多模态融合筛查</span></div>
                </div>
              </section>

              <!-- 影像截图（imagingDesc 字段控制） -->
              <section v-if="activeContent.imagingDesc" class="mt-5">
                <h3 class="report-sec-title">二、多模态影像关键层面</h3>
                <div class="grid grid-cols-3 gap-3 mt-3">
                  <div class="relative rounded border border-line overflow-hidden viewport-dark">
                    <img :src="thumbs.mri" class="w-full block" alt="MRI" />
                    <span class="absolute left-1.5 top-1.5 text-[10px] text-white/85 font-num">MRI T1WI</span>
                  </div>
                  <div class="relative rounded border border-line overflow-hidden viewport-dark">
                    <img :src="thumbs.pet" class="w-full block" alt="PET" />
                    <span class="absolute left-1.5 top-1.5 text-[10px] text-white/85 font-num">PET FDG</span>
                  </div>
                  <div class="relative rounded border border-line overflow-hidden viewport-dark">
                    <img :src="thumbs.fusion" class="w-full block" alt="融合" />
                    <span class="absolute left-1.5 top-1.5 text-[10px] text-white/85 font-num">融合配准</span>
                  </div>
                </div>
              </section>

              <!-- GradCAM 热图（gradcamImage 字段控制；详版/科研版显示） -->
              <section v-if="activeContent.gradcamImage && gradcamUrl" class="mt-5">
                <h3 class="report-sec-title">三、AI 注意力热图（Grad-CAM）</h3>
                <div class="grid grid-cols-2 gap-3 mt-3">
                  <div class="relative rounded border border-line overflow-hidden viewport-dark">
                    <img :src="gradcamUrl" class="w-full block" alt="GradCAM" />
                    <span class="absolute left-1.5 top-1.5 text-[10px] text-white/85 font-num">代谢偏差热力图</span>
                  </div>
                  <div class="text-[12px] text-sub leading-6 p-2">
                    Grad-CAM 揭示模型判定高风险的关键脑区（红/黄色高激活区域），
                    多分布于后扣带回 / 楔前叶 / 双侧海马区域，与 NMI-FDG 异常分布一致。
                  </div>
                </div>
              </section>

              <!-- 风险结论（aiResult + riskStratification 字段控制） -->
              <section v-if="activeContent.aiResult" class="mt-5">
                <h3 class="report-sec-title">四、AI 风险评估结论</h3>
                <div class="mt-3 rounded-lg border border-primary/30 bg-primary-light/50 px-5 py-4">
                  <div class="flex items-center gap-5">
                    <div class="text-center shrink-0">
                      <div class="font-num text-[34px] leading-none font-bold text-primary">{{ data.analysis.riskScore }}</div>
                      <div class="text-[10px] text-hint mt-1">AD 风险评分（0-100）</div>
                    </div>
                    <el-divider direction="vertical" class="!h-12" />
                    <div class="flex-1">
                      <div v-if="activeContent.riskStratification" class="text-[14px] font-semibold text-ink">
                        风险等级：<span class="text-primary">{{ LEVEL_TEXT[data.analysis.riskLevel] }}</span>
                        <span class="ml-4">病程分期：{{ data.analysis.stage }}</span>
                      </div>
                      <p class="mt-2 text-[12.5px] text-sub leading-6">{{ data.analysis.summary }}</p>
                    </div>
                  </div>
                </div>
              </section>

              <!-- 量化数据（brainMetrics 字段控制；科研版才显示） -->
              <section v-if="show.metricsDetail" class="mt-5">
                <h3 class="report-sec-title">五、影像量化分析数据</h3>
                <table class="report-table mt-3">
                  <thead>
                    <tr>
                      <th>量化指标</th>
                      <th class="text-right">测量值</th>
                      <th class="text-right">参考范围</th>
                      <th class="text-center">提示</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="m in data.analysis.metrics" :key="m.key">
                      <td>{{ m.label }}</td>
                      <td class="text-right font-num">{{ m.value }} {{ m.unit }}</td>
                      <td class="text-right font-num text-hint">{{ m.refRange }}</td>
                      <td class="text-center">
                        <span :class="m.status === 'normal' ? 'text-risk-low' : m.status === 'warn' ? 'text-[#B97E14]' : 'text-risk-late'">
                          {{ m.status === 'normal' ? '正常' : m.status === 'warn' ? '临界' : '异常' }}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </section>

              <!-- 科研版附加：模型推理信息（brainMetrics 字段同时控制） -->
              <section v-if="show.researchExtra" class="mt-5">
                <h3 class="report-sec-title">六、模型推理信息（科研归档）</h3>
                <div class="grid grid-cols-4 gap-y-2 gap-x-6 text-[13px] mt-3">
                  <div class="info-item"><label>模型版本</label><span class="font-num">{{ data.analysis.modelVersion }}</span></div>
                  <div class="info-item"><label>融合策略</label><span>{{ data.analysis.fusionStrategy === 'feature' ? '特征级融合' : '像素级融合' }}</span></div>
                  <div class="info-item"><label>推理置信度</label><span class="font-num">{{ (data.analysis.confidence * 100).toFixed(1) }}%</span></div>
                  <div class="info-item"><label>推理耗时</label><span class="font-num">{{ data.analysis.inferenceTime }} s</span></div>
                </div>
              </section>

              <!-- 干预随访建议（interventionAdvice + followupPlan 字段控制） -->
              <section v-if="show.interventions" class="mt-5">
                <h3 class="report-sec-title">
                  {{ show.researchExtra ? '七、干预随访建议' : '六、干预随访建议' }}
                </h3>
                <ul class="mt-3 space-y-1.5 text-[12.5px] text-sub leading-6">
                  <li v-for="(line, i) in interventionSummary" :key="i" class="flex gap-2">
                    <span class="text-primary shrink-0">·</span>
                    <span>{{ line }}</span>
                  </li>
                </ul>
              </section>

              <!-- NIA-AA 框架对齐（niaaAlignment 字段控制；仅医生版显示） -->
              <section v-if="activeContent.niaaAlignment && niaaData" class="mt-5">
                <h3 class="report-sec-title">NIA-AA 框架对齐（A/T/N 生物标志物）</h3>
                <div class="mt-3 flex items-center gap-3 flex-wrap">
                  <el-tooltip :content="niaaData.A_reason" placement="top" :show-after="200">
                    <el-tag
                      :type="NIAA_TAG_TYPE[niaaData.A] ?? 'info'"
                      size="large"
                      effect="light"
                      round
                    >
                      <span class="font-semibold">A</span>：{{ niaaData.A }}
                    </el-tag>
                  </el-tooltip>
                  <el-tooltip :content="niaaData.T_reason" placement="top" :show-after="200">
                    <el-tag
                      :type="NIAA_TAG_TYPE[niaaData.T] ?? 'info'"
                      size="large"
                      effect="light"
                      round
                    >
                      <span class="font-semibold">T</span>：{{ niaaData.T }}
                    </el-tag>
                  </el-tooltip>
                  <el-tooltip :content="niaaData.N_reason" placement="top" :show-after="200">
                    <el-tag
                      :type="NIAA_TAG_TYPE[niaaData.N] ?? 'info'"
                      size="large"
                      effect="light"
                      round
                    >
                      <span class="font-semibold">N</span>：{{ niaaData.N }}
                    </el-tag>
                  </el-tooltip>
                  <span class="text-[11px] text-hint">悬停查看依据</span>
                </div>
                <p class="mt-2 text-[11.5px] text-hint leading-5">{{ niaaData.reasoning }}</p>
                <!-- 2026 版指南：生物学分期 -->
                <div v-if="niaaData.biologicalStage" class="mt-3 flex items-center gap-2 px-3 py-2 rounded bg-[#F0F5FB] border border-[#D0E0EF]">
                  <span class="text-[12px] text-hint">生物学分期</span>
                  <el-tag size="small" type="warning" effect="plain">{{ niaaData.biologicalStage }}</el-tag>
                  <span class="text-[11.5px] text-sub">{{ niaaData.biologicalStageDesc }}</span>
                </div>
              </section>

              <!-- 2026 版指南：ARIA 风险评估 -->
              <section v-if="data?.ariaRisk" class="mt-5">
                <h3 class="report-sec-title">ARIA 风险评估（抗 Aβ 单抗治疗相关）</h3>
                <div class="mt-3 px-4 py-3 rounded border" :class="{
                  'bg-[#FFF7ED] border-[#FDBA74]': data.ariaRisk.riskLevel === '高风险',
                  'bg-[#FFFBEB] border-[#FCD34D]': data.ariaRisk.riskLevel === '中风险',
                  'bg-[#F0FDF4] border-[#BBF7D0]': data.ariaRisk.riskLevel === '低风险',
                }">
                  <div class="flex items-center gap-3 mb-2">
                    <el-tag :type="data.ariaRisk.riskLevel === '高风险' ? 'danger' : data.ariaRisk.riskLevel === '中风险' ? 'warning' : 'success'" effect="dark" size="small">
                      {{ data.ariaRisk.riskLevel }}
                    </el-tag>
                    <span class="text-[11px] text-hint">风险评分 {{ data.ariaRisk.riskScore }} 分</span>
                  </div>
                  <ul class="text-[11.5px] text-sub leading-5 list-disc pl-4">
                    <li v-for="(f, i) in data.ariaRisk.factors" :key="i">{{ f }}</li>
                  </ul>
                  <p class="text-[11px] text-hint mt-2">{{ data.ariaRisk.recommendation }}</p>
                </div>
              </section>

              <!-- 2026 版指南：检查信息 -->
              <section v-if="data?.examIndication || data?.petTracer" class="mt-5">
                <h3 class="report-sec-title">检查信息（2026 版 PET/MRI 指南对齐）</h3>
                <div class="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[12px]">
                  <div v-if="data?.examIndication" class="flex">
                    <span class="text-hint w-24 shrink-0">检查适应证：</span>
                    <span class="text-ink">{{ data.examIndication }}</span>
                  </div>
                  <div v-if="data?.petTracer" class="flex">
                    <span class="text-hint w-24 shrink-0">PET 显像剂：</span>
                    <span class="text-ink">{{ data.petTracer }}</span>
                  </div>
                </div>
              </section>

              <!-- 2026 版指南：检查前评估摘要 -->
              <section v-if="data?.contraindications || data?.specialPopulation" class="mt-5">
                <h3 class="report-sec-title">检查前评估摘要（2026 版指南）</h3>
                <div class="mt-3 space-y-2">
                  <div v-if="data?.specialPopulation" class="text-[12.5px]">
                    <span class="text-hint">特殊人群：</span>
                    <span class="text-ink">{{ data.specialPopulation }}</span>
                  </div>
                  <div v-if="data?.contraindications" class="flex items-center gap-3">
                    <el-tag
                      :type="data.contraindications.cleared ? 'success' : 'danger'"
                      effect="dark"
                      size="small"
                    >{{ data.contraindications.cleared ? '筛查通过' : '存在禁忌' }}</el-tag>
                  </div>
                  <el-alert
                    v-for="(w, i) in data?.contraindications?.warnings ?? []"
                    :key="i"
                    type="warning"
                    :title="w"
                    :closable="false"
                    show-icon
                  />
                </div>
              </section>

              <!-- 2026 版指南：图像质量控制摘要 -->
              <section v-if="data?.imageQuality" class="mt-5">
                <h3 class="report-sec-title">图像质量控制摘要（2026 版指南）</h3>
                <div class="mt-3 text-[12.5px] text-sub leading-6 space-y-2">
                  <div>
                    <span class="text-hint">配准质量：</span>
                    <span class="text-ink">{{ reportRegLabel(data.imageQuality.registrationQuality) }}</span>
                    <span v-if="data.imageQuality.motionArtifact" class="text-[#B97E14] ml-3">· 存在运动伪影</span>
                  </div>
                  <el-alert
                    v-for="(w, i) in data.imageQuality.warnings"
                    :key="i"
                    type="warning"
                    :title="w"
                    :closable="false"
                    show-icon
                  />
                </div>
              </section>

              <!-- 2026 版指南表5：检查所见（结构化分节） -->
              <section v-if="data?.examFindings" class="mt-5">
                <h3 class="report-sec-title">检查所见（指南表5 结构化分节）</h3>
                <div class="mt-3 space-y-3 text-[12.5px] text-sub leading-6">
                  <!-- MRI 表现 -->
                  <div v-if="data.examFindings.mriFindings">
                    <div class="font-medium text-ink mb-1">MRI 表现</div>
                    <div class="grid grid-cols-2 gap-x-6 gap-y-1">
                      <div>对称性：{{ data.examFindings.mriFindings.symmetry }}</div>
                      <div>白质高信号：{{ data.examFindings.mriFindings.whiteMatterHyperintensity }}</div>
                      <div>脑室脑沟：{{ data.examFindings.mriFindings.ventricleSulci }}</div>
                      <div>海马萎缩：{{ data.examFindings.mriFindings.hippocampusAtrophy }}</div>
                      <div>SWI 出血点：{{ data.examFindings.mriFindings.swiHemorrhage }}</div>
                      <div>皮层厚度：{{ data.examFindings.mriFindings.corticalThickness }} mm</div>
                    </div>
                  </div>
                  <!-- PET 检查所见（按显像剂分支） -->
                  <div v-if="data.examFindings.petFindings">
                    <div class="font-medium text-ink mb-1">PET 检查所见 · {{ data.examFindings.petFindings.tracerName }}</div>
                    <!-- FDG -->
                    <template v-if="data.examFindings.petFindings.tracer === 'fdg' && data.examFindings.petFindings.fdgFindings">
                      <div class="text-sub">{{ data.examFindings.petFindings.fdgFindings.patternDescription }}</div>
                      <div class="mt-1">代谢减低区域：</div>
                      <ul class="list-disc pl-5">
                        <li v-for="(r, i) in data.examFindings.petFindings.fdgFindings.metabolicReduction" :key="i">{{ r }}</li>
                      </ul>
                      <div class="mt-1">平均 SUV：<span class="font-num">{{ data.examFindings.petFindings.fdgFindings.meanSUV }}</span></div>
                    </template>
                    <!-- Aβ -->
                    <template v-else-if="data.examFindings.petFindings.tracer === 'amyloid' && data.examFindings.petFindings.amyloidFindings">
                      <div class="text-sub">{{ data.examFindings.petFindings.amyloidFindings.conclusion }}</div>
                      <div class="flex flex-wrap gap-1.5 mt-1">
                        <el-tag
                          v-for="(p, i) in data.examFindings.petFindings.amyloidFindings.patterns"
                          :key="i"
                          size="small" type="warning" effect="plain" round
                        >{{ p }}</el-tag>
                      </div>
                      <div class="mt-1 flex items-center gap-2">
                        <span>SUVR：<span class="font-num">{{ data.examFindings.petFindings.amyloidFindings.suvr }}</span></span>
                        <el-tag
                          :type="data.examFindings.petFindings.amyloidFindings.positive ? 'danger' : 'success'"
                          effect="dark" size="small"
                        >{{ data.examFindings.petFindings.amyloidFindings.positive ? '阳性' : '阴性' }}</el-tag>
                      </div>
                    </template>
                    <!-- Tau -->
                    <template v-else-if="data.examFindings.petFindings.tracer === 'tau' && data.examFindings.petFindings.tauFindings">
                      <div class="text-sub">{{ data.examFindings.petFindings.tauFindings.distribution }}</div>
                      <div class="mt-1 flex items-center gap-2">
                        <span>Braak 分期：</span>
                        <el-tag size="small" type="primary" effect="light" round>{{ data.examFindings.petFindings.tauFindings.braakStage }}</el-tag>
                      </div>
                      <div class="mt-1 flex items-center gap-2">
                        <span>SUVR：<span class="font-num">{{ data.examFindings.petFindings.tauFindings.suvr }}</span></span>
                        <el-tag
                          :type="data.examFindings.petFindings.tauFindings.positive ? 'danger' : 'success'"
                          effect="dark" size="small"
                        >{{ data.examFindings.petFindings.tauFindings.positive ? '阳性' : '阴性' }}</el-tag>
                      </div>
                    </template>
                  </div>
                </div>
              </section>

              <!-- 2026 版指南表5：5 段式诊断意见 -->
              <section v-if="data?.diagnosticConclusions?.sections?.length" class="mt-5">
                <h3 class="report-sec-title">诊断意见（指南表5 5 段式）</h3>
                <div class="mt-3 space-y-3 text-[12.5px] text-sub leading-6">
                  <div
                    v-for="(s, i) in data.diagnosticConclusions.sections"
                    :key="i"
                    class="border-l-2 border-primary/40 pl-3"
                  >
                    <div class="font-medium text-ink">{{ s.title }}</div>
                    <p class="mt-1">{{ s.content }}</p>
                    <div class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-hint">
                      <span v-if="s.meanSUV !== undefined">平均 SUV：<span class="font-num text-ink">{{ s.meanSUV }}</span></span>
                      <span v-if="s.suvr !== null && s.suvr !== undefined">SUVR：<span class="font-num text-ink">{{ s.suvr }}</span></span>
                      <span v-if="s.braakStage">Braak：<span class="text-ink">{{ s.braakStage }}</span></span>
                      <span v-if="s.mta">MTA：<span class="text-ink">{{ s.mta }}</span></span>
                      <span v-if="s.fazekas">Fazekas：<span class="text-ink">{{ s.fazekas }}</span></span>
                      <span v-if="s.ariaRisk">ARIA：<span class="text-ink">{{ s.ariaRisk }}</span></span>
                      <span v-if="s.biologicalStage">生物学分期：<span class="text-ink">{{ s.biologicalStage }}</span></span>
                    </div>
                  </div>
                </div>
              </section>

              <!-- 第 27 轮新增：2026 版指南推荐意见汇总 -->
              <section v-if="data?.recommendationsSummary" class="mt-5">
                <h3 class="report-sec-title">{{ data.recommendationsSummary.title }}</h3>
                <div class="mt-2 mb-3 flex items-center gap-2 text-[12px] text-hint">
                  <span>共 {{ data.recommendationsSummary.totalCount }} 条推荐意见</span>
                  <el-tag size="small" type="success" effect="plain">已应用 {{ data.recommendationsSummary.appliedCount }} 条</el-tag>
                </div>
                <div class="mt-3 space-y-2.5 text-[12.5px] text-sub leading-6">
                  <div
                    v-for="rec in data.recommendationsSummary.recommendations"
                    :key="rec.id"
                    class="border-l-2 pl-3"
                    :class="rec.applied ? 'border-primary/60' : 'border-hint/30'"
                  >
                    <div class="flex items-center gap-2 flex-wrap mb-1">
                      <span class="font-medium text-ink">{{ rec.id }}. {{ rec.topic }}</span>
                      <el-tag size="small" type="warning" effect="plain" round>{{ rec.grade }}</el-tag>
                      <el-tag v-if="rec.applied" size="small" type="success" effect="dark">已应用</el-tag>
                    </div>
                    <p class="text-sub">{{ rec.recommendation }}</p>
                    <div class="mt-1 text-[11px] text-hint">→ {{ rec.note }}</div>
                  </div>
                </div>
                <div class="mt-3 pt-3 border-t border-[#E3E9EF] text-[11px] text-hint italic">
                  指南依据：{{ data.recommendationsSummary.guidelineReference }}
                </div>
              </section>

              <!-- 第 28 轮新增：临床决策辅助（按推荐意见生成下一步建议） -->
              <section v-if="data?.clinicalDecisionSupport" class="mt-5">
                <h3 class="report-sec-title">{{ data.clinicalDecisionSupport.title }}</h3>
                <div class="mt-2 mb-3 flex items-center gap-2 text-[12px] text-hint">
                  <span>共 {{ data.clinicalDecisionSupport.totalRecommendations }} 条决策建议</span>
                  <el-tag size="small" type="primary" effect="plain" round>指南驱动</el-tag>
                </div>
                <div class="mt-3 space-y-2.5 text-[12.5px] text-sub leading-6">
                  <div
                    v-for="(rec, i) in data.clinicalDecisionSupport.recommendations"
                    :key="i"
                    class="border-l-2 pl-3 py-1"
                    :class="{
                      'border-danger/70': rec.priority === 'high',
                      'border-warning/70': rec.priority === 'medium',
                      'border-info/50': rec.priority === 'low'
                    }"
                  >
                    <div class="flex items-center gap-2 flex-wrap mb-1">
                      <el-tag size="small" :type="rec.priority === 'high' ? 'danger' : (rec.priority === 'medium' ? 'warning' : 'info')" effect="dark">
                        {{ rec.priority === 'high' ? '高优先级' : (rec.priority === 'medium' ? '中优先级' : '低优先级') }}
                      </el-tag>
                      <span class="text-hint">{{ rec.category }}</span>
                    </div>
                    <p class="text-sub">{{ rec.action }}</p>
                    <div class="mt-1 text-[11px] text-hint">→ {{ rec.guidelineRef }}</div>
                  </div>
                </div>
                <div class="mt-3 pt-3 border-t border-[#E3E9EF] text-[11px] text-hint italic">
                  决策依据：{{ data.clinicalDecisionSupport.guidelineReference }}
                </div>
              </section>

              <!-- 矛盾证据（conflictEvidence 字段控制；仅医生版显示） -->
              <section v-if="activeContent.conflictEvidence && conflictData" class="mt-5">
                <h3 class="report-sec-title">矛盾证据（影像-认知 / 模态间不一致）</h3>
                <div class="mt-3 space-y-2">
                  <template v-if="conflictData.hasConflict">
                    <el-alert
                      v-for="(item, idx) in conflictData.items"
                      :key="idx"
                      type="warning"
                      :title="`矛盾 ${idx + 1}`"
                      :description="item.description"
                      :closable="false"
                      show-icon
                    />
                  </template>
                  <el-alert
                    v-else
                    type="success"
                    title="未发现矛盾证据"
                    :closable="false"
                    show-icon
                  />
                </div>
              </section>

              <!-- 患者通俗版预览（patientFriendly 字段控制；仅医生版预览） -->
              <section v-if="activeContent.patientFriendly && patientData" class="mt-5">
                <h3 class="report-sec-title">患者通俗版预览（仅供医生参考）</h3>
                <div class="mt-3 rounded-lg border border-line bg-page/40 px-5 py-4 space-y-3">
                  <div>
                    <div class="text-[12px] text-hint mb-1">通俗结论</div>
                    <p class="text-[13px] text-ink leading-6">{{ patientData.summary }}</p>
                  </div>
                  <div>
                    <div class="text-[12px] text-hint mb-1">生活建议</div>
                    <div class="flex flex-wrap gap-2">
                      <el-tag v-for="(l, i) in patientData.lifestyle" :key="i" size="small" effect="plain" round>
                        {{ l }}
                      </el-tag>
                    </div>
                  </div>
                  <div>
                    <div class="text-[12px] text-hint mb-1">随访建议</div>
                    <p class="text-[12.5px] text-sub leading-6">{{ patientData.followUp }}</p>
                  </div>
                </div>
              </section>

              <!-- 报告尾（disclaimer 字段控制免责声明显示） -->
              <div class="mt-auto pt-6 border-t border-line flex items-end justify-between">
                <div class="text-[12px] text-sub leading-6">
                  <div>报告医师：{{ data.doctorName }}</div>
                  <div v-if="activeContent.disclaimer" class="text-hint mt-1">
                    本报告经 AI 辅助筛查，不可替代临床诊断，请结合临床综合判定
                  </div>
                </div>
                <div class="text-[11px] text-hint text-right leading-5">
                  <div>{{ activeHeader?.hospitalName || data.hospital }}</div>
                  <div class="font-num">{{ data.systemName || '脑影·明衰' }} {{ data.systemVersion || 'v1.0.0' }}</div>
                  <div class="font-num">模型 {{ data.modelVersion || 'TransMF-15ens-v4' }}</div>
                  <div class="mt-0.5">{{ data.guideline || '2026 版 PET/MRI 脑成像临床应用指南' }}</div>
                </div>
              </div>
            </template>

            <!-- ==================== 患者版正文（patientMode） ==================== -->
            <template v-else>
              <!-- 患者版占位（无 patientFriendly 数据时） -->
              <div v-if="!patientData" class="mt-5 text-center text-[12px] text-hint py-12">
                暂无患者通俗版内容（请先完成 AI 分析）
              </div>
              <template v-else>
                <!-- 筛查结论 -->
                <section class="mt-5">
                  <h3 class="report-sec-title">筛查结论</h3>
                  <p class="mt-3 text-[14px] text-ink leading-7">{{ patientData.summary }}</p>
                </section>

                <!-- 生活与康复建议 -->
                <section class="mt-5">
                  <h3 class="report-sec-title">生活与康复建议</h3>
                  <ul class="mt-3 space-y-2 text-[13px] text-sub leading-6">
                    <li v-for="(l, i) in patientData.lifestyle" :key="i" class="flex gap-2">
                      <span class="text-primary shrink-0">·</span>
                      <span>{{ l }}</span>
                    </li>
                  </ul>
                </section>

                <!-- 复查与随访建议 -->
                <section class="mt-5">
                  <h3 class="report-sec-title">复查与随访建议</h3>
                  <p class="mt-3 text-[13px] text-sub leading-6">{{ patientData.followUp }}</p>
                </section>

                <!-- 家属常见问题（科普问答） -->
                <section v-if="patientData.faq?.length" class="mt-5">
                  <h3 class="report-sec-title">家属常见问题</h3>
                  <div class="mt-3 space-y-3">
                    <div
                      v-for="(item, i) in patientData.faq"
                      :key="i"
                      class="rounded-lg border border-line bg-page/40 px-4 py-3"
                    >
                      <div class="text-[13px] font-medium text-ink leading-6">
                        <span class="text-primary mr-1">Q{{ i + 1 }}.</span>
                        {{ item.q }}
                      </div>
                      <p class="mt-1.5 text-[12.5px] text-sub leading-6 pl-5">{{ item.a }}</p>
                    </div>
                  </div>
                </section>

                <!-- 照护者日常建议 -->
                <section v-if="patientData.caregiverAdvice?.length" class="mt-5">
                  <h3 class="report-sec-title">照护者日常建议</h3>
                  <ul class="mt-3 space-y-2 text-[13px] text-sub leading-6">
                    <li
                      v-for="(c, i) in patientData.caregiverAdvice"
                      :key="i"
                      class="flex gap-2"
                    >
                      <span class="text-primary shrink-0">·</span>
                      <span>{{ c }}</span>
                    </li>
                  </ul>
                </section>

                <!-- 需立即就诊的警示信号 -->
                <section v-if="patientData.warningSigns?.length" class="mt-5">
                  <h3 class="report-sec-title">需立即就诊的警示信号</h3>
                  <el-alert
                    type="warning"
                    :closable="false"
                    show-icon
                    class="mt-3"
                  >
                    <ul class="space-y-1.5 text-[12.5px] text-sub leading-6 pl-1">
                      <li
                        v-for="(w, i) in patientData.warningSigns"
                        :key="i"
                        class="flex gap-2"
                      >
                        <span class="text-[var(--ad-danger,#C94F4F)] shrink-0">!</span>
                        <span>{{ w }}</span>
                      </li>
                    </ul>
                  </el-alert>
                </section>
              </template>

              <!-- 患者版报告尾 -->
              <div class="mt-auto pt-6 border-t border-line flex items-end justify-between">
                <div class="text-[12px] text-sub leading-6">
                  <div>报告医师：{{ data.doctorName }}</div>
                  <div class="text-hint mt-1">
                    本报告经 AI 辅助筛查，不可替代临床诊断，请结合临床综合判定
                  </div>
                </div>
                <div class="text-[11px] text-hint text-right leading-5">
                  <div>{{ activeHeader?.hospitalName || data.hospital }}</div>
                  <div class="font-num">{{ data.systemName || '脑影·明衰' }} {{ data.systemVersion || 'v1.0.0' }}</div>
                  <div class="font-num">模型 {{ data.modelVersion || 'TransMF-15ens-v4' }}</div>
                  <div class="mt-0.5">{{ data.guideline || '2026 版 PET/MRI 脑成像临床应用指南' }}</div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- ==================== 右侧操作栏 ==================== -->
      <aside class="w-72 shrink-0 flex flex-col gap-4 overflow-y-auto">
        <!-- 当前模板信息（精简卡片，仅展示当前模板与切换入口） -->
        <div class="card-ad p-4">
          <div class="text-[14px] font-semibold text-ink mb-3 flex items-center gap-2">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            当前模板
          </div>
          <div v-if="activeTemplateInfo" class="space-y-2">
            <div class="flex items-center gap-2">
              <span class="text-[14px] font-medium text-ink">{{ activeTemplateInfo.name }}</span>
              <el-tag v-if="activeTemplateInfo.isDefault" size="small" type="success" effect="light" round>默认</el-tag>
            </div>
            <div class="text-[11.5px] text-hint leading-5">
              类型：{{ TEMPLATE_TYPE_LABEL[activeTemplateInfo.templateType] ?? activeTemplateInfo.templateType }}
              <br />
              字段：{{ Object.values(activeTemplateInfo.contentStructure).filter(Boolean).length }} 项显示
              <br />
              抬头：{{ activeTemplateInfo.headerConfig.hospitalName || '默认' }}
            </div>
            <el-button class="!w-full" :icon="'Setting'" @click="templateDialogVisible = true">模板管理</el-button>
          </div>
          <div v-else class="text-[12px] text-hint py-2">
            模板加载中…
          </div>
        </div>

        <!-- 导出与保存操作 -->
        <div class="card-ad p-4 space-y-2.5">
          <div class="text-[14px] font-semibold text-ink flex items-center gap-2 mb-1">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            报告操作
          </div>
          <el-button type="primary" class="!w-full" :icon="'Download'" :loading="exporting" @click="exportPdf">PDF 导出</el-button>
          <el-button class="!w-full" :icon="'Printer'" @click="printReport">在线打印</el-button>
          <el-button class="!w-full" :icon="'UploadFilled'" :loading="cloudSaving" @click="cloudConfirmVisible = true">
            病例数据云端保存
          </el-button>
          <!-- 云端归档状态 -->
          <div class="pt-2 flex items-center justify-between text-[12px]">
            <span class="text-hint">云端归档状态</span>
            <el-tag v-if="data" size="small" round :type="data.caseInfo.cloudSaved ? 'success' : 'info'" effect="light">
              {{ data.caseInfo.cloudSaved ? '已归档' : '未归档' }}
            </el-tag>
          </div>
        </div>

        <!-- 归档说明 -->
        <div class="card-ad p-4">
          <div class="text-[12px] text-hint leading-6">
            <p class="font-medium text-ink mb-1.5">归档说明</p>
            <p>云端保存后报告与干预方案随病例归档，诊断状态更新为「已出报告」，可在病例库中随时调阅。</p>
          </div>
        </div>

        <!-- 历史版本与协作审核 -->
        <div class="card-ad p-4">
          <div class="text-[14px] font-semibold text-ink flex items-center gap-2 mb-1">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            版本与审核
          </div>
          <p class="text-[11.5px] text-hint leading-5 mb-3">
            每次 AI 分析自动留存历史版本，支持两版本指标对比与多人协作审核。
          </p>
          <el-button class="!w-full" :icon="'Histogram'" @click="openVersionDialog">历史版本 / 审核</el-button>
        </div>
      </aside>
    </div>

    <!-- 报告模板管理弹窗 -->
    <ReportTemplateDialog v-model="templateDialogVisible" @template-changed="onTemplateChanged" />

    <!-- 历史版本与协作审核 -->
    <el-dialog v-model="versionDialogVisible" title="分析历史版本 · 对比与协作审核" width="860px" top="6vh">
      <div v-if="versions.length === 0" class="py-10 text-center text-[13px] text-hint">
        暂无历史版本（完成 AI 分析后自动生成版本记录）
      </div>
      <template v-else>
        <!-- 版本列表 -->
        <el-table :data="versions" size="small" max-height="260">
          <el-table-column label="版本" width="64">
            <template #default="{ row }">
              <span class="font-num font-semibold text-ink">v{{ row.version }}</span>
            </template>
          </el-table-column>
          <el-table-column label="生成时间" width="158">
            <template #default="{ row }"><span class="font-num text-[12px]">{{ row.createdAt }}</span></template>
          </el-table-column>
          <el-table-column label="模型 / 来源" width="150">
            <template #default="{ row }">
              <div class="text-[12px] leading-4">
                <div class="font-num text-sub">{{ row.modelVersion }}</div>
                <div :class="row.source === 'real' ? 'text-[#2E9E6B]' : 'text-hint'">
                  {{ row.source === 'real' ? '真实模型' : '模拟推理' }}
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="风险评分" width="84" align="center">
            <template #default="{ row }">
              <span class="font-num font-semibold" :class="row.riskScore >= 62 ? 'text-risk-late' : row.riskScore >= 35 ? 'text-[#B97E14]' : 'text-risk-low'">
                {{ row.riskScore }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="审核状态" width="96">
            <template #default="{ row }">
              <el-tag size="small" :type="REVIEW_TAG[row.reviewStatus]?.type ?? 'info'">
                {{ REVIEW_TAG[row.reviewStatus]?.text ?? row.reviewStatus }}
              </el-tag>
              <div v-if="row.reviewer" class="text-[10.5px] text-hint mt-0.5">{{ row.reviewer }}</div>
            </template>
          </el-table-column>
          <el-table-column label="对比" width="120" align="center">
            <template #default="{ row }">
              <el-radio-group v-model="compareB" size="small">
                <el-radio :value="row.id">新</el-radio>
              </el-radio-group>
              <el-radio-group v-model="compareA" size="small" class="ml-1">
                <el-radio :value="row.id">旧</el-radio>
              </el-radio-group>
            </template>
          </el-table-column>
          <el-table-column label="审核操作" min-width="150">
            <template #default="{ row }">
              <el-button
                v-if="row.reviewStatus !== 'approved'"
                type="success"
                size="small"
                plain
                @click="review(row, 'approve')"
              >通过</el-button>
              <el-button
                v-if="row.reviewStatus !== 'rejected'"
                type="danger"
                size="small"
                plain
                @click="review(row, 'reject')"
              >驳回</el-button>
              <span v-if="row.reviewStatus === 'approved'" class="text-[11px] text-hint">
                {{ row.reviewComment || '已归档' }}
              </span>
              <span v-else-if="row.reviewStatus === 'rejected'" class="text-[11px] text-risk-late">
                {{ row.reviewComment || '已驳回' }}
              </span>
            </template>
          </el-table-column>
        </el-table>

        <!-- 版本对比 -->
        <div v-if="diffRows.length" class="mt-4">
          <div class="text-[13px] font-semibold text-ink mb-2 flex items-center gap-2">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            版本指标对比
            <span class="text-[11px] font-normal text-hint">
              （旧 v{{ versions.find((v) => v.id === compareA)?.version }} → 新 v{{ versions.find((v) => v.id === compareB)?.version }}）
            </span>
          </div>
          <table class="w-full text-[12.5px] version-diff-table">
            <thead>
              <tr>
                <th class="text-left">指标</th>
                <th class="text-right">旧版本</th>
                <th class="text-center w-20">变化</th>
                <th class="text-right">新版本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in diffRows" :key="r.label" :class="r.changed ? 'bg-[#FFF7E6]' : ''">
                <td class="py-1.5 text-sub">{{ r.label }}</td>
                <td class="py-1.5 text-right font-num text-hint">{{ r.oldVal }}</td>
                <td class="py-1.5 text-center">
                  <span v-if="r.changed" class="inline-block px-1.5 py-0.5 rounded text-[10.5px] bg-[#FFE0B2] text-[#B97E14]">变更</span>
                  <span v-else class="text-hint text-[11px]">—</span>
                </td>
                <td class="py-1.5 text-right font-num" :class="r.changed ? 'text-ink font-semibold' : 'text-hint'">{{ r.newVal }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </el-dialog>

    <!-- 云端保存确认 -->
    <ConfirmDialog
      v-model="cloudConfirmVisible"
      title="云端保存确认"
      type="warning"
      content="保存后本报告将随病例归档至云端数据中心，诊断状态更新为「已出报告」。是否确认？"
      confirm-text="确认保存"
      @confirm="saveCloud"
    />
  </div>
</template>

<style scoped>
/* ---------- A4 预览纸（96dpi：794 × 1123px） ---------- */
.report-a4 {
  width: 794px;
  min-height: 1123px;
}

/* 章节标题：医疗公文风格 */
.report-sec-title {
  font-size: 14px;
  font-weight: 600;
  color: #24303c;
  padding-left: 9px;
  border-left: 3px solid #2f6da3;
  line-height: 1.3;
}

/* 信息条目 */
.info-item {
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.info-item label {
  color: #93a1af;
  flex-shrink: 0;
}
.info-item label::after {
  content: '：';
}

/* 报告表格 */
.report-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.report-table th {
  background: #f2f5f9;
  color: #4a5a6a;
  font-weight: 600;
  padding: 7px 10px;
  border: 1px solid #e3e9ef;
}
.report-table td {
  padding: 6.5px 10px;
  border: 1px solid #e3e9ef;
  color: #24303c;
}

/* ---------- 免责水印层：斜向平铺，半透明灰蓝，不遮挡内容 ---------- */
.watermark-layer {
  position: absolute;
  inset: 0;
  z-index: 5;
  pointer-events: none;
  overflow: hidden;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-template-rows: repeat(6, 1fr);
  align-items: center;
  justify-items: center;
}
.watermark-item {
  transform: rotate(-24deg);
  font-size: 15px;
  color: rgba(47, 109, 163, 0.075);
  white-space: nowrap;
  font-weight: 600;
  letter-spacing: 2px;
}

/* 版本对比表 */
.version-diff-table th {
  color: #93a1af;
  font-weight: 500;
  padding: 6px 8px;
  border-bottom: 1px solid #e3e9ef;
}
.version-diff-table td {
  padding: 6px 8px;
  border-bottom: 1px solid #eef2f6;
}
</style>
