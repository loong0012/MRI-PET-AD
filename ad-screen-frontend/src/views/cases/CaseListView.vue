<script setup lang="ts">
/**
 * 病例库管理页
 * ------------------------------------------------------------------
 * 1. 检索区：关键词搜索 + 高级筛选（模态 / 状态 / 风险等级 / 检查日期段）
 * 2. 操作区：DICOM 影像上传弹窗（新建病例）+ CSV 批量导入弹窗
 * 3. 表格区：病例ID / 患者信息 / 模态 / 检查时间 / AI 风险等级 / 诊断状态，
 *    行操作：查看影像（阅片页）、启动 AI 分析、AI 详情、生成报告
 * 4. 分页：标准医疗系统底部分页器
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadUserFile } from 'element-plus'
import { apiQueryCases, apiUploadStudy, apiBatchImport, apiUploadSample, apiDeleteCase, apiDeleteCases, apiToggleFavorite, apiCheckFavorites, type SampleKind } from '@/api/case'
import { apiGetCaseTimeline, type CaseTimelineItem } from '@/api/caseLog'
import { apiBatchAnalysis, type BatchSubmitResult } from '@/api/analysis'
import { apiBatchExportReports } from '@/api/report'
import { useUserStore } from '@/stores/user'
import RiskLevelTag from '@/components/RiskLevelTag.vue'
import DisclaimerBar from '@/components/DisclaimerBar.vue'
import BatchProgressDialog from '@/components/BatchProgressDialog.vue'
import DatasetExportDialog from '@/components/DatasetExportDialog.vue'
import { downloadCsv } from '@/utils/export'
import { formatDate } from '@/utils/format'
import type { CaseRecord, CaseQuery, Modality, RiskLevel, CaseStatus, UploadPayload, ExamIndication, PetTracer, AbsoluteContraindication, RelativeContraindication, SpecialPopulation } from '@/types/case'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

/** 删除会级联清除 AI/干预/随访数据，仅临床医生与管理员可见入口（与后端角色限制一致） */
const canDeleteCase = computed(() => ['radiologist', 'neurologist', 'admin'].includes(userStore.role))

// ---------- 列表查询状态 ----------
const loading = ref(false)
const list = ref<CaseRecord[]>([])
const favoritesMap = ref<Record<string, boolean>>({})
const total = ref(0)

const query = reactive<CaseQuery>({
  keyword: '',
  modality: '',
  status: '',
  riskLevel: '',
  diagStatus: '',
  department: '',
  gender: '',
  ageMin: null,
  ageMax: null,
  hasFollowup: null,
  favorites: null,
  dateRange: null,
  page: 1,
  pageSize: 10
})
// 工作台"查看全部收藏"跳转入口：setup 阶段读取路由参数，确保首次 fetchList 即带 favorites 过滤
if (route.query.favorites === '1') {
  query.favorites = true
}

/** 高级筛选展开开关 */
const showFilter = ref(false)

/** 筛选预设 */
const FILTER_PRESETS_KEY = 'ad_case_filter_presets'
const presetName = ref('')
const savedPresets = ref<{ name: string; query: Record<string, unknown> }[]>([])

function loadPresets(): void {
  try {
    const raw = localStorage.getItem(FILTER_PRESETS_KEY)
    savedPresets.value = raw ? JSON.parse(raw) : []
  } catch { savedPresets.value = [] }
}

function savePreset(): void {
  const name = presetName.value.trim()
  if (!name) { ElMessage.warning('请输入预设名称'); return }
  const preset = { name, query: { ...query } as Record<string, unknown> }
  delete (preset.query as Record<string, unknown>).page
  delete (preset.query as Record<string, unknown>).pageSize
  const idx = savedPresets.value.findIndex(p => p.name === name)
  if (idx >= 0) savedPresets.value[idx] = preset
  else savedPresets.value.push(preset)
  // 持久化失败（隐私模式/配额）不阻断：内存态已更新，仅本次页面生效
  try {
    localStorage.setItem(FILTER_PRESETS_KEY, JSON.stringify(savedPresets.value))
  } catch {
    ElMessage.warning('本地存储不可用，预设仅在当前页面生效')
  }
  ElMessage.success(`预设「${name}」已保存`)
  presetName.value = ''
}

function applyPreset(name: string): void {
  const preset = savedPresets.value.find(p => p.name === name)
  if (!preset) return
  Object.assign(query, preset.query)
  query.page = 1
  fetchList()
  ElMessage.success(`已加载预设「${name}」`)
}

function deletePreset(name: string): void {
  savedPresets.value = savedPresets.value.filter(p => p.name !== name)
  // 内存态已删除；持久化失败静默忽略，刷新后即恢复默认
  try {
    localStorage.setItem(FILTER_PRESETS_KEY, JSON.stringify(savedPresets.value))
  } catch { /* 忽略存储异常 */ }
}

// ---------- 批量分析（异步任务队列）----------
const selectedCases = ref<CaseRecord[]>([])
/** el-table 实例引用（reserve-selection 跨页保留后，删除/重置需显式 clearSelection） */
const caseTableRef = ref<{ clearSelection: () => void } | null>(null)
const batchRunning = ref(false)
const batchTaskId = ref('')
const batchProgressVisible = ref(false)

// ---------- 科研数据集导出 ----------
const datasetExportVisible = ref(false)

function onSelectionChange(rows: CaseRecord[]): void {
  selectedCases.value = rows
}

async function runBatchAnalysis(): Promise<void> {
  if (selectedCases.value.length === 0) {
    ElMessage.warning('请先勾选需要分析的病例')
    return
  }
  const ids = selectedCases.value.map((c) => c.id)
  batchRunning.value = true
  try {
    const res: BatchSubmitResult = await apiBatchAnalysis(ids, userStore.realName || userStore.roleName)
    batchTaskId.value = res.taskId
    batchProgressVisible.value = true
    ElMessage.success(`批量分析任务已提交（${res.total} 例），后台执行中…`)
  } catch (e) {
    // 请求拦截器已统一提示业务错误（__handled），此处仅补纯本地异常提示
    if (!(e as { __handled?: boolean })?.__handled) ElMessage.error('批量分析提交失败，请稍后重试')
  } finally {
    batchRunning.value = false
  }
}

/** 批量进度对话框完成回调 */
function onBatchCompleted(): void {
  fetchList()
}

/** 打开科研数据集导出对话框 */
function openDatasetExport(): void {
  if (selectedCases.value.length === 0) {
    ElMessage.warning('请先勾选需要导出的病例')
    return
  }
  datasetExportVisible.value = true
}

