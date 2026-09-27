<script setup lang="ts">
/**
 * 病例评论/会诊讨论面板
 * ------------------------------------------------------------------
 * 病例详情页内嵌的讨论区，供多学科团队围绕单例病例展开会诊讨论：
 * - 加载该病例全部评论（按时间正序，顶级评论与回复混排）
 * - 发布顶级评论 / 回复指定评论（parentId 指向被回复评论 id）
 * - 删除评论：仅本人或 admin 可删
 * 事件：comment-count（评论数量变化时通知父组件，用于更新入口徽标）
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiListComments, apiCreateComment, apiDeleteComment } from '@/api/comment'
import type { CaseCommentItem } from '@/types/comment'
import { useUserStore } from '@/stores/user'

const props = defineProps<{
  /** 病例编号 */
  caseId: string
}>()

const emit = defineEmits<{
  /** 评论数量变化时通知父组件（用于入口 badge 计数） */
  (e: 'comment-count', count: number): void
}>()

const userStore = useUserStore()

// ---------- 评论列表 ----------
const comments = ref<CaseCommentItem[]>([])
const loading = ref(false)
const submitting = ref(false)

/** 当前正在回复的评论（null 表示发布顶级评论） */
const replyTo = ref<CaseCommentItem | null>(null)

/** 输入框草稿 */
const draft = ref('')

/** id -> 评论 的映射，用于回复时定位被回复人姓名 */
const commentMap = computed<Record<number, CaseCommentItem>>(() => {
  const map: Record<number, CaseCommentItem> = {}
  for (const c of comments.value) {
    map[c.id] = c
  }
  return map
})

/** 加载评论列表 */
async function loadComments(): Promise<void> {
  loading.value = true
  try {
    const res = await apiListComments(props.caseId)
    comments.value = res
    emit('comment-count', res.length)
  } catch {
    /* 网络/业务错误已由 request.ts 统一弹窗提示 */
  } finally {
    loading.value = false
  }
}

/** 判断当前用户是否可删除指定评论（本人或 admin） */
function canDelete(c: CaseCommentItem): boolean {
  if (userStore.role === 'admin') return true
  return c.user === userStore.username
}

/** 点击"回复"：设定回复目标并清空草稿 */
function onReply(c: CaseCommentItem): void {
  replyTo.value = c
  draft.value = ''
}

/** 取消回复 */
function cancelReply(): void {
  replyTo.value = null
}

/** 提交评论（顶级或回复） */
async function onSubmit(): Promise<void> {
  const content = draft.value.trim()
  if (!content) {
    ElMessage.warning('评论内容不能为空')
    return
  }
  submitting.value = true
  try {
    await apiCreateComment(props.caseId, {
      content,
      parentId: replyTo.value ? replyTo.value.id : 0
    })
    ElMessage.success('评论已发布')
    draft.value = ''
    replyTo.value = null
    await loadComments()
  } catch {
    /* 错误已弹窗 */
  } finally {
    submitting.value = false
  }
}

/** 删除评论 */
async function onDelete(c: CaseCommentItem): Promise<void> {
  try {
    await ElMessageBox.confirm('确认删除该评论？删除后不可恢复。', '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch {
    return
  }
  try {
    await apiDeleteComment(c.id)
    ElMessage.success('评论已删除')
    await loadComments()
  } catch {
    /* 错误已弹窗 */
  }
}

// 进入时加载
onMounted(() => {
  void loadComments()
})

// 病例编号变化时重新加载（父组件复用时）
watch(
  () => props.caseId,
  (n, o) => {
    if (n && n !== o) {
      replyTo.value = null
      draft.value = ''
      void loadComments()
    }
  }
)
</script>

<template>
  <div class="card-ad flex flex-col h-full min-h-0">
    <!-- 标题栏 -->
    <div class="card-ad__header shrink-0">
      <div class="card-ad__title">
        <el-icon><ChatDotRound /></el-icon>
        <span>会诊讨论</span>
        <el-tag size="small" type="info" effect="plain" round class="ml-1 font-num">
          {{ comments.length }}
        </el-tag>
      </div>
      <el-button text size="small" :icon="'Refresh'" @click="loadComments">刷新</el-button>
    </div>

    <!-- 评论列表 -->
    <div v-loading="loading" class="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-0">
      <!-- 空状态 -->
      <el-empty
        v-if="!loading && comments.length === 0"
        description="暂无讨论，发表第一条评论"
        :image-size="80"
      />

      <!-- 评论项 -->
      <div
        v-for="c in comments"
        :key="c.id"
        class="rounded-card border border-line p-3 transition-colors hover:border-primary/40"
      >
        <!-- 头部：头像 + 姓名 + 时间 -->
        <div class="flex items-center gap-2.5">
          <div
            class="w-8 h-8 rounded-full bg-primary-light text-primary flex items-center justify-center text-[13px] font-semibold shrink-0"
          >
            {{ c.realName.slice(0, 1) || '?' }}
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <span class="text-[13px] font-medium text-ink">{{ c.realName || c.user }}</span>
              <el-tag v-if="c.user === userStore.username" size="small" type="primary" effect="plain" round>
                我
              </el-tag>
            </div>
            <div class="text-[11.5px] text-hint font-num">{{ c.createdAt }}</div>
          </div>
        </div>

        <!-- 正文（回复时显示 @被回复人） -->
        <div class="mt-2 text-[13.5px] text-ink-secondary leading-6 whitespace-pre-wrap break-words">
          <span v-if="commentMap[c.parentId]" class="text-primary">
            回复 @{{ commentMap[c.parentId].realName }}：
          </span>
          {{ c.content }}
        </div>

        <!-- 操作栏 -->
        <div class="mt-2 flex items-center justify-end gap-1">
          <el-button text size="small" @click="onReply(c)">
            <el-icon class="mr-1"><Promotion /></el-icon>回复
          </el-button>
          <el-button
            v-if="canDelete(c)"
            text
            size="small"
            type="danger"
            @click="onDelete(c)"
          >
            <el-icon class="mr-1"><Delete /></el-icon>删除
          </el-button>
        </div>
      </div>
    </div>

    <!-- 回复提示条 -->
    <div
      v-if="replyTo"
      class="shrink-0 px-4 py-2 border-t border-line bg-primary-light/40 flex items-center justify-between"
    >
      <span class="text-[12px] text-ink-secondary">
        回复 <span class="text-primary font-medium">@{{ replyTo.realName }}</span>
      </span>
      <el-button text size="small" @click="cancelReply">取消</el-button>
    </div>

    <!-- 输入区 -->
    <div class="shrink-0 border-t border-line p-3">
      <el-input
        v-model="draft"
        type="textarea"
        :rows="2"
        :placeholder="replyTo ? `回复 @${replyTo.realName}...` : '请输入评论内容...'"
        resize="none"
        @keydown.ctrl.enter="onSubmit"
      />
      <div class="mt-2 flex items-center justify-between">
        <span class="text-[11px] text-hint">Ctrl + Enter 快速发送</span>
        <el-button
          type="primary"
          :icon="'Promotion'"
          :loading="submitting"
          :disabled="!draft.trim()"
          @click="onSubmit"
        >
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>
