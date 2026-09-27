/**
 * 患者教育 / AD 科普知识库类型定义
 */

/** 科普文章分类 */
export interface KnowledgeCategory {
  key: string
  name: string
  icon: string
  count: number
}

/** 科普文章（列表项不含 content，详情含 content） */
export interface KnowledgeArticle {
  id: number
  category: string
  title: string
  summary: string
  content: string
  tags: string
  icon: string
  createdAt: string
}