/** 列表请求序号：快速翻页/连续筛选时丢弃过期响应，避免旧页 list/total/favoritesMap 覆盖新页 */
let fetchReqSeq = 0

/** 拉取列表 */
async function fetchList(): Promise<void> {
  const seq = ++fetchReqSeq
  loading.value = true
  try {
    const res = await apiQueryCases({ ...query })
    // 在途期间又发起了新请求：本次响应已过期，直接丢弃不更新任何 ref
    if (seq !== fetchReqSeq) return
    list.value = res.list
    total.value = res.total
    // 批量查询当前页收藏状态
    if (res.list.length > 0) {
      const favMap = await apiCheckFavorites(res.list.map((c) => c.id))
      // 收藏查询在途期间同样可能被新请求超越，回包后二次校验
      if (seq !== fetchReqSeq) return
      favoritesMap.value = favMap
    } else {
      favoritesMap.value = {}
    }
  } finally {
    // 仅最新一次请求负责复位 loading
    if (seq === fetchReqSeq) loading.value = false
  }
}

async function toggleFavorite(row: CaseRecord): Promise<void> {
  const res = await apiToggleFavorite(row.id)
  favoritesMap.value[row.id] = res.favorited
  ElMessage.success(res.favorited ? `已收藏 ${row.patient.name}` : `已取消收藏 ${row.patient.name}`)
}

/** 搜索 / 重置 */
function onSearch(): void {
  query.page = 1
  fetchList()
}
function onReset(): void {
  query.keyword = ''
  query.modality = ''
  query.status = ''
  query.riskLevel = ''
  query.diagStatus = ''
  query.department = ''
  query.gender = ''
  query.ageMin = null
  query.ageMax = null
  query.hasFollowup = null
  query.favorites = null
  query.dateRange = null
  onSearch()
}

// 首次进入：支持从工作台快捷入口直接打开上传/批量导入弹窗
onMounted(() => {
  loadPresets()
  fetchList()
  if (route.query.upload === '1') uploadVisible.value = true
  if (route.query.batch === '1') batchVisible.value = true
})

// ---------- 诊断状态 / 模态展示 ----------
function statusTagType(s: CaseStatus): 'warning' | 'primary' | 'success' | 'info' {
  switch (s) {
    case 'pending':
      return 'warning'
    case 'analyzing':
      return 'primary'
    case 'completed':
      return 'success'
    default:
      return 'info'
  }
}
const DIAG_TEXT: Record<CaseStatus, string> = {
  pending: '待分析',
  analyzing: '分析中',
  completed: '已分析',
  reported: '已出报告'
}

/** 模态徽标（MRI / PET 双模态时展示两枚） */
function modalities(m: Modality): string[] {
  return m === 'MRI+PET' ? ['MRI', 'PET'] : [m]
}

// ---------- 科研 CSV 导出（当前筛选结果全量，非仅当前页） ----------
const exporting = ref(false)
const reportExporting = ref(false)
const RISK_TEXT: Record<string, string> = {
  low: '低风险',
  mci: '轻度认知障碍',
  'ad-early': 'AD 早期',
  'ad-late': 'AD 中晚期'
}

