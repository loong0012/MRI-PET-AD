import type { CaseCommentItem, CommentCreateBody } from '@/types/comment'
import { httpGet, httpPost, httpDelete } from '@/utils/request'

/**
 * 病例评论/会诊讨论 API
 * - GET    /comment/{case_id}          获取病例评论列表（按时间正序）
 * - POST   /comment/{case_id}          新增评论（支持回复）
 * - DELETE /comment/{comment_id}       删除评论（本人或 admin）
 */

/** 获取病例评论列表 */
export function apiListComments(caseId: string): Promise<CaseCommentItem[]> {
  return httpGet<CaseCommentItem[]>(`/comment/${caseId}`)
}

/** 新增评论（parentId=0 为顶级评论，>0 为回复指定评论） */
export function apiCreateComment(
  caseId: string,
  body: CommentCreateBody
): Promise<{ id: number }> {
  return httpPost<{ id: number }>(`/comment/${caseId}`, body)
}

/** 删除评论（仅本人或 admin） */
export function apiDeleteComment(commentId: number): Promise<void> {
  return httpDelete<void>(`/comment/${commentId}`)
}
