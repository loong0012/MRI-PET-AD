import { httpGet, httpPost, httpDownload } from '@/utils/request'
import { formatDate } from '@/utils/format'

/**
 * 数据备份与恢复 API（admin）
 */

/** 服务器存档备份文件 */
export interface BackupFile {
  filename: string
  size: number
  createdAt: string
}

/** 数据库信息 */
export interface BackupInfo {
  dbSize: number
  tableCounts: Record<string, number>
  backupCount: number
  backups: string[]
}

/** 数据库大小与各表行数 */
export function apiBackupInfo(): Promise<BackupInfo> {
  return httpGet<BackupInfo>('/system/backup/info')
}

/** 生成一致性备份并下载（同时存档到服务器 backups/） */
export function apiBackupDownload(): Promise<void> {
  return httpDownload('/system/backup/download', `ad_screen_backup_${formatDate(new Date())}.db`)
}

/** 服务器存档备份列表 */
export function apiBackupFiles(): Promise<BackupFile[]> {
  return httpGet<BackupFile[]>('/system/backup/files')
}

/** 从存档恢复（恢复前自动再备份当前库） */
export function apiBackupRestore(filename: string): Promise<{ preRestoreFile: string }> {
  return httpPost<{ preRestoreFile: string }>('/system/backup/restore', { filename })
}
