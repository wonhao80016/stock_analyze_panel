import { describe, expect, it, vi } from 'vitest'
import { addToCompareList, replaceInCompareList } from './compareList'

const FULL = ['1101.TWSE', '1216.TWSE', '2317.TWSE', '2454.TWSE', '2881.TWSE']

describe('addToCompareList', () => {
  it('appends to a list with room without asking', () => {
    const confirm = vi.fn()
    expect(addToCompareList(['1101.TWSE'], '2330.TWSE', confirm)).toEqual(['1101.TWSE', '2330.TWSE'])
    expect(confirm).not.toHaveBeenCalled()
  })

  it('keeps a full list that already holds the symbol without asking', () => {
    const confirm = vi.fn()
    expect(addToCompareList(FULL, '2317.twse', confirm)).toEqual(FULL)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('asks before replacing the earliest symbol of a full list', () => {
    const confirm = vi.fn().mockReturnValue(true)
    expect(addToCompareList(FULL, '2330.TWSE', confirm)).toEqual(['1216.TWSE', '2317.TWSE', '2454.TWSE', '2881.TWSE', '2330.TWSE'])
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(confirm.mock.calls[0][0]).toContain('1101.TWSE')
  })

  it('returns null when the user declines', () => {
    expect(addToCompareList(FULL, '2330.TWSE', () => false)).toBeNull()
  })
})

describe('replaceInCompareList', () => {
  it('drops the chosen symbol', () => {
    expect(replaceInCompareList(FULL, '2330.TWSE', '2454.TWSE')).toEqual(['1101.TWSE', '1216.TWSE', '2317.TWSE', '2881.TWSE', '2330.TWSE'])
  })
})
