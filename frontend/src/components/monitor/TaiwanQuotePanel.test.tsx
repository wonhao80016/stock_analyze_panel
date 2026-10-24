import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type TaiwanRealtimeQuote } from '@/lib/api'
import { useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { TaiwanQuotePanel } from './TaiwanQuotePanel'

vi.mock('@/lib/api', () => ({
  api: { watchlistList: vi.fn(), taiwanQuotes: vi.fn(), preferences: vi.fn() },
  BOARD_LOT_VOLUME_NOTE: '盤中為整股量（不含零股、鉅額、盤後定價）',
}))

vi.mock('@/lib/useQuoteStream', () => ({ useQuoteStreamStatus: vi.fn() }))

beforeEach(() => {
  vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: true } as never)
  vi.mocked(useQuoteStreamStatus).mockReturnValue('connected')
})

afterEach(() => {
  vi.useRealTimers()
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

function mountPanel(onAddRuleForSymbol?: (q: TaiwanRealtimeQuote) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <TaiwanQuotePanel onAddRuleForSymbol={onAddRuleForSymbol} />
    </QueryClientProvider>,
  )
}

async function renderPanel(row: TaiwanRealtimeQuote) {
  vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [{ symbol: row.symbol }] } as never)
  vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [row], count: 1 } as never)
  mountPanel()
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

describe('TaiwanQuotePanel intraday volume', () => {
  it('labels a realtime quote as board-lot volume and shows its missing amount as a dash', async () => {
    await renderPanel(quote({
      amount: null as never,
      source_meta: { source: 'twse:mis', status: 'realtime', is_stale: false, is_realtime: true } as never,
    }))

    expect(screen.getByText('盤中為整股量（不含零股、鉅額、盤後定價）')).toBeInTheDocument()
    expect(screen.getByText('成交金額').nextElementSibling).toHaveTextContent('—')
  })

  it('shows an official snapshot without the label', async () => {
    await renderPanel(quote({
      source_meta: { source: 'twse:official', status: 'official_snapshot', is_stale: false, is_realtime: false } as never,
    }))

    expect(screen.queryByText('盤中為整股量（不含零股、鉅額、盤後定價）')).not.toBeInTheDocument()
    expect(screen.getByText('成交金額').nextElementSibling).toHaveTextContent('520.00 萬')
  })
})

describe('TaiwanQuotePanel selected symbol without a quote (B276)', () => {
  it('shows an empty state instead of another symbol and offers no rule for it', async () => {
    vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [{ symbol: '2330.TWSE' }, { symbol: '2454.TWSE' }] } as never)
    vi.mocked(api.taiwanQuotes).mockResolvedValue({ quotes: [quote({})], count: 1 } as never)
    const onAdd = vi.fn()
    mountPanel(onAdd)

    expect(await screen.findByText('此標的暫無報價')).toBeInTheDocument()
    expect(screen.queryByTitle('開啟個股視窗')).not.toBeInTheDocument()
    expect(screen.queryByText('針對此標的建立監控規則')).not.toBeInTheDocument()
  })

  it('builds the rule for the symbol on screen', async () => {
    vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [{ symbol: '2330.TWSE' }, { symbol: '2454.TWSE' }] } as never)
    vi.mocked(api.taiwanQuotes).mockResolvedValue({
      quotes: [quote({ symbol: '2330.TWSE', name: '台積電' }), quote({})],
      count: 2,
    } as never)
    const onAdd = vi.fn()
    mountPanel(onAdd)

    fireEvent.click(await screen.findByRole('button', { name: /聯發科/ }))
    fireEvent.click(await screen.findByText('針對此標的建立監控規則'))

    expect(onAdd).toHaveBeenCalledTimes(1)
    expect(onAdd.mock.calls[0][0].symbol).toBe('2454.TWSE')
  })
})

// Thursday 2026-10-22 10:00 in Taipei.
const IN_SESSION = new Date('2026-10-22T02:00:00Z')

function setClock(at: Date) {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(at)
}

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

describe('TaiwanQuotePanel refresh without SSE (B275, B277)', () => {
  it('polls every 10 s and says so while realtime quotes are off', async () => {
    setClock(IN_SESSION)
    vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: false } as never)
    await renderPanel(quote({}))
    const before = vi.mocked(api.taiwanQuotes).mock.calls.length

    await advance(10_500)

    await vi.waitFor(() => expect(vi.mocked(api.taiwanQuotes).mock.calls.length).toBeGreaterThan(before))
    expect(screen.getByText('即時行情未開啟，每 10 秒更新一次')).toBeInTheDocument()
    expect(screen.queryByText('即時行情自動同步中 (SSE)')).not.toBeInTheDocument()
  })

  it('polls while the stream is disconnected even with realtime quotes on', async () => {
    setClock(IN_SESSION)
    vi.mocked(useQuoteStreamStatus).mockReturnValue('reconnecting')
    await renderPanel(quote({}))
    const before = vi.mocked(api.taiwanQuotes).mock.calls.length

    await advance(10_500)

    await vi.waitFor(() => expect(vi.mocked(api.taiwanQuotes).mock.calls.length).toBeGreaterThan(before))
    expect(screen.getByText('即時串流未連線，每 10 秒更新一次')).toBeInTheDocument()
  })

  it('leaves refreshing to SSE while realtime quotes are on and connected', async () => {
    setClock(IN_SESSION)
    await renderPanel(quote({}))
    await screen.findByText('即時行情自動同步中 (SSE)')
    const before = vi.mocked(api.taiwanQuotes).mock.calls.length

    await advance(30_000)

    expect(vi.mocked(api.taiwanQuotes).mock.calls.length).toBe(before)
  })
})

describe('TaiwanQuotePanel outside the Taiwan session (P3-c)', () => {
  async function expectNoPolling() {
    const before = vi.mocked(api.taiwanQuotes).mock.calls.length
    await advance(60_000)
    expect(vi.mocked(api.taiwanQuotes).mock.calls.length).toBe(before)
    expect(screen.getByText('非交易時段')).toBeInTheDocument()
    expect(screen.queryByText(/每 10 秒更新一次/)).not.toBeInTheDocument()
  }

  it('does not poll after the close', async () => {
    // Thursday 15:00 in Taipei.
    setClock(new Date('2026-10-22T07:00:00Z'))
    vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: false } as never)
    await renderPanel(quote({ market_status: 'closed' }))

    await expectNoPolling()
  })

  it('does not poll on a weekend', async () => {
    // Saturday 10:00 in Taipei.
    setClock(new Date('2026-10-24T02:00:00Z'))
    vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: false } as never)
    await renderPanel(quote({ market_status: 'non_trading_day' }))

    await expectNoPolling()
  })

  it('does not poll a weekday the exchange reports as closed', async () => {
    setClock(IN_SESSION)
    vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: false } as never)
    await renderPanel(quote({ market_status: 'non_trading_day' }))

    await expectNoPolling()
  })

  it('starts polling once the session opens', async () => {
    // Thursday 08:29 in Taipei.
    setClock(new Date('2026-10-22T00:29:00Z'))
    vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: false } as never)
    await renderPanel(quote({ market_status: 'closed' }))
    await advance(30_000)
    const beforeOpen = vi.mocked(api.taiwanQuotes).mock.calls.length

    await advance(60_000)
    await advance(10_500)

    expect(vi.mocked(api.taiwanQuotes).mock.calls.length).toBeGreaterThan(beforeOpen)
    expect(screen.getByText('即時行情未開啟，每 10 秒更新一次')).toBeInTheDocument()
  })
})
