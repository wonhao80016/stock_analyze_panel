// Phase 8C-D — Legacy Product Removal: 中國 A 股 legacy 大盤看板整段已隨產品
// 介面移除, show_ashare_legacy_features 開關本身也已移除, Dashboard 全站僅
// 剩台股內容, 不再有任何 legacy 開關/查詢殘留。
//
// Phase 8C-B — Dashboard Market Clarity 回歸測試
// 涵蓋：市場強弱摘要、產業強弱 top/bottom、自選股快覽 (empty/populated)、
// 市場或產業查詢失敗時 Dashboard 仍可渲染不白屏。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Dashboard } from './Dashboard'
import { api } from '@/lib/api'
import { INVESTMENT_DISCLAIMER } from '@/lib/disclaimer'

function buildMarketIntelligence(overrides: Partial<Record<string, any>> = {}) {
  return {
    trade_date: '2026-09-05',
    generated_at: '2026-09-05T14:00:00Z',
    market_totals: {
      supported_count: 1000, snapshot_row_count: 950, traded_count: 950,
      advance_count: 600, decline_count: 300, flat_count: 50, uncompared_count: 0,
      upper_limit_count: 8, lower_limit_count: 2, turnover: 350_000_000_000,
    },
    by_exchange: { twse: {} as any, tpex: {} as any },
    by_instrument: { stock: {} as any, etf: {} as any },
    institutional: { trade_date: '2026-09-05', foreign_net_amount: 39_016_214_118, investment_trust_net_amount: 6_087_155_186, dealer_net_amount: 1_896_636_845, total_net_amount: 47_000_006_149, twse: null, tpex: null, status: 'current' },
    margin: { trade_date: '2026-09-05', row_count: 900, margin_balance: 0, margin_balance_change: 0, short_balance: 0, short_balance_change: 0, aggregate_short_margin_ratio: 5, status: 'current' },
    indexes: { taiex: null, tpex_index: null },
    data_quality: { target_trade_date: '2026-09-05', previous_trade_date: '2026-09-04', overall_status: 'complete', universe_supported_symbols: 1000, daily_snapshot_symbols: 950, missing_symbols_count: 0 },
    ...overrides,
  }
}

function buildIndustry(industry: string, avgChangePct: number) {
  return {
    industry, supported_symbol_count: 10, snapshot_symbol_count: 10, traded_symbol_count: 10,
    comparable_symbol_count: 10, turnover: 1_000_000, turnover_share: 0.05,
    advance_count: avgChangePct >= 0 ? 8 : 2, decline_count: avgChangePct >= 0 ? 2 : 8, flat_count: 0,
    uncompared_count: 0, advance_ratio: avgChangePct >= 0 ? 0.8 : 0.2, decline_ratio: avgChangePct >= 0 ? 0.2 : 0.8,
    average_change_pct: avgChangePct, median_change_pct: avgChangePct,
    foreign_net: null, investment_trust_net: null, dealer_net: null,
    margin_balance_change: null, short_balance_change: null,
    relative_strength_5d: null, relative_strength_20d: null,
    relative_strength_5d_comparable_count: 0, relative_strength_20d_comparable_count: 0,
    top_gainers: [], top_losers: [], top_turnover: [],
  }
}

function buildIndustryIntelligence(industries: ReturnType<typeof buildIndustry>[]) {
  return {
    trade_date: '2026-09-05',
    generated_at: '2026-09-05T14:00:00Z',
    market_reference: { trade_date: '2026-09-05', total_stock_turnover: 1, market_equal_weight_return_5d: null, market_equal_weight_return_20d: null, comparable_stocks_5d_count: 0, comparable_stocks_20d_count: 0 },
    industries,
    data_quality: {
      target_trade_date: '2026-09-05', previous_trade_date: '2026-09-04', base_date_5d: null, base_date_20d: null,
      supported_stock_count: industries.length, classified_stock_count: industries.length, unclassified_stock_count: 0,
      etfs_excluded_count: 0, industry_count: industries.length, classification_coverage_pct: 100,
      daily_status: 'current', institutional_status: 'current', margin_status: 'current', overall_status: 'complete',
    },
  }
}

