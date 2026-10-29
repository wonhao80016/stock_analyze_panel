// DAILY_USE_CORE_UX_FIXES (P1-2) — TaiwanStockDetail 返回按鈕回歸測試。
// 通用 idx-based 判斷邏輯已在 useSafeBack.test.tsx 完整驗證; 這裡只驗證本頁
// 真的接上了它 —— 不再寫死「返回即時監控」文案/目的地, 且能在有可信 in-app
// 來源(如監控中心)時正確返回該來源, 直接網址進入時安全落到台股選股 fallback。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockDetail, dailyRowsToOHLC, etfCategoryLabel, signColorClass, signPrefix } from './TaiwanStockDetail'
import { changeBase, unadjustedClose } from '@/components/EChartsCandlestick'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanSearch: vi.fn().mockResolvedValue({ results: [] }),
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    watchlistAdd: vi.fn(),
    watchlistRemove: vi.fn(),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    taiwanStockDetail: vi.fn(),
    taiwanCurrentData: vi.fn().mockResolvedValue(null),
    taiwanCurrentSignals: vi.fn().mockResolvedValue(null),
    taiwanStockResearchContext: vi.fn().mockResolvedValue(null),
    taiwanStockAIResearch: vi.fn(),
  },
}))

function buildDetailResponse(instrumentType: string = 'stock') {
  return {
    symbol: '2330.TWSE',
    identity: {
      symbol: '2330.TWSE',
      code: '2330',
      name: '台積電',
      exchange: 'TWSE',
      instrument_type: instrumentType,
      is_supported: true,
    },
    price_limit: { is_no_limit: false, rule_type: 'standard' },
    realtime: {
      last_price: 100,
      change: 0,
      change_pct: 0,
      market_status: 'closed',
      bids: [],
      asks: [],
    },
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

function renderAt(entries: string[], initialIndex: number) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={entries} initialIndex={initialIndex}>
        <Routes>
          <Route path="/stocks/:symbol" element={<TaiwanStockDetail />} />
          <Route path="/monitor" element={<div>MONITOR PAGE</div>} />
          <Route path="/taiwan-screener" element={<div>SCREENER PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  window.history.replaceState(null, '')
  vi.mocked(api.taiwanStockDetail).mockResolvedValue(buildDetailResponse() as any)
})

afterEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, '')
})

describe('TaiwanStockDetail — back navigation (DAILY_USE_CORE_UX_FIXES P1-2)', () => {
  it('將個股代號與資產類型帶入回測工作區', async () => {
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByRole('link', { name: '回測' })).toHaveAttribute(
      'href',
      '/backtest?symbol=2330.TWSE&asset_type=stock',
    )
  })

  it('waits for ETF identity before exposing the backtest link', async () => {
    let resolveDetail!: (value: ReturnType<typeof buildDetailResponse>) => void
    const detail = new Promise<ReturnType<typeof buildDetailResponse>>(resolve => {
      resolveDetail = resolve
    })
    vi.mocked(api.taiwanStockDetail).mockReturnValue(detail)
    renderAt(['/stocks/0050.TWSE'], 0)

    expect(screen.queryByRole('link', { name: '回測' })).not.toBeInTheDocument()

    await act(async () => {
      resolveDetail({
        ...buildDetailResponse('etf'),
        symbol: '0050.TWSE',
        identity: {
          ...buildDetailResponse('etf').identity,
          symbol: '0050.TWSE',
          code: '0050',
          name: '元大台灣50',
        },
      })
    })

    expect(await screen.findByRole('link', { name: '回測' })).toHaveAttribute(
      'href',
      '/backtest?symbol=0050.TWSE&asset_type=etf',
    )
  })

  it('指數詳情不顯示回測入口', async () => {
    renderAt(['/stocks/t00.TWSE'], 0)

    await screen.findAllByText('台積電')
    expect(screen.queryByRole('link', { name: '回測' })).not.toBeInTheDocument()
  })

  it('never shows the old hardcoded "返回即時監控" label', async () => {
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByText('返回')).toBeInTheDocument()
    expect(screen.queryByText('返回即時監控')).not.toBeInTheDocument()
  })

  it('D. returns to the known source (Monitor) when entered from there', async () => {
    window.history.replaceState({ idx: 1 }, '')
    renderAt(['/monitor', '/stocks/2330.TWSE'], 1)

    fireEvent.click(await screen.findByText('返回'))

    expect(await screen.findByText('MONITOR PAGE')).toBeInTheDocument()
  })

  it('E. direct URL falls back to 台股選股 instead of leaving the app', async () => {
    // window.history.state 為 null(beforeEach 已重置) 模擬直接輸入網址
    renderAt(['/stocks/2330.TWSE'], 0)

    fireEvent.click(await screen.findByText('返回'))

    expect(await screen.findByText('SCREENER PAGE')).toBeInTheDocument()
  })
})

