import type { CaseRecord, CaseQuery, PageResult, UploadPayload, SearchCaseItem, SimilarCase } from '@/types/case'
import { httpPost, httpGet, httpDelete } from '@/utils/request'
import { cases, addCase, pushCaseLog } from '@/mock/db'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

/** 分页检索病例库（关键词 + 多条件筛选） */
export function apiQueryCases(q: CaseQuery): Promise<PageResult<CaseRecord>> {
  if (useMock) {
    let list = [...cases]
    if (q.keyword.trim()) {
      const k = q.keyword.trim().toLowerCase()
      list = list.filter(
        (c) =>
          c.id.toLowerCase().includes(k) ||
          c.patient.name.toLowerCase().includes(k) ||
          c.patient.patientNo.toLowerCase().includes(k)
      )
    }
    if (q.modality) list = list.filter((c) => c.modality === q.modality)
    if (q.status) list = list.filter((c) => c.status === q.status)
    if (q.riskLevel) list = list.filter((c) => c.riskLevel === q.riskLevel)
    if (q.dateRange && q.dateRange.length === 2) {
      const [s, e] = q.dateRange
      list = list.filter((c) => {
        const d = c.examDate.slice(0, 10)
        return (!s || d >= s) && (!e || d <= e)
      })
    }
    const total = list.length
    const start = (q.page - 1) * q.pageSize
    return Promise.resolve({ list: list.slice(start, start + q.pageSize), total })
  }
  return httpPost<PageResult<CaseRecord>>('/case/query', q)
}

/** 影像上传（DICOM 文件列表） */
export function apiUploadStudy(payload: UploadPayload, operator: string): Promise<CaseRecord> {
  if (useMock) {
    const rec = addCase({
      patient: { patientNo: payload.patientNo, name: payload.patientName, gender: payload.gender, age: payload.age },
      modality: payload.modality,
      examDate: new Date().toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-'),
      department: payload.department,
      status: 'pending',
      diagStatus: '待AI分析',
      riskLevel: null,
      riskScore: null,
      hasMRI: payload.modality !== 'PET',
      hasPET: payload.modality !== 'MRI'
    })
    pushCaseLog(rec.id, rec.patient.name, '上传 MRI/PET 影像', operator, `上传 ${payload.files.length} 个 DICOM 文件（${payload.modality}）`)
    return Promise.resolve(rec)
  }
  const fd = new FormData()
  fd.append('patientNo', payload.patientNo)
  fd.append('patientName', payload.patientName)
  fd.append('gender', payload.gender)
  fd.append('age', String(payload.age))
  fd.append('modality', payload.modality)
  fd.append('department', payload.department)
  fd.append('exam_indication', payload.examIndication ?? 'diagnosis')
  fd.append('pet_tracer', payload.petTracer ?? 'fdg')
  // 2026 版指南：禁忌证筛查清单
  fd.append('absolute_contraindications', JSON.stringify(payload.absoluteContraindications ?? []))
  fd.append('relative_contraindications', JSON.stringify(payload.relativeContraindications ?? []))
  fd.append('special_population', payload.specialPopulation ?? 'normal')
  for (const f of payload.files) fd.append('files', f)
  return httpPost<CaseRecord>('/case/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
}

/** 示例影像类型：AD 阳性 / sMCI / CN 正常对照 */
export type SampleKind = 'ad' | 'smci' | 'cn'

const SAMPLE_META: Record<SampleKind, { subject: string; label: string; labelShort: string; gender: 'M' | 'F'; age: number }> = {
  ad: { subject: '137_S_0438', label: 'AD', labelShort: 'AD 阳性', gender: 'M', age: 74 },
  smci: { subject: '137_S_0994', label: 'sMCI', labelShort: 'sMCI', gender: 'M', age: 71 },
  cn: { subject: '137_S_0972', label: 'CN', labelShort: 'CN 正常', gender: 'F', age: 68 }
}

/** 一键载入内置示例影像数据（ADNI 真实 MRI+PET 配对，用于演示） */
export function apiUploadSample(kind: SampleKind, operator: string): Promise<CaseRecord> {
  if (useMock) {
    const m = SAMPLE_META[kind]
    const rec = addCase({
      patient: { patientNo: m.subject, name: `示例患者·${m.labelShort}`, gender: m.gender, age: m.age },
      modality: 'MRI+PET',
      examDate: new Date().toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-'),
      department: '放射科',
      status: 'pending',
      diagStatus: '待AI分析',
      riskLevel: null,
      riskScore: null,
      hasMRI: true,
      hasPET: true
    })
    pushCaseLog(rec.id, rec.patient.name, '上传 MRI/PET 影像', operator, `载入内置示例数据（ADNI ${m.subject} ${m.label}，MRI+PET 配对）`)
    return Promise.resolve(rec)
  }
  return httpPost<CaseRecord>(`/case/upload-sample?sample=${kind}`, {})
}

/** 批量导入病例（CSV 清单） */
export function apiBatchImport(file: File, operator: string): Promise<{ imported: number }> {
  if (useMock) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        const text = String(reader.result ?? '')
        const lines = text.split(/\r?\n/).filter((l) => l.trim())
        let count = 0
        for (const line of lines.slice(1)) {
          // 约定列：姓名,性别(M/F),年龄,模态(MRI/PET/MRI+PET),科室
          const cols = line.split(',')
          if (cols.length < 5) continue
          const [name, gender, age, modality, dept] = cols
          addCase({
            patient: { patientNo: `P${Date.now()}${count}`, name: name.trim(), gender: gender.trim() === 'M' ? 'M' : 'F', age: Number(age) || 65 },
            modality: (['MRI', 'PET', 'MRI+PET'].includes(modality.trim()) ? modality.trim() : 'MRI+PET') as CaseRecord['modality'],
            examDate: new Date().toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-'),
            department: dept.trim() || '神经内科',
            status: 'pending',
            diagStatus: '待AI分析',
            riskLevel: null,
            riskScore: null,
            hasMRI: modality.trim() !== 'PET',
            hasPET: modality.trim() !== 'MRI'
          })
          count++
        }
        pushCaseLog('BATCH', '—', '批量导入病例', operator, `导入 ${count} 条病例记录`)
        resolve({ imported: count })
      }
      reader.onerror = () => reject(new Error('文件读取失败'))
      reader.readAsText(file, 'utf-8')
    })
  }
  const fd = new FormData()
  fd.append('file', file)
  return httpPost<{ imported: number }>('/case/batch-import', fd)
}

