import { describe, expect, it } from 'vitest'
import { BOARD_LOT_VOLUME_NOTE } from './api'
import { turnoverDisplay } from './watchlistTurnover'

describe('turnoverDisplay', () => {
  it('shows 不適用 for an ETF or TDR instead of a dash', () => {
    const d = turnoverDisplay({ turnover_rate: null, turnover_rate_status: 'not_applicable' })
    expect(d.text).toBe('不適用')
    expect(d.suffix).toBeNull()
    expect(d.title).toContain('ETF')
  })

  it('labels a board-lot turnover as an estimate', () => {
    const d = turnoverDisplay({ turnover_rate: 1.234, turnover_rate_status: 'board_lot_estimate', board_lot_volume: true })
    expect(d.text).toBe('1.23%')
    expect(d.suffix).toBe('整股暫估')
    expect(d.title).toContain(BOARD_LOT_VOLUME_NOTE)
    expect(d.title).toContain('暫估')
  })

  it('keeps an official turnover plain and a missing one as a dash', () => {
    expect(turnoverDisplay({ turnover_rate: 2, turnover_rate_status: null })).toEqual({ text: '2.00%', suffix: null, title: '換手率' })
    expect(turnoverDisplay({ turnover_rate: null }).text).toBe('—')
  })
})
