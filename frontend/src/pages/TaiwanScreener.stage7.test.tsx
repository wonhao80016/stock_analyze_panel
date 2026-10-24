import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { TaiwanScreener } from './TaiwanScreener'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { saveLastCompareSymbols } from '@/lib/taiwanCompareSymbols'

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

function screenerResponse(symbol = '2330.TWSE', name = '台積電') {
  return {
    total: 1, page: 1, page_size: 50,
    data_dates: { daily_as_of: '2026-08-28', institutional_as_of: '2026-08-28', margin_as_of: '2026-08-28' },
    items: [{
      symbol, name, exchange: 'TWSE', instrument_type: 'stock', close: 1000, change_pct: 0.01,
      volume: 10000, amount: 100000000, foreign_net: 1000, investment_trust_net: 0, dealer_net: 0,
      margin_balance_change: 0, short_balance: 0, short_margin_ratio: 5, is_no_limit: false,
      price_limit_pct: 0.1, ma5: 990, ma20: 980, rsi_14: 55, momentum_5d: 0.02,
    }],
  }
}

function dataStatus() {
  return {
    daily_as_of: '2026-08-28', institutional_as_of: '2026-08-28', margin_as_of: '2026-08-28',
    target_latest_trading_date: '2026-08-28', is_fully_current: true,
    daily_status: 'current', institutional_status: 'current', margin_status: 'current',
    daily_days_behind: 0, institutional_days_behind: 0, margin_days_behind: 0,
    scheduler_enabled: true, scheduled_update_time: '16:30', scheduled_timezone: 'Asia/Taipei',
  }
}

function industryIntelligence(industries: string[]) {
  return {
    trade_date: '2026-08-28', generated_at: '2026-08-28T16:00:00+08:00',
    market_reference: { trade_date: '2026-08-28', total_stock_turnover: 1, market_equal_weight_return_5d: null, market_equal_weight_return_20d: null, comparable_stocks_5d_count: 0, comparable_stocks_20d_count: 0 },
    industries: industries.map(industry => ({
      industry, supported_symbol_count: 10, snapshot_symbol_count: 10, traded_symbol_count: 10,
      comparable_symbol_count: 10, turnover: 1_000_000, turnover_share: 0.1,
      advance_count: 5, decline_count: 3, flat_count: 2, uncompared_count: 0,
      advance_ratio: 0.5, decline_ratio: 0.3, average_change_pct: 0.01, median_change_pct: 0.01,
      foreign_net: null, investment_trust_net: null, dealer_net: null,
      margin_balance_change: null, short_balance_change: null,
      relative_strength_5d: null, relative_strength_20d: null,
      relative_strength_5d_comparable_count: 0, relative_strength_20d_comparable_count: 0,
      top_gainers: [], top_losers: [], top_turnover: [],
    })),
    data_quality: {
      target_trade_date: '2026-08-28', previous_trade_date: '2026-08-27', base_date_5d: null, base_date_20d: null,
      supported_stock_count: 1, classified_stock_count: 1, unclassified_stock_count: 0,
      etfs_excluded_count: 0, industry_count: industries.length, classification_coverage_pct: 100,
      daily_status: 'current', institutional_status: 'current', margin_status: 'current', overall_status: 'complete',
    },
  }
}

function abnormalItem(signals: any[]) {
  return {
    symbol: '2330.TWSE', code: '2330', name: '台積電', industry: '半導體', close: 1000,
    change_pct: 0.06, amount: 100000000, volume_ratio_5d: 3.2, foreign_net: 1000000,
    margin_balance_change: 0, signal_count: signals.length, signals,
  }
}

