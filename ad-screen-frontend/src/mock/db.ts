/**
 * Mock 内存数据库（演示模式数据源）
 * 所有数据在会话内持久（刷新前一致），API 层基于此实现完整业务流转。
 * 对接真实后端时本文件整体废弃，API 模块切换到 http 分支即可。
 */
import type { CaseRecord, CaseStatus, DiagStatus, Modality, RiskLevel } from '@/types/case'
import type { AnalysisResult, InterventionSection, FollowUpPlan } from '@/types/analysis'
import type { UserRecord, RoleKey, RolePermission, DemoAccount } from '@/types/user'
import type { InferenceLog, ModelConfig, FoldMetric, TrainCurvePoint } from '@/types/model'
import type { SystemLog, CaseLog } from '@/types/system'
import type { DashboardStats, TaskItem, TrendPoint, RiskDistItem, DashboardDistribution } from '@/types/dashboard'
import { mulberry32, strSeed, formatDateTime, formatDate, scoreToRiskLevel } from '@/utils/format'

// ============================================================
// 账号体系
// ============================================================
export const DEMO_ACCOUNTS: DemoAccount[] = [
  { username: 'rad01', password: '123456', role: 'radiologist', roleName: '放射科医师' },
  { username: 'neu01', password: '123456', role: 'neurologist', roleName: '神经内科医师' },
  { username: 'sci01', password: '123456', role: 'researcher', roleName: '科研管理员' },
  { username: 'admin', password: 'admin123', role: 'admin', roleName: '超级管理员' }
]

export const ROLE_NAME: Record<RoleKey, string> = {
  radiologist: '放射科医师',
  neurologist: '神经内科医师',
  researcher: '科研管理员',
  admin: '超级管理员'
}

export const users: UserRecord[] = DEMO_ACCOUNTS.map((a, i) => ({
  id: i + 1,
  username: a.username,
  realName: ['王建国', '李雨薇', '张明远', '系统管理员'][i],
  role: a.role,
  roleName: a.roleName,
  department: ['放射科', '神经内科', '科研部', '信息科'][i],
  status: 1,
  phone: `138${String(10000000 + i * 111111).slice(0, 8)}`,
  createTime: '2025-06-01 09:00:00',
  lastLoginTime: formatDateTime(new Date(Date.now() - (i + 1) * 3600 * 1000))
}))

// 演示：1 个待审核注册账号（供管理员审核流程演示）
users.push({
  id: 5,
  username: 'rad_new01',
  realName: '陈雨桐',
  role: 'radiologist',
  roleName: '放射科医师',
  department: '放射科',
  status: 2, // 待审核
  phone: '13900087654',
  createTime: formatDateTime(new Date(Date.now() - 2 * 3600 * 1000)),
  lastLoginTime: '—'
})

export const rolePermissions: RolePermission[] = [
  { role: 'radiologist', roleName: '放射科医师', permissionKeys: ['dashboard', 'cases', 'viewer', 'analysis', 'report'] },
  { role: 'neurologist', roleName: '神经内科医师', permissionKeys: ['dashboard', 'cases', 'viewer', 'analysis', 'report'] },
  { role: 'researcher', roleName: '科研管理员', permissionKeys: ['dashboard', 'cases', 'model'] },
  { role: 'admin', roleName: '超级管理员', permissionKeys: ['dashboard', 'cases', 'viewer', 'analysis', 'report', 'model', 'system'] }
]

export const ALL_PERMISSIONS = [
  { key: 'dashboard', label: '工作台仪表盘', description: '筛查总览与任务统计' },
  { key: 'cases', label: '病例库管理', description: '病例检索、影像上传与批量导入' },
  { key: 'viewer', label: '阅片与AI分析', description: '多模态阅片、ROI 标注、AI 推理' },
  { key: 'analysis', label: 'AI详情与干预方案', description: '量化结果查看、干预方案编辑' },
  { key: 'report', label: '筛查报告', description: '报告预览、模板切换与导出' },
  { key: 'model', label: '模型科研配置', description: '融合策略、阈值与性能监控' },
  { key: 'system', label: '系统权限管理', description: '账号、角色与审计日志' }
]

