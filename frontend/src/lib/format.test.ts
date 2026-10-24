import { describe, expect, it } from 'vitest'
import { fmtBigNum } from './format'

describe('fmtBigNum', () => {
  it('uses the Taiwanese unit 兆 for trillions', () => {
    expect(fmtBigNum(2_345_000_000_000)).toBe('2.35兆')
  })

  it('keeps 億 and 萬 below a trillion', () => {
    expect(fmtBigNum(123_000_000)).toBe('1.23億')
    expect(fmtBigNum(56_000)).toBe('6萬')
  })
})
