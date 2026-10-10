// The daily K-line info bar shows the turnover_rate the /api/kline/daily rows carry, a backend
// percentage (2434.TWSE on 2026-10-08: 1.6279 means 1.63%); an ETF row has none.
import { describe, expect, it } from 'vitest'
import type { KlineRow } from '@/lib/api'
import { turnoverRateHTML } from './EChartsCandlestick'
import { toOHLC } from './StockDailyKChart'

function row(turnover_rate: number | null | undefined): KlineRow {
  return { date: '2026-10-08', open: 50, high: 51, low: 49, close: 50.5, volume: 154_832, turnover_rate }
}

describe('toOHLC turnover_rate', () => {
  it('carries the row turnover_rate to the bar the info bar reads', () => {
    const [bar] = toOHLC([row(1.6279459)])

    expect(bar.turnover_rate).toBeCloseTo(1.6279459)
    expect(turnoverRateHTML(bar, '#888')).toContain('1.63%')
  })

  it('leaves the bar without turnover when the row has none, as for an ETF', () => {
    const [bar] = toOHLC([row(null), row(undefined)])

    expect(bar.turnover_rate).toBeNull()
    expect(turnoverRateHTML(bar, '#888')).toBe('')
    expect(toOHLC([row(undefined)])[0].turnover_rate).toBeNull()
  })
})
