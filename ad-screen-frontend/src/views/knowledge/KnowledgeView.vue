<script setup lang="ts">
/**
 * 患者教育 / AD 科普知识库
 * ------------------------------------------------------------------
 * 面向患者及家属的公开科普内容，全角色可访问（无需权限）：
 * 1. 顶部标题 + 搜索框（按标题/标签搜索，300ms 防抖）
 * 2. 左侧分类导航（显示分类名 + 文章数）
 * 3. 右侧文章卡片列表（标题、摘要、标签、分类标签）
 * 4. 点击卡片打开抽屉查看文章详情（Markdown 渲染）
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  apiGetKnowledgeArticle,
  apiGetKnowledgeArticles,
  apiGetKnowledgeCategories
} from '@/api/knowledge'
import type { KnowledgeArticle, KnowledgeCategory } from '@/types/knowledge.d'

// ==================== 数据状态 ====================
const categories = ref<KnowledgeCategory[]>([])
const articles = ref<KnowledgeArticle[]>([])
const loading = ref(false)
const activeCategory = ref('') // 空字符串表示全部
const keyword = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null

// 文章详情抽屉
const detailVisible = ref(false)
const detailLoading = ref(false)
const currentArticle = ref<KnowledgeArticle | null>(null)

// ==================== 分类 key → 中文名 ====================
const categoryNameMap = computed<Record<string, string>>(() => {
  const map: Record<string, string> = { '': '全部' }
  categories.value.forEach((c) => {
    map[c.key] = c.name
  })
  return map
})

// ==================== 数据请求 ====================
async function fetchCategories(): Promise<void> {
  try {
    categories.value = await apiGetKnowledgeCategories()
  } catch {
    ElMessage.error('加载科普分类失败')
  }
}

/** 文章列表请求序号：防抖后仍可能有在途旧请求，只接受最后一次的结果 */
let articlesReqSeq = 0

async function fetchArticles(): Promise<void> {
  const seq = ++articlesReqSeq
  loading.value = true
  try {
    const data = await apiGetKnowledgeArticles({
      category: activeCategory.value,
      keyword: keyword.value
    })
    if (seq === articlesReqSeq) articles.value = data
  } catch {
    if (seq === articlesReqSeq) articles.value = []
  } finally {
    if (seq === articlesReqSeq) loading.value = false
  }
}

/** 全库文章总数（各分类计数之和）：左侧"全部文章"徽标不随关键词搜索结果变化 */
const totalArticleCount = computed(() => categories.value.reduce((sum, c) => sum + (c.count || 0), 0))

// 选中分类
function selectCategory(key: string): void {
  activeCategory.value = key
  void fetchArticles()
}

// 搜索输入防抖
function onSearchInput(): void {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => void fetchArticles(), 300)
}

// ==================== 文章详情 ====================
/** 详情请求序号：快速连点不同文章时丢弃旧响应，防止后返回的旧文章覆盖当前选中 */
let detailReqSeq = 0

async function openArticle(id: number): Promise<void> {
  const seq = ++detailReqSeq
  detailVisible.value = true
  detailLoading.value = true
  currentArticle.value = null
  try {
    const data = await apiGetKnowledgeArticle(id)
    if (seq === detailReqSeq) currentArticle.value = data
  } catch {
    if (seq === detailReqSeq) ElMessage.error('加载文章详情失败')
  } finally {
    if (seq === detailReqSeq) detailLoading.value = false
  }
}

// 标签数组（逗号分隔）
function splitTags(tags: string): string[] {
  return (tags || '').split(',').map((t) => t.trim()).filter(Boolean)
}

// ==================== 简易 Markdown 渲染 ====================
/**
 * 将 Markdown 文本转为 HTML（仅支持基础语法，不引入 marked 库）：
 * - # / ## / ### 标题
 * - **加粗**
 * - - 无序列表 / 1. 有序列表
 * - > 引用块
 * - --- 分隔线
 * - 空行分段，行内换行转 <br>
 */