// ============================================================
// 病例库
// ============================================================
const SURNAMES = ['王', '李', '张', '刘', '陈', '杨', '赵', '黄', '周', '吴', '徐', '孙', '胡', '朱', '高', '林', '何', '郭', '马', '罗']
const GIVEN = ['建国', '淑兰', '桂英', '国强', '秀珍', '德华', '凤兰', '忠义', '玉梅', '福贵', '秀英', '文华', '桂香', '永强', '素芬', '长顺', '玉兰', '志明', '淑珍', '广田']
const DEPTS = ['神经内科', '放射科', '记忆门诊', '老年医学科']

function makeCase(i: number): CaseRecord {
  const rand = mulberry32(20260901 + i * 7)
  const gender: 'M' | 'F' = rand() > 0.48 ? 'M' : 'F'
  const age = 60 + Math.floor(rand() * 29)
  const mr = rand() > 0.08
  const pet = rand() > 0.18
  const modality: Modality = mr && pet ? 'MRI+PET' : mr ? 'MRI' : 'PET'
  const examDate = new Date(Date.now() - Math.floor(rand() * 180) * 86400 * 1000 - Math.floor(rand() * 8) * 3600 * 1000)
  // 风险分布：低 45% / MCI 28% / AD早期 18% / AD中晚期 9%
  const r = rand()
  let riskScore: number
  if (r < 0.45) riskScore = 12 + Math.floor(rand() * 22)
  else if (r < 0.73) riskScore = 38 + Math.floor(rand() * 22)
  else if (r < 0.91) riskScore = 62 + Math.floor(rand() * 18)
  else riskScore = 82 + Math.floor(rand() * 17)
  const hasResult = r > 0.16 // 约 16% 待分析
  const riskLevel: RiskLevel | null = hasResult ? scoreToRiskLevel(riskScore) : null
  let status: CaseStatus = 'completed'
  let diagStatus: DiagStatus = 'AI已分析'
  if (!hasResult) {
    status = 'pending'
    diagStatus = '待AI分析'
  } else if (r > 0.94) {
    status = 'reported'
    diagStatus = '已出报告'
  } else if (r > 0.86) {
    diagStatus = '医生已审核'
  }
  return {
    id: `AD${String(260001 + i)}`,
    patient: { patientNo: `P${String(20260101 + i * 3)}`, name: SURNAMES[Math.floor(rand() * SURNAMES.length)] + GIVEN[Math.floor(rand() * GIVEN.length)], gender, age },
    modality,
    examDate: formatDateTime(examDate),
    department: DEPTS[Math.floor(rand() * DEPTS.length)],
    status,
    diagStatus,
    riskLevel,
    riskScore: hasResult ? riskScore : null,
    hasMRI: mr,
    hasPET: pet,
    createTime: formatDateTime(new Date(examDate.getTime() + 1800 * 1000)),
    cloudSaved: false
  }
}

export const cases: CaseRecord[] = Array.from({ length: 47 }, (_, i) => makeCase(i))

// ============================================================
// AI 分析结果与干预方案（按 caseId 惰性生成/保存）
// ============================================================
const REGION_POOL = ['海马', '颞叶皮层', '内嗅皮层', '顶叶皮层', '后扣带回', '额叶皮层', '楔前叶']
const analysisStore = new Map<string, AnalysisResult>()
const interventionStore = new Map<string, InterventionSection[]>()
const followUpStore = new Map<string, FollowUpPlan>()

