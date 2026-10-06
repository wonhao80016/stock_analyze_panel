// Phase 7K — TaiwanScreener 異常訊號面板導航正確性回歸測試
// 涵蓋：異常訊號面板列連結指向真實已註冊路由 /stocks/:symbol（保留交易所後綴），
// 不再指向不存在的 /taiwan/stocks/:symbol；主篩選表格既有正確連結行為不受影響。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useSearchParams } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { TaiwanScreener } from './TaiwanScreener'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanScreenerRun: vi.fn(),
    taiwanDataStatus: vi.fn(),
    taiwanMarketIntelligence: vi.fn(),
    taiwanIndustryIntelligence: vi.fn(),
    taiwanAbnormalDiagnostics: vi.fn(),
    // Phase 8C-A: 結果 row 直接操作 (自選/比較/監控)
    watchlistList: vi.fn(),
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn(),
    taiwanSearch: vi.fn(),
    // 監控規則合併: 結果 row「監控」改開統一的 RuleEditor(simple 模式), 依賴通用
    // 監控規則的選項/偏好等查詢 —— 全部回傳最小可用形狀, 本檔測試不驗證其內容。
    monitorRuleOptions: vi.fn().mockResolvedValue({
      threshold_fields: [],
      builtin_signals: [],
      custom_signals: [],
      operators: ['>', '>=', '<', '<=', '==', '!='],
      types: [],
      scopes: [],
      logics: [],
      severities: [],
      directions: [],
      intraday_signal_support: { available: true, source: null, max_symbols: 0, reason: '' },
      sector_targets: { index: [], concept: [], industry: [] },
    }),
    preferences: vi.fn().mockResolvedValue({}),
    customSignalsList: vi.fn().mockResolvedValue({ signals: [] }),
    instrumentNames: vi.fn().mockResolvedValue({ names: {} }),
  },
  genRuleId: () => 'mr_test',
}))

function buildScreenerResponse() {
  return {
    total: 1,
    page: 1,
    page_size: 50,
    data_dates: { daily_as_of: '2026-08-28', institutional_as_of: '2026-08-28', margin_as_of: '2026-08-28' },
    items: [
      {
        symbol: '2330.TWSE',
        name: '台積電',
        exchange: 'TWSE',
        instrument_type: 'stock',
        close: 1000,
        change_pct: 0.01,
        volume: 10000,
        amount: 100000000,
        foreign_net: 1000,
        investment_trust_net: 0,
        dealer_net: 0,
        margin_balance_change: 0,
        short_balance: 0,
        short_margin_ratio: 5,
        is_no_limit: false,
        price_limit_pct: 0.1,
        ma5: 990,
        ma20: 980,
        rsi_14: 55,
        momentum_5d: 0.02,
      },
    ],
  }
}

function buildDataStatus() {
  return {
    daily_as_of: '2026-08-28',
    institutional_as_of: '2026-08-28',
    margin_as_of: '2026-08-28',
    target_latest_trading_date: '2026-08-28',
    is_fully_current: true,
    daily_status: 'current',
    institutional_status: 'current',
    margin_status: 'current',
    daily_days_behind: 0,
    institutional_days_behind: 0,
    margin_days_behind: 0,
    scheduler_enabled: true,
    scheduled_update_time: '16:30',
    scheduled_timezone: 'Asia/Taipei',
  }
}

function buildAbnormalSignal(overrides: Partial<any> = {}) {
  return {
    symbol: '2330.TWSE',
    code: '2330',
    name: '台積電',
    industry: '半導體',
    close: 1000,
    change_pct: 0.06,
    amount: 100000000,
    volume_ratio_5d: 3.2,
    foreign_net: 1000000,
    margin_balance_change: 0,
    signal_count: 1,
    signals: [{ type: 'PRICE_MOVE', subtype: 'UP', formula: 'abs(chg)>=5%', observed: 0.06, baseline: 0.05 }],
    ...overrides,
  }
}

function buildAbnormalDiagnostics(items: any[]) {
  return { items, universe_count: items.length, generated_at: '2026-08-28T16:00:00+08:00' }
}

function renderScreener() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/taiwan-screener']}>
        <Routes>
          <Route path="/taiwan-screener" element={<TaiwanScreener />} />
          <Route
            path="/stocks/:symbol"
            element={<StockDetailMock />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return queryClient
}

function StockDetailMock() {
  // Reads the matched :symbol via window.location since useParams needs the route context;
  // simplest robust signal for these tests is just a stable marker element.
  return <div data-testid="detail-mock">stock-detail-page</div>
}

