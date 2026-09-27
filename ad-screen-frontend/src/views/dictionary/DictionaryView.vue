<script setup lang="ts">
/**
 * 数据字典中心
 * ------------------------------------------------------------------
 * 面向医护/科研人员的医学术语与系统字段说明库：
 * 1. 左侧分类菜单：病例/AI分析/随访/报告/影像/CDSS决策 6 大分类
 * 2. 右侧字段列表：字段名、key、类型、示例值、说明、参考范围
 * 3. 顶部关键词搜索（300ms 防抖，跨分类检索）
 */
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { apiGetCategories, apiListItems } from '@/api/dictionary'
import type { DictCategory, DictItem } from '@/types/dictionary.d'

const categories = ref<DictCategory[]>([])
const items = ref<DictItem[]>([])
const loading = ref(false)
const activeCategory = ref('')
const keyword = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null

const TYPE_COLORS: Record<string, string> = {
  string: 'info',
  int: 'success',
  float: 'warning',
  boolean: 'info',
  date: 'info',
  enum: 'danger'
}

const TYPE_LABELS: Record<string, string> = {
  string: '文本',
  int: '整数',
  float: '小数',
  boolean: '布尔',
  date: '日期',
  enum: '枚举'
}

async function fetchCategories(): Promise<void> {
  try {
    categories.value = await apiGetCategories()
    if (categories.value.length > 0) {
      activeCategory.value = categories.value[0].key
      await fetchItems()
    }
  } catch {
    ElMessage.error('加载分类失败')
  }
}

async function fetchItems(): Promise<void> {
  loading.value = true
  try {
    items.value = await apiListItems(activeCategory.value, keyword.value)
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function selectCategory(key: string): void {
  activeCategory.value = key
  void fetchItems()
}

function onSearchInput(): void {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => void fetchItems(), 300)
}

const activeCategoryMeta = ref<DictCategory | null>(null)
watch(activeCategory, () => {
  activeCategoryMeta.value = categories.value.find((c) => c.key === activeCategory.value) || null
})

onMounted(() => {
  void fetchCategories()
})

onUnmounted(() => {
  if (searchTimer !== null) clearTimeout(searchTimer)
})
</script>

<template>
  <div class="page-wrap">
    <div class="card-ad anim-fade-up">
      <div class="card-ad__header">
        <span class="card-ad__title">数据字典中心</span>
        <span class="text-[11px] text-hint">医学术语与系统字段说明库</span>
      </div>

      <div class="flex gap-4 p-4" style="min-height: 560px;">
        <!-- 左侧分类菜单 -->
        <div class="w-56 shrink-0 flex flex-col">
          <el-input
            v-model="keyword"
            placeholder="搜索字段名/说明..."
            clearable
            :prefix-icon="'Search'"
            class="mb-3"
            @input="onSearchInput"
            @clear="onSearchInput"
          />
          <el-menu
            :default-active="activeCategory"
            class="!border-r-0 dict-menu flex-1"
            @select="selectCategory"
          >
            <el-menu-item
              v-for="c in categories"
              :key="c.key"
              :index="c.key"
              class="!mx-1 !mb-1 !rounded-lg !h-10"
            >
              <el-icon><component :is="c.icon" /></el-icon>
              <span>{{ c.name }}</span>
            </el-menu-item>
          </el-menu>
          <div v-if="activeCategoryMeta" class="mt-3 p-3.5 rounded-card bg-primary-light/50 border border-primary/15">
            <div class="flex items-center gap-1.5 text-[12px] font-medium text-primary mb-1">
              <el-icon :size="13"><InfoFilled /></el-icon>
              当前分类说明
            </div>
            <div class="text-[12px] text-sub leading-5">{{ activeCategoryMeta.desc }}</div>
          </div>
        </div>

        <!-- 右侧字段列表 -->
        <div class="flex-1 min-w-0">
          <el-table :data="items" v-loading="loading" size="default" border stripe>
            <el-table-column label="字段名" width="150">
              <template #default="{ row }">
                <div class="text-[13px] font-medium text-ink">{{ row.name }}</div>
                <div class="text-[11px] text-hint font-mono">{{ row.key }}</div>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="TYPE_COLORS[row.type] || 'info'" effect="plain">
                  {{ TYPE_LABELS[row.type] || row.type }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="示例值" width="140">
              <template #default="{ row }">
                <span class="font-mono text-[12px] text-sub">{{ row.example }}</span>
              </template>
            </el-table-column>
            <el-table-column label="参考范围" width="160">
              <template #default="{ row }">
                <span v-if="row.refRange" class="text-[12px] font-medium text-[#2E9E6B]">{{ row.refRange }}</span>
                <span v-else class="text-hint">—</span>
              </template>
            </el-table-column>
            <el-table-column label="说明" min-width="280">
              <template #default="{ row }">
                <el-popover
                  trigger="hover"
                  :width="380"
                  placement="top-start"
                >
                  <template #reference>
                    <div class="text-[12px] text-sub line-clamp-2 cursor-pointer hover:text-ink transition-colors">{{ row.desc }}</div>
                  </template>
                  <div class="text-[12px] leading-5 text-ink">{{ row.desc }}</div>
                  <div v-if="row.enum" class="mt-2">
                    <div class="text-[11px] text-hint mb-1">可选值：</div>
                    <el-tag v-for="e in row.enum" :key="e" size="small" class="mr-1 mb-1" effect="plain">{{ e }}</el-tag>
                  </div>
                  <div v-if="row.standard" class="mt-2 text-[11px] text-hint">
                    <el-icon class="align-middle mr-0.5"><Document /></el-icon>标准：{{ row.standard }}
                  </div>
                </el-popover>
              </template>
            </el-table-column>
          </el-table>

          <el-empty v-if="!loading && items.length === 0" description="无匹配字段" :image-size="60" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 字典分类菜单：圆角卡片式激活态 */
.dict-menu :deep(.el-menu-item) {
  transition: background 0.18s ease, color 0.18s ease;
}
.dict-menu :deep(.el-menu-item.is-active) {
  background: var(--ad-primary-light) !important;
  color: var(--ad-primary) !important;
  font-weight: 600;
}
.dict-menu :deep(.el-menu-item:not(.is-active):hover) {
  background: #f3f8fc !important;
  color: var(--ad-primary) !important;
}
</style>
