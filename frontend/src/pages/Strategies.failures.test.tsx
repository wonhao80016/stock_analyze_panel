import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Strategies } from './Strategies'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      screenerStrategies: vi.fn(),
      screenerCached: vi.fn(),
    },
  }
})

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/strategies']}>
        <Strategies />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const PRESETS = [
  { id: 'boll_breakout', name: '布林突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] },
  { id: 'high_turnover_surge', name: '高換手拉升', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] },
  { id: 'oversold_bounce', name: '超跌反彈', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] },
]

function mockCache(failures?: Record<string, { error: string }>) {
  vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: PRESETS, load_errors: [] } as any)
  vi.mocked(api.screenerCached).mockResolvedValue({
    as_of: '2026-09-30',
    results: {
      boll_breakout: { total: 1, as_of: '2026-09-30', rows: [{ symbol: '2330.TWSE', name: '台積電' }] },
    },
    today_ever_matched: null,
    today_ever_rows: null,
    updated_at: null,
    ...(failures ? { failures } : {}),
  } as any)
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('策略庫 — 計算失敗', () => {
  it('計算失敗的策略標出失敗並附上原因, 與單純未執行的分開', async () => {
    mockCache({ high_turnover_surge: { error: 'unsupported matrix feature: turnover_rate' } })
    renderPage()

    expect(
      await screen.findByText('此策略本次計算失敗：unsupported matrix feature: turnover_rate'),
    ).toBeInTheDocument()
    expect(screen.getByText('計算失敗')).toBeInTheDocument()
    expect(screen.getByText('未執行')).toBeInTheDocument()
    expect(screen.getAllByText(/這支策略本輪沒有執行/)).toHaveLength(1)
  })

  it('計算失敗有自己的篩選, 不算進未執行', async () => {
    mockCache({ high_turnover_surge: { error: 'boom' } })
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /計算失敗 1/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /未執行 1/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /計算失敗 1/ }))

    expect(screen.getByText('高換手拉升')).toBeInTheDocument()
    expect(screen.queryByText('超跌反彈')).not.toBeInTheDocument()
    expect(screen.queryByText('布林突破')).not.toBeInTheDocument()
  })

  it('舊快取沒有失敗清單時照舊顯示未執行', async () => {
    mockCache()
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(screen.getAllByText('未執行')).toHaveLength(2)
    expect(screen.queryByText(/此策略本次計算失敗/)).not.toBeInTheDocument()
  })
})
