import { useEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { KlineRow } from '@/lib/api'
import type { StockDailyKChartResult } from '@/components/StockDailyKChart'
import { StockPanel } from './StockPanel'

const daily = vi.hoisted(() => ({ rows: [] as KlineRow[] }))

vi.mock('@/components/StockDailyKChart', async () => {
  const actual = await vi.importActual<typeof import('@/components/StockDailyKChart')>('@/components/StockDailyKChart')
  return {
    getDefaultRange: actual.getDefaultRange,
    StockDailyKChart: ({ onDataChange, onDateClick }: {
      onDataChange?: (result: StockDailyKChartResult) => void
      onDateClick?: (date: string) => void
    }) => {
      useEffect(() => {
        onDataChange?.({ rows: actual.toOHLC(daily.rows), rawRows: daily.rows })
      }, [onDataChange])
      return (
        <div>
          {daily.rows.map(r => (
            <button key={r.date} onClick={() => onDateClick?.(r.date)}>{`pick ${r.date}`}</button>
          ))}
        </div>
      )
    },
  }
})

vi.mock('@/components/StockIntradayChart', () => ({
  StockIntradayChart: ({ date, prevClose }: { date: string; prevClose?: number }) => (
    <div data-testid="intraday" data-date={date} data-prev-close={prevClose == null ? 'none' : String(prevClose)} />
  ),
}))

vi.mock('@/components/StockInfoBar', () => ({ StockInfoBar: () => null }))

function row(date: string, close: number, prev_close?: number): KlineRow {
  return { date, open: close, high: close, low: close, close, volume: 1000, ...(prev_close != null ? { prev_close } : {}) }
}

function intradayBase(): { date: string | null; prevClose: string | null } {
  const el = screen.getByTestId('intraday')
  return { date: el.getAttribute('data-date'), prevClose: el.getAttribute('data-prev-close') }
}

afterEach(() => {
  daily.rows = []
})

describe('StockPanel intraday baseline', () => {
  it('draws the ex-dividend session against the backend reference close, not the previous candle', async () => {
    daily.rows = [row('2026-09-07', 729, 730), row('2026-09-08', 692, 704)]
    render(<StockPanel symbol="2379.TWSE" />)

    await screen.findByTestId('intraday')
    expect(intradayBase()).toEqual({ date: '2026-09-08', prevClose: '704' })
  })

  it('uses the prev_close of the session the user picks', async () => {
    daily.rows = [row('2026-06-23', 541, 563), row('2026-06-24', 540, 534.55), row('2026-06-25', 545, 540)]
    render(<StockPanel symbol="6196.TWSE" />)

    fireEvent.click(await screen.findByText('pick 2026-06-24'))
    expect(intradayBase()).toEqual({ date: '2026-06-24', prevClose: '534.55' })
  })

  it("uses the live quote's previous close for today's candle", async () => {
    daily.rows = [row('2026-09-29', 20.95, 21.5), row('2026-09-30', 20.35, 20.6)]
    render(<StockPanel symbol="8440.TPEX" />)

    await screen.findByTestId('intraday')
    expect(intradayBase().prevClose).toBe('20.6')
  })

  it('gives the intraday chart a previous close at traded prices on a forward-adjusted day', async () => {
    daily.rows = [
      { ...row('2025-06-16', 47.0), raw_close: 188.0 },
      { ...row('2025-06-17', 47.1, 47.0), raw_close: 188.4 },
      row('2025-06-18', 47.5, 47.16),
    ]
    render(<StockPanel symbol="0050.TWSE" />)

    fireEvent.click(await screen.findByText('pick 2025-06-17'))
    expect(Number(intradayBase().prevClose)).toBeCloseTo(188.0, 9)
  })

  it('falls back to the previous candle when the backend sends no prev_close (regression guard)', async () => {
    daily.rows = [row('2026-09-07', 729), row('2026-09-08', 692)]
    render(<StockPanel symbol="2379.TWSE" />)

    await screen.findByTestId('intraday')
    expect(intradayBase().prevClose).toBe('729')
  })
})