function evidence(overrides: Record<string, unknown>) {
  return {
    severity: 'high', observed: 0, baseline: null, ratio: null, delta: null, threshold: 0,
    formula: 'f', lookback_sessions: 20, valid_sessions: 20, source: 's', status: 'active',
    ...overrides,
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
  return queryClient
}

function runPayloads() {
  return vi.mocked(api.taiwanScreenerRun).mock.calls.map(c => c[0] as any)
}

function lastRunPayload() {
  return runPayloads().at(-1)!
}

async function openAdvancedFilters() {
  const toggle = await screen.findByRole('button', { name: /進階條件/ })
  if (toggle.getAttribute('aria-expanded') !== 'true') fireEvent.click(toggle)
}

function translation(request: Record<string, unknown>) {
  return {
    request: {
      exchange: 'ALL', instrument: 'ALL', industry: null, sort_by: 'symbol', sort_order: 'asc',
      page: 1, page_size: 50, ...request,
    },
    recognized_conditions: ['條件'],
    unsupported_conditions: [],
    clarification_needed: false,
    clarification_message: null,
  }
}

async function translateAndApply(request: Record<string, unknown>) {
  vi.mocked(api.taiwanScreenerTranslate).mockResolvedValue(translation(request) as any)
  fireEvent.change(await screen.findByPlaceholderText(/例如：找外資買超/), { target: { value: '條件' } })
  fireEvent.click(screen.getByRole('button', { name: /解析條件/ }))
  fireEvent.click(await screen.findByRole('button', { name: '套用條件至表單' }))
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(api.taiwanScreenerRun).mockResolvedValue(screenerResponse() as any)
  vi.mocked(api.taiwanDataStatus).mockResolvedValue(dataStatus() as any)
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

describe('B061 applying a translation keeps every whitelisted field', () => {
  it('carries the nine upper bounds into the run payload in backend units', async () => {
    renderScreener()
    await translateAndApply({
      volume_max: 2_000_000,
      amount_max: 1_000_000_000,
      momentum_5d_max: -0.02,
      vol_ratio_5d_max: 0.8,
      investment_trust_net_max: -500_000,
      dealer_net_max: -100_000,
      margin_balance_change_max: -1_000_000,
      short_balance_max: 300_000,
      short_margin_ratio_max: 5,
    })
    await waitFor(() => expect(lastRunPayload().short_margin_ratio_max).toBe(5))
    const p = lastRunPayload()
    expect(p.volume_max).toBeCloseTo(2_000_000)
    expect(p.amount_max).toBeCloseTo(1_000_000_000)
    expect(p.momentum_5d_max).toBeCloseTo(-0.02)
    expect(p.vol_ratio_5d_max).toBeCloseTo(0.8)
    expect(p.investment_trust_net_max).toBeCloseTo(-500_000)
    expect(p.dealer_net_max).toBeCloseTo(-100_000)
    expect(p.margin_balance_change_max).toBeCloseTo(-1_000_000)
    expect(p.short_balance_max).toBeCloseTo(300_000)
  })

  it('shows the applied upper bounds in the form', async () => {
    renderScreener()
    await translateAndApply({ volume_max: 2_000_000, short_margin_ratio_max: 5 })
    await openAdvancedFilters()
    expect(screen.getByLabelText('成交量上限 (張)')).toHaveValue(2000)
    expect(screen.getByLabelText('券資比上限 (%)')).toHaveValue(5)
  })

  it('applies translated signals and replaces the previous selection', async () => {
    renderScreener()
    await openAdvancedFilters()
    fireEvent.click(screen.getByRole('button', { name: 'MA5上穿MA20' }))
    await waitFor(() => expect(lastRunPayload().signals).toEqual(['signal_ma_golden_5_20']))
    await translateAndApply({ signals: ['signal_macd_golden'] })
    await waitFor(() => expect(lastRunPayload().signals).toEqual(['signal_macd_golden']))
  })

  it('names the conditions the form cannot carry before they are applied', async () => {
    vi.mocked(api.taiwanScreenerTranslate).mockResolvedValue(
      translation({ distance_to_upper_limit_max: 0.02 }) as any,
    )
    renderScreener()
    fireEvent.change(await screen.findByPlaceholderText(/例如：找外資買超/), { target: { value: '條件' } })
    fireEvent.click(screen.getByRole('button', { name: /解析條件/ }))
    expect(await screen.findByText(/無法套用至表單/)).toHaveTextContent('distance_to_upper_limit_max')
  })
})

describe('B062 reset clears the selected signals', () => {
  it('sends null signals after reset even with the advanced block collapsed', async () => {
    renderScreener()
    await openAdvancedFilters()
    fireEvent.click(screen.getByRole('button', { name: 'MACD金叉' }))
    await waitFor(() => expect(lastRunPayload().signals).toEqual(['signal_macd_golden']))
    fireEvent.click(screen.getByRole('button', { name: /進階條件/ }))
    fireEvent.click(screen.getByRole('button', { name: '重置所有篩選' }))
    // Back to the default payload the cached first scan answers, so change one control to see the next request.
    fireEvent.click(screen.getByRole('button', { name: '上市 (TWSE)' }))
    await waitFor(() => expect(lastRunPayload().exchange).toBe('TWSE'))
    expect(lastRunPayload().signals).toBeNull()
  })
})

describe('B063 / B306 abnormal signal labels come from the signal library', () => {
  it('labels each subtype by its direction', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue({
      diagnostic_count: 1,
      items: [abnormalItem([
        evidence({ type: 'SHORT_SURGE', subtype: 'DECREASE', observed: -300000, baseline: 50000, ratio: 6 }),
        evidence({ type: 'MARGIN_SURGE', subtype: 'DECREASE', observed: -900000, baseline: 100000, ratio: 9 }),
        evidence({ type: 'PRICE_FLOW_DIVERGENCE', subtype: 'PRICE_UP_FOREIGN_SELL', observed: 4.2, baseline: -300000 }),
        evidence({ type: 'RELATIVE_STRENGTH_OUTLIER', subtype: 'LAGGARD', observed: -12.5, baseline: 3.1, delta: -12.5 }),
        evidence({ type: 'TURNOVER_SPIKE', observed: 5e8, baseline: 1e8, ratio: 5 }),
      ])],
    } as any)
    renderScreener()
    const row = (await screen.findByRole('button', { name: /台積電/ })).closest('tr') as HTMLElement
    const cell = within(row)
    expect(cell.getByText('融券大減')).toBeInTheDocument()
    expect(cell.queryByText('融券激增')).not.toBeInTheDocument()
    expect(cell.getByText('融資大減')).toBeInTheDocument()
    expect(cell.getByText('價漲外資賣')).toBeInTheDocument()
    expect(cell.getByText('弱於產業')).toBeInTheDocument()
    expect(cell.getByText('成交額放大')).toBeInTheDocument()
    expect(cell.queryByText('價量背離')).not.toBeInTheDocument()
  })

  it('describes the divergence evidence as the foreign net, not as a baseline', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue({
      diagnostic_count: 1,
      items: [abnormalItem([
        evidence({ type: 'PRICE_FLOW_DIVERGENCE', subtype: 'PRICE_UP_FOREIGN_SELL', observed: 4.2, baseline: -300000 }),
      ])],
    } as any)
    renderScreener()
    const label = await screen.findByText('價漲外資賣')
    expect(label.getAttribute('title')).toContain('外資買賣超 -300,000 股')
    expect(label.getAttribute('title')).not.toContain('基期')
  })
})

