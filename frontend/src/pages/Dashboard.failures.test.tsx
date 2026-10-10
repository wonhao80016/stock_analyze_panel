import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Dashboard } from './Dashboard'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: {
    taiwanDataStatus: vi.fn().mockResolvedValue(null),
    alertsList: vi.fn().mockResolvedValue({ alerts: [] }),
    taiwanMarketIntelligence: vi.fn().mockResolvedValue(null),
    taiwanIndustryIntelligence: vi.fn().mockResolvedValue(null),
    watchlistEnriched: vi.fn().mockResolvedValue({ rows: [], as_of: null, elapsed_ms: 0 }),
    screenerCached: vi.fn(),
    screenerStrategies: vi.fn().mockResolvedValue({
      presets: [{ id: 'high_turnover_surge', name: '高換手拉升' }],
      load_errors: [],
    }),
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

function mockCache(failures?: Record<string, { error: string }>) {
  vi.mocked(api.screenerCached).mockResolvedValue({
    as_of: '2026-09-30',
    results: {},
    today_ever_matched: null,
    today_ever_rows: null,
    updated_at: null,
    ...(failures ? { failures } : {}),
  } as any)
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('Dashboard — 策略當日推薦卡的計算失敗', () => {
  it('沒有選股且有策略計算失敗時顯示失敗狀態, 不說等待排程', async () => {
    mockCache({
      high_turnover_surge: { error: 'boom' },
      composite_blend: { error: 'boom' },
    })
    renderDashboard()

    expect(await screen.findByText('2 支策略本次計算失敗，沒有選股結果')).toBeInTheDocument()
    expect(screen.getByText(/高換手拉升、composite_blend/)).toBeInTheDocument()
    expect(screen.queryByText('今日尚無策略選股結果')).not.toBeInTheDocument()
    expect(screen.queryByText('盤後排程執行後會自動更新')).not.toBeInTheDocument()
  })

  it('沒有失敗清單的空快取照舊顯示等待排程', async () => {
    mockCache()
    renderDashboard()

    expect(await screen.findByText('今日尚無策略選股結果')).toBeInTheDocument()
    expect(screen.queryByText(/本次計算失敗/)).not.toBeInTheDocument()
  })
})