// Phase 8C-A: 驗證「加入比較」handoff 帶著 symbols query param 進入 /stocks/compare
// (MemoryRouter 不會同步真實 window.location, 需透過 useSearchParams 讀取)
function CompareMock() {
  const [params] = useSearchParams()
  return <div data-testid="compare-mock">{params.get('symbols')}</div>
}

function renderScreenerWithCompareRoute() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/taiwan-screener']}>
        <Routes>
          <Route path="/taiwan-screener" element={<TaiwanScreener />} />
          <Route path="/stocks/compare" element={<CompareMock />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return queryClient
}

beforeEach(() => {
  vi.mocked(api.taiwanScreenerRun).mockResolvedValue(buildScreenerResponse() as any)
  vi.mocked(api.taiwanDataStatus).mockResolvedValue(buildDataStatus() as any)
  vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue(
    buildAbnormalDiagnostics([
      buildAbnormalSignal({ symbol: '2330.TWSE', code: '2330', name: '台積電' }),
      buildAbnormalSignal({ symbol: '8069.TPEX', code: '8069', name: '元太' }),
    ]) as any,
  )
  vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistAdd).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistRemove).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistGroups).mockResolvedValue({ groups: [] } as any)
  vi.mocked(api.taiwanSearch).mockResolvedValue({ results: [] } as any)
})

afterEach(() => {
  vi.clearAllMocks()
})

// 標的一律以彈出視窗開啟, 不再離開當前頁面導向 /stocks/:symbol。
describe('Abnormal diagnostics panel opens the preview card', () => {
  it('renders the TWSE row as a button, not a route link', async () => {
    renderScreener()
    expect(await screen.findByRole('button', { name: /台積電/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /台積電/ })).toBeNull()
  })

  it('renders the TPEx row as a button, not a route link', async () => {
    renderScreener()
    expect(await screen.findByRole('button', { name: /元太/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /元太/ })).toBeNull()
  })

  it('clicking a row never navigates to the stock detail route', async () => {
    renderScreener()
    fireEvent.click(await screen.findByRole('button', { name: /台積電/ }))
    await waitFor(() => expect(screen.queryByTestId('detail-mock')).toBeNull())
  })

  it('main screener table exposes the symbol as a button too', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(screen.getAllByRole('button', { name: '2330.TWSE' }).length).toBeGreaterThan(0)
    expect(screen.queryByRole('link', { name: '2330.TWSE' })).toBeNull()
  })
})

describe('Indicator publication notice', () => {
  it('shows the updating notice when the indicators section is degraded', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue({ ...buildScreenerResponse(), degraded_sections: ['indicators'] } as any)
    renderScreener()
    expect(await screen.findByText('技術指標更新中，暫以空值顯示')).toBeTruthy()
  })

  it('shows no notice when indicators are published', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(screen.queryByText('技術指標更新中，暫以空值顯示')).toBeNull()
  })

  it('shows the price-limit notice when the limit references are missing', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue({ ...buildScreenerResponse(), degraded_sections: ['price_limits'] } as any)
    renderScreener()
    expect(await screen.findByText('當日缺少漲跌停參考價，「逼近漲停／跌停」暫無法判斷')).toBeTruthy()
  })

  it('shows no price-limit notice when the limit references are published', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue({ ...buildScreenerResponse(), degraded_sections: ['volume_patterns'] } as any)
    renderScreener()
    expect(await screen.findByText('量價型態尚未發佈')).toBeTruthy()
    expect(screen.queryByText(/漲跌停參考價/)).toBeNull()
  })
})

