<script setup lang="ts">
/**
 * AI 分析详情 + 干预方案页
 * ------------------------------------------------------------------
 * 上半区：多模态影像关键截图（MRI / PET / 融合） + AI 综合诊断摘要
 *         + 四级风险标签 + 全数量化指标表格
 * 下半区：四大干预模块标签页（认知干预 / 生活方式 / 随访复查 / 临床参考），
 *         医生可编辑、增删 AI 生成的干预条目（来源标记区分 AI / 医生），
 *         支持随访时间配置、保存方案、PDF 导出与打印预览。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiGetCase, apiGetSimilarCases } from '@/api/case'
import { apiGetAnalysis, apiGetInterventions, apiSaveInterventions, apiGetFollowUp, apiSaveFollowUp, apiListVersions, apiGetVersion, type AnalysisVersion } from '@/api/analysis'
import { apiAuditCase, apiGetReportData, apiSaveReportCloud } from '@/api/report'
import { caseSeed, makeThumbnail } from '@/utils/imaging'
import { apiGetImagingMeta, apiGetRealThumbnail } from '@/api/imaging'
import type { ImagingMeta } from '@/api/imaging'
import { useUserStore } from '@/stores/user'
import { printSection } from '@/utils/export'
import type { CaseRecord, SimilarCase } from '@/types/case'
import type { AnalysisResult, InterventionSection, InterventionItem, FollowUpPlan, InterventionKey, QuantMetric, AbnormalRegion, ImageQuality } from '@/types/analysis'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import DisclaimerBar from '@/components/DisclaimerBar.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ImageFullscreenViewer from '@/components/ImageFullscreenViewer.vue'
import MetricsFullscreenViewer from '@/components/MetricsFullscreenViewer.vue'
import CaseCommentPanel from '@/components/CaseCommentPanel.vue'
import MedicalViewport from '@/components/MedicalViewport.vue'
// 异步加载 3D 体绘制组件，避免 Three.js（544KB）进入路由级首屏 bundle
import { defineAsyncComponent } from 'vue'
const VolumeRenderer = defineAsyncComponent(() => import('@/components/VolumeRenderer.vue'))
import LongitudinalCompare from '@/views/longitudinal/LongitudinalCompare.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const caseId = computed(() => String(route.params.caseId ?? ''))

// ---------- 页面数据 ----------
const caseInfo = ref<CaseRecord | null>(null)
const analysis = ref<AnalysisResult | null>(null)
const sections = ref<InterventionSection[]>([])
const followUp = ref<FollowUpPlan | null>(null)
const loading = ref(true)

// ---------- 相似病例参考（top-k 推荐） ----------
const similarCases = ref<SimilarCase[]>([])
const similarLoading = ref(false)

// ---------- 影像关键截图（报告与详情页复用同一合成管线） ----------
const thumbs = ref<{ mri: string; pet: string; fusion: string }>({ mri: '', pet: '', fusion: '' })
/** 三类影像展示卡（同轴位层面并排） */
const imageCards = computed(() => [
  { key: 'MRI' as const, src: thumbs.value.mri, tag: 'MRI T1WI', sub: '结构像 · 海马/皮层形态', axis: '轴位', alt: 'MRI 关键层面' },
  { key: 'PET' as const, src: thumbs.value.pet, tag: 'PET FDG', sub: '代谢像 · 葡萄糖摄取分布', axis: '轴位', alt: 'PET 关键层面' },
  { key: 'FUSION' as const, src: thumbs.value.fusion, tag: 'MRI-PET 融合', sub: '结构-代谢配准叠加', axis: '配准叠加', alt: 'MRI-PET 融合' }
])
/** 真实影像元信息（全屏查看器用） */
const imagingMeta = ref<ImagingMeta | null>(null)

// ---------- 内联交互式阅片（三模态同切片联动） ----------
/** 内联视口共享切片索引（三个模态联动，初始为中层） */
const inlineSliceIdx = ref(0)
/** 内联视口可用切片总数 */
const inlineSliceCount = computed(() => imagingMeta.value?.sliceCount ?? 256)
/** 内联视口影像种子（与全屏查看器同一种子） */
const inlineSeed = computed(() => caseSeed(caseId.value))
/** 各模态真实影像可用性 */
const mriReal = computed(() => imagingMeta.value?.available && imagingMeta.value.mri)
const petReal = computed(() => imagingMeta.value?.available && imagingMeta.value.pet)
const fusionReal = computed(() => imagingMeta.value?.available && imagingMeta.value.mri && imagingMeta.value.pet)
/** 模态标签 */
const MODALITY_LABEL: Record<'MRI' | 'PET' | 'FUSION', string> = {
  MRI: 'MRI T1WI · 结构像',
  PET: 'PET FDG · 代谢像',
  FUSION: 'MRI-PET 融合 · 配准叠加'
}

// ---------- 全屏专一影像查看 ----------
const imgViewerVisible = ref(false)
const imgViewerModality = ref<'MRI' | 'PET' | 'FUSION'>('MRI')
function openImageViewer(mod: 'MRI' | 'PET' | 'FUSION'): void {
  imgViewerModality.value = mod
  imgViewerVisible.value = true
}

// ---------- 3D 体绘制 ----------
const volVisible = ref(false)

// ---------- 全屏 AI 量化分析 ----------
const metricsViewerVisible = ref(false)

/** 跳转随访工作台并定位本病例（关键词预填，直接点"记录随访"） */
function goRecordVisit(): void {
  router.push({ path: '/followup', query: { keyword: caseId.value, scope: 'all' } })
}

// ---------- 干预方案编辑 ----------
const activeTab = ref('cognitive')
const saving = ref(false)

/** 编辑弹窗状态（editingItem 为 null 表示新增） */
const editVisible = ref(false)
const editingSection = ref<InterventionKey>('cognitive')
const editingItem = ref<InterventionItem | null>(null)
const editForm = ref({ title: '', content: '', frequency: '', duration: '' })

/** 打开编辑弹窗 */
function openEdit(sectionKey: InterventionKey, item: InterventionItem | null): void {
  editingSection.value = sectionKey
  editingItem.value = item
  editForm.value = item
    ? { title: item.title, content: item.content, frequency: item.frequency, duration: item.duration }
    : { title: '', content: '', frequency: '', duration: '' }
  editVisible.value = true
}

/** 提交条目编辑/新增（来源标记为"医生"） */
function submitEdit(): void {
  if (!editForm.value.title.trim() || !editForm.value.content.trim()) {
    ElMessage.warning('请填写干预项目名称与内容')
    return
  }
  const section = sections.value.find((s) => s.key === editingSection.value)
  if (!section) return
  if (editingItem.value) {
    // 编辑：保留原 id 与来源
    Object.assign(editingItem.value, { ...editForm.value, source: '医生' as const })
  } else {
    section.items.push({
      id: `${caseId.value}-${editingSection.value}-${Date.now()}`,
      ...editForm.value,
      source: '医生'
    })
  }
  editVisible.value = false
  ElMessage.success(editingItem.value ? '已修改干预条目（尚未保存）' : '已新增干预条目（尚未保存）')
}

/** 删除条目 */
function removeItem(sectionKey: InterventionKey, item: InterventionItem): void {
  const section = sections.value.find((s) => s.key === sectionKey)
  if (!section) return
  section.items = section.items.filter((i) => i.id !== item.id)
  ElMessage.success('已删除干预条目（尚未保存）')
}

/** 保存干预方案（医生审核同时落库） */
async function savePlan(): Promise<void> {
  if (!caseInfo.value) return
  saving.value = true
  try {
    await apiSaveInterventions(caseId.value, caseInfo.value.patient.name, sections.value, userStore.userInfo?.username ?? 'unknown')
    if (followUp.value) {
      await apiSaveFollowUp(caseId.value, followUp.value)
    }
    await apiAuditCase(caseInfo.value, userStore.userInfo?.username ?? 'unknown')
    ElMessage.success('干预方案已保存，病例标记为医生已审核')
  } finally {
    saving.value = false
  }
}

// ---------- 随访时间配置 ----------
const followVisible = ref(false)
const followForm = ref<{ cycleMonths: number; nextDate: string; note: string }>({ cycleMonths: 6, nextDate: '', note: '' })

function openFollowConfig(): void {
  if (!followUp.value) return
  followForm.value = {
    cycleMonths: followUp.value.cycleMonths,
    nextDate: followUp.value.nextDate,
    note: followUp.value.note
  }
  followVisible.value = true
}

