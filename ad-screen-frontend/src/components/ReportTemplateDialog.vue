<script setup lang="ts">
/**
 * 报告模板管理弹窗
 * ------------------------------------------------------------------
 * 左侧：模板列表（点选切换当前编辑目标）
 * 右侧：模板编辑表单（名称 / 类型 / 字段勾选 / 抬头 / 默认开关）
 * 底部：保存 / 新建 / 删除 / 关闭
 * 事件：template-changed（模板新增/编辑/删除/设为默认后通知父组件刷新）
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiListReportTemplates,
  apiGetReportTemplate,
  apiCreateReportTemplate,
  apiUpdateReportTemplate,
  apiDeleteReportTemplate,
  apiSetDefaultReportTemplate
} from '@/api/reportTemplate'
import type {
  ReportTemplate,
  ReportTemplateType,
  ReportHeaderConfig
} from '@/types/reportTemplate.d'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'template-changed', t: ReportTemplate | null): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

// ---------- 模板列表 ----------
const templates = ref<ReportTemplate[]>([])
const selectedId = ref<number | null>(null)
const listLoading = ref(false)

async function loadList(): Promise<void> {
  listLoading.value = true
  try {
    const res = await apiListReportTemplates()
    templates.value = res.list
    // 默认选中第一个（或保留已选中）
    if (templates.value.length > 0) {
      const exists = templates.value.some((t) => t.id === selectedId.value)
      if (!exists) selectedId.value = templates.value[0].id
      void loadDetail(selectedId.value!)
    } else {
      // 无模板时进入"新建"状态
      selectedId.value = null
      resetForm()
    }
  } finally {
    listLoading.value = false
  }
}

async function loadDetail(id: number): Promise<void> {
  try {
    const t = await apiGetReportTemplate(id)
    selectedId.value = t.id
    Object.assign(form, {
      name: t.name,
      templateType: t.templateType,
      contentStructure: { ...DEFAULT_FIELDS, ...t.contentStructure },
      headerConfig: { ...DEFAULT_HEADER, ...t.headerConfig },
      isDefault: t.isDefault
    })
  } catch {
    /* 错误已由 request.ts 弹窗提示 */
  }
}

// ---------- 字段定义 ----------
// 13 个可勾选字段及其中文标签
// 10 个基础字段 + 3 个新增字段（NIA-AA 框架对齐 / 矛盾证据 / 患者通俗版）
const FIELD_OPTIONS: { key: string; label: string; desc: string }[] = [
  { key: 'patientInfo', label: '患者基本信息', desc: '姓名/性别/年龄/病例编号等基础信息' },
  { key: 'examInfo', label: '检查信息', desc: '影像模态/检查时间/科室/项目' },
  { key: 'aiResult', label: 'AI 分析结果', desc: '风险评分/风险等级/病程分期/AI 摘要' },
  { key: 'imagingDesc', label: '影像描述', desc: '多模态影像关键层面截图' },
  { key: 'riskStratification', label: '风险分层', desc: '低风险/MCI/AD 早期/AD 中晚期' },
  { key: 'interventionAdvice', label: '干预建议', desc: '认知/生活方式/临床用药方案' },
  { key: 'followupPlan', label: '随访计划', desc: '随访周期/下次随访日期/提醒' },
  { key: 'gradcamImage', label: 'GradCAM 热图', desc: 'AI 注意力热图（详版/科研版显示）' },
  { key: 'brainMetrics', label: '脑区结构指标', desc: '量化分析数据（科研版显示）' },
  { key: 'disclaimer', label: '免责声明', desc: 'AI 辅助筛查，不可替代临床诊断' },
  { key: 'niaaAlignment', label: 'NIA-AA 框架对齐', desc: 'A/T/N 生物标志物标签与依据说明' },
  { key: 'conflictEvidence', label: '矛盾证据', desc: '影像-认知/模态间不一致提示' },
  { key: 'patientFriendly', label: '患者通俗版', desc: '通俗语言版本，便于患者沟通' }
]

const TYPE_OPTIONS: { value: ReportTemplateType; label: string }[] = [
  { value: 'brief', label: '简版' },
  { value: 'detailed', label: '详版' },
  { value: 'research', label: '科研版' },
  { value: 'patient', label: '患者科普版' }
]

