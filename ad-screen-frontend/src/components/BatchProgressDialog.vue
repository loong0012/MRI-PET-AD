<script setup lang="ts">
/**
 * 批量 AI 分析进度对话框
 * ------------------------------------------------------------------
 * 打开后通过轮询（1.5s）拉取批量任务实时状态，展示总体进度、
 * 当前处理病例、成功/失败计数与逐病例结果明细。
 * 任务结束（completed / failed）时自动停止轮询并 emit completed。
 * 采用医疗低饱和蓝灰色系，与全站主题保持一致。
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import type { BatchTaskStatus } from '@/types/batchTask'
import { apiGetBatchTaskStatus } from '@/api/batchTask'

const props = defineProps<{
  /** 弹窗可见性（v-model） */
  modelValue: boolean
  /** 批量任务 ID */
  taskId: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  /** 任务结束（完成或失败）时触发，附带最终状态 */
  (e: 'completed', status: BatchTaskStatus): void
}>()

/** 弹窗可见性双向绑定代理 */
const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

/** 任务实时状态 */
const taskStatus = ref<BatchTaskStatus | null>(null)

/** 轮询定时器句柄 */
let pollTimer: ReturnType<typeof setInterval> | null = null

/** 连续轮询失败次数：超过上限停止静默重试，给出可见错误态（避免永久空转且无任何反馈） */
const MAX_POLL_FAILURES = 5
const pollFailures = ref(0)
const pollError = ref(false)

/** 在途标志：上一轮请求未返回时本轮直接跳过，避免慢响应堆叠并发 */
let inflight = false

/** 总体进度百分比 */
const percentage = computed(() => {
  const total = taskStatus.value?.total ?? 0
  if (total === 0) return 0
  return Math.round(((taskStatus.value?.done ?? 0) / total) * 100)
})

/** 成功病例数 */
const successCount = computed(
  () => taskStatus.value?.results.filter((r) => r.status === 'success').length ?? 0
)

/** 失败病例数（兼容 'failed' / 'error' 两种状态值） */
const failedCount = computed(
  () =>
    taskStatus.value?.results.filter((r) => r.status === 'failed' || r.status === 'error').length ?? 0
)

/** 停止轮询并清理定时器 */
function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

/** 拉取一次任务状态 */
async function fetchStatus(): Promise<void> {
  if (!props.taskId) return
  // 上一轮仍在途则直接跳过，防止慢响应下请求堆叠
  if (inflight) return
  inflight = true
  try {
    const data = await apiGetBatchTaskStatus(props.taskId)
    pollFailures.value = 0
    pollError.value = false
    taskStatus.value = data
    // 任务结束：停止轮询并通知父级
    if (data.status === 'completed' || data.status === 'failed') {
      stopPolling()
      emit('completed', data)
    }
  } catch {
    // 连续失败达到上限：停止静默重试，展示错误态与手动重试入口（任务本身仍在后台运行）
    pollFailures.value += 1
    if (pollFailures.value >= MAX_POLL_FAILURES) {
      stopPolling()
      pollError.value = true
    }
  } finally {
    inflight = false
  }
}

/** 定时轮询回调：页面处于后台标签时跳过本轮（不计失败），恢复可见后自动续上 */
function pollTick(): void {
  if (document.hidden) return
  void fetchStatus()
}

/** 启动轮询（先立即拉取一次，再按 1.5s 间隔） */
function startPolling(): void {
  stopPolling()
  pollFailures.value = 0
  pollError.value = false
  void fetchStatus()
  pollTimer = setInterval(pollTick, 1500)
}

/** 监听弹窗开关：打开时启动轮询，关闭时停止 */
watch(
  () => props.modelValue,
  (val) => {
    if (val) {
      taskStatus.value = null
      startPolling()
    } else {
      stopPolling()
    }
  },
  { immediate: true }
)

