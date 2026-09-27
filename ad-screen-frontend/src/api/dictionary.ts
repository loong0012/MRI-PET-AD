/**
 * 数据字典 API（全角色）
 * 医学术语与系统字段说明库，按模块分类
 */
import type { DictCategory, DictItem } from '@/types/dictionary.d'
import { httpGet } from '@/utils/request'

/** 获取所有分类 */
export function apiGetCategories(): Promise<DictCategory[]> {
  return httpGet<DictCategory[]>('/dictionary/categories')
}

/** 获取字段列表（可按分类+关键词过滤） */
export function apiListItems(category = '', keyword = ''): Promise<DictItem[]> {
  return httpGet<DictItem[]>('/dictionary/items', { category, keyword })
}

/** 单条字段详情 */
export function apiGetItem(key: string): Promise<DictItem> {
  return httpGet<DictItem>(`/dictionary/items/${key}`)
}
