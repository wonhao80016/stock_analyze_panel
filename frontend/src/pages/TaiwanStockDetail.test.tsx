// DAILY_USE_CORE_UX_FIXES (P1-2) — TaiwanStockDetail 返回按鈕回歸測試。
// 通用 idx-based 判斷邏輯已在 useSafeBack.test.tsx 完整驗證; 這裡只驗證本頁
// 真的接上了它 —— 不再寫死「返回即時監控」文案/目的地, 且能在有可信 in-app
// 來源(如監控中心)時正確返回該來源, 直接網址進入時安全落到台股選股 fallback。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanStockDetail, dailyRowsToOHLC, signColorClass, signPrefix } from './TaiwanStockDetail'
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
