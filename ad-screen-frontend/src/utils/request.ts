import axios, { AxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'
import { getToken, clearToken } from './auth'

/**
 * 统一后端响应结构（对接后端时按此约定返回）
 * { code: number, message: string, data: T }
 */
export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

/** 后端约定：code === 200 表示业务成功 */
const SUCCESS_CODE = 200

/** 401 跳转去重：过期瞬间可能有 N 个在途请求同时 401，只提示/跳转一次 */
let isRedirectingToLogin = false

/** 从后端错误体取真实消息（FastAPI HTTPException 返回 {detail}，统一响应返回 {message}） */
function extractErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === 'object') {
    const d = data as { message?: unknown; detail?: unknown }
    if (typeof d.message === 'string' && d.message) return d.message
    if (typeof d.detail === 'string' && d.detail) return d.detail
  }
  return fallback
}

/** 给已统一提示过的错误打标，调用方可据此跳过重复 toast */
function markHandled<T extends Error>(err: T): T {
  ;(err as Error & { __handled?: boolean }).__handled = true
  return err
}

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 30000
})

// ---------- 请求拦截：附加 Token ----------
request.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/**
 * 解析 blob 形式返回的 JSON 错误体。
 * 背景：responseType=blob 时，即便后端返回 {code,message,data} 业务错误
 * （HTTP 200 或 4xx），axios 拿到的也是 Blob；不识别就会把错误 JSON 当成
 * 文件保存，用户下载到损坏文件且看不到任何提示。
 */
async function parseBlobError(blob: Blob): Promise<string | null> {
  if (!blob.type.includes('application/json')) return null
  try {
    const body = JSON.parse(await blob.text()) as { message?: unknown; detail?: unknown }
    if (typeof body.message === 'string' && body.message) return body.message
    if (typeof body.detail === 'string' && body.detail) return body.detail
    return null
  } catch {
    return null
  }
}

// ---------- 响应拦截：统一解包与错误处理 ----------
request.interceptors.response.use(
  async (response: AxiosResponse<ApiResponse<unknown> | Blob>) => {
    // 文件流下载（blob）不经业务码解包，但必须拦截伪装成 blob 的 JSON 业务错误
    if (response.config.responseType === 'blob') {
      if (response.data instanceof Blob) {
        const errMsg = await parseBlobError(response.data)
        if (errMsg !== null) {
          ElMessage.error(errMsg)
          // 已统一提示，打 __handled 避免调用方 catch 再弹一次
          return Promise.reject(markHandled(new Error(errMsg)))
        }
      }
      return response
    }
    const body = response.data as ApiResponse<unknown>
    if (body.code !== SUCCESS_CODE) {
      const msg = body.message || '请求失败，请稍后重试'
      ElMessage.error(msg)
      // 附带业务码（如 404），调用方可据此区分"资源不存在"与网络/服务异常
      const err = markHandled(new Error(msg))
      ;(err as Error & { code?: number }).code = body.code
      return Promise.reject(err)
    }
    // 直接把业务数据返回给调用方
    return body.data as unknown as AxiosResponse
  },
  (error: AxiosError<ApiResponse<unknown> | Blob>) => {
    // 请求被主动取消 / 页面卸载或 HMR 全量刷新导致在途 XHR 中断：
    // 浏览器网络栈会记 net::ERR_ABORTED，此处不再弹任何错误提示
    if (axios.isCancel(error) || error.code === 'ERR_CANCELED') {
      return Promise.reject(error)
    }
    if (error.response?.status === 401) {
      clearToken()
      // 登出时令牌可能已因改密而失效（401 属预期），不弹"过期"提示；
      // 多个在途请求并发 401 时只提示一次、只跳转一次
      const isLogoutCall = error.config?.url?.includes('/auth/logout') ?? false
      if (!isLogoutCall && !isRedirectingToLogin) {
        isRedirectingToLogin = true
        ElMessage.error('登录已过期，请重新登录')
        // 延迟跳转，避免与当前渲染冲突
        setTimeout(() => {
          window.location.href = '/login'
        }, 600)
      }
    } else {
      const respData = error.response?.data
      if (respData instanceof Blob) {
        // blob 请求的 HTTP 错误体也是 Blob，异步解析出后端真实错误消息
        void parseBlobError(respData).then((msg) => {
          ElMessage.error(msg ?? '网络异常，请检查连接')
        })
      } else {
        ElMessage.error(extractErrorMessage(respData, '网络异常，请检查连接'))
      }
    }
    return Promise.reject(markHandled(error))
  }
)

/** GET 请求（返回解包后的业务数据） */
export function httpGet<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  return request.get(url, { params }) as Promise<T>
}

/** POST 请求（返回解包后的业务数据） */
export function httpPost<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return request.post(url, data, config) as Promise<T>
}

/** PUT 请求（返回解包后的业务数据） */
export function httpPut<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return request.put(url, data, config) as Promise<T>
}

/** DELETE 请求（返回解包后的业务数据） */
export function httpDelete<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return request.delete(url, { data, ...config }) as Promise<T>
}

/** POST 文件下载（返回 Blob，自动触发浏览器保存） */
export async function httpDownload(url: string, fallbackName: string, body?: unknown): Promise<void> {
  const resp = (await request.post(url, body ?? {}, { responseType: 'blob' })) as unknown as AxiosResponse<Blob>
  const disposition = resp.headers['content-disposition'] as string | undefined
  let filename = fallbackName
  if (disposition) {
    const match = /filename\*?=(?:UTF-8'')?["']?([^;"']+)/i.exec(disposition)
    if (match) {
      // 畸形编码（如裸 %）会让 decodeURIComponent 抛 URIError，此时回退默认文件名
      try {
        filename = decodeURIComponent(match[1])
      } catch {
        filename = fallbackName
      }
    }
  }
  const blobUrl = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = blobUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(blobUrl)
}

export default request
