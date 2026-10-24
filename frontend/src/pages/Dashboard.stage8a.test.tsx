import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Dashboard } from './Dashboard'
import { api } from '@/lib/api'

const preview = vi.hoisted(() => ({ loads: 0 }))

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

vi.mock('@/components/StockPreviewDialog', () => {
  preview.loads += 1
  return {
    StockPreviewDialog: ({ symbol }: { symbol: string | null }) =>
      symbol ? <div data-testid="preview">{symbol}</div> : null,
  }
})

function mockDefaults() {
  vi.mocked(api.taiwanDataStatus).mockResolvedValue(null as any)
  vi.mocked(api.alertsList).mockResolvedValue({ alerts: [], total: 0 })
  vi.mocked(api.taiwanMarketIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.taiwanIndustryIntelligence).mockResolvedValue(null as any)
  vi.mocked(api.watchlistEnriched).mockResolvedValue({ rows: [], as_of: null, elapsed_ms: 0 } as any)
  vi.mocked(api.screenerCachedSummary).mockResolvedValue(null as any)
  vi.mocked(api.screenerStrategies).mockResolvedValue({
    presets: [{ id: 'lorentzian_knn', name: 'Lorentzian KNN 分類' }] as any,
    load_errors: [],
  } as any)
  vi.mocked(api.screenerCached).mockResolvedValue({
    as_of: '2026-09-21',
    results: { lorentzian_knn: { total: 1, as_of: '2026-09-21', rows: [{ symbol: '2330.TWSE', name: '台積電', close: 100, change_pct: 0.01 }] } },
    today_ever_matched: null,
    today_ever_rows: null,
    updated_at: null,
  } as any)
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('Dashboard preview dialog loading', () => {
  it('loads the preview dialog, and the charts it carries, only when a stock is opened', async () => {
    mockDefaults()
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    const open = await screen.findByRole('button', { name: '查看 台積電 走勢' })
    expect(preview.loads).toBe(0)

    fireEvent.click(open)

    expect(await screen.findByTestId('preview')).toHaveTextContent('2330.TWSE')
    expect(preview.loads).toBe(1)
  })
})