/** 依据病例种子生成稳定的 AI 分析结果（未保存过时） */
export function buildAnalysis(c: CaseRecord): AnalysisResult {
  const cached = analysisStore.get(c.id)
  if (cached) return cached
  const seed = strSeed(c.id)
  const rand = mulberry32(seed)
  const score = c.riskScore ?? 50
  const level = scoreToRiskLevel(score)
  const stageMap: Record<RiskLevel, { code: AnalysisResult['stageCode']; stage: string; desc: string }> = {
    low: { code: 'CN', stage: 'CN（认知正常）', desc: '各脑区代谢与形态学指标均在正常范围内，未见显著认知障碍相关影像学征象。' },
    mci: { code: 'MCI', stage: 'MCI（轻度认知障碍）', desc: '内侧颞叶轻度萎缩伴局部代谢减低，符合轻度认知障碍影像学表现，建议定期随访复查。' },
    'ad-early': { code: 'AD-E', stage: 'AD 早期', desc: '双侧海马体积缩小，后扣带回/楔前叶代谢减低，符合阿尔兹海默病早期影像学改变。' },
    'ad-late': { code: 'AD-L', stage: 'AD 中晚期', desc: '广泛皮层萎缩伴多脑区代谢显著减低，脑室扩大，符合阿尔兹海默病中晚期影像学表现。' }
  }
  const st = stageMap[level]
  const regions: AnalysisResult['abnormalRegions'] = []
  const regionCount = level === 'low' ? 0 : level === 'mci' ? 2 : level === 'ad-early' ? 4 : 6
  for (let i = 0; i < regionCount; i++) {
    const region = REGION_POOL[i % REGION_POOL.length]
    regions.push({
      region,
      side: rand() > 0.62 ? '双侧' : rand() > 0.5 ? '左侧' : '右侧',
      atrophy: score > 75 ? '重度萎缩' : score > 55 ? '中度萎缩' : '轻度萎缩',
      metabolism: Number((0.92 - (score / 100) * 0.3 - rand() * 0.06).toFixed(2)),
      zScore: Number((-(1.2 + (score / 100) * 2.2 + rand() * 0.6)).toFixed(1))
    })
  }
  const metrics: AnalysisResult['metrics'] = [
    { key: 'hvL', label: '左侧海马体积', value: Number((2.9 - (score / 100) * 0.9 - rand() * 0.1).toFixed(2)), unit: 'cm³', refRange: '2.6 ~ 3.6', status: score > 55 ? 'abnormal' : score > 35 ? 'warn' : 'normal' },
    { key: 'hvR', label: '右侧海马体积', value: Number((2.95 - (score / 100) * 0.95 - rand() * 0.1).toFixed(2)), unit: 'cm³', refRange: '2.6 ~ 3.6', status: score > 55 ? 'abnormal' : score > 35 ? 'warn' : 'normal' },
    { key: 'suv', label: '全脑平均代谢 SUV', value: Number((1.12 - (score / 100) * 0.3 + rand() * 0.04).toFixed(2)), unit: 'SUV', refRange: '0.95 ~ 1.25', status: score > 62 ? 'abnormal' : 'normal' },
    { key: 'cort', label: '皮层平均厚度', value: Number((2.9 - (score / 100) * 0.55 + rand() * 0.08).toFixed(2)), unit: 'mm', refRange: '2.6 ~ 3.2', status: score > 70 ? 'abnormal' : score > 45 ? 'warn' : 'normal' },
    { key: 'ven', label: '脑室体积', value: Number((24 + (score / 100) * 26 + rand() * 4).toFixed(1)), unit: 'cm³', refRange: '< 38', status: score > 60 ? 'abnormal' : score > 40 ? 'warn' : 'normal' },
    { key: 'mta', label: 'MTA 视觉评分', value: score > 75 ? 3 : score > 55 ? 2 : score > 35 ? 1 : 0, unit: '级', refRange: '0 ~ 1', status: score > 55 ? 'abnormal' : score > 35 ? 'warn' : 'normal' },
    { key: 'wmh', label: '白质高信号 Fazekas', value: rand() > 0.6 ? 1 : 0, unit: '级', refRange: '0', status: 'normal' }
  ]
  const result: AnalysisResult = {
    caseId: c.id,
    riskScore: score,
    riskLevel: level,
    stage: st.stage,
    stageCode: st.code,
    stageDesc: st.desc,
    confidence: Number((0.86 + rand() * 0.12).toFixed(3)),
    hippocampusVolumeL: metrics[0].value,
    hippocampusVolumeR: metrics[1].value,
    meanSUV: metrics[2].value,
    corticalThickness: metrics[3].value,
    ventricleVolume: metrics[4].value,
    mtaScore: String(metrics[5].value),
    abnormalRegions: regions,
    metrics,
    summary: `多模态融合模型（${modelConfig.snapshotVersion}）对 ${c.patient.name}（${c.patient.gender === 'M' ? '男' : '女'}，${c.patient.age} 岁）的 MRI+PET 影像完成特征级联合分析。综合海马体积、皮层厚度及 FDG 代谢分布，${st.desc}AI 综合风险评分 ${score}/100，置信度 ${((0.86 + rand() * 0.12) * 100).toFixed(1)}%，建议结合临床量表（MMSE/MoCA）由专科医师审核确认。`,
    modelVersion: modelConfig.snapshotVersion,
    fusionStrategy: modelConfig.strategy,
    inferenceTime: Number((2.4 + rand() * 1.6).toFixed(1)),
    finishTime: formatDateTime(new Date())
  }
  analysisStore.set(c.id, result)
  return result
}