function renderMarkdown(md: string): string {
  if (!md) return ''
  const lines = md.split(/\r?\n/)
  const html: string[] = []
  let listType: '' | 'ul' | 'ol' = ''
  let quoteOpen = false

  const closeList = (): void => {
    if (listType) {
      html.push(`</${listType}>`)
      listType = ''
    }
  }
  const closeQuote = (): void => {
    if (quoteOpen) {
      html.push('</blockquote>')
      quoteOpen = false
    }
  }

  for (let line of lines) {
    const trimmed = line.trim()

    // 空行：结束当前块级元素
    if (trimmed === '') {
      closeList()
      closeQuote()
      continue
    }

    // 分隔线
    if (/^-{3,}$/.test(trimmed)) {
      closeList()
      closeQuote()
      html.push('<hr/>')
      continue
    }

    // 标题
    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed)
    if (heading) {
      closeList()
      closeQuote()
      const level = heading[1].length
      html.push(`<h${level}>${inlineFormat(heading[2])}</h${level}>`)
      continue
    }

    // 引用块
    const quote = /^>\s?(.*)$/.exec(trimmed)
    if (quote) {
      closeList()
      if (!quoteOpen) {
        html.push('<blockquote>')
        quoteOpen = true
      }
      html.push(`<p>${inlineFormat(quote[1])}</p>`)
      continue
    }

    // 无序列表
    const ul = /^[-*+]\s+(.*)$/.exec(trimmed)
    if (ul) {
      closeQuote()
      if (listType !== 'ul') {
        if (listType) html.push(`</${listType}>`)
        html.push('<ul>')
        listType = 'ul'
      }
      html.push(`<li>${inlineFormat(ul[1])}</li>`)
      continue
    }

    // 有序列表
    const ol = /^\d+\.\s+(.*)$/.exec(trimmed)
    if (ol) {
      closeQuote()
      if (listType !== 'ol') {
        if (listType) html.push(`</${listType}>`)
        html.push('<ol>')
        listType = 'ol'
      }
      html.push(`<li>${inlineFormat(ol[1])}</li>`)
      continue
    }

    // 普通段落
    closeList()
    closeQuote()
    html.push(`<p>${inlineFormat(trimmed)}</p>`)
  }

  closeList()
  closeQuote()
  return html.join('')
}

/** 行内格式：**加粗** */
function inlineFormat(text: string): string {
  // 转义 HTML 特殊字符，防止注入
  let escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  // **加粗**
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  return escaped
}

// 详情内容的 HTML
const detailHtml = computed(() => renderMarkdown(currentArticle.value?.content || ''))

// ==================== 生命周期 ====================
onMounted(() => {
  void fetchCategories().then(() => void fetchArticles())
})

onUnmounted(() => {
  if (searchTimer !== null) clearTimeout(searchTimer)
})
</script>

<template>
  <div class="page-wrap">
    <div class="card-ad">
      <!-- 顶部标题 + 搜索 -->
      <div class="card-ad__header">
        <span class="card-ad__title">
          <el-icon><Reading /></el-icon>
          患者教育 · AD 科普知识库
        </span>
        <el-input
          v-model="keyword"
          placeholder="搜索文章标题或标签..."
          clearable
          :prefix-icon="'Search'"
          class="w-64"
          @input="onSearchInput"
          @clear="onSearchInput"
        />
      </div>

      <div class="flex gap-4 p-4" style="min-height: 560px">
        <!-- 左侧分类导航 -->
        <div class="w-56 shrink-0">
          <div class="space-y-2">
            <!-- 全部分类 -->
            <div
              class="cat-item"
              :class="{ 'is-active': activeCategory === '' }"
              @click="selectCategory('')"
            >
              <el-icon class="cat-item__icon"><Collection /></el-icon>
              <span class="cat-item__name">全部文章</span>
              <span class="cat-item__count">{{ totalArticleCount }}</span>
            </div>
            <!-- 各分类 -->
            <div
              v-for="c in categories"
              :key="c.key"
              class="cat-item"
              :class="{ 'is-active': activeCategory === c.key }"
              @click="selectCategory(c.key)"
            >
              <el-icon class="cat-item__icon"><component :is="c.icon" /></el-icon>
              <span class="cat-item__name">{{ c.name }}</span>
              <span class="cat-item__count">{{ c.count }}</span>
            </div>
          </div>

          <div class="mt-4 p-3 bg-[#F8FAFC] rounded-card border border-line">
            <div class="text-[12px] text-sub leading-5">
              本知识库内容仅供患者及家属参考，不能替代专业医疗诊断。如有疑问，请咨询神经内科医生。
            </div>
          </div>
        </div>

        <!-- 右侧文章列表 -->
        <div class="flex-1 min-w-0">
          <div v-loading="loading" class="article-grid">
            <el-card
              v-for="(a, idx) in articles"
              :key="a.id"
              class="article-card card-interactive anim-fade-up"
              :style="{ '--anim-delay': `${idx * 60}ms` }"
              shadow="never"
              @click="openArticle(a.id)"
            >
              <!-- 分类标签 + 图标 -->
              <div class="flex items-center justify-between mb-2">
                <el-tag size="small" type="primary" effect="plain" round>
                  {{ categoryNameMap[a.category] || a.category }}
                </el-tag>
                <el-icon class="text-hint text-lg"><component :is="a.icon" /></el-icon>
              </div>

              <!-- 标题 -->
              <div class="article-card__title">{{ a.title }}</div>

              <!-- 摘要 -->
              <div class="article-card__summary">{{ a.summary }}</div>

              <!-- 标签 -->
              <div class="article-card__tags">
                <el-tag
                  v-for="t in splitTags(a.tags)"
                  :key="t"
                  size="small"
                  effect="plain"
                  class="mr-1 mb-1"
                >
                  {{ t }}
                </el-tag>
              </div>

              <!-- 底部日期 -->
              <div class="article-card__meta">
                <el-icon><Clock /></el-icon>
                <span>{{ a.createdAt }}</span>
              </div>
            </el-card>

            <el-empty
              v-if="!loading && articles.length === 0"
              description="暂无匹配的科普文章"
              :image-size="80"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- 文章详情抽屉 -->
    <el-drawer
      v-model="detailVisible"
      :title="currentArticle?.title || '文章详情'"
      direction="rtl"
      size="640px"
      :before-close="() => (detailVisible = false)"
    >
      <div v-loading="detailLoading">
        <template v-if="currentArticle">
          <!-- 文章头部信息 -->
          <div class="mb-4 pb-3 border-b border-line">
            <el-tag type="primary" effect="plain" round class="mb-2">
              {{ categoryNameMap[currentArticle.category] || currentArticle.category }}
            </el-tag>
            <h2 class="text-[20px] font-semibold text-ink leading-7 mb-2">
              {{ currentArticle.title }}
            </h2>
            <div class="flex items-center gap-2 text-[12px] text-hint">
              <el-icon><Clock /></el-icon>
              <span>{{ currentArticle.createdAt }}</span>
            </div>
            <div class="mt-3 flex flex-wrap">
              <el-tag
                v-for="t in splitTags(currentArticle.tags)"
                :key="t"
                size="small"
                effect="plain"
                class="mr-1 mb-1"
              >
                {{ t }}
              </el-tag>
            </div>
          </div>

          <!-- Markdown 正文 -->
          <div class="md-content" v-html="detailHtml"></div>

          <!-- 底部免责声明 -->
          <div class="mt-6 p-3 bg-[#F8FAFC] rounded-card border border-line">
            <div class="text-[12px] text-sub leading-5">
              <el-icon class="align-text-bottom mr-1"><InfoFilled /></el-icon>
              本文内容仅供科普参考，不能替代医生的专业诊断与治疗建议。
            </div>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