async function onExportCsv(): Promise<void> {
  exporting.value = true
  try {
    // 后端单页上限 100，按页循环拉全；单次导出保护上限 5000 例
    const EXPORT_MAX = 5000
    const first = await apiQueryCases({ ...query, page: 1, pageSize: 100 })
    const total = first.total
    const rows = [...first.list]
    const maxPages = Math.ceil(Math.min(total, EXPORT_MAX) / 100)
    for (let p = 2; p <= maxPages && rows.length < total; p++) {
      const r = await apiQueryCases({ ...query, page: p, pageSize: 100 })
      rows.push(...r.list)
    }
    // 超量截断提示：匹配数超过单次导出上限时显式提醒，避免 success 误导用户以为已全量导出
    if (total > rows.length) {
      ElMessage.warning(`匹配 ${total} 例超过单次导出上限 ${EXPORT_MAX} 例，仅导出前 ${rows.length} 例，请缩小筛选范围分批导出`)
    }
    downloadCsv(
      `病例明细_${formatDate(new Date())}.csv`,
      ['病例编号', '患者编号', '姓名', '性别', '年龄', '科室', '影像模态', '检查时间', '风险评分', '风险分级', '诊断状态', 'MRI', 'PET', '建档时间'],
      rows.map((c) => [
        c.id,
        c.patient.patientNo,
        c.patient.name,
        c.patient.gender === 'M' ? '男' : '女',
        c.patient.age,
        c.department,
        c.modality,
        c.examDate,
        c.riskScore ?? '',
        c.riskLevel ? RISK_TEXT[c.riskLevel] ?? c.riskLevel : '',
        c.diagStatus || (DIAG_TEXT[c.status] ?? c.status),
        c.hasMRI ? '有' : '无',
        c.hasPET ? '有' : '无',
        c.createTime ?? ''
      ])
    )
    ElMessage.success(`已导出 ${rows.length} 例病例数据`)
  } catch (e) {
    // 接口错误已由请求拦截器统一提示；此处仅兜底本地导出异常
    if (!(e as { __handled?: boolean })?.__handled) ElMessage.error('导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}

/** 批量导出选中病例的报告汇总 CSV */
async function onBatchExportReport(): Promise<void> {
  if (selectedCases.value.length === 0) {
    ElMessage.warning('请先勾选要导出报告的病例')
    return
  }
  reportExporting.value = true
  try {
    await apiBatchExportReports(selectedCases.value.map((c) => c.id))
    ElMessage.success(`已导出 ${selectedCases.value.length} 例病例的报告汇总`)
  } catch (e) {
    if (!(e as { __handled?: boolean })?.__handled) ElMessage.error('报告导出失败，请稍后重试')
  } finally {
    reportExporting.value = false
  }
}

// ---------- 病例操作历程时间线 ----------
const timelineVisible = ref(false)
const timelineLoading = ref(false)
const timelineCaseId = ref('')
const timelinePatientName = ref('')
const timelineItems = ref<CaseTimelineItem[]>([])

async function openTimeline(row: CaseRecord): Promise<void> {
  timelineCaseId.value = row.id
  timelinePatientName.value = row.patient.name
  timelineItems.value = []
  timelineVisible.value = true
  timelineLoading.value = true
  try {
    timelineItems.value = await apiGetCaseTimeline(row.id)
  } catch (e) {
    if (!(e as { __handled?: boolean })?.__handled) ElMessage.error('操作历程加载失败')
  } finally {
    timelineLoading.value = false
  }
}

// ---------- 病例对比查看（限选 2 例） ----------
const compareVisible = ref(false)
const compareCases = ref<CaseRecord[]>([])

function openCompare(): void {
  if (selectedCases.value.length !== 2) {
    ElMessage.warning('请勾选 2 例病例进行对比')
    return
  }
  compareCases.value = [...selectedCases.value]
  compareVisible.value = true
}

const GENDER_TEXT: Record<string, string> = { M: '男', F: '女' }

/** 对比表格行（基线/影像/AI/状态） */
const compareRows = computed(() => {
  const cs = compareCases.value
  if (cs.length !== 2) return []
  const [a, b] = cs
  const rows = [
    { label: '患者年龄', values: [`${a.patient.age} 岁`, `${b.patient.age} 岁`], highlight: true },
    { label: '性别', values: [GENDER_TEXT[a.patient.gender] ?? a.patient.gender, GENDER_TEXT[b.patient.gender] ?? b.patient.gender], highlight: false },
    { label: '检查日期', values: [a.examDate || '—', b.examDate || '—'], highlight: false },
    { label: '影像模态', values: [a.modality, b.modality], highlight: true },
    { label: 'AI 风险评分', values: [a.riskScore !== null ? a.riskScore : '未分析', b.riskScore !== null ? b.riskScore : '未分析'], highlight: true },
    { label: '风险分级', values: [a.riskLevel ? RISK_TEXT[a.riskLevel] ?? a.riskLevel : '未分析', b.riskLevel ? RISK_TEXT[b.riskLevel] ?? b.riskLevel : '未分析'], highlight: true },
    { label: '影像完备性', values: [`MRI:${a.hasMRI ? '有' : '无'} / PET:${a.hasPET ? '有' : '无'}`, `MRI:${b.hasMRI ? '有' : '无'} / PET:${b.hasPET ? '有' : '无'}`], highlight: false },
    { label: '诊断状态', values: [DIAG_TEXT[a.status as CaseStatus] ?? a.status, DIAG_TEXT[b.status as CaseStatus] ?? b.status], highlight: true },
  ]
  return rows
})

// ---------- 行操作 ----------
/** 查看影像 → 阅片页 */
function goViewer(row: CaseRecord): void {
  router.push(`/viewer/${row.id}`)
}
/** AI 详情 + 干预方案 */
function goAnalysis(row: CaseRecord): void {
  router.push(`/analysis/${row.id}`)
}
/** 生成报告 */
function goReport(row: CaseRecord): void {
  router.push(`/report/${row.id}`)
}
/** 删除后若当前页被删空，回退一页再拉取，避免停在越界空页 */
function retreatPageIfCurrentEmptied(deletedIds: string[]): void {
  const remainingOnPage = list.value.filter((c) => !deletedIds.includes(c.id))
  if (remainingOnPage.length === 0 && query.page > 1) query.page -= 1
}

/** 删除单病例 */
async function onDelete(row: CaseRecord): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除病例 <b>${row.id}</b>（${row.patient.name}）？<br/>将同时清除该病例的 AI 分析结果、干预方案与全部随访记录，<b>操作不可恢复</b>。`,
      '删除病例确认',
      { dangerouslyUseHTMLString: true, confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await apiDeleteCase(row.id, userStore.realName || userStore.roleName)
    ElMessage.success(`病例 ${row.id} 已删除`)
    selectedCases.value = selectedCases.value.filter((c) => c.id !== row.id)
    retreatPageIfCurrentEmptied([row.id])
    fetchList()
  } catch (e) {
    if (!(e as { __handled?: boolean })?.__handled) ElMessage.error('删除失败，请稍后重试')
  }
}

/** 批量删除 */
async function onBatchDelete(): Promise<void> {
  if (selectedCases.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确认批量删除选中的 <b>${selectedCases.value.length}</b> 例病例？<br/>将同时清除其 AI 分析、干预方案与随访记录，<b>操作不可恢复</b>。`,
      '批量删除确认',
      { dangerouslyUseHTMLString: true, confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  const ids = selectedCases.value.map((c) => c.id)
  try {
    const res = await apiDeleteCases(ids, userStore.realName || userStore.roleName)
    ElMessage.success(`已删除 ${res.deleted} 例`)
    // reserve-selection 跨页保留后必须显式清空表格选择态（会触发 onSelectionChange([]) 同步 selectedCases）
    caseTableRef.value?.clearSelection()
    retreatPageIfCurrentEmptied(ids)
    fetchList()
  } catch (e) {
    if (!(e as { __handled?: boolean })?.__handled) ElMessage.error('批量删除失败，请稍后重试')
  }
}
/** 列表内快速启动 AI 分析（未分析病例） */
const analyzingIds = ref<Set<string>>(new Set())
function quickAnalyze(row: CaseRecord): void {
  if (!row.hasMRI && !row.hasPET) {
    ElMessage.warning('该病例暂无可用影像数据，请先上传 MRI/PET 影像')
    return
  }
  analyzingIds.value.add(row.id)
  ElMessage.info(`已加入分析队列：${row.id}（可在阅片页查看进度）`)
  // 列表页快捷入口仅入队提示，正式推理在阅片页完成（保证阅片上下文一致）
  setTimeout(() => {
    analyzingIds.value.delete(row.id)
    router.push(`/viewer/${row.id}`)
  }, 800)
}

// ---------- 影像上传弹窗 ----------
const uploadVisible = ref(false)
const uploadForm = reactive<Omit<UploadPayload, 'files'>>({
  patientNo: '',
  patientName: '',
  gender: 'M',
  age: 65,
  modality: 'MRI+PET',
  department: '神经内科',
  examIndication: 'diagnosis' as ExamIndication,
  petTracer: 'fdg' as PetTracer,
})
const uploadFiles = ref<UploadUserFile[]>([])
const uploading = ref(false)

// ---------- 2026 版指南 表2：PET 显像剂推荐参数（前端联动展示用） ----------
const TRACER_INFO: Record<PetTracer, {
  name: string; cnName: string; dose: string; uptake: string; acq: string; half: string; indication: string; fasting: boolean; glucose: boolean
}> = {
  fdg: {
    name: '¹⁸F-FDG', cnName: '氟代脱氧葡萄糖',
    dose: '37 MBq/kg', uptake: '60 分钟', acq: '10 分钟', half: '109.8 min',
    indication: '评估脑葡萄糖代谢，用于退行性病变鉴别诊断（B 级推荐）',
    fasting: true, glucose: true,
  },
  amyloid: {
    name: '¹⁸F-florbetapir / florbetaben / flutemetamol / ¹¹C-PIB', cnName: 'Aβ 显像剂',
    dose: '185~500 MBq（按显像剂）', uptake: '50~90 分钟', acq: '10~20 分钟', half: '20.4~109.8 min',
    indication: '脑内 Aβ 沉积定性诊断与药物疗效评估（A 级推荐）',
    fasting: false, glucose: false,
  },
  tau: {
    name: '¹⁸F-flortaucipir / ¹⁸F-MK6240', cnName: 'Tau 显像剂',
    dose: '185~370 MBq', uptake: '80~90 分钟', acq: '20 分钟', half: '109.8 min',
    indication: '脑内 tau 病理分布评估与生物学分期（A 级推荐，SUVR>1.65 阳性）',
    fasting: false, glucose: false,
  },
}
const currentTracerInfo = computed(() => TRACER_INFO[uploadForm.petTracer as PetTracer] ?? TRACER_INFO.fdg)

// ---------- 2026 版 PET/MRI 指南：检查前评估（禁忌证 + 特殊人群） ----------
/** 绝对禁忌（多选） */
const absoluteContraindications = ref<AbsoluteContraindication[]>([])
/** 相对禁忌（多选） */
const relativeContraindications = ref<RelativeContraindication[]>([])
/** 特殊人群标记（单选，默认普通人群） */
const specialPopulation = ref<SpecialPopulation>('normal')

/** 绝对禁忌选项（指南"检查前评估"：MRI 不兼容有源植入物） */
const ABSOLUTE_CONTRA_OPTIONS: Array<{ value: AbsoluteContraindication; label: string }> = [
  { value: 'pacemaker', label: '心脏起搏器' },
  { value: 'defibrillator', label: '除颤器' },
  { value: 'cochlear_implant', label: '人工耳蜗' },
  { value: 'dbs', label: '脑深部电刺激' },
  { value: 'insulin_pump', label: '胰岛素泵' }
]

/** 相对禁忌选项（指南"检查前评估"：金属植入物需确认 MRI 安全等级） */
const RELATIVE_CONTRA_OPTIONS: Array<{ value: RelativeContraindication; label: string }> = [
  { value: 'steel_nails', label: '钢钉/钢板' },
  { value: 'artificial_joint', label: '人工关节' },
  { value: 'aneurysm_clip', label: '动脉瘤夹' },
  { value: 'coronary_stent', label: '冠脉支架' },
  { value: 'removable_dentures', label: '可摘义齿' },
  { value: 'claustrophobia', label: '幽闭恐惧' },
  { value: 'diabetes_uncontrolled', label: '糖尿病血糖未控制' }
]

/** 特殊人群选项（指南"检查前准备"） */
const SPECIAL_POP_OPTIONS: Array<{ value: SpecialPopulation; label: string }> = [
  { value: 'normal', label: '普通人群' },
  { value: 'down_synodrome', label: 'Down 综合征 AD 患者' },
  { value: 'claustrophobia', label: '幽闭恐惧症' },
  { value: 'diabetes', label: '糖尿病' },
  { value: 'implanted_device', label: '体内金属植入物' }
]

/** 当勾选任意"绝对禁忌"且模态包含 MRI 时，提示不可进行 MRI 检查 */
const showMriAbsoluteWarn = computed(() =>
  absoluteContraindications.value.length > 0 &&
  uploadForm.modality !== 'PET'
)

/** 提交影像上传（DICOM 文件清单随表单提交） */
async function submitUpload(): Promise<void> {
  if (!uploadForm.patientNo.trim() || !uploadForm.patientName.trim()) {
    ElMessage.warning('请填写患者编号与姓名')
    return
  }
  if (uploadFiles.value.length === 0) {
    ElMessage.warning('请选择需要上传的影像文件')
    return
  }
  // auto-upload=false 时 el-upload 不会触发 before-upload，必须在提交前手动校验，
  // 否则非法文件（如 .txt/.jpg）会一路带到后端才报错
  const invalid = uploadFiles.value.find((f) => !f.raw || !validateDicomFile(f.raw as File))
  if (invalid) return
  uploading.value = true
  try {
    // 演示模式：文件对象仅作数量统计；生产模式走 FormData 直传后端 PACS 网关
    const files = uploadFiles.value.map((f) => f.raw as File)
    await apiUploadStudy({
      ...uploadForm,
      files,
      absoluteContraindications: absoluteContraindications.value,
      relativeContraindications: relativeContraindications.value,
      specialPopulation: specialPopulation.value
    }, userStore.userInfo?.username ?? 'unknown')
    ElMessage.success('影像上传成功，病例已创建')
    uploadVisible.value = false
    resetUploadForm()
    onSearch()
  } finally {
    uploading.value = false
  }
}

function resetUploadForm(): void {
  uploadForm.patientNo = ''
  uploadForm.patientName = ''
  uploadForm.gender = 'M'
  uploadForm.age = 65
  uploadForm.modality = 'MRI+PET'
  uploadForm.department = '神经内科'
  uploadForm.examIndication = 'diagnosis'
  uploadForm.petTracer = 'fdg'
  uploadFiles.value = []
  // 2026 版指南：检查前评估字段同步清空
  absoluteContraindications.value = []
  relativeContraindications.value = []
  specialPopulation.value = 'normal'
}

// ---------- 示例数据一键载入（演示用） ----------
const sampleLoading = ref<SampleKind | ''>('')

/** 载入内置 ADNI 真实示例影像（MRI+PET 配对），建档后可直接发起 AI 分析演示 */
async function loadSample(kind: SampleKind): Promise<void> {
  sampleLoading.value = kind
  try {
    const rec = await apiUploadSample(kind, userStore.userInfo?.username ?? 'unknown')
    ElMessage.success(`示例病例 ${rec.id} 已创建，可点击「阅片分析」发起 AI 融合推理演示`)
    uploadVisible.value = false
    onSearch()
  } finally {
    sampleLoading.value = ''
  }
}

/** 影像文件类型判定（DICOM .dcm / NIfTI .nii、.nii.gz / 无扩展名二进制） */
function isDicomFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return (
    /\.dcm$/i.test(name) ||
    /\.nii$/i.test(name) ||
    /\.nii\.gz$/i.test(name) ||
    file.type === 'application/dicom' ||
    !file.name.includes('.')
  )
}

/** 校验并提示：auto-upload=false 下提交前手动调用（before-upload 不会触发） */
function validateDicomFile(file: File): boolean {
  const ok = isDicomFile(file)
  if (!ok) ElMessage.warning(`文件 ${file.name} 格式不支持（仅 .dcm / .nii / .nii.gz）`)
  return ok
}

// ---------- 批量导入弹窗 ----------
const batchVisible = ref(false)
const batchFile = ref<File | null>(null)
const batchUploadFiles = ref<UploadUserFile[]>([])
const batchLoading = ref(false)

/** 选择导入文件（el-upload change 回调） */
function onBatchFileChange(f: UploadUserFile): void {
  batchFile.value = (f.raw as File) ?? null
}

/** 移除已选文件（点 el-upload 标签 X）：同步清空 batchFile，避免提交残留旧 File */
function onBatchFileRemove(): void {
  batchFile.value = null
}

/** CSV 导入清单大小上限：10MB */
const BATCH_CSV_MAX_SIZE = 10 * 1024 * 1024

/** 校验并提示：扩展名必须 .csv 且不超过 10MB（auto-upload=false，提交前手动校验） */
function validateCsvFile(file: File): boolean {
  if (!/\.csv$/i.test(file.name)) {
    ElMessage.warning(`文件 ${file.name} 格式不支持（仅 .csv）`)
    return false
  }
  if (file.size > BATCH_CSV_MAX_SIZE) {
    ElMessage.warning(`文件 ${file.name} 超过 10MB 上限，请拆分后导入`)
    return false
  }
  return true
}

/** 关闭批量导入弹窗后彻底清理已选文件，避免取消后残留旧清单 */
function resetBatchForm(): void {
  batchFile.value = null
  batchUploadFiles.value = []
}

/** 提交 CSV 批量导入（列约定：姓名,性别(M/F),年龄,模态,科室） */
async function submitBatch(): Promise<void> {
  if (!batchFile.value) {
    ElMessage.warning('请选择 CSV 导入清单')
    return
  }
  if (!validateCsvFile(batchFile.value)) return
  batchLoading.value = true
  try {
    const res = await apiBatchImport(batchFile.value, userStore.userInfo?.username ?? 'unknown')
    ElMessage.success(`批量导入完成，成功导入 ${res.imported} 条病例`)
    batchVisible.value = false
    resetBatchForm()
    onSearch()
  } finally {
    batchLoading.value = false
  }
}

/** 下载导入模板 */
function downloadTemplate(): void {
  const csv = '姓名,性别(M/F),年龄,模态(MRI/PET/MRI+PET),科室\n张三,M,68,MRI+PET,神经内科\n李四,F,72,MRI,记忆门诊'
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '病例批量导入模板.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}

// 监听弹窗打开（从工作台跳入时）—— 通过路由 query
watch(
  () => route.query,
  (q) => {
    if (q.upload === '1') uploadVisible.value = true
    if (q.batch === '1') batchVisible.value = true
    // 收藏过滤：从工作台跳入时打开，离开时关闭
    const wantFav = q.favorites === '1'
    if (wantFav !== !!query.favorites) {
      query.favorites = wantFav || null
      query.page = 1
      fetchList()
    }
  }
)
</script>

<template>
  <div class="page-wrap">
    <!-- 免责提示：列表页展示 AI 风险等级，需要合规声明 -->
    <DisclaimerBar />

    <!-- ==================== 检索区 ==================== -->
    <div class="card-ad p-4">
      <div class="flex items-center gap-3 flex-wrap">
        <el-input
          v-model="query.keyword"
          placeholder="搜索病例编号 / 患者姓名 / 患者编号"
          clearable
          class="!w-64"
          :prefix-icon="'Search'"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-button type="primary" :icon="'Search'" @click="onSearch">查询</el-button>
        <el-button :icon="'Refresh'" @click="onReset">重置</el-button>
        <el-button text type="primary" @click="showFilter = !showFilter">
          高级筛选
          <el-icon class="ml-1 transition-transform" :class="showFilter ? 'rotate-180' : ''"><ArrowDown /></el-icon>
        </el-button>

        <div class="flex-1" />
        <!-- 批量分析 / 影像上传 / 批量导入 -->
        <el-button
          type="success"
          :icon="'Cpu'"
          :loading="batchRunning"
          :disabled="selectedCases.length === 0"
          @click="runBatchAnalysis"
        >
          批量分析{{ selectedCases.length > 0 ? `(${selectedCases.length})` : '' }}
        </el-button>
        <el-button
          v-if="canDeleteCase"
          type="danger"
          plain
          :icon="'Delete'"
          :disabled="selectedCases.length === 0"
          @click="onBatchDelete"
        >
          批量删除{{ selectedCases.length > 0 ? `(${selectedCases.length})` : '' }}
        </el-button>
        <el-button
          type="warning"
          plain
          :icon="'CopyDocument'"
          :disabled="selectedCases.length !== 2"
          @click="openCompare"
        >
          对比查看{{ selectedCases.length === 2 ? '(2)' : '' }}
        </el-button>
        <el-button type="primary" :icon="'UploadFilled'" @click="uploadVisible = true">影像上传</el-button>
        <el-button :icon="'DocumentAdd'" @click="batchVisible = true">批量导入</el-button>
        <el-button
          v-if="['researcher', 'admin'].includes(userStore.role)"
          type="info"
          plain
          :icon="'Download'"
          :disabled="selectedCases.length === 0"
          @click="openDatasetExport"
        >
          导出数据集{{ selectedCases.length > 0 ? `(${selectedCases.length})` : '' }}
        </el-button>
        <el-button :icon="'Download'" :loading="exporting" @click="onExportCsv">导出明细</el-button>
        <el-button
          type="primary"
          plain
          :icon="'Document'"
          :disabled="selectedCases.length === 0"
          :loading="reportExporting"
          @click="onBatchExportReport"
        >
          批量导出报告{{ selectedCases.length > 0 ? `(${selectedCases.length})` : '' }}
        </el-button>
      </div>

      <!-- 高级筛选行 -->
      <div v-show="showFilter" class="mt-4 pt-4 border-t border-line flex items-center gap-3 flex-wrap">
        <el-select v-model="query.modality" placeholder="影像模态" clearable class="!w-36">
          <el-option label="MRI" value="MRI" />
          <el-option label="PET" value="PET" />
          <el-option label="MRI+PET" value="MRI+PET" />
        </el-select>
        <el-select v-model="query.status" placeholder="病例状态" clearable class="!w-36">
          <el-option label="待分析" value="pending" />
          <el-option label="分析中" value="analyzing" />
          <el-option label="已分析" value="completed" />
          <el-option label="已出报告" value="reported" />
        </el-select>
        <el-select v-model="query.riskLevel" placeholder="AI 风险等级" clearable class="!w-40">
          <el-option label="低风险" value="low" />
          <el-option label="轻度认知障碍" value="mci" />
          <el-option label="AD 早期" value="ad-early" />
          <el-option label="AD 中晚期" value="ad-late" />
        </el-select>
        <el-date-picker
          v-model="query.dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="检查开始日期"
          end-placeholder="检查结束日期"
          value-format="YYYY-MM-DD"
          class="!w-72"
        />
        <!-- New filters: department, gender, age range, diagStatus, hasFollowup -->
        <el-select v-model="query.department" placeholder="科室" clearable filterable class="!w-36">
          <el-option label="神经内科" value="神经内科" />
          <el-option label="老年科" value="老年科" />
          <el-option label="放射科" value="放射科" />
          <el-option label="记忆门诊" value="记忆门诊" />
        </el-select>
        <el-select v-model="query.gender" placeholder="性别" clearable class="!w-24">
          <el-option label="男" value="M" />
          <el-option label="女" value="F" />
        </el-select>
        <el-input-number v-model="query.ageMin" :min="0" :max="120" placeholder="最小年龄" controls-position="right" class="!w-32" />
        <span class="text-hint text-xs">~</span>
        <el-input-number v-model="query.ageMax" :min="0" :max="120" placeholder="最大年龄" controls-position="right" class="!w-32" />
        <el-select v-model="query.diagStatus" placeholder="诊断状态" clearable class="!w-36">
          <el-option label="待AI分析" value="待AI分析" />
          <el-option label="AI分析中" value="AI分析中" />
          <el-option label="AI已分析" value="AI已分析" />
          <el-option label="医生已审核" value="医生已审核" />
          <el-option label="已出报告" value="已出报告" />
        </el-select>
        <el-select v-model="query.hasFollowup" placeholder="随访状态" clearable class="!w-32">
          <el-option label="有随访" :value="true" />
          <el-option label="无随访" :value="false" />
        </el-select>

        <!-- Filter presets -->
        <div class="w-full flex items-center gap-2 pt-2 border-t border-line mt-2">
          <el-input v-model="presetName" placeholder="预设名称" class="!w-40" size="small" />
          <el-button size="small" type="primary" plain @click="savePreset">保存预设</el-button>
          <el-dropdown v-if="savedPresets.length > 0" @command="applyPreset" trigger="click">
            <el-button size="small" text type="primary">
              加载预设（{{ savedPresets.length }}）
              <el-icon class="ml-1"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="p in savedPresets" :key="p.name" :command="p.name">
                  <div class="flex items-center justify-between gap-4">
                    <span>{{ p.name }}</span>
                    <el-button text size="small" type="danger" @click.stop="deletePreset(p.name)">删除</el-button>
                  </div>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <div class="flex-1" />
          <el-button size="small" text @click="onReset">重置全部</el-button>
          <el-button size="small" type="primary" @click="onSearch">应用筛选</el-button>
        </div>
      </div>
    </div>

    <!-- ==================== 病例表格 ==================== -->
    <div class="card-ad">
      <el-table
        ref="caseTableRef"
        v-loading="loading"
        :data="list"
        row-key="id"
        class="w-full"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="48" fixed="left" reserve-selection />
        <el-table-column label="收藏" width="56" align="center" fixed="left">
          <template #default="{ row }">
            <el-tooltip :content="favoritesMap[row.id] ? '取消收藏' : '收藏病例'" placement="top">
              <el-icon
                class="cursor-pointer transition-colors"
                :style="{ color: favoritesMap[row.id] ? '#E6A23C' : '#C8D1DC', fontSize: '18px' }"
                @click.stop="toggleFavorite(row)"
              >
                <component :is="favoritesMap[row.id] ? 'StarFilled' : 'Star'" />
              </el-icon>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="病例编号" width="110" fixed="left">
          <template #default="{ row }">
            <el-link type="primary" :underline="'never'" class="font-num" @click="goViewer(row)">{{ row.id }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="患者信息" min-width="180">
          <template #default="{ row }">
            <div class="flex items-center gap-2.5">
              <el-avatar :size="32" class="bg-primary-light text-primary text-[13px] shrink-0">
                {{ row.patient.name.slice(0, 1) }}
              </el-avatar>
              <div class="leading-tight">
                <div class="text-[13px] text-ink font-medium">
                  <el-link
                    type="primary"
                    underline="never"
                    class="!text-[13px] !font-medium"
                    @click.stop="router.push(`/patient/${encodeURIComponent(row.patient.patientNo)}`)"
                  >{{ row.patient.name }}</el-link>
                  <span class="ml-1.5 text-hint font-normal">{{ row.patient.gender === 'M' ? '男' : '女' }} / {{ row.patient.age }} 岁</span>
                </div>
                <div class="text-[12px] text-hint font-num">P号：{{ row.patient.patientNo }} · {{ row.department }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="影像模态" width="150">
          <template #default="{ row }">
            <div class="flex gap-1.5">
              <el-tag
                v-for="m in modalities(row.modality)"
                :key="m"
                size="small"
                effect="plain"
                round
                class="font-num"
                type="info"
              >
                {{ m }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="examDate" label="检查时间" width="160" sortable>
          <template #default="{ row }">
            <span class="font-num text-[12px] text-sub">{{ row.examDate }}</span>
          </template>
        </el-table-column>
        <el-table-column label="AI 风险等级" width="160">
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <RiskLevelTag :risk-level="row.riskLevel as RiskLevel | null" />
              <span v-if="row.riskScore !== null" class="font-num text-[13px] text-sub">{{ row.riskScore }}分</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="诊断状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status as CaseStatus)" size="small" effect="light" round>
              {{ DIAG_TEXT[row.status as CaseStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="360" fixed="right">
          <template #default="{ row }">
            <div class="flex items-center gap-1">
              <el-button link type="primary" :icon="'View'" @click="goViewer(row)">查看影像</el-button>
              <el-button
                v-if="row.riskScore === null"
                link
                type="warning"
                :icon="'Cpu'"
                :loading="analyzingIds.has(row.id)"
                @click="quickAnalyze(row)"
              >
                启动AI分析
              </el-button>
              <el-button v-else link type="primary" :icon="'DataAnalysis'" @click="goAnalysis(row)">AI详情</el-button>
              <el-button
                v-if="row.riskScore !== null"
                link
                type="primary"
                :icon="'Document'"
                @click="goReport(row)"
              >
                生成报告
              </el-button>
              <el-tooltip content="操作历程时间线" placement="top">
                <el-button link type="info" :icon="'Timer'" @click="openTimeline(row)">历程</el-button>
              </el-tooltip>
              <el-button v-if="canDeleteCase" link type="danger" :icon="'Delete'" @click.stop="onDelete(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="flex justify-end px-4 py-3 border-t border-line">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="fetchList"
          @size-change="onSearch"
        />
      </div>
    </div>

    <!-- ==================== 影像上传弹窗 ==================== -->
    <el-dialog v-model="uploadVisible" title="影像上传 · 新建病例（DICOM / NIfTI）" width="560px" @closed="resetUploadForm">
      <el-form :model="uploadForm" label-width="90px">
        <div class="grid grid-cols-2 gap-x-5">
          <el-form-item label="患者编号" required>
            <el-input v-model="uploadForm.patientNo" placeholder="如 P20260901001" />
          </el-form-item>
          <el-form-item label="患者姓名" required>
            <el-input v-model="uploadForm.patientName" placeholder="请输入真实姓名" />
          </el-form-item>
          <el-form-item label="性别">
            <el-radio-group v-model="uploadForm.gender">
              <el-radio value="M">男</el-radio>
              <el-radio value="F">女</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="年龄">
            <el-input-number v-model="uploadForm.age" :min="1" :max="120" class="!w-full" />
          </el-form-item>
          <el-form-item label="影像模态">
            <el-select v-model="uploadForm.modality" class="!w-full">
              <el-option label="MRI" value="MRI" />
              <el-option label="PET" value="PET" />
              <el-option label="MRI+PET（多模态融合）" value="MRI+PET" />
            </el-select>
          </el-form-item>
          <el-form-item label="申请科室">
            <el-select v-model="uploadForm.department" class="!w-full">
              <el-option v-for="d in ['神经内科', '放射科', '记忆门诊', '老年医学科']" :key="d" :label="d" :value="d" />
            </el-select>
          </el-form-item>
        </div>
        <!-- 2026 版 PET/MRI 指南：检查适应证 + PET 显像剂类型 -->
        <div class="grid grid-cols-2 gap-x-5">
          <el-form-item label="检查适应证">
            <el-select v-model="uploadForm.examIndication" class="!w-full">
              <el-option label="诊断及鉴别诊断" value="diagnosis" />
              <el-option label="分期与预后评估" value="staging" />
              <el-option label="疾病修饰治疗前评估" value="pre_dmt" />
              <el-option label="疾病修饰治疗疗效监测" value="dmt_monitoring" />
            </el-select>
          </el-form-item>
          <el-form-item label="PET 显像剂">
            <el-select v-model="uploadForm.petTracer" class="!w-full">
              <el-option label="18F-FDG（葡萄糖代谢）" value="fdg" />
              <el-option label="Aβ PET（淀粉样蛋白）" value="amyloid" />
              <el-option label="Tau PET（tau 蛋白）" value="tau" />
            </el-select>
          </el-form-item>
          <!-- 2026 版指南 表2：所选显像剂推荐参数联动展示 -->
          <div class="px-3 py-2 mb-2 rounded-card border border-[#D0E0EF] bg-[#F0F5FB] text-[12px]">
            <div class="flex items-center gap-2 mb-1.5 flex-wrap">
              <span class="font-medium text-ink">{{ currentTracerInfo.name }}</span>
              <span class="text-hint">{{ currentTracerInfo.cnName }}</span>
              <el-tag v-if="currentTracerInfo.fasting" size="small" type="warning" effect="dark">需空腹</el-tag>
              <el-tag v-if="currentTracerInfo.glucose" size="small" type="warning" effect="dark">血糖控制</el-tag>
            </div>
            <div class="grid grid-cols-2 gap-x-4 gap-y-1">
              <div class="text-hint">推荐剂量：<span class="text-ink font-num font-medium">{{ currentTracerInfo.dose }}</span></div>
              <div class="text-hint">注射后等待：<span class="text-ink font-medium">{{ currentTracerInfo.uptake }}</span></div>
              <div class="text-hint">采集时长：<span class="text-ink font-medium">{{ currentTracerInfo.acq }}</span></div>
              <div class="text-hint">半衰期：<span class="text-ink font-num">{{ currentTracerInfo.half }}</span></div>
            </div>
            <div class="text-sub leading-5 mt-1.5 pt-1.5 border-t border-[#D0E0EF]">
              <span class="text-hint">适应证：</span>{{ currentTracerInfo.indication }}
            </div>
          </div>
        </div>
        <!-- 2026 版 PET/MRI 指南：检查前评估（禁忌证 + 特殊人群） -->
        <el-collapse class="!mt-2 !border-line">
          <el-collapse-item name="preExam">
            <template #title>
              <span class="text-[13px] font-medium text-ink">检查前评估（指南）</span>
              <el-tag
                v-if="absoluteContraindications.length > 0 || relativeContraindications.length > 0 || specialPopulation !== 'normal'"
                size="small" type="warning" effect="light" round class="!ml-2"
              >已配置</el-tag>
            </template>
            <div class="space-y-3 pt-1">
              <!-- 绝对禁忌 -->
              <div>
                <div class="text-[12px] text-hint mb-1">绝对禁忌（MRI 不兼容有源植入物）</div>
                <el-checkbox-group v-model="absoluteContraindications">
                  <el-checkbox
                    v-for="opt in ABSOLUTE_CONTRA_OPTIONS"
                    :key="opt.value"
                    :value="opt.value"
                    class="!mr-4 !text-[12.5px]"
                  >{{ opt.label }}</el-checkbox>
                </el-checkbox-group>
              </div>
              <!-- 相对禁忌 -->
              <div>
                <div class="text-[12px] text-hint mb-1">相对禁忌（金属植入物需确认 MRI 安全等级）</div>
                <el-checkbox-group v-model="relativeContraindications">
                  <el-checkbox
                    v-for="opt in RELATIVE_CONTRA_OPTIONS"
                    :key="opt.value"
                    :value="opt.value"
                    class="!mr-4 !text-[12.5px]"
                  >{{ opt.label }}</el-checkbox>
                </el-checkbox-group>
              </div>
              <!-- 特殊人群 -->
              <div>
                <div class="text-[12px] text-hint mb-1">特殊人群</div>
                <el-radio-group v-model="specialPopulation">
                  <el-radio
                    v-for="opt in SPECIAL_POP_OPTIONS"
                    :key="opt.value"
                    :value="opt.value"
                    class="!mr-4 !text-[12.5px]"
                  >{{ opt.label }}</el-radio>
                </el-radio-group>
              </div>
              <!-- 绝对禁忌 + MRI 警告 -->
              <el-alert
                v-if="showMriAbsoluteWarn"
                type="error"
                :closable="false"
                show-icon
                title="指南明确不可进行 MRI 检查，后端会拒绝建档，请评估替代方案（如 PET-only 或 CT）"
              />
            </div>
          </el-collapse-item>
        </el-collapse>
        <el-form-item label="影像文件">
          <el-upload
            v-model:file-list="uploadFiles"
            drag
            multiple
            accept=".dcm,.nii,.gz,application/dicom"
            :auto-upload="false"
            class="!w-full"
          >
            <el-icon :size="36" class="text-hint"><UploadFilled /></el-icon>
            <div class="text-[13px] text-ink mt-2">拖拽影像文件到此处，或<em class="text-primary not-italic"> 点击选择</em></div>
            <template #tip>
              <div class="text-[11px] text-hint mt-1">
                支持 DICOM（.dcm）与 NIfTI（.nii / .nii.gz），可多选；
                文件名含 MRI/PET（如 sub-xxx_MRI.nii、xxx_PET.nii.gz）将自动识别模态，
                MRI 与 PET 同时上传可走真实 TransMF 模型融合推理
              </div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="示例数据">
          <div class="w-full">
            <div class="text-[11px] text-hint mb-2">
              演示场景：一键载入项目内置的 ADNI 真实影像（同受试者 MRI+PET 配对），无需选择文件，建档后即可发起 AI 分析
            </div>
            <div class="flex gap-2">
              <el-button
                size="small" type="warning" plain
                :loading="sampleLoading === 'ad'"
                :disabled="sampleLoading !== ''"
                @click="loadSample('ad')"
              >AD 阳性示例</el-button>
              <el-button
                size="small" type="primary" plain
                :loading="sampleLoading === 'smci'"
                :disabled="sampleLoading !== ''"
                @click="loadSample('smci')"
              >sMCI 示例</el-button>
              <el-button
                size="small" type="success" plain
                :loading="sampleLoading === 'cn'"
                :disabled="sampleLoading !== ''"
                @click="loadSample('cn')"
              >CN 正常示例</el-button>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">确认上传并建档</el-button>
      </template>
    </el-dialog>

    <!-- ==================== 批量导入弹窗 ==================== -->
    <el-dialog v-model="batchVisible" title="病例批量导入" width="480px" @closed="resetBatchForm">
      <el-alert type="info" :closable="false" class="!mb-4">
        <template #title>
          CSV 列约定：姓名, 性别(M/F), 年龄, 模态(MRI/PET/MRI+PET), 科室
        </template>
      </el-alert>
      <el-upload
        v-model:file-list="batchUploadFiles"
        drag
        accept=".csv"
        :auto-upload="false"
        :limit="1"
        :on-change="onBatchFileChange"
        :on-remove="onBatchFileRemove"
        class="!w-full"
      >
        <el-icon :size="36" class="text-hint"><DocumentAdd /></el-icon>
        <div class="text-[13px] text-ink mt-2">选择 CSV 清单文件</div>
      </el-upload>
      <div class="mt-3">
        <el-link type="primary" :icon="'Download'" :underline="'never'" @click="downloadTemplate">下载导入模板</el-link>
      </div>
      <template #footer>
        <el-button @click="batchVisible = false">取消</el-button>
        <el-button type="primary" :loading="batchLoading" @click="submitBatch">开始导入</el-button>
      </template>
    </el-dialog>

    <!-- 病例操作历程抽屉 -->
    <el-drawer
      v-model="timelineVisible"
      :title="`操作历程 · ${timelineCaseId}（${timelinePatientName}）`"
      size="460px"
      destroy-on-close
    >
      <div v-loading="timelineLoading">
        <el-timeline v-if="timelineItems.length > 0">
          <el-timeline-item
            v-for="(item, idx) in timelineItems"
            :key="item.id"
            :timestamp="item.time"
            placement="top"
            :color="item.actionColor"
            :hollow="idx !== timelineItems.length - 1"
          >
            <el-tag
              :color="item.actionBg"
              :style="{ color: item.actionColor, border: 'none', fontSize: '12px' }"
              effect="plain"
            >{{ item.actionLabel }}</el-tag>
            <div class="mt-1.5 text-[13px] text-sub leading-relaxed">{{ item.detail || '—' }}</div>
            <div class="mt-1 text-[12px] text-hint">操作人：{{ item.operator }}</div>
          </el-timeline-item>
        </el-timeline>
        <div v-else-if="!timelineLoading" class="py-16 flex flex-col items-center gap-2">
          <el-icon :size="36" class="text-hint/40"><Timer /></el-icon>
          <div class="text-sm text-hint">该病例暂无操作记录</div>
        </div>
      </div>
    </el-drawer>

    <!-- 病例对比查看弹窗 -->
    <el-dialog v-model="compareVisible" title="病例对比查看" width="820px" destroy-on-close>
      <el-table :data="compareRows" border>
        <el-table-column label="对比项" width="140" align="center" header-align="center">
          <template #default="{ row }">
            <span class="text-[13px] font-medium text-sub">{{ row.label }}</span>
          </template>
        </el-table-column>
        <el-table-column
          v-for="(c, idx) in compareCases"
          :key="c.id"
          :label="`病例 ${idx + 1}`"
          align="center"
          header-align="center"
        >
          <template #header>
            <div class="flex flex-col items-center">
              <span class="font-num text-[13px] text-primary">{{ c.id }}</span>
              <span class="text-[11px] text-hint">{{ c.patient.name }}</span>
            </div>
          </template>
          <template #default="{ row }">
            <span class="text-[13px]" :class="row.highlight ? 'text-ink font-medium' : 'text-sub'">
              {{ row.values[idx] }}
            </span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="compareVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 批量 AI 分析进度对话框 -->
    <BatchProgressDialog
      v-model="batchProgressVisible"
      :task-id="batchTaskId"
      @completed="onBatchCompleted"
    />

    <!-- 科研数据集导出对话框 -->
    <DatasetExportDialog
      v-model="datasetExportVisible"
      :selected-case-ids="selectedCases.map((c) => c.id)"
    />
  </div>
</template>
