/**
 * 短期媒体票据管理
 * ------------------------------------------------------------------
 * <img src>（影像切片/Grad-CAM）与 EventSource（SSE 推理流）无法携带
 * Authorization 请求头，历史做法是把长效会话 JWT 拼在 URL query 上，
 * 会落入访问日志、浏览器历史与 Referer。改为向后端换取 10 分钟有效的
 * typ=media 专用票据，并在到期前自动续期。
 *
 * - 同步 getMediaToken()：供 URL 拼装函数使用，仅读缓存（不发请求）
 * - 异步 ensureMediaToken()：保证拿到有效票据（带并发去重）
 * - 布局层在登录后预取并定时续期，因此同步读取时缓存通常已就绪
 */
import { httpGet } from './request'

const MEDIA_TOKEN_KEY = 'adscreen_media_token'
const MEDIA_TOKEN_EXP_KEY = 'adscreen_media_token_exp'
/** 提前 60 秒视为过期，避免临界时间内发出的请求在服务端失效 */
const EXPIRE_SAFETY_MS = 60 * 1000

interface MediaTokenResp {
  token: string
  /** 有效期（秒） */
  expiresIn: number
}

/** 续票中的并发去重 Promise，避免短时间内多个组件同时换票 */
let inflight: Promise<string> | null = null

function readCache(): string {
  const exp = Number(localStorage.getItem(MEDIA_TOKEN_EXP_KEY) ?? 0)
  if (!exp || Date.now() > exp - EXPIRE_SAFETY_MS) return ''
  return localStorage.getItem(MEDIA_TOKEN_KEY) ?? ''
}

/** 同步读取当前有效媒体票据；未取得或临近过期时返回空串 */
export function getMediaToken(): string {
  return readCache()
}

/** 清除媒体票据缓存（退出登录时调用） */
export function clearMediaToken(): void {
  localStorage.removeItem(MEDIA_TOKEN_KEY)
  localStorage.removeItem(MEDIA_TOKEN_EXP_KEY)
  inflight = null
}

/**
 * 保证持有有效媒体票据：缓存有效直接复用，否则请求 /auth/media-token 换取。
 * 多个调用方并发时共享同一次请求。
 */
export function ensureMediaToken(): Promise<string> {
  // Mock 模式无后端鉴权，直接返回空串（调用方拼 URL 时会跳过凭证）
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve('')
  const cached = readCache()
  if (cached) return Promise.resolve(cached)
  if (inflight) return inflight
  inflight = httpGet<MediaTokenResp>('/auth/media-token')
    .then((data) => {
      localStorage.setItem(MEDIA_TOKEN_KEY, data.token)
      localStorage.setItem(
        MEDIA_TOKEN_EXP_KEY,
        String(Date.now() + data.expiresIn * 1000)
      )
      return data.token
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}
