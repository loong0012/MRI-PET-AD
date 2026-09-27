import { describe, it, expect } from 'vitest'
import {
  formatDateTime,
  formatDate,
  mulberry32,
  strSeed,
  scoreToRiskLevel
} from '@/utils/format'

describe('日期格式化', () => {
  it('月/日/时分秒不足两位时补零', () => {
    // 构造 2026-01-02 03:04:05，验证所有字段都被 padStart(2,'0')
    const d = new Date(2026, 0, 2, 3, 4, 5)
    expect(formatDateTime(d)).toBe('2026-01-02 03:04:05')
  })

  it('两位以上的值不补零', () => {
    const d = new Date(2026, 10, 12, 13, 14, 15)
    expect(formatDateTime(d)).toBe('2026-11-12 13:14:15')
  })

  it('formatDate 只取日期部分', () => {
    expect(formatDate(new Date(2026, 0, 2, 3, 4, 5))).toBe('2026-01-02')
  })
})

describe('确定性随机（Mock 数据稳定性）', () => {
  it('同种子产生相同序列', () => {
    const a = mulberry32(42)
    const b = mulberry32(42)
    const seqA = [a(), a(), a()]
    const seqB = [b(), b(), b()]
    expect(seqA).toEqual(seqB)
  })

  it('不同种子产生不同序列', () => {
    const a = mulberry32(1)
    const b = mulberry32(2)
    expect(a()).not.toBe(b())
  })

  it('输出落在 [0, 1) 区间', () => {
    const r = mulberry32(7)
    for (let i = 0; i < 500; i++) {
      const v = r()
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThan(1)
    }
  })

  it('strSeed 对相同输入稳定、对不同输入分散', () => {
    expect(strSeed('ADSCREEN')).toBe(strSeed('ADSCREEN'))
    expect(strSeed('A')).not.toBe(strSeed('B'))
    expect(strSeed('')).toBe(2166136261) // FNV-1a 偏移基址
  })
})

describe('风险分级阈值', () => {
  // 默认 threshold=0.8 → 早期分界 60、晚期分界 80
  it('默认阈值下的四级边界', () => {
    expect(scoreToRiskLevel(0)).toBe('low')
    expect(scoreToRiskLevel(34.9)).toBe('low')
    expect(scoreToRiskLevel(35)).toBe('mci')
    expect(scoreToRiskLevel(59.9)).toBe('mci')
    expect(scoreToRiskLevel(60)).toBe('ad-early')
    expect(scoreToRiskLevel(79.9)).toBe('ad-early')
    expect(scoreToRiskLevel(80)).toBe('ad-late')
    expect(scoreToRiskLevel(100)).toBe('ad-late')
  })

  it('阈值可配置：分界随阈值线性移动', () => {
    // threshold=0.6 → 早期分界 45、晚期分界 60
    expect(scoreToRiskLevel(44, 0.6)).toBe('mci')
    expect(scoreToRiskLevel(45, 0.6)).toBe('ad-early')
    expect(scoreToRiskLevel(60, 0.6)).toBe('ad-late')
  })

  it('低风险区间不随阈值变动（35 分界固定）', () => {
    expect(scoreToRiskLevel(34, 0.5)).toBe('low')
    expect(scoreToRiskLevel(34, 0.9)).toBe('low')
  })
})
