/** 系统操作日志 */
export interface SystemLog {
  id: string
  module: string
  action: string
  operator: string
  role: string
  ip: string
  result: '成功' | '失败'
  time: string
  detail: string
}

/** 病例操作记录 */
export interface CaseLog {
  id: string
  caseId: string
  patientName: string
  action: string
  operator: string
  time: string
  detail: string
}
