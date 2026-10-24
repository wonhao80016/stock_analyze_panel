import { useEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { KlineRow } from '@/lib/api'
import type { StockDailyKChartResult } from '@/components/StockDailyKChart'
import { StockPanel } from './StockPanel'

const daily = vi.hoisted(() => ({ bySymbol: {} as Record<string, KlineRow[]> }))

// Like the real chart, a cached symbol's rows are reported as soon as the symbol changes.
vi.mock('@/components/StockDailyKChart', async () => {
  const actual = await vi.importActual<typeof import('@/components/StockDailyKChart')>('@/components/StockDailyKChart')
  return {
    getDefaultRange: actual.getDefaultRange,
    StockDailyKChart: ({ symbol, linkedPrice, onDataChange, onDateClick }: {
      symbol: string
      linkedPrice?: { value: number; traded: number } | null
      onDataChange?: (result: StockDailyKChartResult) => void
      onDateClick?: (date: string) => void
    }) => {
      const rows = daily.bySymbol[symbol] ?? []
      useEffect(() => {
        onDataChange?.({ rows: actual.toOHLC(rows), rawRows: rows })
      }, [symbol, onDataChange]) // eslint-disable-line react-hooks/exhaustive-deps
      return (
        <div
          data-testid="daily"
          data-linked-price={linkedPrice == null ? 'none' : String(linkedPrice.value)}
          data-linked-traded={linkedPrice == null ? 'none' : String(linkedPrice.traded)}
        >
          {rows.map(r => (
            <button key={r.date} onClick={() => onDateClick?.(r.date)}>{`pick ${r.date}`}</button>
          ))}
        </div>
      )
    },
  }
})

vi.mock('@/components/StockIntradayChart', () => ({
  StockIntradayChart: ({ date, onPriceHover, refetchIntervalMs }: {
    date: string
    onPriceHover?: (p: number | null) => void
    refetchIntervalMs?: number
  }) => (
    <div data-testid="intraday" data-date={date} data-refetch={refetchIntervalMs == null ? 'none' : String(refetchIntervalMs)}>
      <button onClick={() => onPriceHover?.(188)}>hover 188</button>
    </div>
  ),
}))

vi.mock('@/components/StockInfoBar', () => ({
  StockInfoBar: ({ rows }: { rows: KlineRow[] }) => <div data-testid="info" data-rows={rows.length} />,
}))
vi.mock('@/lib/useFinancials', () => ({ useFinancialMetrics: () => ({ data: undefined }) }))
vi.mock('@/lib/useSharedQueries', () => ({ useCapabilities: () => ({ data: undefined }) }))

function row(date: string, close: number, extra: Partial<KlineRow> = {}): KlineRow {
  return { date, open: close, high: close, low: close, close, volume: 1000, ...extra }
}

afterEach(() => {
  daily.bySymbol = {}
})

describe('StockPanel symbol switch', () => {
  it('keeps the info bar and intraday chart when the next symbol is already cached', () => {
    daily.bySymbol = {
      '2330.TWSE': [row('2026-10-21', 1000), row('2026-10-22', 1010)],
      '2317.TWSE': [row('2026-10-20', 200), row('2026-10-21', 201), row('2026-10-22', 202)],
    }
    const { rerender } = render(<StockPanel symbol="2330.TWSE" />)
    expect(screen.getByTestId('info').getAttribute('data-rows')).toBe('2')

    rerender(<StockPanel symbol="2317.TWSE" />)

    expect(screen.getByTestId('info').getAttribute('data-rows')).toBe('3')
    expect(screen.getByTestId('intraday').getAttribute('data-date')).toBe('2026-10-22')
  })
})

describe('StockPanel linked price', () => {
  it('moves the hovered traded price onto the forward-adjusted scale of the picked session', () => {
    daily.bySymbol = {
      '0050.TWSE': [
        row('2025-06-16', 47.0, { raw_close: 188.0 }),
        row('2025-06-17', 47.1, { raw_close: 188.4 }),
        row('2025-06-18', 47.5),
      ],
    }
    render(<StockPanel symbol="0050.TWSE" />)

    fireEvent.click(screen.getByText('pick 2025-06-16'))
    fireEvent.click(screen.getByText('hover 188'))

    expect(Number(screen.getByTestId('daily').getAttribute('data-linked-price'))).toBeCloseTo(47.0, 9)
    expect(screen.getByTestId('daily').getAttribute('data-linked-traded')).toBe('188')
  })

  it('passes the hovered price unchanged on an unadjusted session', () => {
    daily.bySymbol = { '2330.TWSE': [row('2026-10-21', 180), row('2026-10-22', 188)] }
    render(<StockPanel symbol="2330.TWSE" />)

    fireEvent.click(screen.getByText('hover 188'))

    expect(screen.getByTestId('daily').getAttribute('data-linked-price')).toBe('188')
  })
})

describe('StockPanel intraday polling', () => {
  it('polls only the latest session, not a past day the user picked', () => {
    daily.bySymbol = { '2330.TWSE': [row('2026-10-23', 180), row('2026-10-24', 188)] }
    render(<StockPanel symbol="2330.TWSE" refetchIntervalMs={6000} />)
    expect(screen.getByTestId('intraday').getAttribute('data-refetch')).toBe('6000')

    fireEvent.click(screen.getByText('pick 2026-10-23'))

    expect(screen.getByTestId('intraday').getAttribute('data-refetch')).toBe('none')
  })
})
