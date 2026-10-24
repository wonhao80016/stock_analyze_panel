// Stage 5D: a missing stock PE is "no data" rather than "not applicable", and a
// symbol without a diagnostic item is "not evaluated" rather than zero signals.
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

function instrument(symbol: string, opts: { pe: number | null; diagnostic: { signal_count: number } | null }) {
  return {
    symbol,
    context: {
      symbol,
      identity: { name: symbol, instrument_type: 'stock' },
      price_context: { close: 100, return_5d: 0.01, return_20d: 0.02 },
      technical_context: { rsi14: 55 },
      institutional_context: { foreign_net_1d: 0 },
      fundamentals_context: { status: 'unavailable', pe: opts.pe },
      etf_context: { leverage_multiplier: null },
    },
    diagnostic_item: opts.diagnostic,
  }
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/stocks/compare?symbols=2330.TWSE,2881.TWSE']}>
        <Routes>
          <Route path="/stocks/compare" element={<TaiwanStockCompare />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function rowCells(label: string): string[] {
  const row = screen.getByText(label).closest('tr') as HTMLElement
  return within(row).getAllByRole('cell').slice(1).map(cell => cell.textContent ?? '')
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('comparison table missing-value wording', () => {
  it('shows a stock PE that was not obtained as 無資料 and an unevaluated diagnostic as 未評估', async () => {
    vi.mocked(api.taiwanStockCompare).mockResolvedValue({
      symbols_requested: ['2330.TWSE', '2881.TWSE'],
      comparison_date: '2026-09-30',
      generated_at: '2026-09-30T17:00:00+08:00',
      instruments: [
        instrument('2330.TWSE', { pe: null, diagnostic: null }),
        instrument('2881.TWSE', { pe: 12.5, diagnostic: { signal_count: 0 } }),
      ],
      unsupported_symbols: [],
    } as any)

    renderPage()
    await waitFor(() => expect(screen.getByText('本益比 (PE)')).toBeInTheDocument())

    expect(rowCells('本益比 (PE)')).toEqual(['無資料', '12.50'])
    expect(rowCells('異常訊號數')).toEqual(['未評估', '0'])
  })
})
