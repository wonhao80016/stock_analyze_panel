import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import type { KlineRow } from '@/lib/api'
import { MiniCandlestick } from './MiniCandlestick'

const BULL = '#C74040'
const BEAR = '#2D9B65'
const NEUTRAL = '#A1A1AA'

function bodyColors(rows: KlineRow[]): (string | null)[] {
  const { container } = render(<MiniCandlestick rows={rows} />)
  return Array.from(container.querySelectorAll('rect')).map(r => r.getAttribute('fill'))
}

function flat(date: string, price: number, prev_close?: number): KlineRow {
  return { date, open: price, high: price, low: price, close: price, ...(prev_close != null ? { prev_close } : {}) }
}

describe('MiniCandlestick flat-bar colour', () => {
  it('colours a flat bar on an ex-dividend session against the backend reference close', () => {
    const rows = [flat('2026-09-07', 729), flat('2026-09-08', 710, 704)]

    expect(bodyColors(rows)[1]).toBe(BULL)
  })

  it("colours today's flat bar against the quote's previous close", () => {
    const rows = [flat('2026-09-29', 20.95), flat('2026-09-30', 20.6, 20.6)]

    expect(bodyColors(rows)[1]).toBe(NEUTRAL)
  })

  it('compares with the previous bar when the backend sends no prev_close (regression guard)', () => {
    const rows = [flat('2026-09-07', 729), flat('2026-09-08', 710)]

    expect(bodyColors(rows)).toEqual([NEUTRAL, BEAR])
  })

  it('keeps the first bar neutral and colours bodies by direction (regression guard)', () => {
    const rows: KlineRow[] = [
      flat('2026-09-04', 730, 700),
      { date: '2026-09-07', open: 720, high: 740, low: 715, close: 729, prev_close: 730 },
      { date: '2026-09-08', open: 717, high: 721, low: 680, close: 692, prev_close: 704 },
    ]

    expect(bodyColors(rows)).toEqual([NEUTRAL, BULL, BEAR])
  })
})