/** 保存随访配置：自动按周期生成提醒日期（到期前 7 天 + 到期日） */
async function submitFollow(): Promise<void> {
  if (!followUp.value) return
  const cycle = followForm.value.cycleMonths
  const next = new Date(followForm.value.nextDate)
  if (Number.isNaN(next.getTime())) {
    ElMessage.warning('请选择下次随访日期')
    return
  }
  const fmt = (d: Date): string =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  followUp.value.cycleMonths = cycle
  followUp.value.nextDate = fmt(next)
  followUp.value.reminders = [fmt(next), fmt(new Date(next.getTime() - 7 * 86400 * 1000))]
  followUp.value.note = followForm.value.note
  followVisible.value = false
  ElMessage.success('随访计划已更新（保存方案后生效）')
}

// ---------- 保存前确认弹窗 ----------
const saveConfirmVisible = ref(false)

// ---------- PDF 导出 / 打印预览（浏览器打印管线，医疗内网零依赖） ----------
function exportPdf(): void {
  ElMessage.info('请在打印对话框中选择"另存为 PDF"完成导出')
  setTimeout(printSection, 350)
}
function printPreview(): void {
  printSection()
}

// ---------- 报告生成（聚合数据 → 云端保存 → 跳转报告预览页） ----------
const reportGenerating = ref(false)
/** 生成并保存筛查报告，成功后跳转 A4 报告预览页 */
async function generateReport(): Promise<void> {
  if (!analysis.value || !caseInfo.value) {
    ElMessage.warning('病例数据未就绪，无法生成报告')
    return
  }
  reportGenerating.value = true
  try {
    const doctorName = userStore.realName || userStore.roleName || '未知医师'
    const operator = userStore.userInfo?.username ?? 'unknown'
    // 1. 聚合报告数据（病例 + 分析 + 干预 + 随访）
    const reportData = await apiGetReportData(caseId.value, doctorName)
    // 2. 云端保存（更新病例状态为"已出报告"，写审计日志）
    await apiSaveReportCloud(reportData, operator)
    ElMessage.success(`报告已生成（编号 ${reportData.reportNo}），正在跳转报告预览...`)
    // 3. 跳转报告预览页（A4 PDF 预览 + 导出 + 打印）
    setTimeout(() => router.push(`/report/${caseId.value}`), 600)
  } catch (e: unknown) {
    ElMessage.error('报告生成失败：' + (e instanceof Error ? e.message : '未知错误'))
  } finally {
    reportGenerating.value = false
  }
}

// ---------- 指标状态色 ----------
function metricDot(status: 'normal' | 'warn' | 'abnormal'): string {
  return status === 'normal' ? '#2E9E6B' : status === 'warn' ? '#D99A2B' : '#C94F4F'
}
const SCORE_COLOR: Record<string, string> = {
  low: '#2E9E6B',
  mci: '#D99A2B',
  'ad-early': '#D96B2B',
  'ad-late': '#C94F4F'
}

// ---------- 页面内嵌图形化：指标迷你参考带 ----------
type MiniRef = { low: number; high: number; mode: 'range' | 'gte' | 'lte' | 'single' }
function parseMiniRef(ref: string): MiniRef {
  const rg = ref.match(/([\d.]+)\s*[-~]\s*([\d.]+)/)
  if (rg) return { low: Number(rg[1]), high: Number(rg[2]), mode: 'range' }
  const g = ref.match(/[≥>]\s*([\d.]+)/)
  if (g) return { low: Number(g[1]), high: Number(g[1]) * 2, mode: 'gte' }
  const l = ref.match(/[≤<]\s*([\d.]+)/)
  if (l) return { low: 0, high: Number(l[1]), mode: 'lte' }
  const s = ref.match(/^\s*=?\s*([\d.]+)\s*$/)
  if (s) return { low: Number(s[1]), high: Number(s[1]), mode: 'single' }
  return { low: 0, high: 100, mode: 'range' }
}
const ORDINAL_KEYS = new Set(['mta', 'wmh'])
function isOrdinalMetric(m: QuantMetric): boolean {
  return ORDINAL_KEYS.has(m.key) || m.unit === '级'
}
function clamp100(v: number): number {
  return Math.max(0, Math.min(100, v))
}
/** 迷你参考带几何（与全屏查看器同一视觉逻辑） */
function miniBar(m: QuantMetric): { bandLeft: number; bandWidth: number; marker: number } {
  const p = parseMiniRef(m.refRange)
  let lo = 0; let hi = 100; let dLo = 0; let dHi = 100
  if (isOrdinalMetric(m)) {
    dLo = 0; dHi = 4
    if (p.mode === 'range') { lo = p.low; hi = p.high }
    else if (p.mode === 'lte') { lo = 0; hi = p.high }
    else { lo = 0; hi = Math.max(1, p.low) }
  } else if (p.mode === 'range') {
    lo = p.low; hi = p.high
    const span = hi - lo || Math.abs(hi) || 1
    dLo = Math.min(lo, m.value) - 0.35 * span
    dHi = Math.max(hi, m.value) + 0.35 * span
  } else if (p.mode === 'gte') {
    lo = p.low
    const span = lo || 1
    dLo = Math.min(lo, m.value) - 0.25 * span
    dHi = lo + 0.6 * span
    hi = dHi
  } else if (p.mode === 'lte') {
    hi = p.high
    dLo = 0
    dHi = Math.max(hi, m.value) * 1.25 || 1
    lo = 0
  } else {
    lo = p.low; hi = p.high
    const span = lo || 1
    dLo = Math.min(lo, m.value) - 0.5 * span
    dHi = Math.max(hi, m.value) + 0.5 * span
  }
  const pct = (v: number): number => (dHi > dLo ? ((v - dLo) / (dHi - dLo)) * 100 : 50)
  const left = clamp100(pct(lo))
  const right = clamp100(pct(hi))
  return { bandLeft: left, bandWidth: Math.max(4, right - left), marker: clamp100(pct(m.value)) }
}

// ---------- 页面内嵌图形化：异常脑区 Z 值条 ----------
const regions = computed<AbnormalRegion[]>(() =>
  [...(analysis.value?.abnormalRegions ?? [])].sort((p, q) => p.zScore - q.zScore)
)
function zColorOf(z: number): string {
  const az = Math.abs(z)
  return az >= 3 ? '#C94F4F' : az >= 2 ? '#D96B2B' : '#D99A2B'
}
const Z_MIN = -4
function zLeftOf(z: number): number {
  return clamp100(((z - Z_MIN) / (0 - Z_MIN)) * 100)
}
/** 风险尺 0-100 指针位置 */
const riskMarkerPct = computed(() => clamp100(analysis.value?.riskScore ?? 0))

// ---------- 2026 版指南：图像质量控制辅助函数 ----------
/** 配准质量等级 → el-tag type */
function regQualityTagType(q: ImageQuality['registrationQuality']): 'success' | 'warning' | 'danger' {
  return q === 'good' ? 'success' : q === 'acceptable' ? 'warning' : 'danger'
}
/** 配准质量等级 → 中文标签 */
function regQualityLabel(q: ImageQuality['registrationQuality']): string {
  return q === 'good' ? '配准良好' : q === 'acceptable' ? '配准可接受' : '配准较差'
}

// ---------- 真实模型推理信息 ----------
const isRealInference = computed(() => analysis.value?.inferenceSource === 'real')
/** 集成单模型 AD 概率区间（百分比） */
const probRangeText = computed(() => {
  const a = analysis.value
  if (!a || typeof a.probMin !== 'number' || typeof a.probMax !== 'number') return ''
  return `${(a.probMin * 100).toFixed(1)}% ~ ${(a.probMax * 100).toFixed(1)}%`
})

// ---------- AI 版本对比 ----------
const compareVisible = ref(false)
// 病例评论 / 会诊讨论
const commentVisible = ref(false)
const commentCount = ref(0)
const versions = ref<AnalysisVersion[]>([])
const compareVerA = ref<number | null>(null)
const compareVerB = ref<number | null>(null)
const compA = ref<AnalysisVersion | null>(null)
const compB = ref<AnalysisVersion | null>(null)
const compareLoading = ref(false)

async function openCompare() {
  compareVisible.value = true
  if (versions.value.length === 0) {
    versions.value = await apiListVersions(caseId.value)
  }
  // 默认选最新两个版本
  const sorted = [...versions.value].sort((x, y) => y.version - x.version)
  compareVerA.value = sorted[0]?.id ?? null
  compareVerB.value = sorted[1]?.id ?? null
  if (compareVerA.value && compareVerB.value) loadCompare()
}

async function loadCompare() {
  if (!compareVerA.value || !compareVerB.value) {
    compA.value = null
    compB.value = null
    return
  }
  compareLoading.value = true
  try {
    const [a, b] = await Promise.all([apiGetVersion(compareVerA.value), apiGetVersion(compareVerB.value)])
    compA.value = a
    compB.value = b
  } finally {
    compareLoading.value = false
  }
}