describe('Main results row direct actions (Phase 8C-A)', () => {
  it('offers 加入自選/加入比較/新增監控/回測/研究 without leaving the page first', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))

    expect(await screen.findByRole('button', { name: '將 2330.TWSE 加入自選' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '將 2330.TWSE 加入比較' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '為 2330.TWSE 新增監控' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '使用 2330.TWSE 回測' })).toHaveAttribute(
      'href',
      '/backtest?symbol=2330.TWSE&asset_type=stock',
    )
    expect(screen.getByRole('button', { name: '開啟 2330.TWSE 個股視窗' })).toBeInTheDocument()
  })

  it('passes ETF rows to the backtest workspace as ETF assets', async () => {
    const response = buildScreenerResponse()
    response.items[0].instrument_type = 'etf'
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue(response as any)
    renderScreener()

    expect(await screen.findByRole('link', { name: '使用 2330.TWSE 回測' })).toHaveAttribute(
      'href',
      '/backtest?symbol=2330.TWSE&asset_type=etf',
    )
  })

  it('加入自選 is a genuine 1-click quick-add — no group picker in the way (Phase 8C-A fix)', async () => {
    renderScreener()
    const addButton = await screen.findByRole('button', { name: '將 2330.TWSE 加入自選' })
    fireEvent.click(addButton)
    // 直接呼叫 watchlistAdd(symbol, '', undefined) → api.ts 正規化為 group_id=null
    // (與既有「未分組」選項相同 backend 語意), 不彈任何選單, 單次 click 即完成。
    await waitFor(() => expect(api.watchlistAdd).toHaveBeenCalledWith('2330.TWSE', '', undefined))
    expect(screen.queryByRole('menuitem', { name: /未分組/ })).not.toBeInTheDocument()
    // 仍停留在 Screener, 沒有被導去別的頁面
    expect(screen.queryByTestId('detail-mock')).not.toBeInTheDocument()
  })

  it('選擇分組加入自選 chevron still opens the group picker (specific-group path preserved)', async () => {
    vi.mocked(api.watchlistGroups).mockResolvedValue({
      groups: [{ id: 'g1', name: '半導體', color: 'blue' }],
    } as any)
    renderScreener()
    const chevron = await screen.findByRole('button', { name: '選擇分組將 2330.TWSE 加入自選' })
    fireEvent.click(chevron)
    const groupOption = await screen.findByRole('menuitem', { name: /半導體/ })
    fireEvent.click(groupOption)
    await waitFor(() => expect(api.watchlistAdd).toHaveBeenCalledWith('2330.TWSE', '', 'g1'))
  })

  it('加入比較 navigates straight to /stocks/compare with the symbol pre-populated', async () => {
    renderScreenerWithCompareRoute()
    const compareButton = await screen.findByRole('button', { name: '將 2330.TWSE 加入比較' })
    fireEvent.click(compareButton)
    await waitFor(() => expect(screen.getByTestId('compare-mock')).toHaveTextContent('2330.TWSE'))
  })

  it('監控 opens the rule editor scoped to the row symbol, without a second Monitor form', async () => {
    renderScreener()
    const monitorButton = await screen.findByRole('button', { name: '為 2330.TWSE 新增監控' })
    fireEvent.click(monitorButton)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: '加入監控' })).toBeInTheDocument()
    expect(within(dialog).getByText('2330.TWSE')).toBeInTheDocument()
  })
})

describe('Abnormal diagnostics panel state UX (Phase 7L)', () => {
  it('shows a loading row when the query is pending with no cached data', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockReturnValue(new Promise(() => {}) as any)
    renderScreener()
    expect(await screen.findByText('正在載入異常診斷…')).toBeInTheDocument()
  })

  it('shows an error row when the query rejects with no cached data', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockRejectedValue(new Error('network error'))
    renderScreener()
    expect(await screen.findByText('異常診斷載入失敗，請稍後再試')).toBeInTheDocument()
  })

  it('error state exposes an accessible alert, with no stack trace/raw exception text', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockRejectedValue(new Error('some internal detail'))
    renderScreener()
    const alertEl = await screen.findByRole('alert')
    expect(alertEl).toHaveTextContent('異常診斷載入失敗，請稍後再試')
    expect(screen.queryByText(/some internal detail/)).not.toBeInTheDocument()
  })

  it('shows an explicit empty-success row when items is []', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue(buildAbnormalDiagnostics([]) as any)
    renderScreener()
    expect(await screen.findByText('目前沒有偵測到異常訊號')).toBeInTheDocument()
  })

  it('empty-success state is not an alert', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue(buildAbnormalDiagnostics([]) as any)
    renderScreener()
    await screen.findByText('目前沒有偵測到異常訊號')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('empty-success state does not use red error styling', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValue(buildAbnormalDiagnostics([]) as any)
    renderScreener()
    const cell = await screen.findByText('目前沒有偵測到異常訊號')
    expect(cell.className).not.toMatch(/text-red-400/)
  })

  it('renders populated abnormal-diagnostics rows on a successful non-empty response', async () => {
    renderScreener()
    expect(await screen.findByRole('button', { name: /台積電/ })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /元太/ })).toBeInTheDocument()
  })

  it('preserves already-loaded populated rows during a background refetch that subsequently errors (cached-data priority)', async () => {
    vi.mocked(api.taiwanAbnormalDiagnostics).mockResolvedValueOnce(
      buildAbnormalDiagnostics([buildAbnormalSignal({ symbol: '2330.TWSE', code: '2330', name: '台積電' })]) as any,
    )
    const queryClient = renderScreener()
    await screen.findByRole('button', { name: /台積電/ })

    // Simulate a background refetch (same query key, still 'ALL' filter — no user action needed
    // to change the key) that fails; cached data must remain visible, not be replaced by an error row.
    vi.mocked(api.taiwanAbnormalDiagnostics).mockRejectedValueOnce(new Error('refetch failed'))
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['taiwanAbnormalDiagnostics', 'ALL'] })
    })

    expect(screen.getByRole('button', { name: /台積電/ })).toBeInTheDocument()
    expect(screen.queryByText('異常診斷載入失敗，請稍後再試')).not.toBeInTheDocument()
    expect(screen.queryByText('正在載入異常診斷…')).not.toBeInTheDocument()
  })
})

