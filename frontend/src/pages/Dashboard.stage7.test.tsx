import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Dashboard } from './Dashboard'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

function dataStatus(overrides: Record<string, unknown> = {}) {
  return {
    daily_as_of: '2026-09-04',
    institutional_as_of: '2026-09-04',
    margin_as_of: '2026-09-04',
    target_latest_trading_date: '2026-09-04',
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
    ...overrides,
  }
}

function cachedSummary(cacheUpdatedAt: number | null) {
  return { as_of: '2026-09-04', results: {}, today_ever_counts: {}, updated_at: Date.now(), cache_updated_at: cacheUpdatedAt }
}

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: {
    taiwanDataStatus: vi.fn(),
    alertsList: vi.fn(),
    taiwanMarketIntelligence: vi.fn(),
    taiwanIndustryIntelligence: vi.fn(),
    watchlistEnriched: vi.fn(),
    screenerCached: vi.fn(),
    screenerCachedSummary: vi.fn(),
    screenerStrategies: vi.fn(),
  },
}))

vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: () => null }))

function mockDefaults() {
  vi.mocked(api.taiwanDataStatus).mockResolvedValue(dataStatus() as any)
  vi.mocked(api.alertsList).mockResolvedValue({ alerts: [], total: 0 })
  vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.watchlistEnriched).mockResolvedValue({ rows: [], as_of: null, elapsed_ms: 0 } as any)
  vi.mocked(api.screenerCached).mockResolvedValue({ as_of: null, results: {}, today_ever_matched: null, today_ever_rows: null, updated_at: null } as any)
  vi.mocked(api.screenerCachedSummary).mockResolvedValue(cachedSummary(1000) as any)
  vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: [], load_errors: [] } as any)
}

function renderDashboard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return qc
}

function pick(symbol: string, name: string) {
  return { symbol, name, close: 100, change_pct: 0.01 }
}

async function settle(qc: QueryClient) {
  await vi.waitFor(() => {
    expect(qc.getQueryData(QK.taiwanDataStatus)).toBeDefined()
    expect(qc.getQueryData(QK.screenerCachedSummary)).toBeDefined()
  })
  await act(async () => {})
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('Dashboard — 監控小組件的查詢鍵 (B293)', () => {
  it('以含 days 與 limit 的 QK.alerts 快取, 不與監控中心共用', async () => {
    mockDefaults()
    const qc = renderDashboard()

    expect(await screen.findByText('暫無觸發記錄')).toBeInTheDocument()
    expect(api.alertsList).toHaveBeenCalledWith({ days: 7, limit: 10 })
    expect(qc.getQueryCache().find({ queryKey: QK.alerts({ days: 7, limit: 10 }), exact: true })).toBeDefined()
    expect(qc.getQueryCache().find({ queryKey: ['alerts', ''], exact: true })).toBeUndefined()
  })
})

describe('Dashboard — 策略當日推薦卡的計數與連結 (B295, B296)', () => {
  it('標頭以去重後的個股數顯示, 不把多支策略的同一檔重複計算', async () => {
    mockDefaults()
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-17',
      results: {
        a: { total: 2, as_of: '2026-09-17', rows: [pick('2330.TWSE', '台積電'), pick('2317.TWSE', '鴻海')] },
        b: { total: 1, as_of: '2026-09-17', rows: [pick('2330.TWSE', '台積電')] },
      },
      updated_at: null,
    } as any)
    renderDashboard()

    expect(await screen.findByText('2026-09-17 · 2 檔')).toBeInTheDocument()
    expect(screen.queryByText('2026-09-17 · 3 檔')).not.toBeInTheDocument()
  })

  it('「還有 N 個策略」連到策略庫', async () => {
    mockDefaults()
    const results: Record<string, unknown> = {}
    for (let i = 0; i < 7; i++) {
      results[`s${i}`] = { total: 1, as_of: '2026-09-17', rows: [pick(`${2300 + i}.TWSE`, `股${i}`)] }
    }
    vi.mocked(api.screenerCached).mockResolvedValue({ as_of: '2026-09-17', results, updated_at: null } as any)
    renderDashboard()

    const link = await screen.findByRole('link', { name: '還有 1 個策略有選股結果 →' })
    expect(link).toHaveAttribute('href', '/strategies')
  })
})

describe('Dashboard — 盤後發佈後自動更新 (B294)', () => {
  it('資料日期改變時重新讀取市場與產業強弱', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockDefaults()
    vi.mocked(api.taiwanDataStatus)
      .mockResolvedValueOnce(dataStatus() as any)
      .mockResolvedValue(dataStatus({ daily_as_of: '2026-09-05', institutional_as_of: '2026-09-05' }) as any)
    const qc = renderDashboard()

    await vi.waitFor(() => expect(api.taiwanMarketIntelligence).toHaveBeenCalledTimes(1))
    await settle(qc)
    await act(async () => { await vi.advanceTimersByTimeAsync(61_000) })

    await vi.waitFor(() => expect(api.taiwanMarketIntelligence).toHaveBeenCalledTimes(2))
    expect(api.taiwanIndustryIntelligence).toHaveBeenCalledTimes(2)
  })

  it('策略快取重新寫入 (盤後與晚間定稿) 時重新讀取選股與市場資料', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockDefaults()
    vi.mocked(api.screenerCachedSummary)
      .mockResolvedValueOnce(cachedSummary(1000) as any)
      .mockResolvedValue(cachedSummary(2000) as any)
    const qc = renderDashboard()

    await vi.waitFor(() => expect(api.screenerCached).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(api.taiwanMarketIntelligence).toHaveBeenCalledTimes(1))
    await settle(qc)
    await act(async () => { await vi.advanceTimersByTimeAsync(61_000) })

    await vi.waitFor(() => expect(api.screenerCached).toHaveBeenCalledTimes(2))
    expect(api.taiwanMarketIntelligence).toHaveBeenCalledTimes(2)
    expect(api.taiwanIndustryIntelligence).toHaveBeenCalledTimes(2)
  })

  it('第一次寫入策略快取 (原本沒有快取檔) 也會重新讀取', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockDefaults()
    vi.mocked(api.screenerCachedSummary)
      .mockResolvedValueOnce(cachedSummary(null) as any)
      .mockResolvedValue(cachedSummary(2000) as any)
    const qc = renderDashboard()

    await vi.waitFor(() => expect(api.screenerCached).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(api.taiwanMarketIntelligence).toHaveBeenCalledTimes(1))
    await settle(qc)
    await act(async () => { await vi.advanceTimersByTimeAsync(61_000) })

    await vi.waitFor(() => expect(api.screenerCached).toHaveBeenCalledTimes(2))
    expect(api.taiwanMarketIntelligence).toHaveBeenCalledTimes(2)
  })

  it('沒有新發佈時不重算市場強弱', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockDefaults()
    const qc = renderDashboard()

    await vi.waitFor(() => expect(api.taiwanMarketIntelligence).toHaveBeenCalledTimes(1))
    await settle(qc)
    await act(async () => { await vi.advanceTimersByTimeAsync(61_000) })

    await vi.waitFor(() => expect(api.screenerCachedSummary).toHaveBeenCalledTimes(2))
    expect(api.taiwanMarketIntelligence).toHaveBeenCalledTimes(1)
    expect(api.screenerCached).toHaveBeenCalledTimes(1)
  })
})
