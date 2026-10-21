import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockDetail } from './TaiwanStockDetail'
import { api } from '@/lib/api'

const setOption = vi.fn()

vi.mock('echarts', () => ({
  init: () => ({
    on: vi.fn(),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
    setOption,
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
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    taiwanStockDetail: vi.fn(),
    taiwanCurrentData: vi.fn().mockResolvedValue(null),
    taiwanStockResearchContext: vi.fn().mockResolvedValue(null),
    taiwanStockAIResearch: vi.fn(),
  },
}))

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

afterEach(() => {
  setOption.mockClear()
  vi.mocked(api.taiwanStockDetail).mockReset()
})

function detail(symbol: string, instrumentType: string, volume: number) {
  const rows = Array.from({ length: 10 }, (_, index) => ({
    date: `2026-09-${String(index + 21).padStart(2, '0')}`,
    open: 50, high: 51, low: 49, close: 50.5, volume,
  }))
  return {
    symbol,
    identity: { symbol, code: symbol.split('.')[0], name: '測試', exchange: 'TWSE', instrument_type: instrumentType, is_supported: true },
    price_limit: { is_no_limit: false, rule_type: 'standard' },
    realtime: { last_price: 50.5, change: 0, change_pct: 0, market_status: 'closed', bids: [], asks: [] },
    daily_history: { status: 'available', rows },
    institutional: { status: 'unavailable' },
    margin: { status: 'unavailable' },
    factors: { status: 'unavailable' },
    market_context: { benchmark_symbol: 't00.TWSE', benchmark_name: '發行量加權股價指數' },
    monitor_summary: { rule_count: 0, active_count: 0, rules: [] },
    recent_alerts: [],
    overall_data_quality: 'partial',
  }
}

function renderPage(symbol: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/stocks/${symbol}`]}>
        <Routes>
          <Route path="/stocks/:symbol" element={<TaiwanStockDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function lastFullOption() {
  const calls = setOption.mock.calls.filter(call => call[1] === true)
  return calls[calls.length - 1][0]
}

function volumePanelTitle(): string | undefined {
  const graphic = (lastFullOption().graphic ?? []) as { id: string; style: { text: string } }[]
  return graphic.find(item => item.id === 'sub-label-vol')?.style.text
}

describe('TaiwanStockDetail daily K volume', () => {
  it('shows the volume panel with the shared unit toggle', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail('2330.TWSE', 'stock', 625_123) as any)
    renderPage('2330.TWSE')

    const toggle = await screen.findByRole('group', { name: '成交量單位' })
    expect(volumePanelTitle()).toBe('成交量(張)')
    const volumeSeries = lastFullOption().series.find((s: { name: string }) => s.name === '成交量')
    expect(volumeSeries.data[0].value).toBe(625_123)

    fireEvent.click(within(toggle).getByRole('button', { name: '股' }))

    expect(volumePanelTitle()).toBe('成交量(股)')
  })

  it('leaves the volume panel out for an index, whose series carry no volume', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail('t00.TWSE', 'index', 0) as any)
    renderPage('t00.TWSE')

    await screen.findByText('歷史日 K 線')
    await vi.waitFor(() => expect(setOption).toHaveBeenCalled())
    expect(volumePanelTitle()).toBeUndefined()
    expect(lastFullOption().series.some((s: { name: string }) => s.name === '成交量')).toBe(false)
    expect(screen.queryByRole('group', { name: '成交量單位' })).not.toBeInTheDocument()
  })
})