export function saveAnalysis(c: CaseRecord, result: AnalysisResult): void {
  analysisStore.set(c.id, result)
  c.riskLevel = result.riskLevel
  c.riskScore = result.riskScore
  c.status = 'completed'
  if (c.diagStatus === '待AI分析' || c.diagStatus === 'AI分析中') c.diagStatus = 'AI已分析'
}

export function getInterventions(c: CaseRecord): InterventionSection[] {
  const cached = interventionStore.get(c.id)
  if (cached) return cached
  const a = buildAnalysis(c)
  const level = a.riskLevel
  const sections: InterventionSection[] = [
    {
      key: 'cognitive',
      title: '认知干预',
      items: [
        { id: `${c.id}-cg-1`, title: '认知功能训练', content: level === 'low' ? '维持性认知训练：每周 3 次记忆卡片与图形推理练习，每次 30 分钟。' : '系统性认知训练：记忆策略训练（联想/位置法）联合执行功能任务，每周 5 次，每次 40 分钟，4 周为一疗程。', frequency: level === 'low' ? '每周 3 次' : '每周 5 次', duration: level === 'low' ? '30 分钟/次' : '40 分钟/次', source: 'AI' },
        { id: `${c.id}-cg-2`, title: '计算机辅助认知康复', content: '基于认知康复平台的注意力与工作记忆自适应训练，难度随正确率动态调整。', frequency: '每周 3 次', duration: '25 分钟/次', source: 'AI' }
      ]
    },
    {
      key: 'lifestyle',
      title: '生活方式干预',
      items: [
        { id: `${c.id}-ls-1`, title: '有氧运动方案', content: '中等强度有氧运动（快走/游泳），靶心率 =（220-年龄）×60%，单次持续 30 分钟以上。', frequency: '每周 5 次', duration: '30-40 分钟/次', source: 'AI' },
        { id: `${c.id}-ls-2`, title: '地中海饮食指导', content: '增加深海鱼类、坚果、橄榄油摄入，控制精制糖与饱和脂肪；合并高血压者限盐 < 5g/日。', frequency: '每日执行', duration: '长期维持', source: 'AI' },
        { id: `${c.id}-ls-3`, title: '睡眠与情绪管理', content: '维持 22:30 前入睡，保证 6.5-8 小时睡眠；PHQ-9 评分 ≥ 5 时转介心理干预。', frequency: '每日', duration: '长期维持', source: 'AI' }
      ]
    },
    {
      key: 'followup',
      title: '随访复查',
      items: [
        { id: `${c.id}-fu-1`, title: '认知量表随访', content: 'MMSE、MoCA、ADL 量表复评，绘制认知轨迹曲线；MCI 及以上等级每 3 个月复评。', frequency: level === 'low' ? '每 12 个月' : '每 3 个月', duration: '40 分钟/次', source: 'AI' },
        { id: `${c.id}-fu-2`, title: '影像学复查', content: level === 'low' ? '年度常规头颅 MRI 复查。' : '6 个月后复查头颅 MRI（海马定量）± FDG-PET，对比脑萎缩进展速率。', frequency: level === 'low' ? '每 12 个月' : '每 6 个月', duration: '按检查科室安排', source: 'AI' },
        { id: `${c.id}-fu-3`, title: '血液生物标志物', content: '血浆 Aβ42/40、p-tau181 检测，动态监测 AD 相关病理改变。', frequency: '每 6 个月', duration: '空腹采血', source: 'AI' }
      ]
    },
    {
      key: 'clinical',
      title: '临床参考',
      items: [
        { id: `${c.id}-cl-1`, title: '专科门诊转介', content: level === 'low' ? '维持记忆门诊常规随访。' : '建议神经内科记忆障碍专科门诊就诊，评估胆碱酯酶抑制剂（多奈哌齐/卡巴拉汀）用药指征。', frequency: level === 'low' ? '每年 1 次' : '2 周内就诊', duration: '按医嘱', source: 'AI' },
        { id: `${c.id}-cl-2`, title: '用药安全提示', content: '避免苯二氮卓类及抗胆碱能药物长期使用；就诊时向医师出示完整用药清单。', frequency: '持续关注', duration: '长期', source: 'AI' },
        { id: `${c.id}-cl-3`, title: '照护者教育', content: '向家属发放 AD 照护手册，进行居家安全（防走失/防跌倒）与环境改造指导。', frequency: '首次宣教 + 随访强化', duration: '30 分钟/次', source: 'AI' }
      ]
    }
  ]
  interventionStore.set(c.id, sections)
  return sections
}