/* 分类导航项 */
.cat-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
  border: 1px solid transparent;
}
.cat-item:hover {
  background: var(--ad-primary-light);
}
.cat-item.is-active {
  background: var(--ad-primary-light);
  border-color: rgba(47, 109, 163, 0.25);
}
.cat-item__icon {
  color: var(--ad-primary);
  font-size: 16px;
}
.cat-item__name {
  flex: 1;
  font-size: 13px;
  color: var(--ad-ink);
}
.cat-item__count {
  font-size: 12px;
  color: var(--ad-ink-3);
  min-width: 20px;
  text-align: right;
}
.cat-item.is-active .cat-item__count {
  color: var(--ad-primary);
  font-weight: 600;
}

/* 文章卡片网格 */
.article-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
.article-card {
  cursor: pointer;
  border: 1px solid var(--ad-border);
  border-radius: 10px;
}
.article-card :deep(.el-card__body) {
  padding: 16px;
}
.article-card__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ad-ink);
  line-height: 1.4;
  margin-bottom: 8px;
}
.article-card__summary {
  font-size: 13px;
  color: var(--ad-ink-2);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin-bottom: 10px;
}
.article-card__tags {
  margin-bottom: 10px;
}
.article-card__meta {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--ad-ink-3);
}

/* Markdown 正文样式 */
.md-content {
  font-size: 14px;
  line-height: 1.8;
  color: var(--ad-ink);
}
.md-content :deep(h1) {
  font-size: 20px;
  font-weight: 700;
  color: var(--ad-ink);
  margin: 20px 0 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--ad-primary-light);
}
.md-content :deep(h2) {
  font-size: 17px;
  font-weight: 600;
  color: var(--ad-ink);
  margin: 18px 0 10px;
}
.md-content :deep(h3) {
  font-size: 15px;
  font-weight: 600;
  color: var(--ad-ink);
  margin: 14px 0 8px;
}
.md-content :deep(p) {
  margin: 8px 0;
  color: var(--ad-ink-2);
}
.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 8px 0;
  padding-left: 22px;
  color: var(--ad-ink-2);
}
.md-content :deep(li) {
  margin: 4px 0;
}
.md-content :deep(strong) {
  color: var(--ad-ink);
  font-weight: 600;
}
.md-content :deep(blockquote) {
  margin: 12px 0;
  padding: 10px 14px;
  background: var(--ad-primary-light);
  border-left: 3px solid var(--ad-primary);
  border-radius: 0 6px 6px 0;
  color: var(--ad-ink-2);
}
.md-content :deep(blockquote p) {
  margin: 0;
}
.md-content :deep(hr) {
  border: none;
  border-top: 1px solid var(--ad-border);
  margin: 16px 0;
}
</style>
