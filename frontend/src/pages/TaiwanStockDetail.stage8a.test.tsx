import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockDetail } from './TaiwanStockDetail'
import { api } from '@/lib/api'

vi.mock('echarts', () => ({
  init: () => ({
    on: vi.fn(),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
    setOption: vi.fn(),
    getOption: () => ({}),
    dispatchAction: vi.fn(),
    clear: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
}))

vi.mock('@/lib/api', async importOriginal => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: {
    taiwanSearch: vi.fn().mockResolvedValue({ results: [] }),
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    taiwanStockDetail: vi.fn(),
    taiwanCurrentData: vi.fn().mockResolvedValue(null),
    taiwanStockResearchContext: vi.fn().mockResolvedValue(null),
    taiwanMarginHistory: vi.fn().mockResolvedValue({ rows: [] }),
    taiwanDataStatus: vi.fn(),
    taiwanCurrentSignals: vi.fn().mockResolvedValue(null),
  },
}))

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

function detail(marketStatus: string) {
  return {
    symbol: '2330.TWSE',
    identity: { symbol: '2330.TWSE', code: '2330', name: '台積電', exchange: 'TWSE', instrument_type: 'stock', is_supported: true },
    price_limit: { is_no_limit: false, rule_type: 'standard' },
    realtime: { last_price: 100, change: 0, change_pct: 0, volume: 1000, market_status: marketStatus, bids: [], asks: [] },
    daily_history: { status: 'available', rows: [] },
    institutional: { status: 'unavailable' },
    margin: { status: 'unavailable' },
    factors: { status: 'unavailable' },
    market_context: { benchmark_symbol: 't00.TWSE', benchmark_name: '發行量加權股價指數' },
    monitor_summary: { rule_count: 0, active_count: 0, rules: [] },
    recent_alerts: [],
    overall_data_quality: 'partial',
  }
}

function dataStatus(asOf: string) {
  return { daily_as_of: asOf, institutional_as_of: asOf, margin_as_of: asOf }
}

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/stocks/2330.TWSE']}>
        <Routes>
          <Route path="/stocks/:symbol" element={<TaiwanStockDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return qc
}

async function pollingAt(isoTime: string, marketStatus = 'trading') {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(isoTime))
  vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail(marketStatus) as any)
  vi.mocked(api.taiwanDataStatus).mockResolvedValue(dataStatus('2026-10-20') as any)
  const qc = renderDetail()
  await screen.findByRole('heading', { name: '台積電' })
  const query = qc.getQueryCache().find({ queryKey: ['taiwan-stock-detail'], exact: false })!
  const interval = query.observers[0].options.refetchInterval
  return typeof interval === 'function' ? interval(query as any) : interval
}

describe('B064 detail polling follows the Taipei session', () => {
  it('polls every 10 seconds during the session', async () => {
    expect(await pollingAt('2026-10-21T02:00:00Z')).toBe(10_000)
  })

  it('does not poll after the close', async () => {
    expect(await pollingAt('2026-10-21T12:00:00Z')).toBe(false)
  })

  it('does not poll on a weekend', async () => {
    expect(await pollingAt('2026-10-24T02:00:00Z')).toBe(false)
  })

  it('does not poll on an exchange holiday reported by the quote', async () => {
    expect(await pollingAt('2026-10-21T02:00:00Z', 'non_trading_day')).toBe(false)
  })
})

describe('B064 detail refresh after the post-close publication', () => {
  async function afterClose(nextAsOf: string) {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-10-21T12:00:00Z'))
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail('closed') as any)
    vi.mocked(api.taiwanDataStatus)
      .mockResolvedValueOnce(dataStatus('2026-10-20') as any)
      .mockResolvedValue(dataStatus(nextAsOf) as any)
    renderDetail()
    await screen.findByRole('heading', { name: '台積電' })
    await vi.waitFor(() => expect(api.taiwanDataStatus).toHaveBeenCalledTimes(1))
    await act(async () => { await vi.advanceTimersByTimeAsync(61_000) })
    await vi.waitFor(() => expect(api.taiwanDataStatus).toHaveBeenCalledTimes(2))
    await act(async () => {})
  }

  it('reloads the detail when a dataset date moves', async () => {
    await afterClose('2026-10-21')

    await vi.waitFor(() => expect(api.taiwanStockDetail).toHaveBeenCalledTimes(2))
  })

  it('reloads the current signals when a dataset date moves', async () => {
    await afterClose('2026-10-21')

    await vi.waitFor(() => expect(api.taiwanCurrentSignals).toHaveBeenCalledTimes(2))
  })

  it('stays quiet when nothing new was published', async () => {
    await afterClose('2026-10-20')

    expect(api.taiwanStockDetail).toHaveBeenCalledTimes(1)
    expect(api.taiwanCurrentSignals).toHaveBeenCalledTimes(1)
  })
})