/** 组件卸载时确保清理定时器 */
onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <el-dialog
    v-model="visible"
    title="批量 AI 分析进度"
    width="560px"
    align-center
    :close-on-click-modal="false"
    :close-on-press-escape="false"
  >
    <div class="batch-progress">
      <!-- 总体进度条 -->
      <div class="progress-section">
        <div class="progress-header">
          <span class="progress-label">总体进度</span>
          <span class="progress-num font-num">
            {{ taskStatus?.done ?? 0 }} / {{ taskStatus?.total ?? 0 }}
          </span>
        </div>
        <el-progress
          :percentage="percentage"
          :stroke-width="10"
          :show-text="false"
          color="var(--ad-primary)"
        />
        <div class="progress-percent font-num">{{ percentage }}%</div>
      </div>

      <!-- 当前处理病例 / 状态提示 -->
      <div class="current-case" v-if="taskStatus?.currentCaseId">
        <span class="dot dot-running"></span>
        <span class="text-ink-secondary text-[13px]">正在处理：</span>
        <span class="font-num text-ink text-[13px]">{{ taskStatus.currentCaseId }}</span>
      </div>
      <div class="current-case" v-else-if="taskStatus?.status === 'completed'">
        <span class="dot dot-success"></span>
        <span class="text-[13px]" style="color: var(--ad-risk-low)">全部病例分析完成</span>
      </div>
      <div class="current-case" v-else-if="taskStatus?.status === 'failed'">
        <span class="dot dot-failed"></span>
        <span class="text-[13px]" style="color: var(--ad-risk-late)">批量任务执行失败</span>
      </div>
      <div class="current-case" v-else>
        <span class="dot dot-running"></span>
        <span class="text-[13px] text-ink-secondary">正在初始化批量任务…</span>
      </div>

      <!-- 轮询连续失败：停止静默重试后的可见错误态（后台任务不受影响） -->
      <div v-if="pollError" class="poll-error">
        <span class="text-[13px]" style="color: var(--ad-risk-late)">进度更新失败，已停止自动刷新（任务仍可能在后台执行）</span>
        <el-button type="primary" plain size="small" @click="startPolling">重新连接</el-button>
      </div>

      <!-- 成功 / 失败 / 总数 统计 -->
      <div class="stats-row">
        <div class="stat-item stat-success">
          <span class="stat-num font-num">{{ successCount }}</span>
          <span class="stat-label">成功</span>
        </div>
        <div class="stat-divider"></div>
        <div class="stat-item stat-failed">
          <span class="stat-num font-num">{{ failedCount }}</span>
          <span class="stat-label">失败</span>
        </div>
        <div class="stat-divider"></div>
        <div class="stat-item">
          <span class="stat-num font-num">{{ taskStatus?.total ?? 0 }}</span>
          <span class="stat-label">总数</span>
        </div>
      </div>

      <!-- 处理结果明细（滚动列表） -->
      <div class="result-list" v-if="taskStatus?.results?.length">
        <div class="result-header">处理结果明细</div>
        <div class="result-scroll">
          <div
            v-for="(item, idx) in taskStatus.results"
            :key="idx"
            class="result-item"
          >
            <span
              class="dot"
              :class="item.status === 'success' ? 'dot-success' : 'dot-failed'"
            ></span>
            <span class="result-case font-num">{{ item.caseId }}</span>
            <span
              class="result-status"
              :class="item.status === 'success' ? 'text-success' : 'text-failed'"
            >
              {{ item.status === 'success' ? '成功' : '失败' }}
            </span>
            <span v-if="item.status === 'success' && item.riskScore != null" class="result-score font-num">
              风险分 {{ item.riskScore }}
            </span>
            <span v-if="item.status === 'success' && item.riskLevel" class="result-level">
              {{ item.riskLevel }}
            </span>
            <span v-if="item.status !== 'success' && item.error" class="result-error">
              {{ item.error }}
            </span>
          </div>
        </div>
      </div>

      <!-- 提示说明 -->
      <div class="tip-box">
        批量分析在后台异步执行，关闭此对话框不影响任务运行，可稍后在任务列表中查看结果。
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.batch-progress {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---------- 进度区 ---------- */
.progress-section {
  position: relative;
}
.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.progress-label {
  font-size: 14px;
  color: var(--ad-ink);
  font-weight: 500;
}
.progress-num {
  font-size: 13px;
  color: var(--ad-ink-2);
}
.progress-percent {
  position: absolute;
  right: 0;
  top: 0;
  font-size: 13px;
  color: var(--ad-primary);
  font-weight: 600;
}

/* ---------- 当前处理病例 ---------- */
.current-case {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--ad-primary-light);
  border-radius: 6px;
}

/* 状态圆点 */
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-running {
  background: var(--ad-primary);
  animation: pulse 1.4s ease-in-out infinite;
}
.dot-success {
  background: var(--ad-risk-low);
}
.dot-failed {
  background: var(--ad-risk-late);
}
@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

/* ---------- 轮询失败态 ---------- */
.poll-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: var(--ad-notify-danger-bg);
  border: 1px solid color-mix(in srgb, var(--ad-risk-late) 35%, transparent);
  border-radius: 6px;
}

/* ---------- 统计行 ---------- */
.stats-row {
  display: flex;
  align-items: center;
  justify-content: space-around;
  padding: 14px 0;
  background: var(--ad-bg);
  border-radius: 8px;
}
.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.stat-num {
  font-size: 22px;
  font-weight: 600;
  color: var(--ad-ink);
}
.stat-label {
  font-size: 12px;
  color: var(--ad-ink-3);
}
.stat-success .stat-num {
  color: var(--ad-risk-low);
}
.stat-failed .stat-num {
  color: var(--ad-risk-late);
}
.stat-divider {
  width: 1px;
  height: 28px;
  background: var(--ad-border);
}

/* ---------- 结果明细列表 ---------- */
.result-list {
  border: 1px solid var(--ad-border);
  border-radius: 8px;
  overflow: hidden;
}
.result-header {
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ad-ink);
  background: var(--ad-bg);
  border-bottom: 1px solid var(--ad-border);
}
.result-scroll {
  max-height: 220px;
  overflow-y: auto;
}
.result-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  font-size: 13px;
  border-bottom: 1px solid var(--ad-border);
}
.result-item:last-child {
  border-bottom: none;
}
.result-case {
  color: var(--ad-ink);
  min-width: 80px;
}
.result-status {
  font-weight: 500;
}
.text-success {
  color: var(--ad-risk-low);
}
.text-failed {
  color: var(--ad-risk-late);
}
.result-score {
  color: var(--ad-ink-2);
  font-size: 12px;
}
.result-level {
  color: var(--ad-ink-2);
  font-size: 12px;
}
.result-error {
  color: var(--ad-ink-3);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 提示框 ---------- */
.tip-box {
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ad-ink-2);
  background: var(--ad-primary-light);
  border-radius: 6px;
}
</style>