// Phase 8C-C — Screener & Watchlist Refinement 回歸測試
function buildIndustryMetric(overrides: Partial<any> = {}) {
  return {
    industry: '半導體業', supported_symbol_count: 10, snapshot_symbol_count: 10, traded_symbol_count: 10,
    comparable_symbol_count: 10, turnover: 1_000_000, turnover_share: 0.1,
    advance_count: 5, decline_count: 3, flat_count: 2, uncompared_count: 0,
    advance_ratio: 0.5, decline_ratio: 0.3, average_change_pct: 0.01, median_change_pct: 0.01,
    foreign_net: null, investment_trust_net: null, dealer_net: null,
    margin_balance_change: null, short_balance_change: null,
    relative_strength_5d: null, relative_strength_20d: null,
    relative_strength_5d_comparable_count: 0, relative_strength_20d_comparable_count: 0,
    top_gainers: [], top_losers: [], top_turnover: [],
    ...overrides,
  }
}

function buildIndustryIntelligence(industries: ReturnType<typeof buildIndustryMetric>[]) {
  return {
    trade_date: '2026-08-28', generated_at: '2026-08-28T16:00:00+08:00',
    market_reference: { trade_date: '2026-08-28', total_stock_turnover: 1, market_equal_weight_return_5d: null, market_equal_weight_return_20d: null, comparable_stocks_5d_count: 0, comparable_stocks_20d_count: 0 },
    industries,
    data_quality: {
      target_trade_date: '2026-08-28', previous_trade_date: '2026-08-27', base_date_5d: null, base_date_20d: null,
      supported_stock_count: industries.length, classified_stock_count: industries.length, unclassified_stock_count: 0,
      etfs_excluded_count: 0, industry_count: industries.length, classification_coverage_pct: 100,
      daily_status: 'current', institutional_status: 'current', margin_status: 'current', overall_status: 'complete',
    },
  }
}

