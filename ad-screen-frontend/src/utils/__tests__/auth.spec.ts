import { describe, it, expect, beforeEach } from 'vitest'
import {
  getToken,
  setToken,
  clearToken,
  getStoredUser,
  setStoredUser,
  clearStoredUser
} from '@/utils/auth'

describe('本地凭证存取', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('未登录时 getToken 返回空串而非 null', () => {
    // 请求拦截器用 `if (token)` 判断是否附加 Authorization 头，
    // 返回 null 会让后续字符串拼接出现 "Bearer null"
    expect(getToken()).toBe('')
  })

  it('Token 可写入并读回', () => {
    setToken('abc.def.ghi')
    expect(getToken()).toBe('abc.def.ghi')
  })

  it('登出清除 Token 与用户信息', () => {
    setToken('t')
    setStoredUser({ username: 'dr_wang' })
    clearToken()
    clearStoredUser()
    expect(getToken()).toBe('')
    expect(getStoredUser()).toBeNull()
  })

  it('用户信息对象可完整往返', () => {
    const user = { username: 'dr_wang', role: 'doctor', dept: '放射科' }
    setStoredUser(user)
    expect(getStoredUser<typeof user>()).toEqual(user)
  })

  it('本地缓存被污染时返回 null 而非抛异常', () => {
    // 用户手改 localStorage 或跨版本升级留下旧格式时，JSON.parse 会抛错。
    // 这里必须兜住——否则一处脏数据会让整个应用白屏。
    localStorage.setItem('adscreen_user', '{not valid json')
    expect(() => getStoredUser()).not.toThrow()
    expect(getStoredUser()).toBeNull()
  })

  it('缓存为 "null" 字面量时返回 null', () => {
    localStorage.setItem('adscreen_user', 'null')
    expect(getStoredUser()).toBeNull()
  })
})
