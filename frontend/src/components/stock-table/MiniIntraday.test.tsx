import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import type { MinuteKlineRow } from '@/lib/api'
import { computeIntradayAverage } from '@/lib/intraday-chart'
import { MiniIntraday } from './MiniIntraday'

function bar(utcTime: string, close: number, volume: number, amount: number): MinuteKlineRow {
  return {
    datetime: `2026-10-08T${utcTime}:00`,
    open: close, high: close, low: close, close,
    volume, amount,
  }
}

function polylines(rows: MinuteKlineRow[], width = 270) {
  const { container } = render(<MiniIntraday rows={rows} prevClose={10} width={width} height={56} />)
  const [average, price] = Array.from(container.querySelectorAll('polyline'))
  const xs = (line: Element) => (line.getAttribute('points') ?? '').split(' ').map(p => Number(p.split(',')[0]))
  return { average, price, xs }
}

describe('MiniIntraday', () => {
  it('places points by their minute of the session, not evenly', () => {
    // 09:00, 09:30 and 13:30 Taipei time.
    const rows = [bar('01:00', 10, 1, 10), bar('01:30', 11, 1, 11), bar('05:30', 12, 1, 12)]
    const { price, xs } = polylines(rows)

    expect(xs(price)).toEqual([0, 30, 270])
  })

  it('draws the VWAP from amount and volume', () => {
    const rows = [bar('01:00', 10, 100, 1_100), bar('01:01', 20, 100, 2_100)]

    expect(computeIntradayAverage(rows)).toEqual([11, 16])
  })

  it('falls back to the volume-weighted close without amounts', () => {
    const rows = [bar('01:00', 10, 100, 0), bar('01:01', 20, 300, 0)]

    expect(computeIntradayAverage(rows)).toEqual([10, 17.5])
  })

  it('weights the average line by volume instead of averaging closes', () => {
    const rows = [bar('01:00', 10, 100, 1_000), bar('01:01', 20, 300, 6_000)]
    const { average } = polylines(rows)
    const { price } = polylines(rows)
    const ys = (line: Element) => (line.getAttribute('points') ?? '').split(' ').map(p => Number(p.split(',')[1]))

    // VWAP 17.5 sits three quarters of the way from the first close to the second.
    const [firstPrice, secondPrice] = ys(price)
    const lastAverage = ys(average)[1]
    expect((lastAverage - firstPrice) / (secondPrice - firstPrice)).toBeCloseTo(0.75, 1)
  })
})
