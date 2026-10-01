// A7: today's mini daily candle must only be patched from a row's own
// live_date (set server-side from a realtime quote), never from a global as_of.
import { describe, expect, it } from 'vitest'
import type { KlineRow } from '@/lib/api'
import { patchLiveCandles } from './liveCandles'

function rows(...dates: string[]): KlineRow[] {
  return dates.map((date, i) => ({
    date, open: 10 + i, high: 11 + i, low: 9 + i, close: 10.5 + i,
  }))
}

describe('patchLiveCandles', () => {
  it('appends a new candle when the last row predates live_date', () => {
    const base = { '2330.TWSE': rows('2026-09-21', '2026-09-22') }
    const live = [{ symbol: '2330.TWSE', live_date: '2026-09-23', open: 100, high: 105, low: 99, close: 103 }]
    const out = patchLiveCandles(base, live)
    expect(out['2330.TWSE']).toHaveLength(3)
    expect(out['2330.TWSE'][2]).toEqual({ date: '2026-09-23', open: 100, high: 105, low: 99, close: 103 })
  })

  it('updates the last candle in place when its date matches live_date', () => {
    const base = { '2330.TWSE': rows('2026-09-22', '2026-09-23') }
    const live = [{ symbol: '2330.TWSE', live_date: '2026-09-23', open: 100, high: 105, low: 99, close: 103 }]
    const out = patchLiveCandles(base, live)
    expect(out['2330.TWSE']).toHaveLength(2)
    expect(out['2330.TWSE'][1]).toMatchObject({ date: '2026-09-23', open: 100, high: 105, low: 99, close: 103 })
  })

  it('leaves the series untouched when live_date is missing (guards against dating a fallback close as today)', () => {
    const base = { '2330.TWSE': rows('2026-09-22') }
    const live = [{ symbol: '2330.TWSE', live_date: null, open: 100, high: 105, low: 99, close: 103 }]
    const out = patchLiveCandles(base, live)
    expect(out['2330.TWSE']).toEqual(base['2330.TWSE'])
  })

  it('leaves the series untouched when the last row is newer than live_date', () => {
    const base = { '2330.TWSE': rows('2026-09-24') }
    const live = [{ symbol: '2330.TWSE', live_date: '2026-09-23', open: 100, high: 105, low: 99, close: 103 }]
    const out = patchLiveCandles(base, live)
    expect(out['2330.TWSE']).toEqual(base['2330.TWSE'])
  })

  it('leaves the series untouched when any OHLC value is null', () => {
    const base = { '2330.TWSE': rows('2026-09-22') }
    const live = [{ symbol: '2330.TWSE', live_date: '2026-09-23', open: 100, high: null, low: 99, close: 103 }]
    const out = patchLiveCandles(base, live)
    expect(out['2330.TWSE']).toEqual(base['2330.TWSE'])
  })

  it('leaves the series untouched when base has no matching live row', () => {
    const base = { '2330.TWSE': rows('2026-09-22'), '6488.TPEX': rows('2026-09-22') }
    const live = [{ symbol: '2330.TWSE', live_date: '2026-09-23', open: 100, high: 105, low: 99, close: 103 }]
    const out = patchLiveCandles(base, live)
    expect(out['6488.TPEX']).toEqual(base['6488.TPEX'])
  })

  it('leaves empty arrays and non-array values untouched', () => {
    const base: Record<string, any> = { '2330.TWSE': [], '6488.TPEX': null }
    const live = [{ symbol: '2330.TWSE', live_date: '2026-09-23', open: 100, high: 105, low: 99, close: 103 }]
    const out = patchLiveCandles(base, live)
    expect(out['2330.TWSE']).toEqual([])
    expect(out['6488.TPEX']).toBeNull()
  })

  it("carries the quote's previous close onto today's candle (8440 ex-dividend, 2026-09-30)", () => {
    const base = { '8440.TPEX': [{ date: '2026-09-29', open: 21.5, high: 21.5, low: 20.65, close: 20.95, prev_close: 21.5 }] }
    const live = [{ symbol: '8440.TPEX', live_date: '2026-09-30', open: 20.25, high: 20.4, low: 20.2, close: 20.35, prev_close: 20.6 }]

    expect(patchLiveCandles(base, live)['8440.TPEX'][1]).toEqual({
      date: '2026-09-30', open: 20.25, high: 20.4, low: 20.2, close: 20.35, prev_close: 20.6,
    })
  })

  it("replaces the stored previous close of today's candle with the quote's", () => {
    const base = { '2379.TWSE': [{ date: '2026-09-08', open: 717, high: 721, low: 680, close: 690, prev_close: 729 }] }
    const live = [{ symbol: '2379.TWSE', live_date: '2026-09-08', open: 717, high: 721, low: 680, close: 692, prev_close: 704 }]

    expect(patchLiveCandles(base, live)['2379.TWSE'][0]).toMatchObject({ close: 692, prev_close: 704 })
  })

  it('keeps the stored previous close when the quote has none (regression guard)', () => {
    const base = { '2379.TWSE': [{ date: '2026-09-08', open: 717, high: 721, low: 680, close: 690, prev_close: 704 }] }
    const live = [{ symbol: '2379.TWSE', live_date: '2026-09-08', open: 717, high: 721, low: 680, close: 692, prev_close: null }]

    expect(patchLiveCandles(base, live)['2379.TWSE'][0].prev_close).toBe(704)
  })

  it('returns base unchanged when liveRows is empty', () => {
    const base = { '2330.TWSE': rows('2026-09-22') }
    const out = patchLiveCandles(base, [])
    expect(out).toBe(base)
  })
})
