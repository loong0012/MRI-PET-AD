<template>
  <!--
    组件级错误边界
    ------------------------------------------------------------------
    作用：捕获子树在渲染/生命周期/侦听器中抛出的异常，展示可恢复的兜底 UI，
    避免单个图表或阅片组件异常导致整个页面白屏（医疗场景下白屏＝医生看不到结果）。

    与 main.ts 的 app.config.errorHandler 分工：
    - errorHandler：全局兜底，只记录日志，不接管 UI；
    - 本组件：接管 UI，提供「重试 / 返回工作台」两条恢复路径。
  -->
  <div v-if="hasError" class="error-boundary">
    <el-result icon="warning" title="页面模块加载异常" :sub-title="subTitle">
      <template #extra>
        <el-space>
          <el-button type="primary" @click="retry">重试</el-button>
          <el-button @click="goDashboard">返回工作台</el-button>
          <el-button v-if="showDetail" text @click="expanded = !expanded">
            {{ expanded ? '收起详情' : '查看技术详情' }}
          </el-button>
        </el-space>
      </template>
    </el-result>

    <el-collapse-transition>
      <div v-show="expanded" class="error-detail">
        <div class="error-detail__meta">
          <span>组件：{{ componentName }}</span>
          <span>时间：{{ occurredAt }}</span>
        </div>
        <pre class="error-detail__stack">{{ stack }}</pre>
      </div>
    </el-collapse-transition>
  </div>

  <!--
    正常路径：用 :key 包裹 slot。
    renderKey 变化时整棵子树重新挂载——重试时给子组件一次干净的重生机会，
    而不是带着已损坏的内部状态继续渲染。
  -->
  <div v-else :key="renderKey">
    <slot />
  </div>
</template>

<script setup lang="ts">
import { onErrorCaptured, ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

const props = withDefaults(
  defineProps<{
    /** 出错组件名，用于兜底 UI 与日志定位 */
    componentName?: string
    /** 是否展示技术详情（生产可关闭，避免向终端用户暴露内部信息） */
    showDetail?: boolean
  }>(),
  { componentName: '未知模块', showDetail: true }
)

const router = useRouter()

const hasError = ref(false)
const stack = ref('')
const expanded = ref(false)
const occurredAt = ref('')
const subTitle = ref('')

// 用于强制重建子树：改变 key 会让 <slot> 内容重新挂载
const renderKey = ref(0)
defineExpose({ renderKey })

onErrorCaptured((err, _instance, info) => {
  // 已被业务层处理过（请求层统一 toast）的错误不接管，交由上层处理
  if ((err as { __handled?: boolean } | null)?.__handled) return false

  const e = err as Error
  stack.value = e?.stack || String(err)
  occurredAt.value = new Date().toLocaleString('zh-CN')
  subTitle.value = `${props.componentName} 渲染失败（${info || '未知阶段'}）。诊断结果数据不会丢失，可重试或返回工作台。`
  hasError.value = true

  // 控制台保留完整堆栈，便于研发定位（不展示给终端用户）
  console.error('[ErrorBoundary]', props.componentName, info, err)
  // 返回 false：阻止异常继续向上冒泡到 app.config.errorHandler 造成重复日志
  return false
})

function retry() {
  hasError.value = false
  stack.value = ''
  expanded.value = false
  // 下一帧重建子树，给组件一次干净的重新挂载机会
  nextTick(() => {
    renderKey.value += 1
  })
}

function goDashboard() {
  ElMessage.info('已返回工作台')
  router.push('/dashboard').catch(() => {})
}
</script>

<style scoped>
.error-boundary {
  padding: 24px;
  min-height: 320px;
}

.error-detail {
  margin: 0 auto;
  max-width: 880px;
  padding: 12px 16px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-light);
}

.error-detail__meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}

.error-detail__stack {
  margin: 0;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}
</style>
