/**
 * 病例评论/会诊讨论类型定义
 */

/** 单条病例评论（支持回复，parentId 指向被回复评论 id，0 表示顶级评论） */
export interface CaseCommentItem {
  id: number
  caseId: string
  /** 用户名（账号） */
  user: string
  /** 真实姓名 */
  realName: string
  /** 评论内容 */
  content: string
  /** 回复的评论 id（0=顶级评论） */
  parentId: number
  /** 创建时间（YYYY-MM-DD HH:mm:ss） */
  createdAt: string
}

/** 新增评论请求体 */
export interface CommentCreateBody {
  content: string
  parentId: number
}
