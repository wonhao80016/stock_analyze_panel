import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type TaiwanRealtimeQuote } from '@/lib/api'
import { useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { TaiwanIndexBar } from './TaiwanIndexBar'

vi.mock('@/lib/api', () => ({
  api: { taiwanQuotes: vi.fn(), preferences: vi.fn() },
  BOARD_LOT_VOLUME_NOTE: '盤中為整股量（不含零股、鉅額、盤後定價）',
}))

vi.mock('@/lib/useQuoteStream', () => ({ useQuoteStreamStatus: vi.fn() }))

function index(symbol: string, name: string, marketStatus = 'open'): TaiwanRealtimeQuote {
  return {
    symbol,
    name,
    exchange: symbol.endsWith('TWSE') ? 'TWSE' : 'TPEX',
    last_price: 23000,
    prev_close: 22900,
    change: 100,
    change_pct: 0.44,
    quote_time: '2026-10-22T10:00:00+08:00',
    trade_date: '2026-10-22',
    market_status: marketStatus,
    source_meta: { source: 'twse:mis', status: 'realtime', is_stale: false },
  } as TaiwanRealtimeQuote
}

function mockIndexes(marketStatus = 'open') {
  vi.mocked(api.taiwanQuotes).mockResolvedValue({
    quotes: [index('t00.TWSE', '加權指數', marketStatus), index('o00.TPEX', '櫃買指數', marketStatus)],
    count: 2,
  } as never)
}

function setClock(iso: string) {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(iso))
}

async function renderBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <TaiwanIndexBar />
    </QueryClientProvider>,
  )
  await screen.findByText('加權指數')
  await act(async () => {})
}

async function callsAfter(ms: number) {
  const before = vi.mocked(api.taiwanQuotes).mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
  return vi.mocked(api.taiwanQuotes).mock.calls.length - before
}

beforeEach(() => {
  vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: false } as never)
  vi.mocked(useQuoteStreamStatus).mockReturnValue('connected')
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('TaiwanIndexBar refresh (P3-a, P3-c)', () => {
  it('polls every 10 s during the session while realtime quotes are off', async () => {
    setClock('2026-10-22T02:00:00Z')
    mockIndexes()
    await renderBar()

    expect(await callsAfter(10_500)).toBeGreaterThan(0)
  })

  it('polls during the session while the stream is disconnected', async () => {
    setClock('2026-10-22T02:00:00Z')
    vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: true } as never)
    vi.mocked(useQuoteStreamStatus).mockReturnValue('disconnected')
    mockIndexes()
    await renderBar()

    expect(await callsAfter(10_500)).toBeGreaterThan(0)
  })

  it('leaves refreshing to SSE while realtime quotes are on and connected', async () => {
    setClock('2026-10-22T02:00:00Z')
    vi.mocked(api.preferences).mockResolvedValue({ realtime_quotes_enabled: true } as never)
    mockIndexes()
    await renderBar()

    expect(await callsAfter(30_000)).toBe(0)
  })

  it('does not poll after the close or on a weekend', async () => {
    setClock('2026-10-22T07:00:00Z')
    mockIndexes('closed')
    await renderBar()
    expect(await callsAfter(60_000)).toBe(0)

    vi.setSystemTime(new Date('2026-10-24T02:00:00Z'))
    expect(await callsAfter(120_000)).toBe(0)
  })
})