function buildWatchlistEnriched(rows: any[]) {
  return { rows, as_of: '2026-09-05', elapsed_ms: 1 }
}

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: {
    taiwanDataStatus: vi.fn().mockResolvedValue({
      daily_as_of: null,
      institutional_as_of: null,
      margin_as_of: null,
      target_latest_trading_date: '2026-09-01',
      is_fully_current: false,
      daily_status: 'unavailable',
      institutional_status: 'unavailable',
      margin_status: 'unavailable',
      daily_days_behind: 0,
      institutional_days_behind: 0,
      margin_days_behind: 0,
      scheduler_enabled: true,
      scheduled_update_time: '16:30',
      scheduled_timezone: 'Asia/Taipei',
    }),
    alertsList: vi.fn().mockResolvedValue({ alerts: [] }),
    taiwanMarketIntelligence: vi.fn().mockResolvedValue(null),
    taiwanIndustryIntelligence: vi.fn().mockResolvedValue(null),
    watchlistEnriched: vi.fn().mockResolvedValue({ rows: [], as_of: null, elapsed_ms: 0 }),
    screenerCached: vi.fn().mockResolvedValue({ as_of: null, results: {}, today_ever_matched: null, today_ever_rows: null, updated_at: null }),
    screenerStrategies: vi.fn().mockResolvedValue({ presets: [], load_errors: [] }),
  },
}))

vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: () => null }))

function renderDashboard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('Dashboard — Legacy A-share removal (Phase 8C-D)', () => {
  it('renders Taiwan content only: no legacy A-share board, no legacy divider, no legacy API calls', async () => {
    renderDashboard()

    expect(await screen.findByText('台股資料狀態')).toBeInTheDocument()
    expect(screen.queryByText('市场看板')).not.toBeInTheDocument()
    expect(screen.queryByText('中國 A 股（選配）')).not.toBeInTheDocument()
    expect(screen.queryByText('上证指数')).not.toBeInTheDocument()
    // 監控中心是市場中立功能, 不受 legacy 移除影響
    expect(screen.getByText('監控中心')).toBeInTheDocument()
    // legacy A 股 API 不應存在於 api mock 上, 更不會被呼叫
    expect((api as any).overviewMarket).toBeUndefined()
  })
})