// 默认字段配置（与后端 DEFAULT_FIELDS 保持一致：13 字段）
const DEFAULT_FIELDS: Record<string, boolean> = {
  patientInfo: true,
  examInfo: true,
  aiResult: true,
  imagingDesc: true,
  riskStratification: true,
  interventionAdvice: true,
  followupPlan: true,
  gradcamImage: false,
  brainMetrics: false,
  disclaimer: true,
  niaaAlignment: false,
  conflictEvidence: false,
  patientFriendly: false
}

const DEFAULT_HEADER: ReportHeaderConfig = {
  hospitalName: '神经影像智能筛查中心',
  department: '放射科 · 核医学科',
  logoUrl: '',
  title: '脑影·明衰  阿尔茨海默病多模态影像筛查报告',
  subtitle: 'BrainImaging · Dementia Insight — MRI/PET 融合智能诊断',
  systemName: '脑影·明衰',
  systemVersion: 'v1.0.0',
  modelVersion: 'TransMF-15ens-v4',
  guideline: '2026 版 PET/MRI 脑成像临床应用指南'
}

// ---------- 编辑表单 ----------
const form = reactive({
  name: '',
  templateType: 'detailed' as ReportTemplateType,
  contentStructure: { ...DEFAULT_FIELDS } as Record<string, boolean>,
  headerConfig: { ...DEFAULT_HEADER } as ReportHeaderConfig,
  isDefault: false
})

const saving = ref(false)

function resetForm(): void {
  form.name = ''
  form.templateType = 'detailed'
  form.contentStructure = { ...DEFAULT_FIELDS }
  form.headerConfig = { ...DEFAULT_HEADER }
  form.isDefault = false
}

/** 按模板类型预设字段（用户点击类型 radio 时联动） */
function applyTypePreset(t: ReportTemplateType): void {
  if (t === 'brief') {
    // 简版：仅核心字段，3 个新字段均关闭
    form.contentStructure = {
      patientInfo: true,
      examInfo: true,
      aiResult: true,
      imagingDesc: false,
      riskStratification: true,
      interventionAdvice: false,
      followupPlan: false,
      gradcamImage: false,
      brainMetrics: false,
      disclaimer: true,
      niaaAlignment: false,
      conflictEvidence: false,
      patientFriendly: false
    }
  } else if (t === 'detailed') {
    // 详版：基本字段 + gradcamImage + NIA-AA + 矛盾证据
    form.contentStructure = {
      ...DEFAULT_FIELDS,
      gradcamImage: true,
      niaaAlignment: true,
      conflictEvidence: true
    }
  } else if (t === 'research') {
    // 科研版：全部字段 + 3 个新字段全部启用
    form.contentStructure = {
      ...DEFAULT_FIELDS,
      gradcamImage: true,
      brainMetrics: true,
      niaaAlignment: true,
      conflictEvidence: true,
      patientFriendly: true
    }
  } else {
    // 患者科普版：仅启用患者通俗版字段，其他字段全部关闭
    form.contentStructure = {
      patientInfo: false,
      examInfo: false,
      aiResult: false,
      imagingDesc: false,
      riskStratification: false,
      interventionAdvice: false,
      followupPlan: false,
      gradcamImage: false,
      brainMetrics: false,
      disclaimer: false,
      niaaAlignment: false,
      conflictEvidence: false,
      patientFriendly: true
    }
  }
}

function onTypeChange(t: ReportTemplateType): void {
  applyTypePreset(t)
}

function buildBody() {
  return {
    name: form.name.trim(),
    templateType: form.templateType,
    contentStructure: { ...form.contentStructure },
    headerConfig: { ...form.headerConfig },
    isDefault: form.isDefault
  }
}

// ---------- 保存 / 新建 / 删除 / 设默认 ----------
async function onSave(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请输入模板名称')
    return
  }
  saving.value = true
  try {
    if (selectedId.value === null) {
      const { id } = await apiCreateReportTemplate(buildBody())
      selectedId.value = id
      ElMessage.success('模板已创建')
    } else {
      await apiUpdateReportTemplate(selectedId.value, buildBody())
      ElMessage.success('模板已更新')
    }
    await loadList()
    emit('template-changed', templates.value.find((t) => t.id === selectedId.value) ?? null)
  } finally {
    saving.value = false
  }
}

