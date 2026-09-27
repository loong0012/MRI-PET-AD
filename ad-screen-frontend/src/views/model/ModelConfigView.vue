<script setup lang="ts">
/**
 * 模型参数科研配置页（科研管理员 / 超级管理员）
 * ------------------------------------------------------------------
 * 1. 左列：推理参数配置 —— 融合策略（特征级 / 像素级）、模态权重（MRI:PET）、
 *          AD 筛查风险阈值、ROI 脑区检测范围，保存前二次确认；
 * 2. 右列：模型性能可视化 —— 5 折交叉验证 AUC/ACC 柱线图、训练曲线（Loss + Val AUC 双轴）、
 *          平均性能指标卡（AUC / ACC / SEN / SPE / F1，源自 75 模型快照集成实测）；
 * 3. 底部：历史推理日志查询表格（关键词过滤 + CSV 导出）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiGetModelConfig, apiSaveModelConfig, apiGetFoldMetrics, apiGetTrainCurve, apiGetInferenceLogs, apiGetModelStatus, apiReloadModel } from '@/api/model'
import { useUserStore } from '@/stores/user'
import { downloadCsv } from '@/utils/export'
import { formatDate } from '@/utils/format'
import type { ModelConfig, FoldMetric, TrainCurvePoint, InferenceLog, ModelRuntimeStatus } from '@/types/model'
import ChartBase from '@/components/ChartBase.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import type { EChartsOption } from 'echarts'

const userStore = useUserStore()

// ---------- 数据 ----------
const config = ref<ModelConfig | null>(null)
const folds = ref<FoldMetric[]>([])
const curve = ref<TrainCurvePoint[]>([])
const logs = ref<InferenceLog[]>([])
const modelStatus = ref<ModelRuntimeStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const reloading = ref(false)

// ---------- 热重载真实模型 ----------
async function handleReload(): Promise<void> {
  reloading.value = true
  try {
    const status = await apiReloadModel()
    modelStatus.value = status
    if (status.ensembleLoaded) {
      ElMessage.success(`模型热重载成功，已加载 ${status.modelCount} 个权重快照`)
    } else {
      ElMessage.warning('模型未完成加载，推理将回退模拟模式')
    }
  } catch {
    ElMessage.error('模型热重载失败，请查看后端日志')
  } finally {
    reloading.value = false
  }
}

// ---------- 平均性能指标卡 ----------
const meanMetric = computed(() => {
  if (folds.value.length === 0) return { auc: 0, acc: 0, sen: 0, spe: 0, f1: 0 }
  const avg = (k: keyof FoldMetric): number =>
    Number((folds.value.reduce((s, f) => s + (f[k] as number), 0) / folds.value.length).toFixed(4))
  return { auc: avg('auc'), acc: avg('acc'), sen: avg('sen'), spe: avg('spe'), f1: avg('f1') }
})

// ---------- 5 折指标图（柱=ACC/SEN/SPE/F1，线=AUC） ----------
const foldOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  legend: { top: 0, left: 'center', itemGap: 18, textStyle: { color: '#5C6B7A', fontSize: 12 } },
  grid: { left: 46, right: 20, top: 38, bottom: 28 },
  xAxis: {
    type: 'category',
    data: folds.value.map((f) => `Fold ${f.fold}`),
    axisLine: { lineStyle: { color: '#E3E9EF' } },
    axisLabel: { color: '#93A1AF' }
  },
  yAxis: {
    type: 'value',
    min: 0.6,
    max: 1,
    splitLine: { lineStyle: { color: '#EFF3F7' } },
    axisLabel: { color: '#93A1AF', formatter: (v: number) => v.toFixed(1) }
  },
  series: [
    { name: 'ACC', type: 'bar', barWidth: 12, itemStyle: { color: '#7FA8CC', borderRadius: [3, 3, 0, 0] }, data: folds.value.map((f) => f.acc) },
    { name: 'SEN', type: 'bar', barWidth: 12, itemStyle: { color: '#A5C6E3', borderRadius: [3, 3, 0, 0] }, data: folds.value.map((f) => f.sen) },
    { name: 'SPE', type: 'bar', barWidth: 12, itemStyle: { color: '#C9DDF0', borderRadius: [3, 3, 0, 0] }, data: folds.value.map((f) => f.spe) },
    { name: 'F1', type: 'bar', barWidth: 12, itemStyle: { color: '#DDE9F5', borderRadius: [3, 3, 0, 0] }, data: folds.value.map((f) => f.f1) },
    {
      name: 'AUC',
      type: 'line',
      smooth: true,
      symbolSize: 7,
      lineStyle: { width: 3, color: '#2F6DA3' },
      itemStyle: { color: '#2F6DA3' },
      data: folds.value.map((f) => f.auc)
    }
  ]
}))

// ---------- 训练曲线（双 Y 轴：Loss / Val AUC） ----------
const curveOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  // 图例置顶居中 + grid top 40，避免与双 Y 轴标签/绘图区互相遮挡
  legend: { top: 0, left: 'center', itemGap: 24, textStyle: { color: '#5C6B7A', fontSize: 12 } },
  grid: { left: 52, right: 52, top: 40, bottom: 32 },
  xAxis: {
    type: 'category',
    data: curve.value.map((c) => c.epoch),
    axisLine: { lineStyle: { color: '#E3E9EF' } },
    axisLabel: { color: '#93A1AF' }
  },
  yAxis: [
    {
      type: 'value',
      name: 'Loss',
      nameTextStyle: { color: '#93A1AF' },
      splitLine: { lineStyle: { color: '#EFF3F7' } },
      axisLabel: { color: '#93A1AF' }
    },
    {
      type: 'value',
      name: 'Val AUC',
      nameTextStyle: { color: '#93A1AF' },
      min: 0.6,
      max: 1,
      splitLine: { show: false },
      axisLabel: { color: '#93A1AF', formatter: (v: number) => v.toFixed(1) }
    }
  ],
  series: [
    {
      name: 'Train Loss',
      type: 'line',
      showSymbol: false,
      lineStyle: { width: 2, color: '#C94F4F' },
      itemStyle: { color: '#C94F4F' },
      data: curve.value.map((c) => c.trainLoss)
    },
    {
      name: 'Val AUC',
      type: 'line',
      yAxisIndex: 1,
      showSymbol: false,
      lineStyle: { width: 2.5, color: '#2F6DA3' },
      itemStyle: { color: '#2F6DA3' },
      data: curve.value.map((c) => c.valAuc)
    }
  ]
}))

// ---------- 配置编辑 ----------
/** ROI 脑区候选（与影像后处理脑图谱一致） */
const ROI_OPTIONS = ['海马', '颞叶皮层', '内嗅皮层', '后扣带回', '顶叶皮层', '额叶皮层', '楔前叶']

