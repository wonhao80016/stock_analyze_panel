// DAILY_USE_CORE_UX_FIXES (P1-3) — StockPreviewDialog「查看完整個股」出口
// 回歸測試。重的子元件(K線/分時圖/監控編輯器/擴充插槽)與本測試要驗證的
// 「有明確路徑離開空白預覽、進到完整個股頁」無關, 全部 mock 掉。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StockPreviewDialog } from './StockPreviewDialog'

vi.mock('@/lib/api', () => ({
  api: {
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    monitorRulesList: vi.fn().mockResolvedValue({ rules: [] }),
    preferences: vi.fn().mockResolvedValue({}),
    quoteStatus: vi.fn().mockResolvedValue({ running: false }),
  },
}))

vi.mock('@/components/StockPanel', () => ({
  StockPanel: () => <div data-testid="stock-panel" />,
  getDefaultRange: () => ({ start: '2026-03-09', end: '2026-09-09' }),
}))
vi.mock('@/components/StockMultiDayIntradayChart', () => ({
  StockMultiDayIntradayChart: () => <div data-testid="intraday-chart" />,
}))
vi.mock('@/components/monitor/RuleEditor', () => ({ RuleEditor: () => null }))
vi.mock('@/components/stock-analysis/PriceAlertDialog', () => ({ PriceAlertDialog: () => null }))
vi.mock('@/extensions/ExtensionSlot', () => ({ ExtensionSlot: () => null }))
vi.mock('@/components/WatchlistAddMenu', () => ({ WatchlistAddMenu: () => null }))

function renderDialog(symbol = '2454.TWSE') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/watchlist']}>
        <Routes>
          <Route path="/watchlist" element={<div>WATCHLIST PAGE (dialog host)</div>} />
          <Route path="/stocks/:symbol" element={<div>STOCK DETAIL PAGE</div>} />
        </Routes>
        <StockPreviewDialog symbol={symbol} name="聯發科" onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

// 研究頁內容已內嵌於本 dialog, 不再提供離開本頁的出口。
describe('StockPreviewDialog — 不再導向個股頁', () => {
  it('不提供「查看完整個股」出口, 也不會切換到個股 route', async () => {
    renderDialog('2454.TWSE')

    await screen.findByTitle('關閉')
    expect(screen.queryByRole('button', { name: /查看.*完整個股頁/ })).not.toBeInTheDocument()
    expect(screen.queryByText('STOCK DETAIL PAGE')).toBeNull()
  })

  it('非台股代碼同樣不提供該出口', async () => {
    renderDialog('DEMO.CUSTOM')

    await screen.findByTitle('關閉')
    expect(screen.queryByRole('button', { name: /查看.*完整個股頁/ })).not.toBeInTheDocument()
  })
})