function onNew(): void {
  selectedId.value = null
  resetForm()
  ElMessage.info('已进入新建模式，请填写模板信息后保存')
}

async function onDelete(): Promise<void> {
  if (selectedId.value === null) {
    ElMessage.warning('当前为新建模式，无可删除模板')
    return
  }
  const cur = templates.value.find((t) => t.id === selectedId.value)
  if (!cur) return
  if (cur.isDefault) {
    ElMessage.warning('默认模板不允许删除，请先切换其他模板为默认')
    return
  }
  try {
    await ElMessageBox.confirm(`确认删除模板「${cur.name}」？`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch {
    return
  }
  try {
    await apiDeleteReportTemplate(selectedId.value)
    ElMessage.success('模板已删除')
    selectedId.value = null
    await loadList()
    emit('template-changed', null)
  } catch {
    /* 错误已弹窗 */
  }
}

async function onSetDefault(val: boolean): Promise<void> {
  // 开启默认：调用 set-default 接口（后端会先取消其他模板默认）
  // 关闭默认：仅在保存时通过 update 生效；此处若关闭当前已默认模板，提示用户改选其他模板
  if (selectedId.value === null) {
    // 新建模式下：仅作为本次提交时的标志，无需调用接口
    return
  }
  const cur = templates.value.find((t) => t.id === selectedId.value)
  if (val) {
    if (cur?.isDefault) return
    try {
      await apiSetDefaultReportTemplate(selectedId.value)
      ElMessage.success('已设为默认模板')
      await loadList()
      emit('template-changed', templates.value.find((t) => t.id === selectedId.value) ?? null)
    } catch {
      form.isDefault = false
    }
  } else {
    if (cur?.isDefault) {
      ElMessage.warning('请改为选中其他模板并开启"设为默认"以切换默认')
      form.isDefault = true
    }
  }
}

// ---------- 弹窗打开时加载 ----------
watch(visible, (v) => {
  if (v) void loadList()
})
</script>

<template>
  <el-dialog
    v-model="visible"
    title="报告模板管理"
    width="960px"
    top="6vh"
    :close-on-click-modal="false"
  >
    <div class="flex gap-4 h-[560px]">
      <!-- 左侧：模板列表 -->
      <aside class="w-[260px] shrink-0 flex flex-col border border-line rounded-card overflow-hidden">
        <div class="px-3 h-10 flex items-center justify-between border-b border-line bg-page">
          <span class="text-[13px] font-semibold text-ink">模板列表</span>
          <el-button size="small" type="primary" plain :icon="'Plus'" @click="onNew">新建</el-button>
        </div>
        <div v-loading="listLoading" class="flex-1 overflow-y-auto p-2 space-y-1.5">
          <div
            v-for="t in templates"
            :key="t.id"
            class="px-3 py-2.5 rounded-card border cursor-pointer transition-colors"
            :class="selectedId === t.id ? 'border-primary bg-primary-light' : 'border-line hover:border-primary/50'"
            @click="loadDetail(t.id)"
          >
            <div class="flex items-center gap-2">
              <el-icon v-if="selectedId === t.id" class="text-primary"><CircleCheckFilled /></el-icon>
              <span class="text-[13px] font-medium text-ink flex-1 truncate">{{ t.name }}</span>
              <el-tag v-if="t.isDefault" size="small" type="success" effect="light" round>默认</el-tag>
            </div>
            <div class="mt-1 text-[11px] text-hint">
              {{ TYPE_OPTIONS.find((o) => o.value === t.templateType)?.label ?? t.templateType }}
              <span class="ml-2 font-num">{{ t.createdAt.slice(0, 10) }}</span>
            </div>
          </div>
          <div v-if="templates.length === 0 && !listLoading" class="py-10 text-center text-[12px] text-hint">
            暂无模板，点击右上"新建"创建
          </div>
        </div>
      </aside>

      <!-- 右侧：编辑表单 -->
      <section class="flex-1 min-w-0 flex flex-col gap-4 overflow-y-auto pr-1">
        <!-- 基础信息 -->
        <div class="card-ad p-4">
          <div class="text-[13px] font-semibold text-ink mb-3 flex items-center gap-2">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            基础信息
          </div>
          <div class="grid grid-cols-2 gap-x-5 gap-y-3">
            <div class="flex items-center gap-3">
              <label class="w-[78px] text-[12.5px] text-sub shrink-0">模板名称</label>
              <el-input v-model="form.name" placeholder="如：门诊简版报告" size="default" class="flex-1" />
            </div>
            <div class="flex items-center gap-3">
              <label class="w-[78px] text-[12.5px] text-sub shrink-0">模板类型</label>
              <el-radio-group v-model="form.templateType" @change="onTypeChange(form.templateType)">
                <el-radio v-for="o in TYPE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
              </el-radio-group>
            </div>
          </div>
        </div>

        <!-- 字段勾选 -->
        <div class="card-ad p-4">
          <div class="text-[13px] font-semibold text-ink mb-3 flex items-center gap-2">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            报告字段（勾选后显示）
          </div>
          <div class="grid grid-cols-2 gap-y-2 gap-x-6">
            <el-tooltip
              v-for="f in FIELD_OPTIONS"
              :key="f.key"
              :content="f.desc"
              placement="top"
              :show-after="300"
            >
              <el-checkbox v-model="form.contentStructure[f.key]">
                <span class="text-[12.5px]">{{ f.label }}</span>
              </el-checkbox>
            </el-tooltip>
          </div>
        </div>

        <!-- 抬头配置 -->
        <div class="card-ad p-4">
          <div class="text-[13px] font-semibold text-ink mb-3 flex items-center gap-2">
            <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
            医院抬头配置
          </div>
          <div class="grid grid-cols-2 gap-x-5 gap-y-3">
            <div class="flex items-center gap-3">
              <label class="w-[78px] text-[12.5px] text-sub shrink-0">医院名称</label>
              <el-input v-model="form.headerConfig.hospitalName" placeholder="如：神经影像智能筛查中心" class="flex-1" />
            </div>
            <div class="flex items-center gap-3">
              <label class="w-[78px] text-[12.5px] text-sub shrink-0">科室</label>
              <el-input v-model="form.headerConfig.department" placeholder="如：放射科" class="flex-1" />
            </div>
            <div class="flex items-center gap-3 col-span-2">
              <label class="w-[78px] text-[12.5px] text-sub shrink-0">报告标题</label>
              <el-input v-model="form.headerConfig.title" placeholder="如：脑影·明衰  阿尔茨海默病多模态影像筛查报告" class="flex-1" />
            </div>
            <div class="flex items-center gap-3 col-span-2">
              <label class="w-[78px] text-[12.5px] text-sub shrink-0">英文副标题</label>
              <el-input v-model="form.headerConfig.subtitle" placeholder="如：BrainImaging · Dementia Insight" class="flex-1" />
            </div>
            <div class="flex items-center gap-3 col-span-2">
              <label class="w-[78px] text-[12.5px] text-sub shrink-0">Logo URL</label>
              <el-input v-model="form.headerConfig.logoUrl" placeholder="https://...（仅支持 URL 引用，留空使用系统默认 Logo）" class="flex-1" />
              <span class="text-[11px] text-hint shrink-0">仅支持 URL</span>
            </div>
          </div>
        </div>

        <!-- 默认开关 -->
        <div class="card-ad p-4 flex items-center justify-between">
          <div>
            <div class="text-[13px] font-semibold text-ink flex items-center gap-2">
              <span class="w-1 h-3.5 rounded-full bg-primary inline-block" />
              设为默认模板
            </div>
            <p class="mt-1 text-[11.5px] text-hint leading-5">报告页首次进入时使用默认模板；同时仅一个模板可为默认。</p>
          </div>
          <el-switch v-model="form.isDefault" @change="onSetDefault" />
        </div>
      </section>
    </div>

    <template #footer>
      <div class="flex justify-between items-center">
        <el-button type="danger" plain :icon="'Delete'" :disabled="selectedId === null" @click="onDelete">
          删除模板
        </el-button>
        <div class="flex gap-3">
          <el-button @click="visible = false">关 闭</el-button>
          <el-button type="primary" :icon="'Check'" :loading="saving" @click="onSave">保 存</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>