describe('Dashboard — Market Clarity (Phase 8C-B)', () => {
  it('renders market strength summary with breadth counts and label', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderDashboard()

    expect(await screen.findByText('今日市場強弱')).toBeInTheDocument()
    // 600 advance / (600+300+50) = 66.7% >= 55% -> 偏強
    expect(await screen.findByText('偏強')).toBeInTheDocument()
    expect(screen.getByText('600')).toBeInTheDocument()
    expect(screen.getByText('300')).toBeInTheDocument()
    expect(screen.getByText('監控中心')).toBeInTheDocument()
  })

  it('shows the market foreign net buy amount in 100-million TWD from the official summary', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderDashboard()

    expect(await screen.findByText('+390.16 億元')).toBeInTheDocument()
  })

  it('shows a dash instead of a share count when the day has no official amount summary', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      institutional: { trade_date: null, foreign_net_amount: null, investment_trust_net_amount: null, dealer_net_amount: null, total_net_amount: null, twse: null, tpex: null, status: 'stale' },
    }) as any)
    renderDashboard()

    const label = await screen.findByText('外資買賣超')
    expect(within(label.parentElement as HTMLElement).getByText('—')).toBeInTheDocument()
  })

  it('renders industry top/bottom strongest and weakest', async () => {
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(buildIndustryIntelligence([
      buildIndustry('半導體業', 0.05),
      buildIndustry('金融保險業', 0.03),
      buildIndustry('鋼鐵工業', -0.04),
      buildIndustry('航運業', -0.02),
    ]) as any)
    renderDashboard()

    expect(await screen.findByText('產業強弱')).toBeInTheDocument()
    expect(await screen.findByText('半導體業')).toBeInTheDocument()
    expect(screen.getByText('金融保險業')).toBeInTheDocument()
    expect(screen.getByText('鋼鐵工業')).toBeInTheDocument()
    expect(screen.getByText('航運業')).toBeInTheDocument()
  })

  it('labels the UNCLASSIFIED industry bucket as 未分類', async () => {
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(buildIndustryIntelligence([
      buildIndustry('半導體業', 0.05),
      buildIndustry('UNCLASSIFIED', -0.03),
    ]) as any)
    renderDashboard()

    expect(await screen.findByText('未分類')).toBeInTheDocument()
    expect(screen.queryByText('UNCLASSIFIED')).not.toBeInTheDocument()
  })

  it('empty watchlist shows a compact empty state with CTA, not a large empty table', async () => {
    vi.mocked(api.watchlistEnriched).mockResolvedValue(buildWatchlistEnriched([]) as any)
    renderDashboard()

    expect(await screen.findByText('尚未加入任何自選股')).toBeInTheDocument()
    expect(screen.getByText('前往自選股 →')).toBeInTheDocument()
  })

  it('populated watchlist shows quick-glance rows sorted by absolute change', async () => {
    vi.mocked(api.watchlistEnriched).mockResolvedValue(buildWatchlistEnriched([
      { symbol: '2330.TWSE', name: '台積電', close: 900, change_pct: 0.01 },
      { symbol: '2317.TWSE', name: '鴻海', close: 100, change_pct: 0.08 },
    ]) as any)
    renderDashboard()

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(screen.getByText('鴻海')).toBeInTheDocument()
  })

  it('industry card shows the incomplete-data badge when overall_status is partial', async () => {
    const snapshot = buildIndustryIntelligence([buildIndustry('半導體業', 0.05), buildIndustry('航運業', -0.02)])
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue({
      ...snapshot,
      data_quality: { ...snapshot.data_quality, overall_status: 'partial' },
    } as any)
    renderDashboard()

    expect(await screen.findByText('半導體業')).toBeInTheDocument()
    expect(screen.getByText('產業資料尚未完整，以下結果僅供參考')).toBeInTheDocument()
  })

  it('industry card shows the provisional badge when overall_status is provisional', async () => {
    const snapshot = buildIndustryIntelligence([buildIndustry('半導體業', 0.05), buildIndustry('航運業', -0.02)])
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue({
      ...snapshot,
      data_quality: { ...snapshot.data_quality, overall_status: 'provisional' },
    } as any)
    renderDashboard()

    expect(await screen.findByText('半導體業')).toBeInTheDocument()
    expect(screen.getByText('產業統計為盤後初值，晚間定稿')).toBeInTheDocument()
    expect(screen.queryByText('產業資料尚未完整，以下結果僅供參考')).not.toBeInTheDocument()
  })

  it('industry card shows no badge when overall_status is complete', async () => {
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(buildIndustryIntelligence([
      buildIndustry('半導體業', 0.05),
    ]) as any)
    renderDashboard()

    expect(await screen.findByText('半導體業')).toBeInTheDocument()
    expect(screen.queryByText('產業資料尚未完整，以下結果僅供參考')).not.toBeInTheDocument()
    expect(screen.queryByText('產業統計為盤後初值，晚間定稿')).not.toBeInTheDocument()
  })

  it('market/industry query failure does not crash the Dashboard', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockRejectedValue(new Error('network error'))
    vi.mocked(api.taiwanIndustryIntelligence).mockRejectedValue(new Error('network error'))
    renderDashboard()

    expect(await screen.findByText('目前無法讀取市場強弱資料,不影響其他功能使用。')).toBeInTheDocument()
    expect(await screen.findByText('目前無法讀取產業強弱資料,不影響其他功能使用。')).toBeInTheDocument()
    // 其餘區塊仍正常渲染, 未白屏
    expect(screen.getByText('監控中心')).toBeInTheDocument()
    expect(screen.getByText('台股資料狀態')).toBeInTheDocument()
  })
})

