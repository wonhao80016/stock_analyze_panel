import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type TaiwanRealtimeQuote } from '@/lib/api'
import { TaiwanQuotePanel } from './TaiwanQuotePanel'

vi.mock('@/lib/api', () => ({
  api: { watchlistList: vi.fn(), taiwanQuotes: vi.fn() },
}))

afterEach(() => {
  vi.clearAllMocks()
})

function quote(overrides: Partial<TaiwanRealtimeQuote>): TaiwanRealtimeQuote {
  return {
    symbol: '2454.TWSE',
    name: '聯發科',
    exchange: 'TWSE',
    last_price: 5200,
    prev_close: 5180,
    open: 5180,
    high: 5210,
    low: 5170,
    change: 20,
    change_pct: 0.39,
    volume: 1000,
    amount: 5_200_000,
    quote_time: '2026-10-06T10:00:00+08:00',
    trade_date: '2026-10-06',
    market_status: 'open',
    source_meta: { source: 'twse:mis', status: 'realtime', is_stale: false },
    ...overrides,
  } as TaiwanRealtimeQuote
}

async function renderPanel(row: TaiwanRealtimeQuote) {
  vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [{ symbol: row.symbol }] } as never)
  vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [row], count: 1 } as never)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <TaiwanQuotePanel />
    </QueryClientProvider>,
  )
  await screen.findByText(row.symbol)
}

describe('TaiwanQuotePanel price-limit labels', () => {
  it('shows the decimal price_limit_pct as a percentage', async () => {
    await renderPanel(quote({ price_limit_pct: 0.1, is_no_limit: false, limit_up: 5695, limit_down: 4665 }))

    expect(screen.getByText('漲停價 (+10%)')).toBeInTheDocument()
    expect(screen.getByText('跌停價 (-10%)')).toBeInTheDocument()
    expect(screen.queryByText(/0\.1%/)).not.toBeInTheDocument()
    expect(screen.getByText('5695.00')).toBeInTheDocument()
    expect(screen.getByText('4665.00')).toBeInTheDocument()
  })

  it('shows a leveraged ETF as 20%', async () => {
    await renderPanel(quote({ symbol: '00631L.TWSE', name: '元大台灣50正2', price_limit_pct: 0.2, is_no_limit: false }))

    expect(screen.getByText('漲停價 (+20%)')).toBeInTheDocument()
  })

  it('marks an instrument without limits', async () => {
    await renderPanel(quote({ symbol: '00885.TWSE', name: '富邦越南', price_limit_pct: null, is_no_limit: true }))

    expect(screen.getByText(/^無漲跌幅限制/)).toBeInTheDocument()
    expect(screen.getByText('漲停價 (無限制)')).toBeInTheDocument()
  })

  it('marks an unknown rule instead of calling it unlimited', async () => {
    await renderPanel(quote({ symbol: '00998A.TPEX', name: '未確認ETF', price_limit_pct: null, is_no_limit: false }))

    expect(screen.getByText('漲跌幅規則未知')).toBeInTheDocument()
    expect(screen.getByText('漲停價 (未知)')).toBeInTheDocument()
    expect(screen.queryByText('漲停價 (無限制)')).not.toBeInTheDocument()
  })
})