describe('Filter hierarchy — basic vs. advanced (Phase 8C-C)', () => {
  it('basic filters render by default: market/instrument/change-pct/price/volume/amount', async () => {
    renderScreener()
    expect(await screen.findByText('市場交易所')).toBeInTheDocument()
    expect(screen.getByText('標的型態')).toBeInTheDocument()
    expect(screen.getByText('漲跌幅區間 (%)')).toBeInTheDocument()
    expect(screen.getByText('價格區間 (TWD)')).toBeInTheDocument()
    expect(screen.getByText('最低成交量 (張)')).toBeInTheDocument()
    expect(screen.getByText('最低成交金額 (百萬 TWD)')).toBeInTheDocument()
  })

  it('advanced filters are collapsed by default (RSI/institutional/margin fields not shown)', async () => {
    renderScreener()
    await screen.findByText('市場交易所')
    expect(screen.queryByText('RSI (14) 區間')).not.toBeInTheDocument()
    expect(screen.queryByText('外資買賣超區間 (張)')).not.toBeInTheDocument()
    expect(screen.queryByText('券資比最低 (%)')).not.toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: /進階條件/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('expanding advanced filters reveals technical/institutional/margin groups, original controls still work', async () => {
    renderScreener()
    const toggle = await screen.findByRole('button', { name: /進階條件/ })
    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('技術面')).toBeInTheDocument()
    expect(screen.getByText('法人')).toBeInTheDocument()
    expect(screen.getByText('籌碼與融資券')).toBeInTheDocument()
    expect(screen.getByText('RSI (14) 區間')).toBeInTheDocument()
    expect(screen.getByText('外資買賣超區間 (張)')).toBeInTheDocument()
    expect(screen.getByText('券資比最低 (%)')).toBeInTheDocument()

    // 原本站上 MA20 按鈕行為未被破壞
    const ma20Button = screen.getByRole('button', { name: '站上 MA20 (月線)' })
    fireEvent.click(ma20Button)
    await waitFor(() => {
      const lastCall = vi.mocked(api.taiwanScreenerRun).mock.calls.at(-1)![0] as any
      expect(lastCall.above_ma20).toBe(true)
    })
  })
})

describe('Quick presets (Phase 8C-C)', () => {
  it('clicking 強勢股 preset updates existing filter state (above_ma20 + momentum_5d_min), not a second screener logic', async () => {
    renderScreener()
    const preset = await screen.findByRole('button', { name: '強勢股' })
    fireEvent.click(preset)

    await waitFor(() => {
      const lastCall = vi.mocked(api.taiwanScreenerRun).mock.calls.at(-1)![0] as any
      expect(lastCall.above_ma20).toBe(true)
      expect(lastCall.momentum_5d_min).toBeCloseTo(0.02)
    })
    expect(await screen.findByRole('button', { name: '✓ 強勢股', pressed: true })).toBeInTheDocument()
  })

  it('clicking an already-active preset clears just its own fields', async () => {
    renderScreener()
    const preset = await screen.findByRole('button', { name: '放量股' })
    fireEvent.click(preset)
    const active = await screen.findByRole('button', { name: '✓ 放量股' })
    expect(active).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(active)
    // 已清除: 按鈕回到未 active 的原始標籤與 aria-pressed=false
    // (以 UI 狀態驗證, 不看 network mock 呼叫次數 — react-query 對「回到與初次
    // 掛載時完全相同的預設 payload」可能命中既有快取而不重新呼叫 queryFn)。
    expect(await screen.findByRole('button', { name: '放量股' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('button', { name: '✓ 放量股' })).not.toBeInTheDocument()
  })

  it('重置所有篩選 clears an active preset and returns to default filter state', async () => {
    renderScreener()
    const preset = await screen.findByRole('button', { name: '法人買超' })
    fireEvent.click(preset)
    await screen.findByRole('button', { name: '✓ 法人買超' })

    fireEvent.click(screen.getByRole('button', { name: '重置所有篩選' }))
    const reverted = await screen.findByRole('button', { name: '法人買超' })
    expect(reverted).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('button', { name: '✓ 法人買超' })).not.toBeInTheDocument()
  })

  it('row actions (自選/比較/監控/研究) remain available after applying a preset', async () => {
    renderScreener()
    fireEvent.click(await screen.findByRole('button', { name: '突破轉強' }))
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(screen.getByRole('button', { name: '將 2330.TWSE 加入自選' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '將 2330.TWSE 加入比較' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '為 2330.TWSE 新增監控' })).toBeInTheDocument()
  })
})

describe('Industry name display (Phase 8C-C)', () => {
  it('renders the readable industry name from taiwan-industry-intelligence as-is (no numeric code)', async () => {
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(
      buildIndustryIntelligence([buildIndustryMetric({ industry: '半導體業' })]) as any,
    )
    renderScreener()
    // 產業名稱按鈕的可存取名稱直接就是可讀中文名稱, 而不是像 "24"/"91" 這種
    // 數字代碼 — 確認 UI 消費既有 industry-intelligence API 回傳的 industry
    // 欄位時原樣顯示, 沒有在前端另外做代碼轉換或截斷。
    expect(await screen.findByRole('button', { name: '半導體業' })).toBeInTheDocument()
  })

  it('industry intelligence table defaults to top 10 rows with an expand control for the rest', async () => {
    const many = Array.from({ length: 15 }, (_, i) => buildIndustryMetric({ industry: `產業${i + 1}` }))
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(buildIndustryIntelligence(many) as any)
    renderScreener()

    await screen.findByRole('button', { name: '產業1' })
    expect(screen.queryByRole('button', { name: '產業15' })).not.toBeInTheDocument()
    const expandButton = screen.getByRole('button', { name: /顯示全部 15 檔/ })
    expect(expandButton).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(expandButton)
    expect(await screen.findByRole('button', { name: '產業15' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '收合 ▴' })).toHaveAttribute('aria-expanded', 'true')
  })
})

describe('Results table density (Phase 8C-C)', () => {
  it('default view hides secondary technical/institutional columns behind 更多欄位', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(screen.queryByText('投信買賣超')).not.toBeInTheDocument()
    expect(screen.queryByText('RSI (14)')).not.toBeInTheDocument()

    const toggle = screen.getByRole('button', { name: /更多欄位/ })
    fireEvent.click(toggle)
    expect(await screen.findByText('投信買賣超')).toBeInTheDocument()
    expect(screen.getByText('RSI (14)')).toBeInTheDocument()
  })

  it('renders an RSI of exactly 0 as a value, not as missing', async () => {
    const response = buildScreenerResponse()
    response.items[0].rsi_14 = 0
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue(response as any)
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    fireEvent.click(screen.getByRole('button', { name: /更多欄位/ }))

    const rsi = await screen.findByText('0.0')
    expect(rsi).toHaveClass('text-emerald-400')
  })
})

// Data Freshness & Source Labels batch (Post-8C follow-up)：Market Intelligence
// 的 trade_date 是「應有最新交易日」(resolve_target_latest_trading_date, 依當下
// 時間推算)，不是本地實際持有資料的日期。當該日尚無完整市場資料時，下方統計
// 全部是 0 —— 若沒有明確標示，容易被誤讀為「今日零成交」而非「無資料」。
function buildMarketIntelligence(overrides: Partial<Record<string, any>> = {}) {
  return {
    trade_date: '2026-09-08',
    generated_at: '2026-09-08T16:00:00+08:00',
    market_totals: {
      supported_count: 2375, snapshot_row_count: 0, traded_count: 0,
      advance_count: 0, decline_count: 0, flat_count: 0, uncompared_count: 0,
      upper_limit_count: 0, lower_limit_count: 0, turnover: 0,
    },
    by_exchange: { twse: { turnover: 0 } as any, tpex: { turnover: 0 } as any },
    by_instrument: { stock: {} as any, etf: {} as any },
    institutional: { trade_date: null, foreign_net_amount: null, investment_trust_net_amount: null, dealer_net_amount: null, total_net_amount: null, twse: null, tpex: null, status: 'unavailable' },
    margin: { trade_date: '2026-09-08', row_count: 0, margin_balance: null, margin_balance_change: null, short_balance: null, short_balance_change: null, aggregate_short_margin_ratio: null, status: 'unavailable' },
    indexes: { taiex: null, tpex_index: null },
    data_quality: { target_trade_date: '2026-09-08', previous_trade_date: '2026-09-03', overall_status: 'unavailable', universe_supported_symbols: 2375, daily_snapshot_symbols: 0, missing_symbols_count: 2375 },
    ...overrides,
  }
}

describe('Market institutional amounts', () => {
  it('shows foreign, trust and dealer net amounts in 100-million TWD and a dash when one is missing', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      institutional: { trade_date: '2026-09-08', foreign_net_amount: 39_016_214_118, investment_trust_net_amount: -2_203_631_373, dealer_net_amount: null, total_net_amount: null, twse: null, tpex: null, status: 'current' },
    }) as any)
    renderScreener()

    await screen.findByText('交易日: 2026-09-08')
    expect(screen.getByText('+390.16 億元')).toBeInTheDocument()
    expect(screen.getByText('-22.04 億元')).toBeInTheDocument()
    expect(within(screen.getByText('自營商買賣超').parentElement as HTMLElement).getByText('—')).toBeInTheDocument()
  })

  it('orders the seven stat cards so three columns put the institutional cards on one row', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderScreener()

    await screen.findByText('交易日: 2026-09-08')
    const grid = (screen.getByText('自營商買賣超').parentElement as HTMLElement).parentElement as HTMLElement
    const labels = Array.from(grid.children).map((card) => card.querySelector('span')?.textContent)
    expect(labels).toEqual(['全市場漲跌家數', '漲跌停家數', '交易所成交額', '外資買賣超', '投信買賣超', '自營商買賣超', '融資餘額增減'])
    const columns = grid.className.split(' ').filter((c) => c.includes('grid-cols-'))
    expect(columns).toEqual(['grid-cols-2', 'sm:grid-cols-3', 'lg:grid-cols-4', '2xl:grid-cols-7'])
  })
})

