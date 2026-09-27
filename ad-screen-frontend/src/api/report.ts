import type { ReportData, ReportTemplate, NiaaAlignment, ConflictEvidence, PatientFriendly } from '@/types/report'
import type { CaseRecord } from '@/types/case'
import type { InterventionSection, FollowUpPlan, AnalysisResult } from '@/types/analysis'
import { httpGet, httpPost, httpDownload } from '@/utils/request'
import { buildAnalysis, getInterventions, getFollowUp, cases as dbCases, pushCaseLog } from '@/mock/db'
import { formatDate } from '@/utils/format'

const useMock = import.meta.env.VITE_USE_MOCK === 'true'

export const REPORT_TEMPLATES: ReportTemplate[] = [
  { key: 'standard', name: '标准筛查报告', desc: '完整版：患者信息 + 影像截图 + 全部量化指标 + 干预建议' },
  { key: 'brief', name: '简明结论报告', desc: '精简版：核心结论 + 关键指标，适合门诊快速沟通' },
  { key: 'research', name: '科研随访报告', desc: '科研版：增加模型版本/置信度/评分明细，适合随访研究归档' },
  { key: 'patient', name: '患者科普版', desc: '通俗简化版：仅含结论与生活建议，适合患者沟通' }
]

// ---------- Mock 模式：与后端 routers/report.py 同口径的规则派生 ----------

/** Mock：NIA-AA A/T/N 标签（影像代理，规则与后端 _compute_niaa 一致） */
function buildMockNiaa(a: AnalysisResult): NiaaAlignment {
  const suv = a.meanSUV
  const A = suv < 0.85 ? 'A+' : 'A-'
  const A_reason = suv < 0.85
    ? `PET 平均 SUV=${suv.toFixed(2)}<0.85，提示淀粉样蛋白沉积可能`
    : `PET 平均 SUV=${suv.toFixed(2)}≥0.85，无明显淀粉样蛋白沉积征象`
  const hippo = a.abnormalRegions.filter((r) => r.region.includes('海马'))
  const severe = hippo.filter((r) => r.zScore < -2)
  const T = severe.length > 0 ? 'T+' : 'T-'
  const T_reason = severe.length > 0
    ? `海马 zScore<${severe[0].zScore.toFixed(1)}<-2，提示 tau 病理可能`
    : '海马区域无明显萎缩征象'
  const totalHv = a.hippocampusVolumeL + a.hippocampusVolumeR
  const N = totalHv < 6.0 ? 'N+' : 'N-'
  const N_reason = totalHv < 6.0
    ? `双侧海马体积总和=${totalHv.toFixed(2)}cm³<6.0，神经变性证据`
    : `双侧海马体积总和=${totalHv.toFixed(2)}cm³≥6.0，在正常下限以上`
  return { A, A_reason, T, T_reason, N, N_reason, reasoning: `${A_reason}；${T_reason}；${N_reason}` }
}

/** Mock：矛盾证据（无随访 MMSE 数据，仅检测模态内部冲突，规则与后端 _compute_conflict 一致） */
function buildMockConflict(a: AnalysisResult): ConflictEvidence {
  const items: ConflictEvidence['items'] = []
  const hasSevereHippo = a.abnormalRegions.some(
    (r) => r.region.includes('海马') && r.atrophy.includes('重度')
  )
  if (hasSevereHippo && a.meanSUV > 0.9) {
    items.push({
      type: 'modality_internal',
      description: `MRI 显示海马重度萎缩，但 PET 平均 SUV=${a.meanSUV.toFixed(2)}>0.9（代谢尚可），存在结构-代谢不一致`
    })
  }
  return { hasConflict: items.length > 0, items }
}