export function saveInterventions(caseId: string, sections: InterventionSection[]): void {
  interventionStore.set(caseId, sections)
}

export function getFollowUp(c: CaseRecord): FollowUpPlan {
  const cached = followUpStore.get(c.id)
  if (cached) return cached
  const plan: FollowUpPlan = {
    cycleMonths: 6,
    nextDate: formatDateTime(new Date(Date.now() + 180 * 86400 * 1000)).slice(0, 10),
    reminders: [
      formatDateTime(new Date(Date.now() + 180 * 86400 * 1000)).slice(0, 10),
      formatDateTime(new Date(Date.now() + 174 * 86400 * 1000)).slice(0, 10)
    ],
    note: '随访内容：认知量表复评 + 头颅 MRI 海马定量'
  }
  followUpStore.set(c.id, plan)
  return plan
}

export function saveFollowUp(caseId: string, plan: FollowUpPlan): void {
  followUpStore.set(caseId, plan)
}

// ============================================================
// 模型科研配置（默认值取自本项目 75 模型快照集成真实结果：5 seeds × 5 folds × 3 snapshots）
// ============================================================
export const modelConfig: ModelConfig = {
  strategy: 'feature',
  mriWeight: 0.55,
  riskThreshold: 0.8,
  roiRegions: ['海马', '颞叶皮层', '内嗅皮层', '后扣带回'],
  snapshotVersion: 'TransMF-15ens-v4',
  updatedAt: '2026-09-02 15:20:00',
  updatedBy: 'sci01'
}

export const foldMetrics: FoldMetric[] = [
  { fold: 0, auc: 0.9061, acc: 0.851, sen: 0.862, spe: 0.84, f1: 0.833 },
  { fold: 1, auc: 0.902, acc: 0.851, sen: 0.848, spe: 0.855, f1: 0.821 },
  { fold: 2, auc: 0.8444, acc: 0.776, sen: 0.812, spe: 0.748, f1: 0.756 },
  { fold: 3, auc: 0.933, acc: 0.88, sen: 0.906, spe: 0.86, f1: 0.869 },
  { fold: 4, auc: 0.9365, acc: 0.902, sen: 0.894, spe: 0.909, f1: 0.878 }
]

