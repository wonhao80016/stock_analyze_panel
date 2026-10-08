import { describe, expect, it } from 'vitest'
import {
  changeBase,
  rawChangeBase,
  unadjustedClose,
  unadjustedCloseHTML,
  volumeInfo,
  volumeRatioAt,
  volumeRatioInfo,
  type OHLC,
} from './EChartsCandlestick'

function bar(date: string, close: number, prev_close?: number | null): OHLC {
  return { date, open: close, high: close, low: close, close, prev_close }
}

function pct(close: number, base: number | null): string {
  return base == null ? '' : ((close - base) / base * 100).toFixed(2)
}

describe('changeBase', () => {
  it('measures an ex-dividend session from the backend reference close (2379, 2026-09-08)', () => {
    const rows = [bar('2026-09-07', 729, 730), bar('2026-09-08', 692, 704)]

    expect(changeBase(rows, 1)).toBe(704)
    expect(pct(692, changeBase(rows, 1))).toBe('-1.70')
  })

  it('shows a gain on an ex-dividend session that closed below the previous close (6196, 2026-06-24)', () => {
    const rows = [bar('2026-06-23', 541, 563), bar('2026-06-24', 540, 534.55)]

    expect(pct(540, changeBase(rows, 1))).toBe('1.02')
  })

  it('falls back to the previous bar when the row carries no prev_close', () => {
    const rows = [bar('2026-09-07', 729), bar('2026-09-08', 692)]

    expect(changeBase(rows, 1)).toBe(729)
    expect(pct(692, changeBase(rows, 1))).toBe('-5.08')
  })

  it('uses prev_close on the first loaded bar and has no base without it', () => {
    expect(changeBase([bar('2026-09-08', 692, 704)], 0)).toBe(704)
    expect(changeBase([bar('2026-09-08', 692)], 0)).toBeNull()
  })

  it('ignores a prev_close that is not a positive number', () => {
    const rows = [bar('2026-09-07', 729), bar('2026-09-08', 692, 0), bar('2026-09-09', 700, Number.NaN)]

    expect(changeBase(rows, 1)).toBe(729)
    expect(changeBase(rows, 2)).toBe(692)
  })
})

function adjusted(date: string, close: number, raw_close: number, prev_close?: number): OHLC {
  return { date, open: close, high: close, low: close, close, raw_close, prev_close }
}

describe('rawChangeBase', () => {
  it('moves the base of a forward-adjusted bar back to traded prices (0050 before its 2025-06-18 split)', () => {
    const rows = [adjusted('2025-06-16', 47.0, 188.0), adjusted('2025-06-17', 47.1, 188.4, 47.0)]

    expect(rawChangeBase(rows, 1)).toBeCloseTo(188.0, 9)
  })

  it('equals changeBase on a bar that is not adjusted', () => {
    const rows = [bar('2026-09-07', 729, 730), bar('2026-09-08', 692, 704)]

    expect(rawChangeBase(rows, 1)).toBe(704)
    expect(rawChangeBase([bar('2026-09-08', 692)], 0)).toBeNull()
  })
})

describe('unadjustedClose', () => {
  it('shows the traded close of a forward-adjusted bar under its own label', () => {
    const bar0050 = adjusted('2025-06-17', 47.1, 188.4)

    expect(unadjustedClose(bar0050)).toBe(188.4)
    expect(unadjustedCloseHTML(bar0050, '#fff')).toContain('原始收盤')
    expect(unadjustedCloseHTML(bar0050, '#fff')).toContain('188.40')
  })

  it('stays hidden when the traded close is what the bar already shows', () => {
    expect(unadjustedClose(adjusted('2026-09-30', 692, 692.001))).toBeNull()
    expect(unadjustedClose(bar('2026-09-30', 692))).toBeNull()
    expect(unadjustedCloseHTML(bar('2026-09-30', 692), '#fff')).toBe('')
  })
})

function volumeBar(date: string, volume: number, extra: Partial<OHLC> = {}): OHLC {
  return { date, open: 10, high: 10, low: 10, close: 10, volume, ...extra }
}

function fiveDaysThen(today: OHLC): OHLC[] {
  return [
    volumeBar('2026-09-30', 1000),
    volumeBar('2026-10-01', 1000),
    volumeBar('2026-10-02', 1000),
    volumeBar('2026-10-05', 1000),
    volumeBar('2026-10-06', 1000),
    today,
  ]
}

describe('volumeRatioAt', () => {
  it('scales the partial volume of a live bar at 10:00 to a full session', () => {
    const rows = fiveDaysThen(volumeBar('2026-10-07', 300, { volume_time_factor: 4.5 }))

    expect(volumeRatioAt(rows, 5, 5)).toBeCloseTo(1.35, 12)
    expect(volumeRatioInfo(rows, 5, 5)).toMatchObject({ label: '量比5', value: '1.35x 推估' })
  })

  it('shows a closed bar as it is, without the estimate label', () => {
    const rows = fiveDaysThen(volumeBar('2026-10-07', 1500, { volume_time_factor: 1 }))

    expect(volumeRatioAt(rows, 5, 5)).toBeCloseTo(1.5, 12)
    expect(volumeRatioInfo(rows, 5, 5).value).toBe('1.50x')
    expect(volumeRatioInfo(fiveDaysThen(volumeBar('2026-10-07', 1500)), 5, 5).value).toBe('1.50x')
  })
})

describe('volumeInfo', () => {
  it('labels intraday board-lot volume', () => {
    expect(volumeInfo(volumeBar('2026-10-07', 300, { board_lot_volume: true })).value).toBe(
      '300 盤中為整股量（不含零股、鉅額、盤後定價）',
    )
    expect(volumeInfo(volumeBar('2026-10-07', 300)).value).toBe('300')
  })
})
