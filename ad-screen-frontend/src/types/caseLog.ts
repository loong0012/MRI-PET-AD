/**
 * 病例操作审计日志类型定义
 */

/** 操作类型字典项 */
export interface ActionOption {
  value: string
  label: string
  color: string
  bg: string
}

/** 单条操作日志 */
export interface CaseLogItem {
  id: string
  caseId: string
  patientName: string
  action: string
  actionLabel: string
  actionColor: string
  actionBg: string
  operator: string
  time: string
  detail: string
}

/** 查询参数 */
export interface CaseLogQuery {
  keyword: string
  action: string
  operator: string
  dateRange: string[] | null
  page: number
  pageSize: number
}

/** 分页结果 */
export interface CaseLogPage {
  total: number
  list: CaseLogItem[]
}
