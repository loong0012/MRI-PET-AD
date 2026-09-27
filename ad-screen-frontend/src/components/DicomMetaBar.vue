<script setup lang="ts">
/**
 * DICOM 元数据信息条
 * ------------------------------------------------------------------
 * 嵌入 MedicalViewport 顶部，呈现病例真实 DICOM 标签：
 *  - 默认显示 6 个核心字段（患者/检查/序列/窗宽窗位/层厚）
 *  - el-collapse 展开"全部字段"显示完整 12 字段
 *  - meta === null 时显示灰底占位（历史病例或合成影像）
 *
 * 风格：低饱和蓝灰医疗、圆角卡片、紧凑布局；只读，不参与交互。
 */
import { computed } from 'vue'
import type { DicomMeta } from '@/api/imaging'

const props = defineProps<{
  meta: DicomMeta | null
}>()

/** 是否有可用元数据 */
const hasMeta = computed<boolean>(() => !!props.meta)

/** 6 核心字段（默认展示） */
const coreFields = computed<Array<{ label: string; value: string }>>(() => {
  const m = props.meta
  if (!m) return []
  const wc = m.windowCenter !== null ? m.windowCenter.toFixed(1) : '—'
  const ww = m.windowWidth !== null ? m.windowWidth.toFixed(1) : '—'
  const px = m.patientName ?? '—'
  const pid = m.patientId ?? '—'
  const sd = m.studyDate ?? '—'
  const sdesc = m.seriesDescription ?? '—'
  const st = m.sliceThickness !== null ? `${m.sliceThickness.toFixed(2)} mm` : '—'
  return [
    { label: '患者姓名', value: px },
    { label: '患者 ID', value: pid },
    { label: '检查日期', value: sd },
    { label: '序列描述', value: sdesc },
    { label: '窗宽 / 窗位', value: `${ww} / ${wc}` },
    { label: '层厚', value: st }
  ]
})

/** 全部 12 字段（折叠展开） */
const allFields = computed<Array<{ label: string; value: string }>>(() => {
  const m = props.meta
  if (!m) return []
  const fmt = (v: string | null): string => (v === null || v === '' ? '—' : v)
  const fmtNum = (v: number | null, unit = ''): string =>
    v === null ? '—' : `${v.toFixed(2)}${unit ? ' ' + unit : ''}`
  const fmtPx = (v: [number, number] | null): string =>
    v === null ? '—' : `${v[0].toFixed(3)} × ${v[1].toFixed(3)} mm`
  const studyTime = m.studyTime
  // DICOM StudyTime 形如 "093000" → "09:30:00"
  const studyTimeFmt = (() => {
    if (!studyTime) return '—'
    const s = studyTime.padEnd(6, '0').slice(0, 6)
    return `${s.slice(0, 2)}:${s.slice(2, 4)}:${s.slice(4, 6)}`
  })()
  const studyDateFmt = (() => {
    if (!m.studyDate) return '—'
    const s = m.studyDate
    if (s.length === 8) return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`
    return s
  })()
  return [
    { label: '患者姓名', value: fmt(m.patientName) },
    { label: '患者 ID', value: fmt(m.patientId) },
    { label: '检查日期', value: studyDateFmt },
    { label: '检查时间', value: studyTimeFmt },
    { label: '序列描述', value: fmt(m.seriesDescription) },
    { label: '模态', value: fmt(m.modality) },
    { label: '设备厂商', value: fmt(m.manufacturer) },
    { label: '场强 (T)', value: fmt(m.fieldStrength) },
    { label: '窗位 (WC)', value: fmtNum(m.windowCenter) },
    { label: '窗宽 (WW)', value: fmtNum(m.windowWidth) },
    { label: '层厚', value: fmtNum(m.sliceThickness, 'mm') },
    { label: '像素间距', value: fmtPx(m.pixelSpacing) }
  ]
})
</script>

<template>
  <div class="dicom-meta-bar">
    <!-- 有元数据：6 核心字段 + 折叠全字段 -->
    <div v-if="hasMeta" class="dmbar-card">
      <div class="dmbar-grid">
        <div v-for="f in coreFields" :key="f.label" class="dmbar-item">
          <span class="dmbar-label">{{ f.label }}</span>
          <span class="dmbar-value" :title="f.value">{{ f.value }}</span>
        </div>
      </div>
      <el-collapse class="dmbar-collapse">
        <el-collapse-item title="全部 DICOM 字段（12）" name="all">
          <div class="dmbar-all-grid">
            <div v-for="f in allFields" :key="f.label" class="dmbar-all-item">
              <span class="dmbar-all-label">{{ f.label }}</span>
              <span class="dmbar-all-value" :title="f.value">{{ f.value }}</span>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>
    <!-- 无元数据：灰底占位 -->
    <div v-else class="dmbar-empty">
      <el-icon :size="14"><InfoFilled /></el-icon>
      <span>元数据不可用（历史病例或合成影像）</span>
    </div>
  </div>
</template>

<style scoped>
.dicom-meta-bar {
  width: 100%;
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
}
.dmbar-card {
  position: relative;
  background: linear-gradient(180deg, var(--ad-surface) 0%, var(--ad-bg) 100%);
  border: 1px solid var(--ad-border);
  border-radius: 8px;
  padding: 5px 10px 4px 10px;
  color: var(--ad-ink);
}
/* 核心字段单行排列；窄视口（四分区/三平面）下横向滚动，绝不换行挤压画布高度 */
.dmbar-grid {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px 14px;
  overflow-x: auto;
  scrollbar-width: thin;
}
.dmbar-item {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  min-width: 96px;
  font-size: 11.5px;
  line-height: 18px;
  white-space: nowrap;
}
.dmbar-label {
  color: var(--ad-ink-2);
  flex-shrink: 0;
}
.dmbar-value {
  color: var(--ad-ink);
  font-weight: 600;
  font-family: Consolas, "Courier New", monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.dmbar-collapse {
  margin-top: 2px;
  border-top: 1px dashed var(--ad-border);
  position: relative;
}
.dmbar-collapse :deep(.el-collapse-item__header) {
  height: 22px !important;
  line-height: 22px !important;
  min-height: 22px;
  font-size: 11px;
  color: var(--ad-ink-2);
  background: transparent;
  border-bottom: none;
}
/* 展开的全字段面板：绝对定位浮于画布之上，不占纵向空间，避免小视口下把画布压成 0 高 */
.dmbar-collapse :deep(.el-collapse-item__wrap) {
  position: absolute;
  top: 100%;
  left: -1px;
  right: -1px;
  z-index: 40;
  background: linear-gradient(180deg, var(--ad-surface) 0%, var(--ad-bg) 100%);
  border: 1px solid var(--ad-border);
  border-top: none;
  border-radius: 0 0 8px 8px;
  box-shadow: 0 10px 24px rgba(10, 20, 24, 0.28);
}
.dmbar-collapse :deep(.el-collapse-item__content) {
  padding: 6px 10px 8px 10px;
}
.dmbar-all-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 2px 12px;
}
.dmbar-all-item {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  font-size: 10.5px;
  line-height: 16px;
}
.dmbar-all-label {
  color: var(--ad-ink-2);
  flex-shrink: 0;
}
.dmbar-all-value {
  color: var(--ad-ink);
  font-family: Consolas, "Courier New", monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.dmbar-empty {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--ad-bg);
  border: 1px dashed var(--ad-border);
  border-radius: 6px;
  color: var(--ad-ink-3);
  font-size: 11.5px;
}
</style>
