import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockDetail } from './TaiwanStockDetail'
import { api, ApiError } from '@/lib/api'
import { setVolumeUnit } from '@/lib/volumeUnit'

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
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    taiwanStockDetail: vi.fn(),
    taiwanCurrentData: vi.fn().mockResolvedValue(null),
    taiwanStockResearchContext: vi.fn().mockResolvedValue(null),
    taiwanStockAIResearch: vi.fn(),
    taiwanInstitutionalHistory: vi.fn(),
    taiwanMarginHistory: vi.fn().mockResolvedValue({ rows: [] }),
  },
}))

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

afterEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.taiwanStockDetail).mockReset()
  setVolumeUnit('lots')
  localStorage.clear()
})

function detail(symbol = '2330.TWSE', overrides: Record<string, unknown> = {}) {
  return {
    symbol,
    identity: { symbol, code: symbol.split('.')[0], name: '台積電', exchange: 'TWSE', instrument_type: 'stock', is_supported: true },
    price_limit: { is_no_limit: false, rule_type: 'standard' },
    realtime: { last_price: 100, change: 0, change_pct: 0, volume: 625_123, market_status: 'closed', bids: [], asks: [] },
    daily_history: { status: 'available', rows: [] },
    institutional: { status: 'unavailable' },
    margin: { status: 'unavailable' },
    factors: { status: 'unavailable' },
    market_context: { benchmark_symbol: 't00.TWSE', benchmark_name: '發行量加權股價指數' },
    monitor_summary: { rule_count: 0, active_count: 0, rules: [] },
    recent_alerts: [],
    overall_data_quality: 'partial',
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

function renderRoute(path = '/stocks/2330.TWSE') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/stocks/compare" element={<LocationProbe />} />
          <Route path="/stocks/:symbol" element={<TaiwanStockDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function renderEmbedded(symbol: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const ui = (s: string) => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TaiwanStockDetail embeddedSymbol={s} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const view = render(ui(symbol))
  return { switchTo: (s: string) => view.rerender(ui(s)) }
}

function report(symbol: string) {
  return {
    status: 'success',
    report: { symbol, name: symbol, overview: `${symbol} 研究摘要`, key_observations: [], risk_factors: [], missing_information: [], disclaimer: '免責聲明' },
  }
}

describe('B065 AI report follows the symbol', () => {
  it('drops the previous symbol report after switching', async () => {
    vi.mocked(api.taiwanStockDetail).mockImplementation(async (s: string) => detail(s) as any)
    vi.mocked(api.taiwanStockAIResearch).mockResolvedValue(report('2330.TWSE') as any)
    const view = renderEmbedded('2330.TWSE')

    fireEvent.click(screen.getByRole('button', { name: /生成 AI 研究報告/ }))
    expect(await screen.findByText('2330.TWSE 研究摘要')).toBeInTheDocument()

    view.switchTo('2317.TWSE')

    expect(screen.queryByText('2330.TWSE 研究摘要')).not.toBeInTheDocument()
    expect(screen.getByText('尚未生成 AI 研究報告')).toBeInTheDocument()
  })

  it('discards a response that arrives after the symbol changed', async () => {
    vi.mocked(api.taiwanStockDetail).mockImplementation(async (s: string) => detail(s) as any)
    let resolve!: (value: unknown) => void
    vi.mocked(api.taiwanStockAIResearch).mockReturnValue(new Promise(r => { resolve = r }) as any)
    const view = renderEmbedded('2330.TWSE')

    fireEvent.click(screen.getByRole('button', { name: /生成 AI 研究報告/ }))
    expect(screen.getByText('正在客觀分析中...')).toBeInTheDocument()
    view.switchTo('2317.TWSE')
    await act(async () => { resolve(report('2330.TWSE')) })

    expect(screen.queryByText('2330.TWSE 研究摘要')).not.toBeInTheDocument()
    expect(screen.queryByText('正在客觀分析中...')).not.toBeInTheDocument()
    expect(screen.getByText('尚未生成 AI 研究報告')).toBeInTheDocument()
  })
})

describe('B308 range buttons request trading-day counts', () => {
  it('asks for about 126 sessions by default and 245 for one year', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail() as any)
    renderRoute()

    fireEvent.click(await screen.findByRole('button', { name: '1 年' }))

    await waitFor(() => expect(api.taiwanStockDetail).toHaveBeenCalledTimes(2))
    const days = vi.mocked(api.taiwanStockDetail).mock.calls.map(call => call[1])
    expect(days).toEqual([126, 245])
  })
})

describe('B309 one volume unit per page', () => {
  it('shows the quote volume in the site-wide unit', async () => {
    setVolumeUnit('shares')
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail() as any)
    renderRoute()

    expect(await screen.findByText('63萬股')).toBeInTheDocument()
  })

  it('shows full figures in the chip history table', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail() as any)
    vi.mocked(api.taiwanInstitutionalHistory).mockResolvedValue({
      rows: [{ date: '2026-10-22', foreign_net: 12_345_678, investment_trust_net: -2_600, dealer_net: 0, total_net: 12_343_178 }],
    } as any)
    renderRoute()

    fireEvent.click(await screen.findByRole('button', { name: '查看三大法人歷史資料' }))

    expect(await screen.findByText('+12,346張')).toBeInTheDocument()
    expect(screen.getByText('+12,343張')).toBeInTheDocument()
    expect(screen.getByText('-3張')).toBeInTheDocument()
  })
})

