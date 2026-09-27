import { httpPost } from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { DatasetExportParams } from '@/types/dataset'

/**
 * 导出科研数据集
 * 后端返回文件流（JSON 数组 / CSV），前端拿到 Blob 后自行触发下载。
 * 注意：responseType 设为 'blob' 时，request.ts 响应拦截器会直接返回完整 AxiosResponse，
 *       因此这里取 .data 即为 Blob。
 */
export function apiExportDataset(params: DatasetExportParams): Promise<Blob> {
  return httpPost<AxiosResponse<Blob>>('/dataset/export', params, {
    responseType: 'blob'
  }).then((resp) => resp.data)
}