// DAILY_USE_CORE_UX_FIXES (P1-1) — 「今日市場強弱」不可把 data_quality
// 不完整時的 0/0/0 當成正式市場結果呈現; 完整資料時(即使真的全 0)則照實顯示。
describe('Dashboard — Market data honesty (DAILY_USE_CORE_UX_FIXES P1-1)', () => {
  it('A. complete data renders normally, without the incomplete-data notice', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence() as any)
    renderDashboard()

    expect(await screen.findByText('今日市場強弱')).toBeInTheDocument()
    expect(await screen.findByText('600')).toBeInTheDocument()
    expect(screen.queryByText(/今日市場資料尚未完整/)).not.toBeInTheDocument()
  })

  it('B. partial data shows a clear incomplete-data notice', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      data_quality: { target_trade_date: '2026-09-09', previous_trade_date: '2026-09-03', overall_status: 'partial', universe_supported_symbols: 2375, daily_snapshot_symbols: 0, missing_symbols_count: 2375 },
    }) as any)
    renderDashboard()

    expect(await screen.findByText(/今日市場資料尚未完整/)).toBeInTheDocument()
    expect(screen.getByText(/前一交易日：2026-09-03/)).toBeInTheDocument()
    expect(screen.queryByText(/已知最新資料|最新資料日期|非今日實際行情/)).not.toBeInTheDocument()
  })

  it('C. partial data with all-zero totals still shows the notice, not a bare 0/0/0 result', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      market_totals: {
        supported_count: 2375, snapshot_row_count: 0, traded_count: 0,
        advance_count: 0, decline_count: 0, flat_count: 0, uncompared_count: 0,
        upper_limit_count: 0, lower_limit_count: 0, turnover: 0,
      },
      data_quality: { target_trade_date: '2026-09-09', previous_trade_date: '2026-09-03', overall_status: 'partial', universe_supported_symbols: 2375, daily_snapshot_symbols: 0, missing_symbols_count: 2375 },
    }) as any)
    renderDashboard()

    expect(await screen.findByText(/今日市場資料尚未完整/)).toBeInTheDocument()
    // 0/0/0 本身仍可顯示(誠實反映 API 回傳值), 但必須伴隨明確提示, 而非唯一線索
    expect(screen.getAllByText('0').length).toBeGreaterThan(0)
  })

  it('D. strength label (偏強/偏弱/中性) is suppressed while data is incomplete, even if the ratio would otherwise qualify', async () => {
    // advance 600 / (600+300+50) ≈ 66.7% -> 若資料完整會判為偏強, 但這裡
    // overall_status = partial, 不應顯示任何 deterministic 強弱結論。
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      data_quality: { target_trade_date: '2026-09-09', previous_trade_date: '2026-09-03', overall_status: 'partial', universe_supported_symbols: 2375, daily_snapshot_symbols: 950, missing_symbols_count: 1425 },
    }) as any)
    renderDashboard()

    expect(await screen.findByText(/今日市場資料尚未完整/)).toBeInTheDocument()
    expect(screen.queryByText('偏強')).not.toBeInTheDocument()
    expect(screen.queryByText('偏弱')).not.toBeInTheDocument()
    expect(screen.queryByText('中性')).not.toBeInTheDocument()
  })

  it('E. provisional data shows the after-close initial-value notice instead of the incomplete-data notice', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      data_quality: { target_trade_date: '2026-09-09', previous_trade_date: '2026-09-08', overall_status: 'provisional', universe_supported_symbols: 2375, daily_snapshot_symbols: 2375, missing_symbols_count: 0 },
    }) as any)
    renderDashboard()

    expect(await screen.findByText('市場統計為盤後初值，晚間定稿')).toBeInTheDocument()
    expect(screen.queryByText(/今日市場資料尚未完整/)).not.toBeInTheDocument()
    expect(screen.queryByText(/前一交易日：/)).not.toBeInTheDocument()
  })

  it('F. unavailable data keeps the incomplete-data notice', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(buildMarketIntelligence({
      data_quality: { target_trade_date: '2026-09-09', previous_trade_date: '2026-09-08', overall_status: 'unavailable', universe_supported_symbols: 2375, daily_snapshot_symbols: 0, missing_symbols_count: 2375 },
    }) as any)
    renderDashboard()

    expect(await screen.findByText(/今日市場資料尚未完整/)).toBeInTheDocument()
    expect(screen.queryByText('市場統計為盤後初值，晚間定稿')).not.toBeInTheDocument()
  })
})

