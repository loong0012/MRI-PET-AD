/**
 * 数据脱敏审计与导出合规 API
 * 对应后端 routers/privacy.py
 */
import { httpPost } from '@/utils/request'
import type {
  AuditParams,
  AuditReport,
  MaskParams,
  MaskResult
} from '@/types/privacy'

/**
 * 脱敏审计：导出前扫描 PHI 字段命中 + k-匿名检查 + 合规评级
 * - 扫描 patient_json 中的 PHI 字段（姓名/身份证/电话/住址/患者编号/出生日期）
 * - 按 (性别 × 年龄段 × 队列) 分组做 k-匿名检查（k=5）
 * - 综合合规评级：green 可直接导出 / yellow 需脱敏 / red k-匿名违反
 */
export function apiPrivacyAudit(params: AuditParams): Promise<AuditReport> {
  return httpPost<AuditReport>('/privacy/audit', params)
}

/**
 * 应用脱敏规则：对选中病例的 patient_json 应用脱敏，返回脱敏后的数据
 * 用于实际导出时应用脱敏：fields 指定要脱敏的字段键集合，空=全部 PHI 命中字段
 */
export function apiPrivacyMask(params: MaskParams): Promise<MaskResult> {
  return httpPost<MaskResult>('/privacy/mask', params)
}
