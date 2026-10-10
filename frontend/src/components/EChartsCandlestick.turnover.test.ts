import { describe, expect, it } from 'vitest'
import { turnoverRateHTML, type OHLC } from './EChartsCandlestick'

function bar(turnover_rate?: number | null): OHLC {
  return { date: '2026-09-23', open: 100, high: 100, low: 100, close: 100, volume: 6_425_491, turnover_rate }
}

describe('turnoverRateHTML', () => {
  it('shows the bar turnover_rate as the backend percentage (2454, 2026-09-23)', () => {
    const html = turnoverRateHTML(bar(0.400354), '#888')

    expect(html).toContain('換手')
    expect(html).toContain('0.40%')
  })

  it('shows nothing for a bar without turnover, such as an ETF or an earlier session', () => {
    expect(turnoverRateHTML(bar(null), '#888')).toBe('')
    expect(turnoverRateHTML(bar(undefined), '#888')).toBe('')
  })
})
