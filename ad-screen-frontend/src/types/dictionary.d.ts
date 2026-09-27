/**
 * 数据字典类型定义
 * 医学术语与系统字段说明库
 */

/** 字典分类 */
export interface DictCategory {
  key: string
  name: string
  icon: string
  desc: string
}

/** 字典字段项 */
export interface DictItem {
  key: string
  category: string
  name: string
  type: string
  example: string
  desc: string
  refRange?: string
  standard?: string
  enum?: string[]
}
