/** 日期格式化：Date → 'YYYY-MM-DD HH:mm:ss' */
export function formatDateTime(d: Date): string {
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 日期格式化：Date → 'YYYY-MM-DD' */
export function formatDate(d: Date): string {
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** 当前时间字符串（用于日志/任务时间戳） */
export function now(): string {
  return formatDateTime(new Date())
}

/** 简易种子随机数（mulberry32）：保证 Mock 数据在每次刷新间保持稳定 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 由字符串生成稳定数字种子 */
export function strSeed(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** 风险评分 → 四级风险等级（阈值可由模型配置下发） */
export function scoreToRiskLevel(score: number, threshold = 0.8): 'low' | 'mci' | 'ad-early' | 'ad-late' {
  // score 为 0-100；threshold 为高风险判定阈值（0-1），折算为评分分界
  const earlyCut = threshold * 100 * 0.75 // AD 早期分界
  if (score < 35) return 'low'
  if (score < earlyCut) return 'mci'
  if (score < threshold * 100) return 'ad-early'
  return 'ad-late'
}