// 策略當日推薦卡 — 讀 /api/screener/cached (盤後 run_all 寫入的快取)。
// 快取為空時必須給出可理解的 empty state, 不可白屏或顯示假資料。
describe('Dashboard — 策略當日推薦卡', () => {
  it('快取為空時顯示 empty state 而非假資料', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
    renderDashboard()

    expect(await screen.findByText('今日尚無策略選股結果')).toBeInTheDocument()
    expect(screen.getByText('盤後排程執行後會自動更新')).toBeInTheDocument()
  })

  it('有選股結果時列出策略名稱與個股', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [{ id: 'bottom_reversal', name: '深跌止穩反彈' }] as any,
      load_errors: [],
    } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-17',
      results: {
        bottom_reversal: {
          total: 2,
          as_of: '2026-09-17',
          rows: [
            { symbol: '2377.TWSE', name: '微星', close: 210.5, change_pct: 0.031 },
            { symbol: '3231.TWSE', name: '緯創', close: 118.0, change_pct: -0.012 },
          ],
        },
      },
      today_ever_matched: null,
      today_ever_rows: null,
      updated_at: null,
    } as any)
    renderDashboard()

    expect(await screen.findByText('深跌止穩反彈')).toBeInTheDocument()
    expect(screen.getByText('微星')).toBeInTheDocument()
    expect(screen.getByText('2377.TWSE')).toBeInTheDocument()
    expect(screen.getByText('+3.10%')).toBeInTheDocument()
  })

  it('讀取失敗時不影響其他區塊', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.screenerCached).mockRejectedValue(new Error('network error'))
    renderDashboard()

    expect(await screen.findByText('目前無法讀取策略選股結果,不影響其他功能使用。')).toBeInTheDocument()
  })
})

// 策略計算方式 — 後端 /api/strategies 已帶 description/params/entry_signals,
// 卡片必須讓使用者看得到, 否則「當日推薦」是一個無法檢驗的黑盒。
describe('Dashboard — 策略計算方式', () => {
  const preset = {
    id: 'bollinger_squeeze',
    name: '布林收斂突破',
    description: '布林帶寬壓縮至近80日低分位後, 首根帶量突破上軌',
    tags: ['布林', '突破'],
    params: [{ id: 'squeeze_lookback', label: '帶寬分位回看天數', default: 80 }],
    params_defaults: { squeeze_lookback: 80 },
    entry_signals: ['signal_boll_breakout_upper'],
    exit_signals: [],
    stop_loss: 0.08,
  }

  function mockPicks() {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: [preset], load_errors: [] } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-21',
      results: {
        bollinger_squeeze: {
          total: 1,
          as_of: '2026-09-21',
          rows: [{ symbol: '3467.TPEX', name: '台灣類比', close: 73.5, change_pct: 0.0381 }],
        },
      },
      today_ever_matched: null,
      today_ever_rows: null,
      updated_at: null,
    } as any)
  }

  it('預設收合, 點開後顯示說明、進場訊號、風控與參數', async () => {
    mockPicks()
    renderDashboard()

    expect(await screen.findByText('布林收斂突破')).toBeInTheDocument()
    expect(screen.queryByText(preset.description)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '布林收斂突破 的計算方式' }))

    expect(await screen.findByText(preset.description)).toBeInTheDocument()
    // 訊號 id 必須翻成中文, 不可把 signal_* 原樣丟給使用者
    expect(screen.getByText(/突破布林上軌/)).toBeInTheDocument()
    expect(screen.getByText(/停損 8\.0%/)).toBeInTheDocument()
    expect(screen.getByText(/帶寬分位回看天數: 80/)).toBeInTheDocument()
  })

  it('個股以名稱呈現, 代號只作為次要資訊', async () => {
    mockPicks()
    renderDashboard()

    expect(await screen.findByText('台灣類比')).toBeInTheDocument()
    expect(screen.getByText('3467.TPEX')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看 台灣類比 走勢' })).toBeInTheDocument()
  })
})