const saveConfirmVisible = ref(false)
async function saveConfig(): Promise<void> {
  if (!config.value) return
  // ROI 至少保留一个脑区：空数组下发会导致推理引擎无 ROI 可检测
  if (config.value.roiRegions.length === 0) {
    ElMessage.warning('请至少选择一个 ROI 脑区')
    return
  }
  saving.value = true
  try {
    await apiSaveModelConfig(config.value, userStore.userInfo?.username ?? 'unknown')
    ElMessage.success('模型科研配置已保存并下发推理引擎')
    // 仅成功时关闭确认弹窗；失败时保留弹窗便于修改后重试
    saveConfirmVisible.value = false
  } catch {
    ElMessage.error('配置保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

// ---------- 推理日志查询 ----------
const logKeyword = ref('')
const filteredLogs = computed<InferenceLog[]>(() => {
  const k = logKeyword.value.trim().toLowerCase()
  if (!k) return logs.value
  return logs.value.filter(
    (l) => l.caseId.toLowerCase().includes(k) || l.patientName.toLowerCase().includes(k) || l.operator.toLowerCase().includes(k)
  )
})

/** 导出推理日志 CSV（科研留档） */
function exportLogs(): void {
  downloadCsv(
    `推理日志_${formatDate(new Date())}.csv`,
    ['日志ID', '病例编号', '患者', '模型版本', '融合策略', '来源', '耗时(s)', '风险评分', '状态', '操作人', '时间'],
    filteredLogs.value.map((l) => [
      l.id,
      l.caseId,
      l.patientName,
      l.modelVersion,
      l.strategy === 'feature' ? '特征级' : '像素级',
      l.source === 'real' ? '真实模型' : '模拟演示',
      l.durationSec,
      l.riskScore,
      l.status,
      l.operator,
      l.time
    ])
  )
}

onMounted(async () => {
  // 分块容错：单个图表接口失败只空对应区块，不让全屏 loading 卡死
  try {
    const [cfg, f, c, l, s] = await Promise.allSettled([
      apiGetModelConfig(),
      apiGetFoldMetrics(),
      apiGetTrainCurve(),
      apiGetInferenceLogs(),
      apiGetModelStatus()
    ])
    if (cfg.status === 'fulfilled') config.value = cfg.value
    if (f.status === 'fulfilled') folds.value = f.value
    if (c.status === 'fulfilled') curve.value = c.value
    if (l.status === 'fulfilled') logs.value = l.value
    if (s.status === 'fulfilled') modelStatus.value = s.value
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-loading="loading" class="page-wrap">
    <!-- ==================== 真实 TransMF 模型运行时状态 ==================== -->
    <div class="card-ad mb-4">
      <div class="card-ad__header">
        <span class="card-ad__title">TransMF 真实模型运行时</span>
        <div class="flex items-center gap-2">
          <el-tag
            v-if="modelStatus"
            size="small"
            round
            :type="modelStatus.ensembleLoaded ? 'success' : 'info'"
            effect="light"
          >
            {{ modelStatus.ensembleLoaded ? '模型已加载' : '模拟模式（未加载）' }}
          </el-tag>
          <el-button
            type="primary"
            size="small"
            :icon="'RefreshRight'"
            :loading="reloading"
            @click="handleReload"
          >
            热重载模型
          </el-button>
        </div>
      </div>
      <div v-if="modelStatus" class="p-5">
        <div class="grid grid-cols-4 gap-x-6 gap-y-4">
          <div>
            <div class="text-[12px] text-hint">推理设备</div>
            <div class="font-num text-[15px] font-medium text-ink mt-0.5">
              {{ modelStatus.device || '—' }}
              <span v-if="modelStatus.gpuName" class="text-[12px] text-sub font-normal ml-1">{{ modelStatus.gpuName }}</span>
            </div>
          </div>
          <div>
            <div class="text-[12px] text-hint">已加载权重快照</div>
            <div class="font-num text-[15px] font-medium text-ink mt-0.5">
              {{ modelStatus.modelCount }}
              <span class="text-[12px] text-sub font-normal">/ {{ modelStatus.checkpointCount }} checkpoint</span>
            </div>
          </div>
          <div>
            <div class="text-[12px] text-hint">推理框架</div>
            <div class="font-num text-[13px] text-ink mt-1">
              torch {{ modelStatus.torchVersion || '—' }} · monai {{ modelStatus.monaiVersion || '—' }}
            </div>
          </div>
          <div>
            <div class="text-[12px] text-hint">模型架构</div>
            <div class="text-[13px] text-ink mt-0.5">{{ modelStatus.modelArch }}</div>
          </div>
          <div>
            <div class="text-[12px] text-hint">架构参数</div>
            <div class="font-num text-[13px] text-ink mt-0.5">
              dim {{ modelStatus.modelParams.dim }} · depth {{ modelStatus.modelParams.depth }} ·
              heads {{ modelStatus.modelParams.heads }}
            </div>
          </div>
          <div>
            <div class="text-[12px] text-hint">最近一次推理</div>
            <div v-if="modelStatus.lastInference" class="font-num text-[13px] text-ink mt-0.5">
              AD 概率 {{ (modelStatus.lastInference.prob * 100).toFixed(1) }}% ·
              {{ modelStatus.lastInference.nModels }} 模型 · {{ modelStatus.lastInference.elapsed.toFixed(1) }}s
            </div>
            <div v-else class="text-[13px] text-hint mt-0.5">暂无（等待首次真实推理）</div>
          </div>
        </div>
        <div class="mt-3 text-[11px] text-hint">
          热重载会释放显存中的旧权重并重新加载全部 TransMF 快照（约 5-8s），期间推理请求将短暂回退模拟模式。
        </div>
      </div>
    </div>

    <div class="grid grid-cols-12 gap-4">
      <!-- ==================== 左列：推理参数配置 ==================== -->
      <div class="card-ad col-span-4 self-start">
        <div class="card-ad__header">
          <span class="card-ad__title">推理参数配置</span>
          <span v-if="config" class="text-[11px] text-hint font-num">v{{ config.snapshotVersion }}</span>
        </div>
        <div v-if="config" class="p-5 space-y-6">
          <!-- 融合策略 -->
          <div>
            <div class="text-[13px] font-medium text-ink mb-2.5">多模态融合策略</div>
            <el-radio-group v-model="config.strategy" class="!w-full">
              <div class="grid grid-cols-2 gap-3 w-full">
                <div
                  class="px-4 py-3 rounded-card border cursor-pointer transition-colors"
                  :class="config.strategy === 'feature' ? 'border-primary bg-primary-light' : 'border-line hover:border-primary/50'"
                  @click="config.strategy = 'feature'"
                >
                  <div class="text-[13px] font-medium text-ink">特征级融合</div>
                  <div class="mt-1 text-[11px] text-hint leading-4">TransMF 双流编码后特征拼接</div>
                </div>
                <div
                  class="px-4 py-3 rounded-card border cursor-pointer transition-colors"
                  :class="config.strategy === 'pixel' ? 'border-primary bg-primary-light' : 'border-line hover:border-primary/50'"
                  @click="config.strategy = 'pixel'"
                >
                  <div class="text-[13px] font-medium text-ink">像素级融合</div>
                  <div class="mt-1 text-[11px] text-hint leading-4">影像空间配准后像素叠加输入</div>
                </div>
              </div>
            </el-radio-group>
          </div>

          <!-- 模态权重 -->
          <div>
            <div class="flex items-center justify-between mb-1">
              <span class="text-[13px] font-medium text-ink">模态权重（MRI : PET）</span>
              <span class="font-num text-[13px] text-primary font-medium">
                {{ (config.mriWeight * 100).toFixed(0) }} : {{ (100 - config.mriWeight * 100).toFixed(0) }}
              </span>
            </div>
            <el-slider v-model="config.mriWeight" :min="0.1" :max="0.9" :step="0.05" show-stops />
            <div class="text-[11px] text-hint">MRI 侧重建模形态学萎缩特征，PET 侧重建模代谢减低特征</div>
          </div>

          <!-- 风险阈值 -->
          <div>
            <div class="flex items-center justify-between mb-1">
              <span class="text-[13px] font-medium text-ink">AD 筛查风险阈值</span>
              <span class="font-num text-[13px] text-primary font-medium">{{ config.riskThreshold.toFixed(2) }}</span>
            </div>
            <el-slider v-model="config.riskThreshold" :min="0.3" :max="0.8" :step="0.01" />
            <div class="text-[11px] text-hint">评分 ≥ 阈值×100 判定为 AD 高风险（触发临床预警）</div>
          </div>

          <!-- ROI 脑区范围 -->
          <div>
            <div class="text-[13px] font-medium text-ink mb-2.5">ROI 脑区检测范围</div>
            <el-checkbox-group v-model="config.roiRegions">
              <div class="grid grid-cols-2 gap-y-1.5">
                <el-checkbox v-for="r in ROI_OPTIONS" :key="r" :value="r" class="!mr-0">{{ r }}</el-checkbox>
              </div>
            </el-checkbox-group>
          </div>

          <!-- 保存 -->
          <el-button type="primary" class="!w-full" :icon="'FolderChecked'" :loading="saving" @click="saveConfirmVisible = true">
            保存并下发配置
          </el-button>
          <div v-if="config" class="text-[11px] text-hint text-center">
            最近更新：{{ config.updatedAt }} · {{ config.updatedBy }}
          </div>
        </div>
      </div>

      <!-- ==================== 右列：性能可视化 ==================== -->
      <div class="col-span-8 space-y-4">
        <!-- 平均性能指标卡 -->
        <div class="grid grid-cols-5 gap-4">
          <div v-for="(item, i) in [
            { label: '平均 AUC', value: meanMetric.auc, color: '#2F6DA3' },
            { label: '平均 ACC', value: meanMetric.acc, color: '#2E9E6B' },
            { label: '平均灵敏度', value: meanMetric.sen, color: '#D99A2B' },
            { label: '平均特异度', value: meanMetric.spe, color: '#7FA8CC' },
            { label: '平均 F1', value: meanMetric.f1, color: '#D96B2B' }
          ]" :key="i" class="card-ad p-4 text-center">
            <div class="text-[12px] text-hint">{{ item.label }}</div>
            <div class="font-num text-[24px] font-semibold mt-1.5 leading-none" :style="{ color: item.color }">
              {{ item.value.toFixed(4) }}
            </div>
          </div>
        </div>

        <!-- 5 折交叉验证 -->
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">5 折交叉验证性能（队列分层）</span>
            <span class="text-[11px] text-hint">75 模型集成 · 5 种子 × 5 折 × 3 Snapshot</span>
          </div>
          <div class="p-4">
            <ChartBase :option="foldOption" :height="280" :empty="folds.length === 0" />
          </div>
        </div>

        <!-- 训练曲线 -->
        <div class="card-ad">
          <div class="card-ad__header">
            <span class="card-ad__title">训练曲线</span>
            <span class="text-[11px] text-hint">50 Epoch · Label Smoothing CE</span>
          </div>
          <div class="p-4">
            <ChartBase :option="curveOption" :height="260" :empty="curve.length === 0" />
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== 历史推理日志 ==================== -->
    <div class="card-ad">
      <div class="card-ad__header">
        <span class="card-ad__title">历史推理日志</span>
        <div class="flex items-center gap-2.5">
          <el-input
            v-model="logKeyword"
            placeholder="搜索病例 / 患者 / 操作人"
            clearable
            class="!w-56"
            size="small"
            :prefix-icon="'Search'"
          />
          <el-button size="small" :icon="'Download'" @click="exportLogs">导出 CSV</el-button>
        </div>
      </div>
      <el-table :data="filteredLogs" class="w-full" size="default" max-height="420">
        <el-table-column prop="id" label="日志ID" width="90">
          <template #default="{ row }"><span class="font-num text-[12px]">{{ row.id }}</span></template>
        </el-table-column>
        <el-table-column prop="caseId" label="病例编号" width="100">
          <template #default="{ row }"><span class="font-num text-[12px]">{{ row.caseId }}</span></template>
        </el-table-column>
        <el-table-column prop="patientName" label="患者" min-width="90" />
        <el-table-column prop="modelVersion" label="模型版本" min-width="130">
          <template #default="{ row }"><span class="font-num text-[12px]">{{ row.modelVersion }}</span></template>
        </el-table-column>
        <el-table-column label="融合策略" width="96" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" round>{{ row.strategy === 'feature' ? '特征级' : '像素级' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="88" align="center">
          <template #default="{ row }">
            <el-tag
              size="small"
              round
              :type="row.source === 'real' ? 'primary' : 'info'"
              effect="light"
            >
              {{ row.source === 'real' ? '真实模型' : '模拟' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="76" align="right">
          <template #default="{ row }"><span class="font-num text-[12px]">{{ row.durationSec }}s</span></template>
        </el-table-column>
        <el-table-column label="风险评分" width="88" align="right">
          <template #default="{ row }">
            <span class="font-num font-medium" :class="row.riskScore >= 62 ? 'text-risk-late' : 'text-ink'">{{ row.riskScore }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" round :type="row.status === '成功' ? 'success' : 'danger'" effect="light">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="operator" label="操作人" width="84" />
        <el-table-column prop="time" label="时间" min-width="150">
          <template #default="{ row }"><span class="font-num text-[12px] text-sub">{{ row.time }}</span></template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 保存确认 -->
    <ConfirmDialog
      v-model="saveConfirmVisible"
      title="保存模型科研配置"
      type="warning"
      content="配置变更将即时下发推理引擎，影响后续所有病例的 AI 分析结果与高风险判定。是否确认保存？"
      confirm-text="确认下发"
      @confirm="saveConfig"
    />
  </div>
</template>
