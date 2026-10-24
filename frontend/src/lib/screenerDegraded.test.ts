import { describe, expect, it } from 'vitest'
import { missingChipExchangesNote } from './screenerDegraded'

describe('missingChipExchangesNote', () => {
  it('names each chip dataset whose exchange table is missing on the chip date', () => {
    const note = missingChipExchangesNote(['institutional_missing_tpex', 'margin_missing_twse', 'indicators'])
    expect(note).toContain('三大法人缺上櫃')
    expect(note).toContain('融資券缺上市')
  })

  it('returns null when both exchanges are present', () => {
    expect(missingChipExchangesNote(['indicators'])).toBeNull()
    expect(missingChipExchangesNote(undefined)).toBeNull()
  })
})

describe('missingChipExchangesNote window sections', () => {
  it('explains that the 5-session sum is hidden when a session lacks an exchange', () => {
    expect(missingChipExchangesNote(['institutional_window_missing_tpex'])).toBe('近 5 日內有交易日缺上櫃三大法人資料，5 日合計不顯示')
  })

  it('joins a window note with a chip-date note', () => {
    const note = missingChipExchangesNote(['institutional_missing_tpex', 'institutional_window_missing_twse'])
    expect(note).toContain('三大法人缺上櫃')
    expect(note).toContain('近 5 日內有交易日缺上市三大法人資料，5 日合計不顯示')
  })
})