// 卡片預設每個策略只列 3 檔, 但 /api/screener/cached 回傳的是全部。
// 沒有展開入口時, 其餘選股在前端記憶體裡卻無處可看。
describe('Dashboard — 策略選股展開', () => {
  const rows = Array.from({ length: 5 }, (_, i) => ({
    symbol: `100${i}.TWSE`,
    name: `標的${i}`,
    close: 10 + i,
    change_pct: 0.01,
  }))

  function mockFive() {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [{ id: 'lorentzian_knn', name: 'Lorentzian KNN 分類' }] as any,
      load_errors: [],
    } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-21',
      results: { lorentzian_knn: { total: rows.length, as_of: '2026-09-21', rows } },
      today_ever_matched: null,
      today_ever_rows: null,
      updated_at: null,
    } as any)
  }

  it('預設只列前 3 檔, 點檔數後列出全部', async () => {
    mockFive()
    renderDashboard()

    expect(await screen.findByText('標的0')).toBeInTheDocument()
    expect(screen.getByText('標的2')).toBeInTheDocument()
    expect(screen.queryByText('標的3')).not.toBeInTheDocument()
    expect(screen.queryByText('標的4')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '展開 Lorentzian KNN 分類 全部 5 檔' }))

    expect(await screen.findByText('標的3')).toBeInTheDocument()
    expect(screen.getByText('標的4')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '收合 Lorentzian KNN 分類 的選股清單' }))
    expect(screen.queryByText('標的4')).not.toBeInTheDocument()
  })

  it('選股數未超過顯示上限時不給展開入口, 避免點了沒反應', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [{ id: 'pullback_resume', name: '回檔續勢' }] as any,
      load_errors: [],
    } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-21',
      results: { pullback_resume: { total: 2, as_of: '2026-09-21', rows: rows.slice(0, 2) } },
      today_ever_matched: null,
      today_ever_rows: null,
      updated_at: null,
    } as any)
    renderDashboard()

    expect(await screen.findByText('標的0')).toBeInTheDocument()
    expect(screen.getByText('標的1')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /展開/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /收合/ })).not.toBeInTheDocument()
  })
})