/** 两个数值做差并染色 */
function diffCell(a: number | undefined, b: number | undefined): { text: string; color: string } {
  if (a == null || b == null) return { text: '—', color: 'var(--ad-ink-3)' }
  const d = a - b
  const text = `${d >= 0 ? '+' : ''}${d.toFixed(2)}`
  // 正向差异色（蓝）、负向差异色（橙），仅作视觉提示不代表好坏
  const color = d > 0 ? '#2F6DA3' : d < 0 ? '#D99A2B' : 'var(--ad-ink-3)'
  return { text, color }
}

/** 版本对比表格行 */
const compareRows = computed(() => {
  const a = compA.value?.result
  const b = compB.value?.result
  const numRow = (label: string, av?: number, bv?: number, unit = '') => {
    const d = diffCell(av, bv)
    return {
      label,
      a: av == null ? '—' : `${av}${unit}`,
      b: bv == null ? '—' : `${bv}${unit}`,
      diff: d.text,
      diffColor: d.color
    }
  }
  const strRow = (label: string, av?: string, bv?: string) => ({
    label,
    a: av ?? '—',
    b: bv ?? '—',
    diff: (av && bv && av !== bv) ? '不同' : (av && bv ? '相同' : '—'),
    diffColor: (av && bv && av !== bv) ? '#D99A2B' : 'var(--ad-ink-3)'
  })
  return [
    numRow('AD 风险评分', a?.riskScore, b?.riskScore, ' 分'),
    strRow('风险分级', a?.riskLevel, b?.riskLevel),
    strRow('病程分期', a?.stage, b?.stage),
    numRow('推理置信度', a?.confidence != null ? Math.round(a.confidence * 1000) / 10 : undefined, b?.confidence != null ? Math.round(b.confidence * 1000) / 10 : undefined, '%'),
    numRow('左侧海马体积', a?.hippocampusVolumeL, b?.hippocampusVolumeL, ' cm³'),
    numRow('右侧海马体积', a?.hippocampusVolumeR, b?.hippocampusVolumeR, ' cm³'),
    numRow('PET 平均代谢 SUV', a?.meanSUV, b?.meanSUV),
    numRow('皮层平均厚度', a?.corticalThickness, b?.corticalThickness, ' mm'),
    numRow('脑室体积', a?.ventricleVolume, b?.ventricleVolume, ' cm³'),
    strRow('MTA 萎缩评分', a?.mtaScore, b?.mtaScore),
    numRow('异常脑区数', a?.abnormalRegions?.length, b?.abnormalRegions?.length, ' 个'),
    strRow('模型版本', a?.modelVersion, b?.modelVersion),
    strRow('融合策略', a?.fusionStrategy === 'feature' ? '特征级' : '像素级', b?.fusionStrategy === 'feature' ? '特征级' : '像素级')
  ]
})

