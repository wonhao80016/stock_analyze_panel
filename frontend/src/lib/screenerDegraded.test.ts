import { describe, expect, it } from 'vitest'
import { dailyExchangesNote, missingChipExchangesNote, staleQuoteDate } from './screenerDegraded'

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

describe('dailyExchangesNote', () => {
  it('explains a stale and a missing exchange', () => {
    const note = dailyExchangesNote(['daily_stale_tpex', 'daily_missing_twse', 'indicators'])
    expect(note).toContain('上櫃個股暫以較早交易日的日線顯示')
    expect(note).toContain('最近 5 個已存交易日都沒有上市日線')
  })

  it('returns null without daily sections', () => {
    expect(dailyExchangesNote(['institutional_missing_tpex'])).toBeNull()
    expect(dailyExchangesNote(undefined)).toBeNull()
  })
})

describe('staleQuoteDate', () => {
  it('returns the quote date only when it is older than the daily date', () => {
    expect(staleQuoteDate('2026-10-22', '2026-10-23')).toBe('2026-10-22')
    expect(staleQuoteDate('2026-10-23', '2026-10-23')).toBeNull()
    expect(staleQuoteDate(null, '2026-10-23')).toBeNull()
    expect(staleQuoteDate('2026-10-22', null)).toBeNull()
  })
})