/** Mock：患者通俗版（stageCode 映射，与后端 _PATIENT_STAGE_MAP 一致） */
const MOCK_PATIENT_STAGE: Record<string, PatientFriendly> = {
  CN: {
    summary: '当前脑部影像与认知功能均在正常范围，未发现阿尔茨海默病征象。建议保持健康生活方式并定期体检。',
    lifestyle: ['每周 150 分钟中等强度有氧运动', '地中海饮食模式', '保证 7-8 小时睡眠', '保持社交与认知活动', '控制血压、血糖、血脂'],
    followUp: '建议 2-3 年复查一次脑部影像与认知评估。'
  },
  MCI: {
    summary: '当前存在轻度认知障碍，部分脑区出现早期变化。这不等于阿尔茨海默病，但需要密切随访和积极干预以延缓进展。',
    lifestyle: ['坚持规律有氧运动（如快走、游泳）', '认知训练（阅读、棋类、学习新技能）', '地中海/MIND 饮食', '保证睡眠质量', '控制心血管危险因素', '避免独居与社交隔离'],
    followUp: '建议每 6 个月复查认知功能（MMSE/MoCA），每年复查脑部 MRI。'
  },
  'AD-E': {
    summary: '当前提示阿尔茨海默病早期改变，脑部影像已出现特征性萎缩与代谢异常。早期诊断有助于尽早开始干预治疗，建议尽快到神经内科就诊。',
    lifestyle: ['在家人陪伴下保持适度活动', '简化日常事务、使用备忘工具', '规律作息、避免夜间躁动', '继续认知刺激训练', '注意营养与水分摄入'],
    followUp: '建议每 3 个月复查认知与功能评估，与神经内科医生讨论是否需要药物治疗（如胆碱酯酶抑制剂）。'
  },
  'AD-L': {
    summary: '当前提示阿尔茨海默病中晚期改变，脑部萎缩与代谢异常较为显著。需要综合照护方案，建议尽快与神经内科、老年科医生共同制定治疗与照护计划。',
    lifestyle: ['24 小时照护与防跌倒', '协助进食、穿衣、如厕等日常活动', '防走失（定位手环、家中门锁）', '规律作息、避免环境剧变', '关注吞咽与营养支持'],
    followUp: '建议每 3 个月复查，重点关注并发症预防、照护负担评估与家庭支持。'
  }
}

function buildMockPatientFriendly(a: AnalysisResult): PatientFriendly {
  return MOCK_PATIENT_STAGE[a.stageCode] ?? MOCK_PATIENT_STAGE.CN
}

/** 聚合报告数据 */
export function apiGetReportData(caseId: string, doctorName: string): Promise<ReportData> {
  if (useMock) {
    const c = dbCases.find((x) => x.id === caseId)
    if (!c) return Promise.reject(new Error('病例不存在'))
    const analysis = buildAnalysis(c)
    const interventions = getInterventions(c)
    const followUp = getFollowUp(c)
    return Promise.resolve({
      caseInfo: c,
      analysis,
      interventions,
      followUp,
      reportNo: `RPT-${c.id}-${Date.now().toString().slice(-4)}`,
      reportDate: formatDate(new Date()),
      hospital: '神经影像智能筛查中心',
      doctorName,
      // Mock 模式同步派生 3 个增强字段，保证 mock/real 两条链路数据结构一致
      niaaAlignment: buildMockNiaa(analysis),
      conflictEvidence: buildMockConflict(analysis),
      patientFriendly: buildMockPatientFriendly(analysis)
    })
  }
  return httpGet<ReportData>(`/report/${caseId}`)
}

/** 报告云端保存 */
export function apiSaveReportCloud(data: ReportData, operator: string): Promise<void> {
  if (useMock) {
    data.caseInfo.cloudSaved = true
    data.caseInfo.diagStatus = '已出报告'
    data.caseInfo.status = 'reported'
    pushCaseLog(data.caseInfo.id, data.caseInfo.patient.name, '生成筛查报告', operator, `报告编号 ${data.reportNo} 已云端保存`)
    return Promise.resolve()
  }
  return httpPost<void>('/report/save', data)
}

/** 批量导出报告汇总 CSV（选中病例） */
export function apiBatchExportReports(caseIds: string[]): Promise<void> {
  return httpDownload('/report/batch-export', `批量报告_${formatDate(new Date())}.csv`, { caseIds })
}

/** 更新病例诊断状态为医生已审核 */
export function apiAuditCase(c: CaseRecord, operator: string): Promise<void> {
  if (useMock) {
    c.diagStatus = '医生已审核'
    pushCaseLog(c.id, c.patient.name, '医生审核', operator, '医师完成结果审核')
    return Promise.resolve()
  }
  return httpPost<void>(`/case/${c.id}/audit`, {})
}

/** 干预方案转报告摘要文本 */
export function interventionToSummary(sections: InterventionSection[], followUp: FollowUpPlan): string[] {
  const out: string[] = []
  for (const s of sections) {
    for (const item of s.items.slice(0, 2)) {
      out.push(`【${s.title}】${item.title}：${item.content}（${item.frequency}，${item.duration}）`)
    }
  }
  out.push(`【随访计划】随访周期 ${followUp.cycleMonths} 个月，下次随访 ${followUp.nextDate}；${followUp.note}`)
  return out
}