describe('B310 adding to a full compare list', () => {
  const FULL = ['1101.TWSE', '1216.TWSE', '2317.TWSE', '2454.TWSE', '2881.TWSE']

  it('replaces the earliest symbol only after the user confirms', async () => {
    localStorage.setItem('tw_compare:last_symbols', JSON.stringify(FULL))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail() as any)
    renderRoute()

    fireEvent.click(await screen.findByRole('button', { name: /加入比較/ }))

    expect(confirm.mock.calls[0][0]).toContain('1101.TWSE')
    const target = new URL(`http://x${screen.getByTestId('location').textContent}`)
    expect(target.pathname).toBe('/stocks/compare')
    expect(target.searchParams.get('symbols')?.split(',')).toEqual(['1216.TWSE', '2317.TWSE', '2454.TWSE', '2881.TWSE', '2330.TWSE'])
    confirm.mockRestore()
  })

  it('stays on the page when the user declines', async () => {
    localStorage.setItem('tw_compare:last_symbols', JSON.stringify(FULL))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail() as any)
    renderRoute()

    fireEvent.click(await screen.findByRole('button', { name: /加入比較/ }))

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('location')).not.toBeInTheDocument()
    confirm.mockRestore()
  })
})

describe('B311 detail failures', () => {
  it('keeps the data with a light notice when a background refresh fails', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail() as any)
    renderRoute()
    await screen.findByRole('heading', { name: '台積電' })

    vi.mocked(api.taiwanStockDetail).mockRejectedValue(new ApiError('boom', 500, undefined, '/api'))
    fireEvent.click(screen.getByTitle('手動重新整理'))

    expect(await screen.findByText(/更新失敗/)).toBeInTheDocument()
    expect(screen.queryByText('個股資料載入失敗')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '台積電' })).toBeInTheDocument()
  })

  it('lets the first load raise the global error and keeps later refreshes quiet', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail() as any)
    renderRoute()
    await screen.findByRole('heading', { name: '台積電' })
    fireEvent.click(screen.getByTitle('手動重新整理'))
    await waitFor(() => expect(api.taiwanStockDetail).toHaveBeenCalledTimes(2))

    expect(vi.mocked(api.taiwanStockDetail).mock.calls.map(call => call[2])).toEqual([false, true])
  })

  it('names an invalid code only for a 400 or 422 response', async () => {
    vi.mocked(api.taiwanStockDetail).mockRejectedValue(new ApiError('invalid symbol', 422, undefined, '/api'))
    renderRoute('/stocks/XYZ.TWSE')

    expect(await screen.findByText(/代碼是否為標準台股規範/)).toBeInTheDocument()
  })

  it('shows the server message and a retry for any other failure', async () => {
    vi.mocked(api.taiwanStockDetail).mockRejectedValue(new ApiError('security master unavailable', 503, undefined, '/api'))
    renderRoute()

    expect(await screen.findByText('security master unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/代碼是否為標準台股規範/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重試連線' })).toBeInTheDocument()
  })
})

describe('B312 order book levels', () => {
  it('numbers the best ask as 賣 1 when fewer than five levels are quoted', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail('2330.TWSE', {
      realtime: { last_price: 100, change: 0, change_pct: 0, market_status: 'open', bids: [], asks: [[101, 1000], [102, 2000], [103, 3000]] },
    }) as any)
    renderRoute()

    const best = (await screen.findByText('101.00')).parentElement as HTMLElement
    expect(best.textContent).toContain('賣 1')
    expect((screen.getByText('103.00').parentElement as HTMLElement).textContent).toContain('賣 3')
  })
})

describe('B313 chip factor card periods', () => {
  it('labels the single-day margin change and ratio apart from the 5-day flows', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail('2330.TWSE', {
      factors: { status: 'available', foreign_net_5d: 1000, investment_trust_net_5d: 0, dealer_net_5d: 0, margin_balance_change: -2000, short_margin_ratio: 3.2 },
    }) as any)
    renderRoute()

    expect(await screen.findByText('融資單日增減')).toBeInTheDocument()
    expect(screen.getByText('當日券資比')).toBeInTheDocument()
    expect(screen.queryByText('近 5 日指標')).not.toBeInTheDocument()
  })
})

describe('B314 alert time', () => {
  it('shows the trigger date and Taipei time', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail('2330.TWSE', {
      recent_alerts: [{ alert_id: 'a1', rule_id: 'r1', rule_name: '突破', rule_type: 'price', severity: 'warning',
        trigger_price: 100, trigger_value: 0, message: 'm', triggered_at: '2026-10-21T18:30:00+00:00' }],
    }) as any)
    renderRoute()

    expect(await screen.findByText('10-22 02:30:00')).toBeInTheDocument()
  })
})

describe('B315 research card colours', () => {
  it('colours returns and MA20 distance with the page up and down colours', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(detail() as any)
    vi.mocked(api.taiwanStockResearchContext).mockResolvedValue({
      symbol: '2330.TWSE',
      as_of_date: '2026-10-08',
      data_quality: { overall_status: 'partial', sections: [], target_trade_date: '2026-10-08' },
      evidence_summary: { known_fields_count: 1, missing_fields_count: 0, derived_fields_count: 1, missing_sections: [] },
      price_context: { trade_date: '2026-10-08', close: 100, return_5d: 0.05, return_20d: -0.03 },
      technical_context: { distance_to_ma20: 0.02, vol_ratio_5d: 1.2 },
      industry_context: { industry: null },
    } as any)
    renderRoute()

    expect(await screen.findByText('5.00%')).toHaveClass('text-rose-500')
    expect(screen.getByText('-3.00%')).toHaveClass('text-emerald-500')
    expect(screen.getByText('2.00%')).toHaveClass('text-rose-500')
  })
})