describe('B302 translation cannot be sent twice', () => {
  it('ignores Enter while a translation is in flight', async () => {
    vi.mocked(api.taiwanScreenerTranslate).mockReturnValue(new Promise(() => {}))
    renderScreener()
    const input = await screen.findByPlaceholderText(/例如：找外資買超/)
    fireEvent.change(input, { target: { value: '外資買超' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.keyDown(input, { key: 'Enter' })
    await screen.findByText('解析中...')
    expect(api.taiwanScreenerTranslate).toHaveBeenCalledTimes(1)
  })
})

describe('B303 typing does not rescan the market per keystroke', () => {
  it('sends only the settled value of a number input', async () => {
    renderScreener()
    const input = await screen.findByLabelText('漲跌幅下限 (%)')
    await waitFor(() => expect(api.taiwanScreenerRun).toHaveBeenCalled())
    fireEvent.change(input, { target: { value: '1' } })
    fireEvent.change(input, { target: { value: '12' } })
    await waitFor(() => expect(lastRunPayload().change_pct_min).toBeCloseTo(0.12))
    expect(runPayloads().some(p => p.change_pct_min !== null && Math.abs(p.change_pct_min - 0.01) < 1e-9)).toBe(false)
  })

  it('marks the previous rows as updating while the next scan is loading', async () => {
    renderScreener()
    expect(await screen.findByRole('button', { name: '2330.TWSE' })).toBeInTheDocument()
    vi.mocked(api.taiwanScreenerRun).mockReturnValue(new Promise(() => {}))
    fireEvent.change(screen.getByLabelText('漲跌幅下限 (%)'), { target: { value: '3' } })
    await waitFor(() => expect(lastRunPayload().change_pct_min).toBeCloseTo(0.03))
    expect(screen.getByRole('button', { name: '2330.TWSE' })).toBeInTheDocument()
    expect(screen.getByText(/更新中/)).toBeInTheDocument()
    expect(document.getElementById('screener-results-table')).toHaveClass('opacity-50')
  })

  it('does not call an empty previous result a no-match while updating', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue({ ...screenerResponse(), total: 0, items: [] } as any)
    renderScreener()
    expect(await screen.findByText('沒有符合當前條件的台股標的')).toBeInTheDocument()
    vi.mocked(api.taiwanScreenerRun).mockReturnValue(new Promise(() => {}))
    fireEvent.change(screen.getByLabelText('漲跌幅下限 (%)'), { target: { value: '3' } })
    await waitFor(() => expect(lastRunPayload().change_pct_min).toBeCloseTo(0.03))
    expect(screen.getByText(/更新中/)).toBeInTheDocument()
    expect(screen.queryByText('沒有符合當前條件的台股標的')).not.toBeInTheDocument()
  })

  it('resets the page only when the new value is sent', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue({ ...screenerResponse(), total: 120 } as any)
    renderScreener()
    fireEvent.click(await screen.findByRole('button', { name: '下一頁' }))
    await waitFor(() => expect(lastRunPayload().page).toBe(2))
    const before = runPayloads().length
    fireEvent.change(screen.getByLabelText('漲跌幅下限 (%)'), { target: { value: '3' } })
    await waitFor(() => expect(lastRunPayload().change_pct_min).toBeCloseTo(0.03))
    const sent = runPayloads().slice(before)
    expect(sent).toHaveLength(1)
    expect(sent[0].page).toBe(1)
  })

  it('passes the query abort signal to the request', async () => {
    renderScreener()
    await waitFor(() => expect(api.taiwanScreenerRun).toHaveBeenCalled())
    expect(vi.mocked(api.taiwanScreenerRun).mock.calls[0][1]).toBeInstanceOf(AbortSignal)
  })
})

describe('B304 / B285 Taiwan screener queries use the shared query keys', () => {
  it('refetches the data status when QK.taiwanDataStatus is invalidated', async () => {
    const qc = renderScreener()
    await waitFor(() => expect(api.taiwanDataStatus).toHaveBeenCalledTimes(1))
    await qc.invalidateQueries({ queryKey: QK.taiwanDataStatus })
    await waitFor(() => expect(api.taiwanDataStatus).toHaveBeenCalledTimes(2))
  })

  it('registers the market intelligence queries under QK', async () => {
    const qc = renderScreener()
    await waitFor(() => expect(api.taiwanAbnormalDiagnostics).toHaveBeenCalled())
    await waitFor(() => expect(api.taiwanScreenerRun).toHaveBeenCalled())
    const cache = qc.getQueryCache()
    expect(cache.find({ queryKey: QK.taiwanMarketIntelligence })).toBeDefined()
    expect(cache.find({ queryKey: QK.taiwanIndustryIntelligence('turnover', 'desc') })).toBeDefined()
    expect(cache.find({ queryKey: QK.taiwanAbnormalDiagnostics('ALL') })).toBeDefined()
    expect(cache.findAll({ queryKey: QK.taiwanScreenerAll }).length).toBeGreaterThan(0)
  })
})

describe('B305 the industry picked from the table is visible and clearable', () => {
  it('shows a chip for the applied industry and clears it', async () => {
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(industryIntelligence(['半導體業']) as any)
    renderScreener()
    fireEvent.click(await screen.findByRole('button', { name: '半導體業' }))
    await waitFor(() => expect(lastRunPayload().industry).toBe('半導體業'))
    expect(screen.getByText('產業：半導體業')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '清除產業篩選' }))
    expect(screen.queryByText('產業：半導體業')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '上市 (TWSE)' }))
    await waitFor(() => expect(lastRunPayload().exchange).toBe('TWSE'))
    expect(lastRunPayload().industry).toBeNull()
  })
})