describe('Market Intelligence date honesty (Data Freshness & Source Labels batch)', () => {
  it('shows an explicit "no complete data" notice when data_quality.overall_status is not complete, instead of silently presenting all-zero stats as today\'s real market', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderScreener()

    await screen.findByText('交易日: 2026-09-08')
    expect(screen.getByText('尚無完整市場資料，以下統計非今日實際行情')).toBeInTheDocument()
  })

  it('does not show the notice when data_quality.overall_status is complete (genuine zero-activity day stays unflagged)', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(
      buildMarketIntelligence({ data_quality: { target_trade_date: '2026-09-08', previous_trade_date: '2026-09-05', overall_status: 'complete', universe_supported_symbols: 2375, daily_snapshot_symbols: 2375, missing_symbols_count: 0 } }) as any,
    )
    renderScreener()

    await screen.findByText('交易日: 2026-09-08')
    expect(screen.queryByText('尚無完整市場資料，以下統計非今日實際行情')).not.toBeInTheDocument()
    expect(screen.queryByText('盤後初值，次日校正')).not.toBeInTheDocument()
  })

  it('shows the provisional label instead of the no-complete-data notice when overall_status is provisional', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(
      buildMarketIntelligence({ data_quality: { target_trade_date: '2026-09-08', previous_trade_date: '2026-09-05', overall_status: 'provisional', universe_supported_symbols: 2377, daily_snapshot_symbols: 2336, missing_symbols_count: 41 } }) as any,
    )
    renderScreener()

    await screen.findByText('交易日: 2026-09-08')
    expect(screen.getByText('盤後初值，次日校正')).toBeInTheDocument()
    expect(screen.queryByText('尚無完整市場資料，以下統計非今日實際行情')).not.toBeInTheDocument()
  })

  it('keeps the no-complete-data notice when overall_status is partial', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(
      buildMarketIntelligence({ data_quality: { target_trade_date: '2026-09-08', previous_trade_date: '2026-09-05', overall_status: 'partial', universe_supported_symbols: 2377, daily_snapshot_symbols: 2300, missing_symbols_count: 77 } }) as any,
    )
    renderScreener()

    await screen.findByText('交易日: 2026-09-08')
    expect(screen.getByText('尚無完整市場資料，以下統計非今日實際行情')).toBeInTheDocument()
    expect(screen.queryByText('盤後初值，次日校正')).not.toBeInTheDocument()
  })
})