describe('TaiwanStockDetail — current signals', () => {
  it('requests and shows the section for a stock', async () => {
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByRole('heading', { name: '目前觸發的訊號' })).toBeInTheDocument()
    expect(api.taiwanCurrentSignals).toHaveBeenCalledWith('2330.TWSE')
  })

  it('neither requests nor shows it for an index', async () => {
    renderAt(['/stocks/t00.TWSE'], 0)

    await screen.findAllByText('台積電')
    expect(screen.queryByRole('heading', { name: '目前觸發的訊號' })).not.toBeInTheDocument()
    expect(api.taiwanCurrentSignals).not.toHaveBeenCalled()
  })
})

describe('TaiwanStockDetail — price-limit card', () => {
  it('shows an index rule as not applicable without limit prices', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue({
      ...buildDetailResponse('index'),
      price_limit: {
        is_no_limit: false, rule_type: '不適用（指數）', price_limit_pct: null, limit_up: null, limit_down: null,
      },
    } as any)
    renderAt(['/stocks/t00.TWSE'], 0)

    expect(await screen.findByText('不適用（指數）')).toBeInTheDocument()
    expect(screen.queryByText(/^漲停 /)).not.toBeInTheDocument()
    expect(screen.queryByText(/^跌停 /)).not.toBeInTheDocument()
  })

  it('keeps the limit prices of an ordinary stock', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue({
      ...buildDetailResponse('stock'),
      symbol: '2454.TWSE',
      price_limit: {
        is_no_limit: false, rule_type: '±10%', price_limit_pct: 0.1, limit_up: 5695, limit_down: 4665,
      },
    } as any)
    renderAt(['/stocks/2454.TWSE'], 0)

    expect(await screen.findByText('漲停 5695.00')).toBeInTheDocument()
    expect(screen.getByText('跌停 4665.00')).toBeInTheDocument()
  })
})

describe('TaiwanStockDetail — sign helpers', () => {
  it('treats missing values as neutral, not as a zero net buy', () => {
    expect(signColorClass(null)).toBe('text-muted')
    expect(signColorClass(undefined)).toBe('text-muted')
    expect(signColorClass(NaN)).toBe('text-muted')
    expect(signPrefix(null)).toBe('')
    expect(signPrefix(undefined)).toBe('')
    expect(signPrefix(NaN)).toBe('')
  })

  it('treats zero as neutral without a plus sign', () => {
    expect(signColorClass(0)).toBe('text-muted')
    expect(signColorClass(-0)).toBe('text-muted')
    expect(signPrefix(0)).toBe('')
    expect(signPrefix(-0)).toBe('')
  })

  it('marks positive values as up with a plus sign', () => {
    expect(signColorClass(1)).toBe('text-rose-500')
    expect(signColorClass(0.01)).toBe('text-rose-500')
    expect(signPrefix(1)).toBe('+')
    expect(signPrefix(0.01)).toBe('+')
  })

  it('marks negative values as down without a plus sign', () => {
    expect(signColorClass(-1)).toBe('text-emerald-500')
    expect(signColorClass(-0.01)).toBe('text-emerald-500')
    expect(signPrefix(-1)).toBe('')
    expect(signPrefix(-0.01)).toBe('')
  })
})

describe('TaiwanStockDetail — daily K rows', () => {
  it('hands the chart the traded close and the reference price of a split session', () => {
    const bars = dailyRowsToOHLC([
      { date: '2025-06-17', open: 47.1625, high: 47.1625, low: 47.1625, close: 47.1625, volume: 4000,
        prev_close: 47.5, raw_close: 188.65 },
      { date: '2025-06-18', open: 47.5, high: 47.5, low: 47.5, close: 47.5, volume: 4000,
        prev_close: 47.16, raw_close: 47.5 },
    ])

    expect(changeBase(bars, 1)).toBe(47.16)
    expect(unadjustedClose(bars[0])).toBe(188.65)
    expect(unadjustedClose(bars[1])).toBeNull()
  })
})

describe('TaiwanStockDetail — ETF category badge', () => {
  it('labels every security-master category in Traditional Chinese', () => {
    expect(etfCategoryLabel('domestic_equity')).toBe('國內成分')
    expect(etfCategoryLabel('foreign_equity')).toBe('國外成分')
    expect(etfCategoryLabel('bond')).toBe('債券')
    expect(etfCategoryLabel('leveraged')).toBe('槓桿')
    expect(etfCategoryLabel('inverse')).toBe('反向')
    expect(etfCategoryLabel('futures')).toBe('期貨')
    expect(etfCategoryLabel('unknown')).toBe('未分類')
  })

  it('keeps an unrecognized category visible as-is', () => {
    expect(etfCategoryLabel('commodity')).toBe('commodity')
  })

  it('shows the futures label on a futures-trust ETF', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue({
      ...buildDetailResponse('etf'),
      symbol: '00635U.TWSE',
      identity: {
        ...buildDetailResponse('etf').identity,
        symbol: '00635U.TWSE',
        code: '00635U',
        name: '期元大S&P黃金',
        etf_category: 'futures',
      },
    } as any)
    renderAt(['/stocks/00635U.TWSE'], 0)

    expect(await screen.findByText('ETF · 期貨')).toBeInTheDocument()
  })
})

