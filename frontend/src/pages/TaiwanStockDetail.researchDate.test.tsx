import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockDetail, researchDataDate } from './TaiwanStockDetail'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanSearch: vi.fn().mockResolvedValue({ results: [] }),
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    taiwanStockDetail: vi.fn(),
    taiwanCurrentData: vi.fn().mockResolvedValue(null),
    taiwanStockResearchContext: vi.fn(),
    taiwanStockAIResearch: vi.fn(),
  },
}))

const detail = {
  symbol: '2330.TWSE',
  identity: {
    symbol: '2330.TWSE',
    code: '2330',
    name: '台積電',
    exchange: 'TWSE',
    instrument_type: 'stock',
    is_supported: true,
  },
  price_limit: { is_no_limit: false, rule_type: 'standard' },
  realtime: { last_price: 100, change: 0, change_pct: 0, market_status: 'closed', bids: [], asks: [] },
  daily_history: { status: 'available', rows: [] },
  institutional: { status: 'unavailable' },
  margin: { status: 'unavailable' },
  factors: { status: 'unavailable' },
  market_context: { benchmark_symbol: 't00.TWSE', benchmark_name: '發行量加權股價指數' },
  monitor_summary: { rule_count: 0, active_count: 0, rules: [] },
  recent_alerts: [],
  overall_data_quality: 'partial',
}

function research(asOf: string, tradeDate: string) {
  return {
    symbol: '2330.TWSE',
    as_of_date: asOf,
    data_quality: { overall_status: 'partial', sections: [], target_trade_date: asOf },
    evidence_summary: { known_fields_count: 10, missing_fields_count: 0, derived_fields_count: 12, missing_sections: [] },
    price_context: { trade_date: tradeDate, close: 107, return_5d: 0.01, return_20d: 0.02 },
    technical_context: { distance_to_ma20: 0.03, vol_ratio_5d: 1.2 },
    industry_context: { industry: null },
  }
}

function renderDetail() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/stocks/2330.TWSE']}>
        <Routes>
          <Route path="/stocks/:symbol" element={<TaiwanStockDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('research card data date', () => {
  it('names the session only when it differs from the requested date', () => {
    expect(researchDataDate(research('2026-10-09', '2026-10-08') as any)).toBe('2026-10-08')
    expect(researchDataDate(research('2026-10-08', '2026-10-08') as any)).toBeNull()
  })

  it('labels a card priced from an earlier session', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail as any)
    vi.mocked(api.taiwanStockResearchContext).mockResolvedValue(research('2026-10-09', '2026-10-08') as any)
    renderDetail()

    expect(await screen.findByText('資料日期 2026-10-08')).toBeInTheDocument()
  })

  it('shows no label when the card prices the requested date', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail as any)
    vi.mocked(api.taiwanStockResearchContext).mockResolvedValue(research('2026-10-08', '2026-10-08') as any)
    renderDetail()

    expect(await screen.findByText('結構化研究證據上下文')).toBeInTheDocument()
    expect(screen.queryByText(/資料日期/)).not.toBeInTheDocument()
  })
})
