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

const PRESETS = [
  { id: 'boll_breakout', name: '布林突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] },
  { id: 'oversold_bounce', name: '超跌反彈', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] },
]

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

afterEach(() => {
  vi.clearAllMocks()
})

describe('策略庫 — 策略結果的載入與失敗狀態 (B300)', () => {
  it('結果載入中顯示讀取狀態, 不把策略標成未執行', async () => {
    vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: PRESETS, load_errors: [] } as any)
    vi.mocked(api.screenerCached).mockReturnValue(new Promise(() => {}))
    renderPage()

    expect(await screen.findByText('正在讀取策略選股結果…')).toBeInTheDocument()
    expect(screen.queryByText('未執行', { selector: 'span' })).not.toBeInTheDocument()
    expect(screen.queryByText(/這支策略本輪沒有執行/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /未執行 2/ })).not.toBeInTheDocument()
  })

  it('結果讀取失敗時顯示錯誤並可重試, 不把策略標成未執行', async () => {
    vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: PRESETS, load_errors: [] } as any)
    vi.mocked(api.screenerCached)
      .mockRejectedValueOnce(new Error('network error'))
      .mockResolvedValue({
        as_of: '2026-09-22',
        results: { boll_breakout: { total: 0, as_of: '2026-09-22', rows: [] } },
        updated_at: null,
      } as any)
    renderPage()

    expect(await screen.findByText('目前無法讀取策略選股結果。')).toBeInTheDocument()
    expect(screen.queryByText('未執行', { selector: 'span' })).not.toBeInTheDocument()
    expect(screen.queryByText(/這支策略本輪沒有執行/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重試' }))

    expect(await screen.findByText('0 檔')).toBeInTheDocument()
    expect(screen.getByText('未執行', { selector: 'span' })).toBeInTheDocument()
    expect(api.screenerCached).toHaveBeenCalledTimes(2)
  })
})