describe('F5 out-of-range values are flagged and never sent', () => {
  it('shows the bound next to the field and keeps the last valid scan', async () => {
    renderScreener()
    const input = await screen.findByLabelText('價格下限 (元)')
    expect(input).toHaveAttribute('min', '0')
    await waitFor(() => expect(api.taiwanScreenerRun).toHaveBeenCalled())
    fireEvent.change(input, { target: { value: '-5' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('價格需 ≥ 0')
    await new Promise(resolve => setTimeout(resolve, 450))
    expect(runPayloads().some(p => p.price_min === -5)).toBe(false)
    fireEvent.change(input, { target: { value: '5' } })
    await waitFor(() => expect(lastRunPayload().price_min).toBe(5))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('bounds the change and RSI ranges on both sides', async () => {
    renderScreener()
    await openAdvancedFilters()
    expect(screen.getByLabelText('漲跌幅上限 (%)')).toHaveAttribute('max', '100')
    expect(screen.getByLabelText('漲跌幅下限 (%)')).toHaveAttribute('min', '-100')
    fireEvent.change(screen.getByLabelText('RSI上限'), { target: { value: '120' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('RSI需介於 0 至 100')
  })
})

describe('F6 applying a translation clears the volume patterns', () => {
  it('sends no volume patterns after apply', async () => {
    renderScreener()
    await openAdvancedFilters()
    fireEvent.click(screen.getByRole('button', { name: '倍量' }))
    await waitFor(() => expect(lastRunPayload().volume_patterns).toEqual(['vp_double']))
    await translateAndApply({ short_margin_ratio_max: 5 })
    await waitFor(() => expect(lastRunPayload().short_margin_ratio_max).toBe(5))
    expect(lastRunPayload().volume_patterns).toBeNull()
  })
})

function ComparePath() {
  const location = useLocation()
  return <div data-testid="compare-path">{location.pathname}{location.search}</div>
}

function renderWithCompareRoute() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/taiwan-screener']}>
        <Routes>
          <Route path="/taiwan-screener" element={<TaiwanScreener />} />
          <Route path="/stocks/compare" element={<ComparePath />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('adding to a full compare list asks first', () => {
  const FULL = ['2317.TWSE', '2454.TWSE', '2412.TWSE', '1301.TWSE', '2882.TWSE']

  it('stays on the page when the user declines', async () => {
    saveLastCompareSymbols(FULL)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderWithCompareRoute()
    fireEvent.click(await screen.findByRole('button', { name: '將 2330.TWSE 加入比較' }))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('compare-path')).not.toBeInTheDocument()
    confirm.mockRestore()
  })

  it('replaces the earliest symbol once confirmed', async () => {
    saveLastCompareSymbols(FULL)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderWithCompareRoute()
    fireEvent.click(await screen.findByRole('button', { name: '將 2330.TWSE 加入比較' }))
    const path = await screen.findByTestId('compare-path')
    expect(decodeURIComponent(path.textContent ?? '')).toBe('/stocks/compare?symbols=2454.TWSE,2412.TWSE,1301.TWSE,2882.TWSE,2330.TWSE')
    confirm.mockRestore()
  })
})
