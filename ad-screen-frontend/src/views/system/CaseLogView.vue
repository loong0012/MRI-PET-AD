<script setup lang="ts">
/**
 * 病例操作审计日志页（admin）
 * ------------------------------------------------------------------
 * 满足临床合规审计：全量操作记录查询（含已软删除病例的删除操作）。
 * 筛选：病例ID/姓名关键字、操作类型、操作人、时间范围。
 * 操作类型彩色标签，详情列支持长文本省略+tooltip。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiGetActionOptions, apiQueryCaseLogs } from '@/api/caseLog'
import type { ActionOption, CaseLogItem, CaseLogQuery } from '@/types/caseLog'
import { downloadCsv } from '@/utils/export'
import { formatDate } from '@/utils/format'

const router = useRouter()

const loading = ref(false)
const exporting = ref(false)
const list = ref<CaseLogItem[]>([])
const total = ref(0)
const actionOptions = ref<ActionOption[]>([])

const query = reactive<CaseLogQuery>({
  keyword: '',
  action: '',
  operator: '',
  dateRange: null,
  page: 1,
  pageSize: 20
})

onMounted(async () => {
  actionOptions.value = await apiGetActionOptions()
  await fetchList()
})

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await apiQueryCaseLogs(query)
    list.value = res.list
    total.value = res.total
  } catch {
    ElMessage.error('日志加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  query.page = 1
  void fetchList()
}

function onReset(): void {
  query.keyword = ''
  query.action = ''
  query.operator = ''
  query.dateRange = null
  query.page = 1
  void fetchList()
}

/** 点击病例ID跳转阅片页（不存在 /cases/:id 路由，旧写法会被 catch-all 静默带到仪表盘） */
function goCase(caseId: string): void {
  if (!caseId || caseId === 'BATCH') return
  void router.push(`/viewer/${caseId}`)
}

const ACTION_MAP = computed(() => {
  const m: Record<string, ActionOption> = {}
  for (const a of actionOptions.value) m[a.value] = a
  return m
})

/** 导出当前筛选结果 CSV（分页循环拉全；后端单页上限 100，单次导出保护上限 5000 条） */
async function onExportCsv(): Promise<void> {
  exporting.value = true
  try {
    const EXPORT_MAX = 5000
    const first = await apiQueryCaseLogs({ ...query, page: 1, pageSize: 100 })
    const total = first.total
    const rows = [...first.list]
    const maxPages = Math.ceil(Math.min(total, EXPORT_MAX) / 100)
    for (let p = 2; p <= maxPages && rows.length < total; p++) {
      const r = await apiQueryCaseLogs({ ...query, page: p, pageSize: 100 })
      rows.push(...r.list)
    }
    if (total > rows.length) {
      ElMessage.warning(`匹配 ${total} 条超过单次导出上限 ${EXPORT_MAX} 条，仅导出前 ${rows.length} 条，请缩小筛选范围`)
    }
    downloadCsv(
      `操作审计日志_${formatDate(new Date())}.csv`,
      ['日志ID', '病例ID', '患者姓名', '操作类型', '操作人', '操作时间', '操作详情'],
      rows.map((l) => [l.id, l.caseId, l.patientName, l.actionLabel, l.operator, l.time, l.detail])
    )
    ElMessage.success(`已导出 ${rows.length} 条审计日志`)
  } catch {
    ElMessage.error('导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div class="page-wrap" v-loading="loading">
    <!-- 筛选栏 -->
    <div class="card-ad anim-fade-up p-4">
      <div class="grid grid-cols-5 gap-3">
        <el-input
          v-model="query.keyword"
          placeholder="病例ID / 患者姓名"
          clearable
          @keyup.enter="onSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="query.action" placeholder="操作类型" clearable>
          <el-option
            v-for="a in actionOptions"
            :key="a.value"
            :label="a.label"
            :value="a.value"
          />
        </el-select>
        <el-input
          v-model="query.operator"
          placeholder="操作人"
          clearable
          @keyup.enter="onSearch"
        />
        <el-date-picker
          v-model="query.dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          style="width: 100%"
        />
        <div class="flex gap-2">
          <el-button type="primary" :icon="'Search'" @click="onSearch">查询</el-button>
          <el-button :icon="'RefreshLeft'" @click="onReset">重置</el-button>
        </div>
      </div>
    </div>

    <!-- 结果统计 + 导出 -->
    <div class="card-ad anim-fade-up" style="--anim-delay: 80ms">
      <div class="card-ad__header">
        <span class="card-ad__title">操作审计记录</span>
        <span class="text-xs text-hint">共 {{ total }} 条 · 含已删除病例的操作记录</span>
        <div class="flex-1" />
        <el-button :icon="'Download'" :loading="exporting" @click="onExportCsv">导出 CSV</el-button>
      </div>

      <el-table
        :data="list"
        style="width: 100%"
        :header-cell-style="{ background: '#F7FAFC', color: '#5C6B7A', fontWeight: 600, fontSize: '13px' }"
        :cell-style="{ fontSize: '13px' }"
        stripe
      >
        <el-table-column prop="caseId" label="病例ID" width="130">
          <template #default="{ row }">
            <el-link
              v-if="row.caseId && row.caseId !== 'BATCH'"
              type="primary"
              underline="never"
              class="!font-num"
              @click="goCase(row.caseId)"
            >{{ row.caseId }}</el-link>
            <span v-else class="text-hint">{{ row.caseId || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="patientName" label="患者姓名" width="110" />
        <el-table-column label="操作类型" width="120">
          <template #default="{ row }">
            <el-tag
              :color="row.actionBg"
              :style="{ color: row.actionColor, border: 'none', fontSize: '12px' }"
              effect="plain"
            >{{ row.actionLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="operator" label="操作人" width="110" />
        <el-table-column prop="time" label="操作时间" width="170">
          <template #default="{ row }">
            <span class="font-num text-hint">{{ row.time }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="detail" label="操作详情" min-width="280">
          <template #default="{ row }">
            <el-tooltip :content="row.detail" placement="top" :disabled="!row.detail || row.detail.length <= 40">
              <span class="text-sub block truncate">{{ row.detail || '—' }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>

      <div class="flex justify-end p-4 border-t border-line">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="fetchList"
          @size-change="onSearch"
        />
      </div>
    </div>
  </div>
</template>
