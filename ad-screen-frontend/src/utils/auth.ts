const TOKEN_KEY = 'adscreen_token'
const USER_KEY = 'adscreen_user'

/** 读取本地 Token */
export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

/** 写入 Token */
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

/** 清除 Token */
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

/** 读取本地缓存的用户信息（JSON） */
export function getStoredUser<T>(): T | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

/** 缓存用户信息 */
export function setStoredUser<T>(user: T): void {
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

/** 清除用户信息 */
export function clearStoredUser(): void {
  localStorage.removeItem(USER_KEY)
}
