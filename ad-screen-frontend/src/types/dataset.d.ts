/** 科研数据集导出格式 */
export type DatasetFormat = 'json' | 'csv'

/** 科研数据集导出请求参数 */
export interface DatasetExportParams {
  /** 要导出的病例 ID 列表 */
  caseIds: string[]
  /** 是否包含 ROI 标注 */
  includeAnnotations: boolean
  /** 是否包含影像文件路径 */
  includeImaging: boolean
  /** 导出格式：json / csv */
  format: DatasetFormat
}
