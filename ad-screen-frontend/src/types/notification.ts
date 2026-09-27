/**
 * 站内通知类型定义
 */

/** 通知类型：followup=随访提醒 review=审核待办 system=系统公告 announce=全员公告 */
export type NotificationType = 'followup' | 'review' | 'system' | 'announce'

/** 通知项 */
export interface NotificationItem {
  id: number
  type: NotificationType
  title: string
  content: string
  /** 前端跳转路由 */
  link: string
  isRead: boolean
  createdAt: string
}

/** 未读徽标数据 */
export interface UnreadInfo {
  count: number
  /** 是否存在未读随访提醒（徽标红色高亮） */
  hasFollowUp: boolean
}

/** 系统公告（admin 管理） */
export interface Announcement {
  id: number
  title: string
  content: string
  publisher: string
  isActive: boolean
  createdAt: string
  revokedAt: string
}