export const trainCurve: TrainCurvePoint[] = Array.from({ length: 50 }, (_, i) => {
  const rand = mulberry32(i * 13 + 5)
  const p = i / 49
  return {
    epoch: i + 1,
    trainLoss: Number((0.62 * Math.exp(-2.6 * p) + 0.08 + rand() * 0.02).toFixed(4)),
    valAuc: Number((0.72 + 0.19 * (1 - Math.exp(-3.2 * p)) + rand() * 0.012).toFixed(4))
  }
})

// ============================================================
// 日志审计
// ============================================================
export const inferenceLogs: InferenceLog[] = cases
  .filter((c) => c.riskScore !== null)
  .slice(0, 26)
  .map((c, i) => ({
    id: `INF${String(90210 + i)}`,
    caseId: c.id,
    patientName: c.patient.name,
    modelVersion: modelConfig.snapshotVersion,
    strategy: modelConfig.strategy,
    durationSec: Number((2.4 + mulberry32(i)() * 1.8).toFixed(1)),
    riskScore: c.riskScore ?? 0,
    status: i === 17 ? '失败' : '成功',
    operator: ['rad01', 'neu01', 'sci01'][i % 3],
    time: c.examDate
  }))

export const systemLogs: SystemLog[] = [
  { id: 'S1', module: '系统管理', action: '新增用户', operator: 'admin', role: '超级管理员', ip: '192.168.3.21', result: '成功', time: '2026-09-06 10:12:33', detail: '新增账号 neu02（神经内科医师）' },
  { id: 'S2', module: '模型配置', action: '修改风险阈值', operator: 'sci01', role: '科研管理员', ip: '192.168.3.45', result: '成功', time: '2026-09-06 09:02:11', detail: '高风险阈值 0.75 → 0.80' },
  { id: 'S3', module: '报告管理', action: '导出报告 PDF', operator: 'rad01', role: '放射科医师', ip: '192.168.3.88', result: '成功', time: '2026-09-05 16:44:02', detail: '病例 AD260008 报告导出' },
  { id: 'S4', module: '系统管理', action: '禁用用户', operator: 'admin', role: '超级管理员', ip: '192.168.3.21', result: '成功', time: '2026-09-04 11:20:47', detail: '禁用账号 rad03' },
  { id: 'S5', module: '认证登录', action: '用户登录', operator: 'neu01', role: '神经内科医师', ip: '192.168.3.102', result: '失败', time: '2026-09-04 08:55:19', detail: '密码错误连续 2 次' }
]

export const caseLogs: CaseLog[] = cases.slice(0, 14).map((c, i) => ({
  id: `CL${String(51001 + i)}`,
  caseId: c.id,
  patientName: c.patient.name,
  action: ['上传 MRI/PET 影像', '启动 AI 分析', '保存 AI 分析结果', '生成筛查报告', '编辑干预方案'][i % 5],
  operator: ['rad01', 'neu01', 'rad01', 'rad01', 'neu01'][i % 5],
  time: c.examDate,
  detail: `病例 ${c.id}（${c.patient.name}）由 rad01 完成【${['上传 MRI/PET 影像', '启动 AI 分析', '保存 AI 分析结果', '生成筛查报告', '编辑干预方案'][i % 5]}】`
}))

// ============================================================
// 工作台统计
// ============================================================
export function dashboardStats(): DashboardStats {
  const pending = cases.filter((c) => c.status === 'pending').length
  const completed = cases.filter((c) => c.status === 'completed' || c.status === 'reported').length
  const highRisk = cases.filter((c) => c.riskLevel === 'ad-early' || c.riskLevel === 'ad-late').length
  return {
    pendingCases: pending,
    completedScreening: completed,
    highRiskCases: highRisk,
    todayInference: inferenceLogs.filter((l) => l.time.slice(0, 10) === formatDate(new Date())).length + 26,
    pendingDelta: -2,
    completedDelta: 12,
    highRiskDelta: 3,
    todayDelta: 8
  }
}

