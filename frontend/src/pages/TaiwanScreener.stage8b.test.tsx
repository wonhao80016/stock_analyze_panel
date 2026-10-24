import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { TaiwanScreener } from './TaiwanScreener'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: {
    taiwanScreenerRun: vi.fn(),
    taiwanScreenerTranslate: vi.fn(),
    taiwanDataStatus: vi.fn(),
    taiwanMarketIntelligence: vi.fn(),
    taiwanIndustryIntelligence: vi.fn(),
    taiwanAbnormalDiagnostics: vi.fn(),
    watchlistList: vi.fn(),
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn(),
    taiwanSearch: vi.fn(),
    monitorRuleOptions: vi.fn().mockResolvedValue({
      threshold_fields: [], builtin_signals: [], custom_signals: [],
      operators: ['>', '>=', '<', '<=', '==', '!='], types: [], scopes: [], logics: [],
      severities: [], directions: [],
      intraday_signal_support: { available: true, source: null, max_symbols: 0, reason: '' },
      sector_targets: { index: [], concept: [], industry: [] },
    }),
    preferences: vi.fn().mockResolvedValue({}),
    customSignalsList: vi.fn().mockResolvedValue({ signals: [] }),
    instrumentNames: vi.fn().mockResolvedValue({ names: {} }),
  },
  genRuleId: () => 'mr_test',
}))

function item(symbol: string, name: string, exchange: string, quoteDate: string) {
  return {
    symbol, name, exchange, instrument_type: 'stock', close: 100, change_pct: 0.01,
    volume: 10000, amount: 1000000, quote_date: quoteDate, is_no_limit: false, price_limit_pct: 0.1,
  }
}

function screenerResponse() {
  return {
    total: 2, page: 1, page_size: 50, sort_by: 'symbol', sort_order: 'asc',
    data_dates: {
      daily_as_of: '2026-10-23', daily_unlisted_count: 3, institutional_as_of: '2026-10-23', margin_as_of: '2026-10-23',
    },
    degraded_sections: ['daily_stale_tpex'],
    items: [item('2330.TWSE', '台積電', 'TWSE', '2026-10-23'), item('8069.TPEX', '元太', 'TPEX', '2026-10-22')],
  }
}

function renderScreener() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/taiwan-screener']}>
        <TaiwanScreener />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(api.taiwanScreenerRun).mockResolvedValue(screenerResponse() as any)
  vi.mocked(api.taiwanDataStatus).mockResolvedValue(null as any)
  vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue({ items: [], diagnostic_count: 0 } as any)
  vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistGroups).mockResolvedValue({ groups: [] } as any)
  vi.mocked(api.taiwanSearch).mockResolvedValue({ results: [] } as any)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('TaiwanScreener daily dates per exchange', () => {
  it('marks a row whose quote date is older than the daily date', async () => {
    renderScreener()

    const stale = (await screen.findByRole('button', { name: '8069.TPEX' })).closest('tr')!
    const fresh = screen.getByRole('button', { name: '2330.TWSE' }).closest('tr')!
    expect(within(stale).getByTitle('此列為較早交易日的日線')).toHaveTextContent('2026-10-22')
    expect(within(fresh).queryByTitle('此列為較早交易日的日線')).toBeNull()
  })

  it('counts the symbols without a daily row', async () => {
    renderScreener()

    expect(await screen.findByText('（3 檔無日線資料）')).toBeInTheDocument()
  })

  it('explains an exchange served from an older partition', async () => {
    renderScreener()

    expect(await screen.findByText(/上櫃個股暫以較早交易日的日線顯示/)).toBeInTheDocument()
  })
})
