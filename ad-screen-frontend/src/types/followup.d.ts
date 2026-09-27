/**
 * 随访管理相关类型（对应后端 routers/followup.py）
 */

/** 随访执行记录 */
export interface FollowUpVisit {
  id: number
  caseId: string
  patientName: string
  visitDate: string
  visitType: string
  mmse: number | null
  moca: number | null
  cognitionChange: string
  medicationAdherence: string
  adverseEvent: string
  notes: string
  nextDate: string
  operator: string
  createdAt: string
}

/** 随访工作台条目 */
export interface FollowUpWorkItem {
  caseId: string
  patientName: string
  gender: string
  age: number | null
  department: string
  caseStatus: string
  riskLevel: string | null
  riskScore: number | null
  cycleMonths: number
  nextDate: string
  /** 距下次随访天数（负=已逾期） */
  daysDiff: number | null
  note: string
  visitCount: number
  lastVisit: FollowUpVisit | null
}

/** 工作台汇总 */
export interface FollowUpSummary {
  overdue: number
  due7: number
  due30: number
  total: number
}

/** 记录随访问卷入参 */
export interface FollowUpVisitPayload {
  visitDate: string
  visitType: string
  mmse: number | null
  moca: number | null
  cognitionChange: string
  medicationAdherence: string
  adverseEvent: string
  notes: string
  nextDate: string
  operator: string
}