export function trendData(): TrendPoint[] {
  const rand = mulberry32(88)
  return Array.from({ length: 6 }, (_, i) => {
    const d = new Date()
    d.setMonth(d.getMonth() - (5 - i))
    return {
      month: `${d.getMonth() + 1}月`,
      total: 150 + Math.floor(rand() * 60) + i * 18,
      highRisk: 18 + Math.floor(rand() * 12) + i * 4
    }
  })
}

export function riskDistribution(): RiskDistItem[] {
  return [
    { key: 'low', name: '低风险', value: cases.filter((c) => c.riskLevel === 'low').length },
    { key: 'mci', name: '轻度认知障碍', value: cases.filter((c) => c.riskLevel === 'mci').length },
    { key: 'ad-early', name: 'AD 早期', value: cases.filter((c) => c.riskLevel === 'ad-early').length },
    { key: 'ad-late', name: 'AD 中晚期', value: cases.filter((c) => c.riskLevel === 'ad-late').length }
  ]
}

export function recentTasks(): TaskItem[] {
  return cases.slice(0, 8).map((c, i) => ({
    id: `T${9000 + i}`,
    caseId: c.id,
    patientName: c.patient.name,
    type: (['初筛分析', '复筛分析', '随访复筛', '报告生成'] as const)[i % 4],
    status: c.status === 'pending' ? '排队中' : i === 1 ? '执行中' : '已完成',
    operator: ['rad01', 'neu01'][i % 2],
    time: c.examDate
  }))
}

/** 看板增强分布统计（Mock） */
export function dashboardDistributions(): DashboardDistribution {
  const scored = cases.filter((c) => c.riskScore !== null && c.riskScore !== undefined)
  const avg = scored.length ? Math.round(scored.reduce((s, c) => s + (c.riskScore ?? 0), 0) / scored.length * 10) / 10 : 0
  const modalityDistribution = [
    { name: '仅 MRI', value: cases.filter((c) => c.modality === 'MRI').length, key: 'MRI' },
    { name: '仅 PET', value: cases.filter((c) => c.modality === 'PET').length, key: 'PET' },
    { name: 'MRI+PET 多模态', value: cases.filter((c) => c.modality === 'MRI+PET').length, key: 'MRI+PET' }
  ]
  const deptMap = new Map<string, number>()
  for (const c of cases) deptMap.set(c.department, (deptMap.get(c.department) ?? 0) + 1)
  const departmentDistribution = Array.from(deptMap.entries())
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 6)
  return {
    avgRiskScore: avg,
    scoredCount: scored.length,
    modalityDistribution,
    departmentDistribution,
    reviewPending: 2
  }
}

/** 取当前数组中某前缀 id 的最大数字后缀 + 1（避免删除数据后 id 冲突） */
function nextMockId(list: { id: string }[], prefix: string, start: number): string {
  let max = start - 1
  for (const item of list) {
    if (typeof item.id === 'string' && item.id.startsWith(prefix)) {
      const tail = item.id.slice(prefix.length)
      if (/^\d+$/.test(tail)) max = Math.max(max, parseInt(tail, 10))
    }
  }
  return `${prefix}${max + 1}`
}

/** 新增病例（上传/批量导入共用） */
export function addCase(payload: Omit<CaseRecord, 'id' | 'createTime' | 'cloudSaved'>): CaseRecord {
  const c: CaseRecord = {
    ...payload,
    id: nextMockId(cases, 'AD', 260001),
    createTime: formatDateTime(new Date()),
    cloudSaved: false
  }
  cases.unshift(c)
  return c
}

/** 记录一次推理日志 */
export function pushInferenceLog(log: InferenceLog): void {
  inferenceLogs.unshift(log)
}

/** 记录病例操作 */
export function pushCaseLog(caseId: string, patientName: string, action: string, operator: string, detail: string): void {
  caseLogs.unshift({
    id: nextMockId(caseLogs, 'CL', 51001),
    caseId,
    patientName,
    action,
    operator,
    time: formatDateTime(new Date()),
    detail
  })
}
