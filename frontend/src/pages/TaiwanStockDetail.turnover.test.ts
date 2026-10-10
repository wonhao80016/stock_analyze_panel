// The detail K-line info bar shows the published turnover_rate the detail daily rows carry, a backend
// percentage (1.6279 means 1.63%); ETF rows and sessions without published turnover have none.
import { describe, expect, it } from 'vitest'
import type { TaiwanDailyItem } from '@/lib/api'
import { turnoverRateHTML } from '@/components/EChartsCandlestick'
import { dailyRowsToOHLC } from './TaiwanStockDetail'

function row(turnover_rate: number | null | undefined): TaiwanDailyItem {
  return { date: '2026-10-08', open: 50, high: 51, low: 49, close: 50.5, volume: 154_832, turnover_rate }
}

describe('dailyRowsToOHLC turnover_rate', () => {
  it('carries the row turnover_rate to the bar the info bar reads', () => {
    const [bar] = dailyRowsToOHLC([row(1.6279459)])

    expect(bar.turnover_rate).toBeCloseTo(1.6279459)
    expect(turnoverRateHTML(bar, '#888')).toContain('1.63%')
  })

  it('leaves the bar without turnover when the row has none', () => {
    const bars = dailyRowsToOHLC([row(null), row(undefined)])

    expect(bars.map(bar => bar.turnover_rate)).toEqual([null, null])
    expect(bars.map(bar => turnoverRateHTML(bar, '#888'))).toEqual(['', ''])
  })
})