/** 病例详情 */
export function apiGetCase(caseId: string): Promise<CaseRecord | undefined> {
  if (useMock) return Promise.resolve(cases.find((c) => c.id === caseId))
  return httpGet<CaseRecord>(`/case/${caseId}`)
}

/** 删除单病例（软删除，级联清理 AI/干预/随访数据，保留审计日志） */
export function apiDeleteCase(caseId: string, operator: string): Promise<void> {
  if (useMock) {
    const idx = cases.findIndex((c) => c.id === caseId)
    if (idx >= 0) cases.splice(idx, 1)
    pushCaseLog(caseId, '—', '删除病例', operator, `软删除病例 ${caseId}，AI/干预/随访数据级联清理`)
    return Promise.resolve()
  }
  return httpDelete<void>(`/case/${caseId}`)
}

/** 批量删除病例 */
export function apiDeleteCases(caseIds: string[], operator: string): Promise<{ deleted: number }> {
  if (useMock) {
    const before = cases.length
    const ids = new Set(caseIds)
    for (let i = cases.length - 1; i >= 0; i--) {
      if (ids.has(cases[i].id)) cases.splice(i, 1)
    }
    pushCaseLog('BATCH', '—', '批量删除病例', operator, `软删除 ${caseIds.length} 例`)
    return Promise.resolve({ deleted: before - cases.length })
  }
  return httpDelete<{ deleted: number }>('/case/batch', { caseIds })
}

/** 全局快捷搜索（病例ID / 患者姓名） */
export function apiSearchCases(keyword: string, limit = 8): Promise<SearchCaseItem[]> {
  return httpGet<SearchCaseItem[]>('/case/search', { q: keyword, limit: String(limit) })
}

/** 相似病例 top-k 推荐（风险等级 + 异常脑区 Jaccard + 人口学特征综合匹配） */
export function apiGetSimilarCases(caseId: string, limit = 3): Promise<SimilarCase[]> {
  return httpGet<SimilarCase[]>(`/case/${caseId}/similar`, { limit: String(limit) })
}

// 病例收藏
export function apiListFavorites(): Promise<CaseRecord[]> {
  return httpGet<CaseRecord[]>('/case/favorites/list')
}
export function apiToggleFavorite(caseId: string): Promise<{ favorited: boolean }> {
  return httpPost<{ favorited: boolean }>(`/case/favorites/${caseId}`, {})
}
export function apiCheckFavorites(caseIds: string[]): Promise<Record<string, boolean>> {
  return httpGet<Record<string, boolean>>('/case/favorites/check', { caseIds: caseIds.join(',') })
}