describe('TaiwanStockDetail — depositary receipts', () => {
  function tdrDetail() {
    return {
      ...buildDetailResponse(),
      symbol: '910322.TWSE',
      identity: {
        ...buildDetailResponse().identity,
        symbol: '910322.TWSE',
        code: '910322',
        name: '康師傅-DR',
        industry: null,
        security_class: 'tdr',
      },
    }
  }

  function capitalData(values: Record<string, unknown>) {
    return {
      symbol: '910322.TWSE',
      exchange: 'TWSE',
      security_type: 'stock',
      identity: {},
      sections: {
        share_capital_record: {
          dataset: 'share_capital_record',
          status: 'data_insufficient',
          usage_scope: 'current_reference',
          historically_eligible: false,
          data: { period_end: '2026-09-22', values },
        },
      },
    }
  }

  it('shows the depositary receipt badge', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(tdrDetail() as any)
    renderAt(['/stocks/910322.TWSE'], 0)

    expect(await screen.findByText('存託憑證')).toBeInTheDocument()
  })

  it('labels the underlying share count and keeps a foreign par value as official text', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue(tdrDetail() as any)
    vi.mocked(api.taiwanCurrentData).mockResolvedValue(capitalData({
      issued_shares: 5_620_600_000,
      par_value_twd: null,
      par_value_raw: '美金0.0050元',
      par_value_currency: 'USD',
    }) as any)
    renderAt(['/stocks/910322.TWSE'], 0)

    expect(await screen.findByText('TDR原股發行股數')).toBeInTheDocument()
    expect(screen.queryByText('已發行普通股數')).not.toBeInTheDocument()
    expect(screen.getByText('美金0.0050元')).toBeInTheDocument()
  })

  it('keeps the ordinary share label and NTD par value for a stock', async () => {
    vi.mocked(api.taiwanCurrentData).mockResolvedValue(capitalData({
      issued_shares: 25_930_380_458,
      par_value_twd: 10,
      par_value_raw: '新台幣 10.0000元',
      par_value_currency: 'TWD',
    }) as any)
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByText('已發行普通股數')).toBeInTheDocument()
    expect(screen.getByText('10 元')).toBeInTheDocument()
    expect(screen.queryByText('存託憑證')).not.toBeInTheDocument()
  })
})

describe('TaiwanStockDetail — benchmark card', () => {
  it('shows the stored benchmark close with its session date', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue({
      ...buildDetailResponse(),
      market_context: {
        benchmark_symbol: 'TAIEX',
        benchmark_name: '發行量加權股價指數',
        close: 22200,
        change: 200,
        change_pct: 0.91,
        meta: { source: 'taiwan_index_store', trade_date: '2026-10-22', status: 'available', is_stale: false },
      },
    } as any)
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByText('22200.00')).toBeInTheDocument()
    expect(screen.getByText('+0.91%')).toBeInTheDocument()
    expect(screen.getByText('2026-10-22')).toBeInTheDocument()
  })
})

describe('TaiwanStockDetail — review fixes', () => {
  it('marks a benchmark close older than the latest session', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue({
      ...buildDetailResponse(),
      market_context: {
        benchmark_symbol: 'TAIEX',
        benchmark_name: '發行量加權股價指數',
        close: 22000,
        meta: { source: 'taiwan_index_store', trade_date: '2026-10-21', status: 'stale', is_stale: true },
      },
    } as any)
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByText('2026-10-21')).toBeInTheDocument()
    expect(screen.getByText('非最新交易日')).toBeInTheDocument()
  })

  it('shows -- for an alert without a trigger price', async () => {
    vi.mocked(api.taiwanStockDetail).mockResolvedValue({
      ...buildDetailResponse(),
      recent_alerts: [
        { alert_id: 'a1', rule_id: 'r1', rule_name: '無價格規則', rule_type: 'price', severity: 'warning',
          trigger_price: null, trigger_value: 0, message: 'm1', triggered_at: '2026-10-22T10:00:00' },
        { alert_id: 'a2', rule_id: 'r2', rule_name: '有價格規則', rule_type: 'price', severity: 'warning',
          trigger_price: 24.3, trigger_value: 0, message: 'm2', triggered_at: '2026-10-22T10:01:00' },
      ],
    } as any)
    renderAt(['/stocks/2330.TWSE'], 0)

    expect(await screen.findByText('觸發價 --')).toBeInTheDocument()
    expect(screen.getByText('觸發價 24.30')).toBeInTheDocument()
  })
})
