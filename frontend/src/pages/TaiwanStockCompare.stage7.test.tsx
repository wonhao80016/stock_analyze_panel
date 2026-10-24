// A missing figure is "no data", which is not the same as a field that does not apply.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockCompare } from './TaiwanStockCompare'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanSearch: vi.fn().mockResolvedValue({ results: [] }),
    taiwanStockCompare: vi.fn(),
    taiwanStockCompareAIResearch: vi.fn(),
    taiwanDataStatus: vi.fn().mockResolvedValue(null),
  },
}))

function instrument(symbol: string, present: boolean) {
  return {
    symbol,
    context: {
      symbol,
      identity: { name: symbol, instrument_type: 'stock' },
      price_context: present ? { close: 100, return_5d: 0.01, return_20d: 0.02 } : { close: null, return_5d: null, return_20d: null },
      technical_context: { rsi14: present ? 55 : null },
      institutional_context: { foreign_net_1d: present ? 1000 : null },
      fundamentals_context: { status: 'unavailable', pe: null },
      etf_context: { leverage_multiplier: null },
    },
    diagnostic_item: { signal_count: 0 },
  }
}

function rowCells(label: string): string[] {
  const row = screen.getByText(label).closest('tr') as HTMLElement
  return within(row).getAllByRole('cell').slice(1).map(cell => cell.textContent ?? '')
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('comparison table missing figures', () => {
  it('shows a missing price, return, RSI or flow as 無資料 rather than 不適用', async () => {
    vi.mocked(api.taiwanStockCompare).mockResolvedValue({
      symbols_requested: ['2330.TWSE', '7799.TWSE'],
      comparison_date: '2026-09-30',
      generated_at: '2026-09-30T17:00:00+08:00',
      instruments: [instrument('2330.TWSE', true), instrument('7799.TWSE', false)],
      unsupported_symbols: [],
    } as any)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/stocks/compare?symbols=2330.TWSE,7799.TWSE']}>
          <Routes>
            <Route path="/stocks/compare" element={<TaiwanStockCompare />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getByText('收盤價')).toBeInTheDocument())

    for (const label of ['收盤價', '5 日報酬率', '20 日報酬率', 'RSI(14)', '外資買賣超 (1日)', '本益比 (PE)']) {
      expect(rowCells(label)[1]).toBe('無資料')
    }
    expect(screen.queryByText('N/A（不適用）')).not.toBeInTheDocument()
  })
})
