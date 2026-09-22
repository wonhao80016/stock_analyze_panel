// 策略庫 — 看板的推薦卡只列出「今日有選到股票」的策略, 所以「跑了但零命中」
// 與「根本沒跑」在畫面上長得一樣(都是不存在)。這一頁的存在理由就是把三種
// 狀態分開, 因此測試鎖的是狀態的區分, 不是版面。
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
    api: { ...actual.api, screenerStrategies: vi.fn(), screenerCached: vi.fn() },
  }
})

vi.mock('@/components/StockPreviewDialog', () => ({
  StockPreviewDialog: ({ symbol }: { symbol: string }) => <div>preview:{symbol}</div>,
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Strategies />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const PRESETS = [
  { id: 'boll_breakout', name: '布林突破', description: '突破布林上軌加放量', tags: ['布林'], params: [], entry_signals: [], exit_signals: [] },
  { id: 'oversold_bounce', name: '超跌反彈', description: 'RSI14 低於 30', tags: [], params: [], entry_signals: [], exit_signals: [] },
  { id: 'high_turnover_surge', name: '高換手拉升', description: '換手率大於 5%', tags: [], params: [], entry_signals: [], exit_signals: [] },
]

function mockAll() {
  vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: PRESETS, load_errors: [] } as any)
  vi.mocked(api.screenerCached).mockResolvedValue({
    as_of: '2026-09-22',
    results: {
      boll_breakout: { total: 2, as_of: '2026-09-22', rows: [
        { symbol: '2330.TWSE', name: '台積電', close: 1000, change_pct: 0.02 },
        { symbol: '2317.TWSE', name: '鴻海', close: 200, change_pct: -0.01 },
      ] },
      oversold_bounce: { total: 0, as_of: '2026-09-22', rows: [] },
      // high_turnover_surge 不在 results 裡 = 本輪沒有執行
    },
    updated_at: null,
  } as any)
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('策略庫', () => {
  it('把「有命中」「零命中」「未執行」分成三種狀態, 而不是只顯示有命中的', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(screen.getByText('2 檔')).toBeInTheDocument()
    expect(screen.getByText('超跌反彈')).toBeInTheDocument()
    expect(screen.getByText('0 檔')).toBeInTheDocument()
    expect(screen.getByText('高換手拉升')).toBeInTheDocument()
    expect(screen.getByText('未執行')).toBeInTheDocument()
  })

  it('未執行的策略要說明原因, 不能只是沒有結果', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText(/這支策略本輪沒有執行/)).toBeInTheDocument()
    expect(screen.getByText(/流通股數/)).toBeInTheDocument()
  })

  it('預設收合, 展開後才列出選股與計算方式', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(screen.queryByText('台積電')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '布林突破 的細節' }))

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(screen.getByText('鴻海')).toBeInTheDocument()
    expect(screen.getByText('突破布林上軌加放量')).toBeInTheDocument()
  })

  it('篩選器可以只看未執行的策略', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /未執行 1/ }))

    expect(screen.getByText('高換手拉升')).toBeInTheDocument()
    expect(screen.queryByText('布林突破')).not.toBeInTheDocument()
  })

  it('選股清單為空時不會誤報成未執行', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('超跌反彈')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '超跌反彈 的細節' }))
    expect(screen.getByText(/沒有任何標的符合這支策略的條件/)).toBeInTheDocument()
  })
})
