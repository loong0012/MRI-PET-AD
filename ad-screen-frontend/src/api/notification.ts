import type { NotificationItem, UnreadInfo, Announcement } from '@/types/notification'
import { httpGet, httpPost } from '@/utils/request'

/**
 * 站内通知中心 API（全角色）
 * 消息由后端拉取时按角色懒生成（随访到期 / 审核待办），幂等去重。
 */

/** 最近通知列表 */
export function apiGetNotifications(onlyUnread = false, limit = 20): Promise<NotificationItem[]> {
  return httpGet<NotificationItem[]>('/notification/list', {
    onlyUnread: String(onlyUnread),
    limit: String(limit)
  })
}

/** 未读数（顶栏徽标） */
export function apiGetUnreadCount(): Promise<UnreadInfo> {
  return httpGet<UnreadInfo>('/notification/unread-count')
}

/** 标记单条已读 */
export function apiMarkRead(id: number): Promise<void> {
  return httpPost<void>(`/notification/${id}/read`)
}

/** 全部已读 */
export function apiMarkAllRead(): Promise<void> {
  return httpPost<void>('/notification/read-all')
}

/** 发布系统公告（admin，全员通知） */
export function apiPublishAnnouncement(payload: { title: string; content: string }): Promise<{ id: number; receivers: number }> {
  return httpPost<{ id: number; receivers: number }>('/notification/announce', payload)
}

/** 公告列表（admin） */
export function apiListAnnouncements(): Promise<Announcement[]> {
  return httpGet<Announcement[]>('/notification/announcements')
}

/** 下线公告（admin） */
export function apiRevokeAnnouncement(id: number): Promise<void> {
  return httpPost<void>(`/notification/announce/${id}/revoke`)
}
