import { httpGet, httpPut } from '@/utils/request'
import type {
  UserProfile, ProfileUpdatePayload, ProfileUpdateResult,
  PasswordChangePayload, ProfileStats, ActivityItem
} from '@/types/user'

/** 获取当前登录用户的完整个人档案 */
export function apiGetProfile(): Promise<UserProfile> {
  return httpGet<UserProfile>('/auth/profile')
}

/** 更新个人资料（返回新档案 + 新 token） */
export function apiUpdateProfile(payload: ProfileUpdatePayload): Promise<ProfileUpdateResult> {
  return httpPut<ProfileUpdateResult>('/auth/profile', payload)
}

/** 修改密码（成功后需重新登录） */
export function apiChangePassword(payload: PasswordChangePayload): Promise<void> {
  return httpPut<void>('/auth/password', payload)
}

/** 个人临床工作统计 */
export function apiGetProfileStats(): Promise<ProfileStats> {
  return httpGet<ProfileStats>('/auth/profile/stats')
}

/** 个人近期活动记录 */
export function apiGetProfileActivities(): Promise<ActivityItem[]> {
  return httpGet<ActivityItem[]>('/auth/profile/activities')
}