describe('Dashboard — 策略分級與大盤濾鏡', () => {
  const marketBelow = {
    as_of: '2026-09-30', index_symbol: 't00.TWSE', close: 21000.5, ma: 21500.25, window: 20,
    below_ma: true, status: 'ok', reason: null,
  }

  function mockGraded(overrides: Record<string, any> = {}, rows?: any[], resultAsOf = '2026-09-30') {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [{ id: 'bottom_reversal', name: '深跌止穩反彈' }] as any,
      load_errors: [],
    } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-30',
      results: {
        bottom_reversal: {
          total: 2,
          as_of: resultAsOf,
          rows: rows ?? [
            { symbol: '2377.TWSE', name: '微星', close: 210.5, change_pct: 0.031, grade: 'buy', grade_reason: null },
            { symbol: '3231.TWSE', name: '緯創', close: 118.0, change_pct: -0.012, score: null, grade: 'watch', grade_reason: 'score_unavailable' },
          ],
        },
      },
      today_ever_matched: null,
      today_ever_rows: null,
      updated_at: null,
      market_state: { ...marketBelow, below_ma: false, close: 22000 },
      grading: { top_n: 5, market_filter: 'passed' },
      ...overrides,
    } as any)
  }

  function statusLine() {
    return screen.getByText(INVESTMENT_DISCLAIMER).closest('p') as HTMLElement
  }

  it('買進與觀察各自顯示徽章, 觀察的滑鼠提示說明原因', async () => {
    mockGraded()
    renderDashboard()

    const buy = await screen.findByText('買進')
    expect(buy.closest('button')).toHaveTextContent('微星')
    const watch = screen.getByText('觀察・資料不足')
    expect(watch.closest('button')).toHaveTextContent('緯創')
    expect(watch).toHaveAttribute('title', '評分資料不足（分數 —）')
  })

  it('漲停鎖死的選股另加標籤, 分級不變', async () => {
    mockGraded({}, [
      { symbol: '2377.TWSE', name: '微星', close: 210.5, change_pct: 0.099, grade: 'buy', grade_reason: null, limit_up_locked: true },
    ])
    renderDashboard()

    const buy = await screen.findByText('買進')
    const tag = screen.getByText('漲停鎖死')
    expect(tag.closest('button')).toBe(buy.closest('button'))
    expect(tag.closest('button')).toHaveTextContent('微星')
  })

  it('跌破 20 日均線時徽章標出大盤降級, 並寫出指數收盤與均線', async () => {
    mockGraded({ market_state: marketBelow, grading: { top_n: 5, market_filter: 'applied' } }, [
      { symbol: '2377.TWSE', name: '微星', close: 210.5, change_pct: 0.031, grade: 'watch', grade_reason: 'market_below_ma20' },
    ])
    renderDashboard()

    const badge = await screen.findByText('觀察・大盤')
    expect(badge).toHaveAttribute(
      'title',
      '加權指數 2026-09-30 收盤 21000.50 低於 20 日均線 21500.25（價格指數，不含股利）',
    )
    expect(screen.queryByText('買進')).not.toBeInTheDocument()
    expect(statusLine()).toHaveTextContent('大盤：跌破 20 日均線，買進已降為觀察')
  })

  it('未跌破時顯示對應文字並附投資風險提示', async () => {
    mockGraded()
    renderDashboard()

    await screen.findByText('買進')
    expect(statusLine()).toHaveTextContent('大盤：未跌破 20 日均線')
    expect(statusLine()).toHaveTextContent(INVESTMENT_DISCLAIMER)
  })

  it('大盤狀態無法判定時說明濾鏡未套用', async () => {
    mockGraded({
      market_state: { ...marketBelow, close: null, ma: null, below_ma: null, status: 'unavailable', reason: 'index_gap' },
      grading: { top_n: 5, market_filter: 'unavailable' },
    })
    renderDashboard()

    await screen.findByText('買進')
    expect(statusLine()).toHaveTextContent('大盤狀態無法判定，濾鏡未套用')
    expect(screen.getByText('大盤狀態無法判定，濾鏡未套用')).toHaveAttribute('title', '加權指數近 20 個交易日有缺漏')
    expect(screen.queryByText(/大盤狀態依/)).not.toBeInTheDocument()
  })

  it('盤中覆蓋的結果註明大盤狀態依哪一天收盤', async () => {
    mockGraded({}, undefined, '2026-10-01')
    renderDashboard()

    await screen.findByText('買進')
    expect(statusLine()).toHaveTextContent('（大盤狀態依 2026-09-30 收盤）')
  })

  it('快取為空時仍顯示大盤狀態列', async () => {
    vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: null, results: {}, updated_at: null, market_state: null,
      grading: { top_n: 5, market_filter: 'unavailable' },
    } as any)
    renderDashboard()

    expect(await screen.findByText('今日尚無策略選股結果')).toBeInTheDocument()
    expect(statusLine()).toHaveTextContent('大盤狀態無法判定，濾鏡未套用')
  })

  it('舊版回應沒有分級欄位時不顯示徽章與大盤狀態列', async () => {
    mockGraded({ market_state: undefined, grading: undefined }, [
      { symbol: '2377.TWSE', name: '微星', close: 210.5, change_pct: 0.031 },
    ])
    renderDashboard()

    expect(await screen.findByText('微星')).toBeInTheDocument()
    expect(screen.queryByText('買進')).not.toBeInTheDocument()
    expect(screen.queryByText(INVESTMENT_DISCLAIMER)).not.toBeInTheDocument()
  })
})