function lastRunPayload() {
  return vi.mocked(api.taiwanScreenerRun).mock.calls.at(-1)![0] as any
}

function resultsTable() {
  return within(document.getElementById('screener-results-table') as HTMLElement)
}

function volumePatternCell(symbol: string) {
  const table = resultsTable()
  const headers = table.getAllByRole('columnheader')
  const index = headers.findIndex(h => h.textContent === '量價型態')
  const row = table.getByRole('button', { name: symbol }).closest('tr') as HTMLElement
  return within(row).getAllByRole('cell')[index]
}

function withPatterns(volume_patterns: string[] | undefined, extra: Record<string, unknown> = {}) {
  const base = buildScreenerResponse()
  return { ...base, items: [{ ...base.items[0], volume_patterns }], ...extra }
}

async function openAdvancedFilters() {
  fireEvent.click(await screen.findByRole('button', { name: /進階條件/ }))
}

describe('Volume pattern filters', () => {
  it('sends no volume patterns by default', async () => {
    renderScreener()
    await waitFor(() => expect(api.taiwanScreenerRun).toHaveBeenCalled())
    expect(lastRunPayload().volume_patterns).toBeNull()
  })

  it('sends a single selected pattern id', async () => {
    renderScreener()
    await openAdvancedFilters()
    fireEvent.click(screen.getByRole('button', { name: '倍量' }))
    await waitFor(() => expect(lastRunPayload().volume_patterns).toEqual(['vp_double']))
    expect(screen.getByRole('button', { name: '倍量' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('sends every selected pattern id when several are selected', async () => {
    renderScreener()
    await openAdvancedFilters()
    fireEvent.click(screen.getByRole('button', { name: '低量' }))
    fireEvent.click(screen.getByRole('button', { name: '放量滯漲' }))
    fireEvent.click(screen.getByRole('button', { name: '梯量' }))
    await waitFor(() =>
      expect(lastRunPayload().volume_patterns).toEqual(['vp_low', 'vp_up_stall', 'vp_step_up']),
    )
    expect(lastRunPayload().signals).toBeNull()
  })

  it('clearing the selection sends null again', async () => {
    renderScreener()
    await openAdvancedFilters()
    fireEvent.click(screen.getByRole('button', { name: '平量' }))
    fireEvent.click(screen.getByRole('button', { name: '縮量' }))
    await waitFor(() => expect(lastRunPayload().volume_patterns).toEqual(['vp_flat', 'vp_step_down']))
    fireEvent.click(screen.getByRole('button', { name: '清除量價型態' }))
    expect(screen.getByRole('button', { name: '平量' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: '縮量' })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByRole('button', { name: '站上 MA5' }))
    await waitFor(() => expect(lastRunPayload().above_ma5).toBe(true))
    expect(lastRunPayload().volume_patterns).toBeNull()
  })

  it('reset clears the selected patterns', async () => {
    renderScreener()
    await openAdvancedFilters()
    fireEvent.click(screen.getByRole('button', { name: '倍量' }))
    await waitFor(() => expect(lastRunPayload().volume_patterns).toEqual(['vp_double']))
    fireEvent.click(screen.getByRole('button', { name: '重置所有篩選' }))
    expect(screen.getByRole('button', { name: '倍量' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('lists the six patterns under the AND label, each with its formula tooltip', async () => {
    renderScreener()
    await openAdvancedFilters()
    expect(screen.getByText('量價型態（多選為且）')).toBeInTheDocument()
    const expected: Record<string, string> = {
      倍量: '今日量 ≥ 前一交易日量 2 倍；兩日皆須有成交（量 > 0）',
      梯量: '連 3 個交易日量遞增，且今日收盤 > 2 日前收盤；3 日皆須有成交（量 > 0）',
      縮量: '連 3 個交易日量遞減，且今日收盤 < 2 日前收盤；3 日皆須有成交（量 > 0）',
      低量: '今日量 < 前 20 個交易日中有成交日均量的 60%；今日須有成交，前 20 日至少 15 日有成交（量 > 0）',
      平量: '連 3 個交易日 (最大量−最小量)／最大量 < 15%；3 日皆須有成交（量 > 0）',
      放量滯漲: '今日量 ≥ 前 5 個交易日均量 1.5 倍，且符合任一：收盤 < 開盤；實體 > 0 且上影 > 2 倍實體；0 ≤ 漲幅 < 1%。今日與前 5 日皆須有成交（量 > 0）',
    }
    for (const [name, formula] of Object.entries(expected)) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('title', formula)
    }
  })
})

describe('Volume pattern results column', () => {
  it('places the column right after volume', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    const headers = resultsTable().getAllByRole('columnheader').map(h => h.textContent)
    const index = headers.indexOf('量價型態')
    expect(index).toBeGreaterThan(0)
    expect(headers[index - 1]).toContain('成交量')
  })

  it('shows matched pattern names in definition order with formula tooltips', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue(withPatterns(['vp_up_stall', 'vp_double']) as any)
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    const cell = volumePatternCell('2330.TWSE')
    expect(cell).toHaveTextContent('倍量放量滯漲')
    expect(within(cell).getByText('倍量')).toHaveAttribute('title', '今日量 ≥ 前一交易日量 2 倍；兩日皆須有成交（量 > 0）')
  })

  it('shows a dash when the row matched no pattern', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue(withPatterns([]) as any)
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(volumePatternCell('2330.TWSE')).toHaveTextContent(/^—$/)
  })

  it('shows a dash when the response omits the field', async () => {
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(volumePatternCell('2330.TWSE')).toHaveTextContent(/^—$/)
  })

  it('keeps empty-state rows spanning every column', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue({ ...buildScreenerResponse(), items: [], total: 0 } as any)
    renderScreener()
    const empty = await screen.findByText('沒有符合當前條件的台股標的')
    expect(empty.closest('td')).toHaveAttribute('colspan', String(resultsTable().getAllByRole('columnheader').length))
    fireEvent.click(screen.getByRole('button', { name: /更多欄位/ }))
    const expanded = await screen.findByText('沒有符合當前條件的台股標的')
    expect(expanded.closest('td')).toHaveAttribute('colspan', String(resultsTable().getAllByRole('columnheader').length))
  })
})

describe('Volume pattern publication notice', () => {
  it('shows the notice when volume patterns are degraded', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue(
      withPatterns(undefined, { degraded_sections: ['volume_patterns'] }) as any,
    )
    renderScreener()
    expect(await screen.findByText('量價型態尚未發佈')).toHaveAttribute('role', 'status')
    expect(screen.queryByText('技術指標更新中，暫以空值顯示')).toBeNull()
    expect(volumePatternCell('2330.TWSE')).toHaveTextContent(/^—$/)
  })

  it('shows both notices when indicators and volume patterns are degraded', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue({
      ...buildScreenerResponse(),
      degraded_sections: ['indicators', 'volume_patterns'],
    } as any)
    renderScreener()
    expect(await screen.findByText('量價型態尚未發佈')).toBeInTheDocument()
    expect(screen.getByText('技術指標更新中，暫以空值顯示')).toBeInTheDocument()
  })

  it('shows no notice when volume patterns are published', async () => {
    vi.mocked(api.taiwanScreenerRun).mockResolvedValue(withPatterns([], { degraded_sections: [] }) as any)
    renderScreener()
    await waitFor(() => expect(screen.getAllByText('2330.TWSE').length).toBeGreaterThan(0))
    expect(screen.queryByText('量價型態尚未發佈')).toBeNull()
  })
})