onMounted(async () => {
  try {
    const c = await apiGetCase(caseId.value)
    if (!c) {
      ElMessage.error('病例不存在')
      router.push('/cases')
      return
    }
    caseInfo.value = c
    const [a, plan, fu] = await Promise.all([
      apiGetAnalysis(caseId.value).catch(() => null),
      apiGetInterventions(c),
      apiGetFollowUp(c)
    ])
    if (!a) {
      ElMessage.warning('该病例尚未完成 AI 分析，请先在阅片页启动分析')
      router.push(`/viewer/${caseId.value}`)
      return
    }
    analysis.value = a
    sections.value = plan
    followUp.value = fu
    // 生成三模态关键层面截图（海马中心层面）：真实病例优先用真实切片，失败回退合成
    const seed = caseSeed(caseId.value)
    const fallback = {
      mri: makeThumbnail('MRI', seed),
      pet: makeThumbnail('PET', seed),
      fusion: makeThumbnail('FUSION', seed)
    }
    thumbs.value = fallback
    inlineSliceIdx.value = Math.floor(256 / 2)
    try {
      const meta = await apiGetImagingMeta(caseId.value)
      imagingMeta.value = meta
      inlineSliceIdx.value = Math.floor(meta.sliceCount / 2)
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
    // 相似病例 top-k 推荐（不阻塞主流程，失败静默置空）
    similarLoading.value = true
    apiGetSimilarCases(caseId.value, 3)
      .then((list) => (similarCases.value = list))
      .catch(() => (similarCases.value = []))
      .finally(() => (similarLoading.value = false))
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-loading="loading" class="page-wrap space-y-4">
    <!-- ==================== 行1：多模态影像（全宽 · 内联交互式阅片，三模态同切片联动） ==================== -->
    <div class="card-ad">
      <div class="card-ad__header">
        <span class="card-ad__title">多模态影像交互阅片</span>
        <el-tooltip
          v-if="analysis?.abnormalRegions?.length"
          content="每个视口右侧工具栏已默认开启 AI 异常脑区热力图（🔥图标可切换）；MRI 真实影像可点击☀图标叠加 Grad-CAM 代谢偏差热力图"
          placement="bottom"
          :show-after="200"
        >
          <el-tag size="small" type="warning" effect="plain" round class="!ml-1">
            <el-icon class="mr-0.5"><HotWater /></el-icon>
            AI 热力图已启用
          </el-tag>
        </el-tooltip>
        <el-tag v-else size="small" type="info" effect="plain" round class="!ml-1">
          完成 AI 分析后显示异常脑区热力图
        </el-tag>
        <div class="flex items-center gap-3">
          <span class="text-[11px] text-hint font-num">
            切片 {{ inlineSliceIdx + 1 }} / {{ inlineSliceCount }}
          </span>
          <el-slider
            v-model="inlineSliceIdx"
            :min="0"
            :max="Math.max(0, inlineSliceCount - 1)"
            :step="1"
            size="small"
            class="!w-40"
          />
          <el-button size="small" :icon="'Box'" @click="volVisible = true">3D 体绘制</el-button>
        </div>
      </div>
      <div class="p-4 grid grid-cols-3 gap-4">
        <!-- MRI 视口 -->
        <div class="rounded-lg overflow-hidden border border-line viewport-dark relative h-72">
          <MedicalViewport
            modality="MRI"
            :seed="inlineSeed"
            :slice-count="inlineSliceCount"
            :slice-idx="inlineSliceIdx"
            :show-roi="true"
            :label="MODALITY_LABEL.MRI"
            :real-case-id="caseId"
            :real-available="mriReal"
            orientation="axial"
            :dicom-meta="imagingMeta?.dicomMeta ?? null"
            :heatmap-regions="analysis?.abnormalRegions ?? []"
            @update:slice-idx="inlineSliceIdx = $event"
          />
          <div class="absolute top-1.5 right-1.5 z-10">
            <el-button size="small" :icon="'FullScreen'" circle @click="openImageViewer('MRI')" title="全屏查看" />
          </div>
        </div>
        <!-- PET 视口 -->
        <div class="rounded-lg overflow-hidden border border-line viewport-dark relative h-72">
          <MedicalViewport
            modality="PET"
            :seed="inlineSeed"
            :slice-count="inlineSliceCount"
            :slice-idx="inlineSliceIdx"
            :show-roi="true"
            :label="MODALITY_LABEL.PET"
            :real-case-id="caseId"
            :real-available="petReal"
            orientation="axial"
            :dicom-meta="imagingMeta?.dicomMeta ?? null"
            :heatmap-regions="analysis?.abnormalRegions ?? []"
            @update:slice-idx="inlineSliceIdx = $event"
          />
          <div class="absolute top-1.5 right-1.5 z-10">
            <el-button size="small" :icon="'FullScreen'" circle @click="openImageViewer('PET')" title="全屏查看" />
          </div>
        </div>
        <!-- 融合视口 -->
        <div class="rounded-lg overflow-hidden border border-line viewport-dark relative h-72">
          <MedicalViewport
            modality="FUSION"
            :seed="inlineSeed"
            :slice-count="inlineSliceCount"
            :slice-idx="inlineSliceIdx"
            :show-roi="true"
            :label="MODALITY_LABEL.FUSION"
            :real-case-id="caseId"
            :real-available="fusionReal"
            orientation="axial"
            :dicom-meta="imagingMeta?.dicomMeta ?? null"
            :heatmap-regions="analysis?.abnormalRegions ?? []"
            @update:slice-idx="inlineSliceIdx = $event"
          />
          <div class="absolute top-1.5 right-1.5 z-10">
            <el-button size="small" :icon="'FullScreen'" circle @click="openImageViewer('FUSION')" title="全屏查看" />
          </div>
        </div>
      </div>
      <div class="px-4 pb-3 text-[11px] text-hint flex items-center gap-4">
        <span>各视口可独立操作：悬浮工具栏含窗宽窗位 / 平移 / 测量 / 缩放 / 重置</span>
        <span v-if="!imagingMeta?.available" class="text-[#D99A2B]">当前为演示合成影像，无真实 NIfTI 数据</span>
      </div>
    </div>

    <!-- ==================== 行2：AI 综合诊断摘要（8/12） + 量化指标图形化（4/12） ==================== -->
    <div class="grid grid-cols-12 gap-4 items-start">
      <!-- AI 综合诊断摘要（行2 · 左 8/12） -->
      <div class="card-ad col-span-8 flex flex-col min-w-0">
        <div class="card-ad__header">
          <span class="card-ad__title flex items-center gap-2">
            AI 综合诊断摘要
            <el-tag
              v-if="analysis"
              size="small"
              round
              effect="dark"
              :type="isRealInference ? 'primary' : 'info'"
            >
              {{ isRealInference ? '真实 TransMF 模型推理' : '模拟演示推理' }}
            </el-tag>
          </span>
          <span class="text-[11px] text-hint font-num">
            {{ analysis?.modelVersion }} · {{ analysis?.fusionStrategy === 'feature' ? '特征级融合' : '像素级融合' }}
          </span>
          <el-button size="small" text type="primary" :icon="'DataLine'" class="!ml-2" @click="openCompare">版本对比</el-button>
          <el-button size="small" type="primary" :icon="'Document'" :loading="reportGenerating" @click="generateReport">生成报告</el-button>
        </div>
        <div class="p-4 flex-1 flex flex-col gap-3">
          <!-- 风险标签 + 评分 -->
          <div class="flex items-center gap-4">
            <RiskLevelTag :risk-level="analysis?.riskLevel ?? null" size="large" />
            <div class="font-num text-[26px] font-semibold leading-none" :style="{ color: analysis ? SCORE_COLOR[analysis.riskLevel] : '#24303C' }">
              {{ analysis?.riskScore }}<span class="text-[12px] text-hint font-sans ml-1">/ 100 分</span>
            </div>
            <el-divider direction="vertical" />
            <div class="text-[13px] text-ink">
              病程分期：<b>{{ analysis?.stage }}</b>
              <span class="ml-2 text-hint text-[12px]">推理置信度 {{ ((analysis?.confidence ?? 0) * 100).toFixed(1) }}%</span>
            </div>
          </div>
          <!-- 风险分级标尺：四段色带 + 评分指针，图形化呈现风险位置 -->
          <div v-if="analysis" class="px-1">
            <div class="relative h-3 rounded-full overflow-hidden flex">
              <div class="flex-1 bg-[#2E9E6B]/70" />
              <div class="flex-1 bg-[#D99A2B]/70" />
              <div class="flex-1 bg-[#D96B2B]/70" />
              <div class="flex-1 bg-[#C94F4F]/70" />
            </div>
            <div class="relative h-4 mt-0.5">
              <div
                class="absolute -translate-x-1/2 top-0 transition-all duration-500"
                :style="{ left: riskMarkerPct + '%' }"
              >
                <div class="w-0 h-0 border-x-[6px] border-x-transparent border-t-[8px]" :style="{ borderTopColor: SCORE_COLOR[analysis.riskLevel] }" />
              </div>
            </div>
            <div class="flex justify-between text-[10.5px] text-hint font-num -mt-0.5 px-0.5">
              <span>0 · 正常</span>
              <span>25 · 临界</span>
              <span>50 · 痴呆早期</span>
              <span>75 · AD 中晚期</span>
              <span>100</span>
            </div>
          </div>
          <!-- 2026 版 PET/MRI 指南：生物学分期 + ARIA 风险 -->
          <div v-if="analysis?.biologicalStage" class="flex items-center gap-3 px-3 py-2 rounded-card bg-[#F0F5FB] border border-[#D0E0EF] text-[12px]">
            <div class="flex items-center gap-2">
              <span class="text-hint">生物学分期</span>
              <el-tag size="small" type="warning" effect="plain">{{ analysis.biologicalStage }}</el-tag>
              <span class="text-sub">{{ analysis.biologicalStageDesc }}</span>
            </div>
          </div>
          <div v-if="analysis?.ariaRisk" class="px-3 py-2 rounded-card border text-[12px]" :class="{
            'bg-[#FFF7ED] border-[#FDBA74]': analysis.ariaRisk.riskLevel === '高风险',
            'bg-[#FFFBEB] border-[#FCD34D]': analysis.ariaRisk.riskLevel === '中风险',
            'bg-[#F0FDF4] border-[#BBF7D0]': analysis.ariaRisk.riskLevel === '低风险',
          }">
            <div class="flex items-center gap-2 mb-1">
              <span class="font-medium text-ink">ARIA 风险评估</span>
              <el-tag size="small" :type="analysis.ariaRisk.riskLevel === '高风险' ? 'danger' : analysis.ariaRisk.riskLevel === '中风险' ? 'warning' : 'success'" effect="dark">
                {{ analysis.ariaRisk.riskLevel }}
              </el-tag>
            </div>
            <div class="text-hint leading-5">
              <span v-for="(f, i) in analysis.ariaRisk.factors" :key="i" class="mr-2">· {{ f }}</span>
            </div>
            <div class="text-sub mt-1">{{ analysis.ariaRisk.recommendation }}</div>
          </div>

          <!-- 2026 版指南：图像质量控制（运动伪影 + PET-MRI 配准质量） -->
          <div v-if="analysis?.imageQuality" class="px-3 py-2 rounded-card border border-[#E3E9EF] bg-[#F7FAFC] text-[12px]">
            <div class="flex items-center gap-2 mb-2">
              <span class="font-medium text-ink">图像质量控制</span>
              <el-tag
                size="small"
                effect="dark"
                :type="regQualityTagType(analysis.imageQuality.registrationQuality)"
              >{{ regQualityLabel(analysis.imageQuality.registrationQuality) }}</el-tag>
              <el-tag
                v-if="analysis.imageQuality.motionArtifact"
                size="small" type="warning" effect="plain" round
              >运动伪影</el-tag>
            </div>
            <el-alert
              v-for="(w, i) in analysis.imageQuality.warnings"
              :key="i"
              type="warning"
              :title="w"
              :closable="false"
              show-icon
              class="!mb-1.5"
            />
            <div v-if="analysis.imageQuality.recommendation" class="text-sub mt-1">
              {{ analysis.imageQuality.recommendation }}
            </div>
            <!-- 第 28 轮新增：图像质量失败处置流程 -->
            <div v-if="analysis.imageQuality.acquisitionAdjustment" class="mt-2 pt-2 border-t border-[#FFE0B2]">
              <div class="flex items-center gap-2 mb-1.5 flex-wrap">
                <span class="font-medium text-ink">重新采集建议</span>
                <el-tag size="small" :type="analysis.imageQuality.acquisitionAdjustment.required ? 'danger' : 'warning'" effect="dark">
                  {{ analysis.imageQuality.acquisitionAdjustment.required ? '需重新采集' : '可补救' }}
                </el-tag>
                <el-tag size="small" :type="analysis.imageQuality.acquisitionAdjustment.priority === 'high' ? 'danger' : (analysis.imageQuality.acquisitionAdjustment.priority === 'medium' ? 'warning' : 'info')" effect="plain">
                  {{ analysis.imageQuality.acquisitionAdjustment.priority === 'high' ? '高优先级' : (analysis.imageQuality.acquisitionAdjustment.priority === 'medium' ? '中优先级' : '低优先级') }}
                </el-tag>
              </div>
              <div class="text-sub mb-1.5">{{ analysis.imageQuality.acquisitionAdjustment.reason }}</div>
              <ul class="space-y-1">
                <li v-for="(adj, i) in analysis.imageQuality.acquisitionAdjustment.adjustments" :key="i" class="text-sub leading-5 flex gap-1.5">
                  <span class="text-primary shrink-0">·</span>
                  <span>{{ adj }}</span>
                </li>
              </ul>
              <div class="text-hint mt-1.5 italic">{{ analysis.imageQuality.acquisitionAdjustment.reimbursementNote }}</div>
            </div>
          </div>

          <!-- 2026 版指南：PET 视觉判读征象 -->
          <div v-if="analysis?.petPatterns" class="px-3 py-2 rounded-card border border-[#D0E0EF] bg-[#F0F5FB] text-[12px]">
            <div class="flex items-center gap-2 mb-2">
              <span class="font-medium text-ink">PET 视觉判读征象</span>
              <el-tag size="small" type="info" effect="plain" round>{{ analysis.petPatterns.tracerName }}</el-tag>
            </div>
            <div class="text-sub mb-2">{{ analysis.petPatterns.conclusion }}</div>
            <div v-if="analysis.petPatterns.patterns?.length" class="flex flex-wrap gap-1.5 mb-2">
              <el-tag
                v-for="(p, i) in analysis.petPatterns.patterns"
                :key="i"
                size="small" type="warning" effect="light" round
              >{{ p }}</el-tag>
            </div>
            <div
              v-if="analysis.petPatterns.suvr !== null && analysis.petPatterns.suvrThreshold !== null"
              class="flex items-center gap-2 mb-2 text-hint"
            >
              <span>SUVR：<span class="font-num text-ink">{{ analysis.petPatterns.suvr }}</span></span>
              <span>阈值：<span class="font-num text-ink">{{ analysis.petPatterns.suvrThreshold }}</span></span>
              <el-tag
                v-if="(analysis.petPatterns.suvr ?? 0) > (analysis.petPatterns.suvrThreshold ?? Infinity)"
                size="small" type="danger" effect="dark" round
              >阳性</el-tag>
              <el-tag v-else size="small" type="success" effect="dark" round>阴性</el-tag>
            </div>
            <div v-if="analysis.petPatterns.affectedRegions?.length" class="text-hint leading-5 mb-1">
              <span class="text-ink font-medium">受累脑区：</span>
              <span v-for="(r, i) in analysis.petPatterns.affectedRegions" :key="i" class="mr-2">· {{ r }}</span>
            </div>
            <div v-if="analysis.petPatterns.braakStage" class="flex items-center gap-2 mt-1">
              <span class="text-hint">Braak 分期：</span>
              <el-tag size="small" type="primary" effect="light" round>{{ analysis.petPatterns.braakStage }}</el-tag>
            </div>
            <div v-if="analysis.petPatterns.quantificationTool" class="text-hint mt-1">
              半定量工具：<span class="text-ink">{{ analysis.petPatterns.quantificationTool }}</span>
            </div>
            <!-- 第 28 轮新增：显像剂特异性伪影识别 -->
            <div v-if="analysis.petPatterns.tracerSpecificArtifacts?.length" class="mt-2 pt-2 border-t border-[#D0E0EF]">
              <div class="text-hint mb-1">显像剂特异性伪影识别：</div>
              <div v-for="(a, i) in analysis.petPatterns.tracerSpecificArtifacts" :key="i" class="text-sub leading-5 mb-1 flex gap-1.5">
                <span class="text-warning shrink-0">!</span>
                <div>
                  <span class="text-ink font-medium">{{ a.artifact }}</span>
                  <span class="text-sub ml-1">— {{ a.note }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 2026 版指南 表2：PET 显像剂推荐参数 -->
          <div v-if="analysis?.tracerParameters" class="px-3 py-2 rounded-card border border-[#D0E0EF] bg-[#F0F5FB] text-[12px]">
            <div class="flex items-center gap-2 mb-2">
              <span class="font-medium text-ink">PET 显像剂推荐参数</span>
              <el-tag size="small" type="info" effect="plain" round>{{ analysis.tracerParameters.typeName }}</el-tag>
            </div>
            <div class="grid grid-cols-2 gap-x-4 gap-y-1.5 mb-2">
              <div class="text-hint">
                显像剂：<span class="text-ink font-medium">{{ analysis.tracerParameters.name }}</span>
                <span class="ml-1">{{ analysis.tracerParameters.cnName }}</span>
              </div>
              <div class="text-hint">
                推荐剂量：<span class="text-ink font-num font-medium">{{ analysis.tracerParameters.dose }}</span>
              </div>
              <div class="text-hint">
                注射后等待：<span class="text-ink font-num font-medium">{{ analysis.tracerParameters.uptakeMinutes }}</span> 分钟
              </div>
              <div class="text-hint">
                采集时长：<span class="text-ink font-num font-medium">{{ analysis.tracerParameters.acquisitionMinutes }}</span> 分钟
              </div>
              <div class="text-hint">
                半衰期：<span class="text-ink font-num">{{ analysis.tracerParameters.halfLife }}</span>
              </div>
              <div v-if="analysis.tracerParameters.suvrThreshold" class="text-hint">
                SUVR 阳性阈值：<span class="text-ink font-num font-medium">{{ analysis.tracerParameters.suvrThreshold }}</span>
              </div>
            </div>
            <div class="text-sub leading-5 border-t border-[#D0E0EF] pt-1.5">
              <span class="text-hint">适应证：</span>{{ analysis.tracerParameters.indication }}
            </div>
          </div>

          <!-- 2026 版指南：检查前准备规则清单 -->
          <div v-if="analysis?.prepInstructions" class="px-3 py-2 rounded-card border border-[#E3E9EF] bg-[#F7FAFC] text-[12px]">
            <div class="flex items-center gap-2 mb-2 flex-wrap">
              <span class="font-medium text-ink">检查前准备清单</span>
              <el-tag size="small" type="info" effect="plain" round>{{ analysis.prepInstructions.tracerName }}</el-tag>
              <el-tag v-if="analysis.prepInstructions.fastingRequired" size="small" type="warning" effect="dark">需空腹</el-tag>
              <el-tag v-if="analysis.prepInstructions.glucoseControl" size="small" type="warning" effect="dark">血糖控制</el-tag>
            </div>
            <ul class="space-y-1 mb-2">
              <li v-for="(item, i) in analysis.prepInstructions.items" :key="i" class="text-sub leading-5 flex gap-1.5">
                <span class="text-primary shrink-0">·</span>
                <span>{{ item }}</span>
              </li>
            </ul>
            <div v-if="analysis.prepInstructions.specialNotes?.length" class="mt-1.5 pt-1.5 border-t border-[#E3E9EF]">
              <div class="text-hint mb-1">特殊人群提示：</div>
              <div v-for="(note, i) in analysis.prepInstructions.specialNotes" :key="i" class="text-sub leading-5 flex gap-1.5">
                <span class="text-warning shrink-0">!</span>
                <span>{{ note }}</span>
              </div>
            </div>
            <div class="mt-1.5 pt-1.5 border-t border-[#E3E9EF] text-hint leading-5">
              <span class="font-medium text-ink">纵向随访：</span>{{ analysis.prepInstructions.longitudinalProtocol }}
            </div>
          </div>

          <!-- 2026 版指南：MRI 定量评估工具（SPM/CAT12/Freesurfer） -->
          <div v-if="analysis?.mriQuantification" class="px-3 py-2 rounded-card border border-[#D0E0EF] bg-[#F0F5FB] text-[12px]">
            <div class="flex items-center gap-2 mb-2 flex-wrap">
              <span class="font-medium text-ink">MRI 定量评估工具</span>
              <el-tag size="small" type="info" effect="plain" round>{{ analysis.mriQuantification.status === 'simulated' ? '模拟数据' : '真实流水线' }}</el-tag>
            </div>
            <div class="grid grid-cols-1 gap-1.5 mb-2">
              <div v-for="tool in analysis.mriQuantification.tools" :key="tool.code" class="flex gap-2 items-start">
                <el-tag size="small" type="primary" effect="plain" class="shrink-0">{{ tool.name }}</el-tag>
                <div class="flex-1">
                  <div class="text-sub leading-5">{{ tool.purpose }}</div>
                  <div class="text-hint leading-5 mt-0.5">输出：{{ tool.outputs.join('、') }}</div>
                </div>
              </div>
            </div>
            <div class="grid grid-cols-2 gap-x-4 gap-y-1 mb-2 pt-1.5 border-t border-[#D0E0EF]">
              <div class="text-hint">
                全脑灰质体积：<span class="text-ink font-num font-medium">{{ analysis.mriQuantification.metrics.totalGrayMatterVolume }}</span>
                {{ analysis.mriQuantification.metrics.unit.volume }}
              </div>
              <div class="text-hint">
                平均皮层厚度：<span class="text-ink font-num font-medium">{{ analysis.mriQuantification.metrics.meanCorticalThickness }}</span>
                {{ analysis.mriQuantification.metrics.unit.thickness }}
              </div>
            </div>
            <div class="text-hint mb-1">海马亚区体积（{{ analysis.mriQuantification.metrics.unit.volume }}）：</div>
            <div class="grid grid-cols-2 gap-x-4 gap-y-0.5 mb-2">
              <div v-for="sf in analysis.mriQuantification.metrics.hippocampalSubfields" :key="sf.name" class="text-hint flex gap-2">
                <span class="text-ink font-medium">{{ sf.name }}</span>
                <span>L {{ sf.left }}</span>
                <span>R {{ sf.right }}</span>
              </div>
            </div>
            <div class="text-sub leading-5 border-t border-[#D0E0EF] pt-1.5">
              <span class="text-hint">萎缩模式：</span>{{ analysis.mriQuantification.atrophyPattern }}
            </div>
            <div class="text-hint leading-5 mt-1 italic">{{ analysis.mriQuantification.note }}</div>
          </div>

          <!-- 2026 版指南：MTA 视觉评分详细描述（推荐意见 2） -->
          <div v-if="analysis?.mriQuantification?.mtaDetail" class="px-3 py-2 rounded-card border border-[#D0E0EF] bg-[#F0F5FB] text-[12px]">
            <div class="flex items-center gap-2 mb-2 flex-wrap">
              <span class="font-medium text-ink">MTA 视觉评分</span>
              <el-tag size="small" :type="analysis.mriQuantification.mtaDetail.isAbnormal ? 'danger' : 'success'" effect="dark">
                {{ analysis.mriQuantification.mtaDetail.score }} 级
              </el-tag>
              <el-tag v-if="analysis.mriQuantification.mtaDetail.isAbnormal" size="small" type="warning" effect="plain">异常</el-tag>
            </div>
            <div class="text-hint mb-2">{{ analysis.mriQuantification.mtaDetail.slicePosition }}</div>
            <div class="grid grid-cols-1 gap-1 mb-2">
              <div v-for="g in analysis.mriQuantification.mtaDetail.grading" :key="g.grade"
                   class="flex gap-2 items-center text-hint"
                   :class="{ 'text-primary font-medium': g.grade === analysis.mriQuantification.mtaDetail.currentGrade?.grade }">
                <span class="shrink-0 w-4 text-center">{{ g.grade }}</span>
                <span class="leading-5">{{ g.desc }}</span>
              </div>
            </div>
            <div class="text-sub leading-5 border-t border-[#D0E0EF] pt-1.5">
              <span class="text-hint">年龄校正：</span>{{ analysis.mriQuantification.mtaDetail.ageAdjustment }}
            </div>
          </div>

          <!-- 2026 版指南：Fazekas 评分详细描述（报告模板表5） -->
          <div v-if="analysis?.mriQuantification?.fazekasDetail" class="px-3 py-2 rounded-card border border-[#E3E9EF] bg-[#F7FAFC] text-[12px]">
            <div class="flex items-center gap-2 mb-2 flex-wrap">
              <span class="font-medium text-ink">Fazekas 白质高信号评分</span>
              <el-tag size="small" :type="analysis.mriQuantification.fazekasDetail.isAbnormal ? 'danger' : 'success'" effect="dark">
                总分 {{ analysis.mriQuantification.fazekasDetail.totalScore }} 级
              </el-tag>
              <el-tag size="small" type="info" effect="plain">{{ analysis.mriQuantification.fazekasDetail.imagingSequence }}</el-tag>
            </div>
            <div class="grid grid-cols-1 gap-2 mb-2">
              <div class="flex gap-2 items-start">
                <span class="text-hint shrink-0 w-20">脑室旁 PV-WMH</span>
                <div class="flex-1">
                  <span class="text-ink font-medium">{{ analysis.mriQuantification.fazekasDetail.pvWmh.grade }} 级</span>
                  <span class="text-sub ml-1">{{ analysis.mriQuantification.fazekasDetail.pvWmh.desc }}</span>
                </div>
              </div>
              <div class="flex gap-2 items-start">
                <span class="text-hint shrink-0 w-20">深部 DW-WMH</span>
                <div class="flex-1">
                  <span class="text-ink font-medium">{{ analysis.mriQuantification.fazekasDetail.dwWmh.grade }} 级</span>
                  <span class="text-sub ml-1">{{ analysis.mriQuantification.fazekasDetail.dwWmh.desc }}</span>
                </div>
              </div>
            </div>
            <div class="text-sub leading-5 border-t border-[#E3E9EF] pt-1.5">
              <span class="text-hint">临床意义：</span>{{ analysis.mriQuantification.fazekasDetail.clinicalSignificance }}
            </div>
          </div>

          <!-- 2026 版指南：SWI 序列出血点检测（推荐意见 3） -->
          <div v-if="analysis?.mriQuantification?.swiFindings" class="px-3 py-2 rounded-card border border-[#E3E9EF] bg-[#F7FAFC] text-[12px]">
            <div class="flex items-center gap-2 mb-2 flex-wrap">
              <span class="font-medium text-ink">SWI 出血点检测</span>
              <el-tag size="small" type="info" effect="plain">{{ analysis.mriQuantification.swiFindings.sequence }}</el-tag>
              <el-tag size="small" :type="analysis.mriQuantification.swiFindings.microBleedCount >= 5 ? 'danger' : (analysis.mriQuantification.swiFindings.microBleedCount > 0 ? 'warning' : 'success')" effect="dark">
                微出血 {{ analysis.mriQuantification.swiFindings.microBleedCount }} 个
              </el-tag>
              <el-tag v-if="analysis.mriQuantification.swiFindings.hemosiderinDeposit" size="small" type="warning" effect="plain">含铁血黄素阳性</el-tag>
            </div>
            <div class="text-hint mb-2">
              出血位置：<span class="text-ink">{{ analysis.mriQuantification.swiFindings.microBleedLocations.join('、') }}</span>
            </div>
            <div class="text-sub leading-5 mb-1">
              <span class="text-hint">含铁血黄素：</span>{{ analysis.mriQuantification.swiFindings.hemosiderinNote }}
            </div>
            <div class="text-sub leading-5 border-t border-[#E3E9EF] pt-1.5">
              <span class="text-hint">ARIA 相关性：</span>{{ analysis.mriQuantification.swiFindings.ariaRelevance }}
            </div>
          </div>

          <!-- 2026 版指南：标准化成像协议参数 -->
          <div v-if="analysis?.scannerInfo" class="px-3 py-2 rounded-card border border-[#E3E9EF] bg-[#F7FAFC] text-[12px]">
            <div class="flex items-center gap-2 mb-2 flex-wrap">
              <span class="font-medium text-ink">标准化成像协议参数</span>
              <el-tag size="small" type="info" effect="plain" round>{{ analysis.scannerInfo.status === 'simulated' ? '模拟机型' : '实际机型' }}</el-tag>
            </div>
            <div class="grid grid-cols-2 gap-x-4 gap-y-1 mb-2">
              <div class="text-hint">
                厂商：<span class="text-ink font-medium">{{ analysis.scannerInfo.manufacturer }}</span>
              </div>
              <div class="text-hint">
                机型：<span class="text-ink font-medium">{{ analysis.scannerInfo.model }}</span>
              </div>
              <div class="text-hint">
                场强：<span class="text-ink font-num">{{ analysis.scannerInfo.fieldStrength }}</span>
              </div>
              <div class="text-hint">
                PET 探测器：<span class="text-ink">{{ analysis.scannerInfo.petDetector }}</span>
              </div>
              <div class="text-hint col-span-2">
                重建方法：<span class="text-ink font-medium">{{ analysis.scannerInfo.reconMethod }}</span>
              </div>
              <div class="text-hint col-span-2">
                MRI 序列：<span class="text-ink">{{ analysis.scannerInfo.mriSequences }}</span>
              </div>
            </div>
            <div class="text-sub leading-5 border-t border-[#E3E9EF] pt-1.5">
              <span class="text-hint">纵向随访一致性：</span>{{ analysis.scannerInfo.longitudinalConsistency }}
            </div>
          </div>

          <!-- 真实模型集成信息 -->
          <div
            v-if="isRealInference"
            class="flex flex-wrap items-center gap-x-5 gap-y-1.5 px-3 py-2 rounded-card bg-primary-light border border-[#CBDEEF] text-[12px] text-primary"
          >
            <span class="flex items-center gap-2">
              <span class="font-medium">集成 AD 概率</span>
              <span class="font-num">{{ ((analysis?.ensembleProb ?? 0) * 100).toFixed(1) }}%</span>
              <!-- 概率迷你条 -->
              <span class="inline-block relative w-20 h-1.5 rounded-full bg-white/70 align-middle">
                <span
                  class="absolute left-0 top-0 h-full rounded-full transition-all duration-500"
                  :style="{ width: ((analysis?.ensembleProb ?? 0) * 100) + '%', background: analysis ? SCORE_COLOR[analysis.riskLevel] : '#5B8CAF' }"
                />
              </span>
            </span>
            <span v-if="probRangeText">
              <span class="font-medium">单模型概率区间</span>：<span class="font-num">{{ probRangeText }}</span>
            </span>
            <span v-if="typeof analysis?.probStd === 'number'">
              <span class="font-medium">概率标准差</span>：<span class="font-num">{{ (analysis.probStd * 100).toFixed(2) }}%</span>
            </span>
            <span v-if="analysis?.modelCount">
              <span class="font-medium">集成规模</span>：<span class="font-num">{{ analysis.modelCount }}</span> 个快照
            </span>
            <span v-if="analysis?.device">
              <span class="font-medium">推理设备</span>：<span class="font-num">{{ analysis.device }}</span>
            </span>
          </div>
          <!-- 摘要正文 -->
          <p class="text-[13px] text-sub leading-7 flex-1">
            {{ analysis?.summary }}
          </p>
          <!-- 病例上下文 -->
          <div class="flex items-center gap-4 text-[12px] text-hint border-t border-line pt-3">
            <span>病例编号：<span class="font-num">{{ caseInfo?.id }}</span></span>
            <span>患者：{{ caseInfo?.patient.name }}（{{ caseInfo?.patient.gender === 'M' ? '男' : '女' }} / {{ caseInfo?.patient.age }} 岁）</span>
            <span>检查时间：<span class="font-num">{{ caseInfo?.examDate }}</span></span>
          </div>
          <DisclaimerBar />
        </div>
      </div>

      <!-- 全数量化指标（行2 · 右 4/12：内嵌图形化，无需点开即可看偏离） -->
      <div class="card-ad col-span-4 min-w-0">
        <div class="card-ad__header">
          <span class="card-ad__title">量化指标</span>
          <el-button size="small" :icon="'FullScreen'" @click="metricsViewerVisible = true">全屏</el-button>
        </div>
        <div class="p-3 cursor-pointer" @click="metricsViewerVisible = true">
          <!-- 指标迷你参考带 -->
          <div class="space-y-2.5">
            <div v-for="m in analysis?.metrics ?? []" :key="m.key" class="group">
              <div class="flex items-baseline gap-2">
                <span class="text-[12px] text-ink truncate flex-1">{{ m.label }}</span>
                <span class="font-num text-[12.5px] font-semibold shrink-0" :style="{ color: metricDot(m.status) }">
                  {{ m.value }}{{ m.unit }}
                </span>
              </div>
              <div class="relative h-2 mt-1">
                <div class="absolute inset-x-0 top-0 h-2 rounded-full bg-[#EEF3F7]" />
                <div
                  class="absolute top-0 h-2 rounded-full bg-[#2E9E6B]/20 border-x border-[#2E9E6B]/45"
                  :style="{ left: miniBar(m).bandLeft + '%', width: miniBar(m).bandWidth + '%' }"
                />
                <div
                  class="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2.5 h-2.5 rounded-full border-[1.5px] border-white"
                  :style="{ left: miniBar(m).marker + '%', background: metricDot(m.status) }"
                />
              </div>
            </div>
          </div>

          <!-- 异常脑区 Z 值迷你分布 -->
          <div v-if="regions.length" class="mt-4 pt-3 border-t border-line">
            <div class="flex items-center justify-between mb-2">
              <span class="text-[12px] font-medium text-ink">异常脑区 · Z 值</span>
              <span class="text-[10.5px] text-hint">虚线 = -2 临界</span>
            </div>
            <div class="space-y-1.5">
              <div v-for="(r, i) in regions" :key="i" class="flex items-center gap-2">
                <span class="w-[72px] shrink-0 text-[11px] text-sub truncate">{{ r.region }}</span>
                <div class="flex-1 relative h-1.5">
                  <div class="absolute inset-x-0 top-0 h-1.5 rounded-full bg-[#EEF3F7]" />
                  <div
                    class="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-px h-2.5 border-l border-dashed border-[#9AA7B2]"
                    style="left: 50%"
                  />
                  <div
                    class="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2 h-2 rounded-full border border-white"
                    :style="{ left: zLeftOf(r.zScore) + '%', background: zColorOf(r.zScore) }"
                  />
                </div>
                <span class="w-8 shrink-0 text-right font-num text-[11px] font-semibold" :style="{ color: zColorOf(r.zScore) }">
                  {{ r.zScore.toFixed(1) }}
                </span>
              </div>
            </div>
          </div>
          <div class="mt-3 text-[10.5px] text-hint text-center">点击查看完整参考范围 / 代谢偏离可视化</div>
        </div>
      </div>
    </div><!-- /行2 grid -->

    <!-- ==================== 行2.5：相似病例参考（top-k 自动推荐） ==================== -->
    <div class="card-ad">
      <div class="card-ad__header">
        <span class="card-ad__title">相似病例参考</span>
        <span class="text-[11px] text-hint">基于风险等级、异常脑区、人口学特征综合匹配 Top 3</span>
      </div>
      <div class="p-4">
        <div v-if="similarLoading" class="grid grid-cols-3 gap-4">
          <el-skeleton v-for="i in 3" :key="i" :rows="4" animated />
        </div>
        <div v-else-if="similarCases.length === 0" class="text-center py-6 text-hint text-[13px]">
          暂无相似病例（病例库中需有分析记录的其他病例）
        </div>
        <div v-else class="grid grid-cols-3 gap-4">
          <el-card
            v-for="c in similarCases"
            :key="c.caseId"
            shadow="hover"
            class="cursor-pointer hover:border-primary transition-colors"
            @click="router.push(`/analysis/${c.caseId}`)"
          >
            <div class="flex items-center justify-between mb-2">
              <span class="font-num text-[13px] font-semibold text-ink">{{ c.caseId }}</span>
              <RiskLevelTag :risk-level="c.riskLevel" />
            </div>
            <div class="text-[13px] text-ink mb-1">{{ c.patientName }}</div>
            <div class="text-[12px] text-sub mb-2">
              {{ c.gender === 'M' ? '男' : '女' }} / {{ c.age }} 岁 · {{ c.examDate }}
            </div>
            <div class="flex items-center justify-between text-[12px] text-hint">
              <span>风险分 <span class="font-num text-ink">{{ c.riskScore }}</span></span>
              <span>异常脑区 <span class="font-num text-ink">{{ c.abnormalRegionCount }}</span> 个</span>
            </div>
          </el-card>
        </div>
      </div>
    </div>

    <!-- ==================== 行3：干预方案（全宽） ==================== -->
    <div class="card-ad">
      <div class="card-ad__header !h-auto !min-h-12 py-1.5 flex-wrap gap-y-1.5">
        <span class="card-ad__title">干预方案（AI 生成 · 医生可编辑）</span>
        <div class="flex items-center gap-2.5 flex-wrap justify-end">
          <el-button size="small" :icon="'Clock'" @click="openFollowConfig">随访时间配置</el-button>
          <el-button size="small" :icon="'Printer'" @click="printPreview">打印预览</el-button>
          <el-button size="small" :icon="'Download'" @click="exportPdf">PDF 导出</el-button>
          <el-button size="small" :icon="'ChatDotRound'" @click="commentVisible = true">
            会诊讨论<el-badge v-if="commentCount > 0" :value="commentCount" class="!ml-1" />
          </el-button>
          <el-button size="small" type="primary" :icon="'FolderChecked'" :loading="saving" @click="saveConfirmVisible = true">
            保存方案
          </el-button>
        </div>
      </div>

      <div class="p-4 print-area">
        <el-tabs v-model="activeTab">
          <el-tab-pane v-for="s in sections" :key="s.key" :name="s.key">
            <template #label>
              <span class="flex items-center gap-1.5">
                {{ s.title }}
                <el-tag size="small" effect="plain" round class="ml-0.5">{{ s.items.length }}</el-tag>
              </span>
            </template>

            <!-- 随访复查模块附加：随访计划摘要 -->
            <div v-if="s.key === 'followup' && followUp" class="mb-3 px-4 py-3 rounded-card bg-primary-light border border-[#CBDEEF] flex items-center gap-6">
              <div class="text-[13px] text-primary">
                <span class="font-medium">随访周期</span>：每 {{ followUp.cycleMonths }} 个月
              </div>
              <div class="text-[13px] text-primary">
                <span class="font-medium">下次随访</span>：<span class="font-num">{{ followUp.nextDate }}</span>
              </div>
              <div class="text-[13px] text-primary flex-1 truncate">
                <span class="font-medium">提醒</span>：<span class="font-num">{{ followUp.reminders.join(' / ') }}</span>
              </div>
              <el-button size="small" type="primary" plain :icon="'Calendar'" @click="goRecordVisit">记录本次随访</el-button>
              <el-button size="small" text type="primary" @click="openFollowConfig">修改计划</el-button>
            </div>

            <!-- 干预条目列表 -->
            <div class="space-y-2.5">
              <div
                v-for="item in s.items"
                :key="item.id"
                class="group px-4 py-3 rounded-card border border-line hover:border-primary/50 transition-colors flex gap-3"
              >
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2">
                    <span class="text-[14px] font-medium text-ink">{{ item.title }}</span>
                    <el-tag size="small" effect="plain" round :type="item.source === 'AI' ? 'primary' : 'success'">
                      {{ item.source === 'AI' ? 'AI 生成' : '医生修订' }}
                    </el-tag>
                  </div>
                  <p class="mt-1.5 text-[13px] text-sub leading-6">{{ item.content }}</p>
                  <div class="mt-1.5 flex items-center gap-4 text-[12px] text-hint">
                    <span><el-icon class="align-[-2px]"><Timer /></el-icon> 频率：{{ item.frequency }}</span>
                    <span><el-icon class="align-[-2px]"><Stopwatch /></el-icon> 时长：{{ item.duration }}</span>
                  </div>
                </div>
                <!-- 行内操作（打印时隐藏） -->
                <div class="shrink-0 flex items-center gap-1 print:hidden">
                  <el-button link type="primary" :icon="'EditPen'" @click="openEdit(s.key, item)">编辑</el-button>
                  <el-button link type="danger" :icon="'Delete'" @click="removeItem(s.key, item)">删除</el-button>
                </div>
              </div>
            </div>

            <!-- 新增条目 -->
            <div class="mt-3 print:hidden">
              <el-button class="!w-full" plain :icon="'Plus'" @click="openEdit(s.key, null)">
                新增{{ s.title }}条目
              </el-button>
            </div>
          </el-tab-pane>

          <!-- 纵向随访：同患者多期影像量化对比 + NIA-AA 纵向进展分级 -->
          <el-tab-pane name="longitudinal" v-if="caseInfo?.patient.patientNo">
            <template #label>
              <span class="flex items-center gap-1.5">
                纵向随访
                <el-icon class="align-[-2px]"><DataLine /></el-icon>
              </span>
            </template>
            <div class="py-2">
              <LongitudinalCompare :patient-no="caseInfo!.patient.patientNo" />
            </div>
          </el-tab-pane>
        </el-tabs>

        <!-- 随访计划打印摘要（打印可见） -->
        <div v-if="followUp" class="hidden print:block mt-4 text-[13px] text-ink leading-7">
          <b>随访计划：</b>周期每 {{ followUp.cycleMonths }} 个月，下次随访 {{ followUp.nextDate }}；{{ followUp.note }}
        </div>
      </div>
    </div>

    <!-- ==================== 弹窗组 ==================== -->
    <!-- 3D 体绘制弹窗 -->
    <el-dialog v-model="volVisible" title="3D 体绘制 · MRI-PET 融合与重点脑区标注" width="860px" destroy-on-close>
      <div style="height: 540px">
        <VolumeRenderer
          v-if="volVisible"
          :case-id="caseId"
          initial-modality="MRI"
          :abnormal-regions="analysis?.abnormalRegions ?? []"
        />
      </div>
      <template #footer>
        <span class="text-[11px] text-hint">
          脑区标注基于标准脑图谱近似位置，仅供示教理解；融合视图叠加 PET 代谢信息，红色 ⚠ 标记为 AI 提示异常的重点脑区
        </span>
      </template>
    </el-dialog>

    <!-- 条目编辑弹窗 -->
    <el-dialog
      v-model="editVisible"
      :title="editingItem ? '编辑干预条目' : '新增干预条目'"
      width="540px"
    >
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="项目名称" required>
          <el-input v-model="editForm.title" placeholder="如：认知功能训练" maxlength="30" />
        </el-form-item>
        <el-form-item label="干预内容" required>
          <el-input v-model="editForm.content" type="textarea" :rows="4" placeholder="详细干预内容说明" maxlength="300" show-word-limit />
        </el-form-item>
        <div class="grid grid-cols-2 gap-x-5">
          <el-form-item label="执行频率">
            <el-input v-model="editForm.frequency" placeholder="如：每周 5 次" />
          </el-form-item>
          <el-form-item label="单次时长">
            <el-input v-model="editForm.duration" placeholder="如：40 分钟/次" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="submitEdit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 随访时间配置弹窗 -->
    <el-dialog v-model="followVisible" title="随访时间配置" width="480px">
      <el-form :model="followForm" label-width="90px">
        <el-form-item label="随访周期">
          <el-radio-group v-model="followForm.cycleMonths">
            <el-radio-button :value="3">每 3 个月</el-radio-button>
            <el-radio-button :value="6">每 6 个月</el-radio-button>
            <el-radio-button :value="12">每 12 个月</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="下次随访">
          <el-date-picker v-model="followForm.nextDate" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" class="!w-full" />
        </el-form-item>
        <el-form-item label="随访备注">
          <el-input v-model="followForm.note" type="textarea" :rows="2" placeholder="随访内容说明" />
        </el-form-item>
      </el-form>
      <div class="text-[12px] text-hint px-2">系统将按到期前 7 天与到期日自动生成两条提醒日期</div>
      <template #footer>
        <el-button @click="followVisible = false">取消</el-button>
        <el-button type="primary" @click="submitFollow">确定</el-button>
      </template>
    </el-dialog>

    <!-- 保存确认弹窗 -->
    <ConfirmDialog
      v-model="saveConfirmVisible"
      title="保存干预方案"
      type="info"
      content="保存后干预方案将归档至病例记录，病例状态更新为「医生已审核」。是否确认保存？"
      confirm-text="确认保存"
      @confirm="savePlan"
    />

    <!-- 全屏专一影像查看器 -->
    <ImageFullscreenViewer
      v-model="imgViewerVisible"
      :case-id="caseId"
      :initial-modality="imgViewerModality"
      :meta="imagingMeta"
    />

    <!-- 全屏 AI 量化分析查看器 -->
    <MetricsFullscreenViewer
      v-model="metricsViewerVisible"
      :analysis="analysis"
    />

    <!-- ==================== AI 版本对比弹窗 ==================== -->
    <el-dialog
      v-model="compareVisible"
      title="AI 分析版本对比"
      width="900px"
      append-to-body
      :close-on-click-modal="false"
    >
      <div v-if="versions.length < 2" class="py-10 text-center text-hint">
        该病例暂无两个及以上分析版本，无法对比
      </div>
      <div v-else>
        <div class="flex items-center gap-3 mb-4">
          <span class="text-[13px] text-sub">版本 A：</span>
          <el-select v-model="compareVerA" class="!w-52" @change="loadCompare">
            <el-option
              v-for="v in versions"
              :key="v.id"
              :label="`v${v.version} · ${v.modelVersion || '—'} · ${v.riskScore}分`"
              :value="v.id"
            />
          </el-select>
          <el-icon class="text-hint"><Right /></el-icon>
          <span class="text-[13px] text-sub">版本 B：</span>
          <el-select v-model="compareVerB" class="!w-52" @change="loadCompare">
            <el-option
              v-for="v in versions"
              :key="v.id"
              :label="`v${v.version} · ${v.modelVersion || '—'} · ${v.riskScore}分`"
              :value="v.id"
            />
          </el-select>
        </div>

        <el-table :data="compareRows" v-loading="compareLoading" border size="default" class="w-full">
          <el-table-column prop="label" label="对比项" width="150" align="center" header-align="center">
            <template #default="{ row }">
              <span class="text-[13px] font-medium text-ink">{{ row.label }}</span>
            </template>
          </el-table-column>
          <el-table-column label="版本 A" align="center">
            <template #default="{ row }">
              <span class="font-num text-[13px] text-ink">{{ row.a }}</span>
            </template>
          </el-table-column>
          <el-table-column label="版本 B" align="center">
            <template #default="{ row }">
              <span class="font-num text-[13px] text-ink">{{ row.b }}</span>
            </template>
          </el-table-column>
          <el-table-column label="差异 (A−B)" width="120" align="center">
            <template #default="{ row }">
              <span class="font-num text-[13px]" :style="{ color: row.diffColor }">{{ row.diff }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="mt-3 text-[12px] text-hint">
          差异列数值为 A−B，正向蓝色 / 负向橙色仅作差异方向提示，不代表临床优劣；模型版本与融合策略变更请结合审核记录综合判断。
        </div>
      </div>
    </el-dialog>

    <!-- 病例评论 / 会诊讨论抽屉 -->
    <el-drawer v-model="commentVisible" title="病例评论 · 会诊讨论" size="460px" destroy-on-close>
      <CaseCommentPanel :case-id="caseId" @comment-count="commentCount = $event" />
    </el-drawer>
  </div>
</template>
