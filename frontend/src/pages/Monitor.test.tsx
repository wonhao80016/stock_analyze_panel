import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Monitor } from './Monitor'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

vi.mock('@/lib/api', () => ({
  api: {
    alertsList: vi.fn(),
    alertsClear: vi.fn(),
    alertDelete: vi.fn(),
    monitorRulesList: vi.fn(),
  },
}))

vi.mock('@/lib/monitorBadge', () => ({ markSeen: vi.fn(), leaveMonitorPage: vi.fn() }))
vi.mock('@/components/monitor/TaiwanIndexBar', () => ({ TaiwanIndexBar: () => null }))
vi.mock('@/components/monitor/TaiwanQuotePanel', () => ({ TaiwanQuotePanel: () => null }))
vi.mock('@/components/monitor/GenericRulesList', () => ({ GenericRulesList: () => null }))
vi.mock('@/components/monitor/RuleEditor', () => ({ RuleEditor: () => null }))

const EMPTY_TEXT = '尚無即時觸發告警紀錄'

function renderMonitor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <Monitor />
    </QueryClientProvider>,
  )
  return qc
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('監控中心 — 觸發記錄的載入與錯誤狀態 (B298)', () => {
  it('載入中顯示讀取狀態, 不說沒有記錄', async () => {
    vi.mocked(api.monitorRulesList).mockResolvedValue({ rules: [] } as any)
    vi.mocked(api.alertsList).mockReturnValue(new Promise(() => {}))
    renderMonitor()

    expect(await screen.findByText('正在讀取觸發記錄…')).toBeInTheDocument()
    expect(screen.queryByText(EMPTY_TEXT)).not.toBeInTheDocument()
    expect(screen.queryByText('0 筆事件')).not.toBeInTheDocument()
  })

  it('讀取失敗時顯示錯誤與重試, 不說沒有記錄, 也不因此停用清空', async () => {
    vi.mocked(api.monitorRulesList).mockResolvedValue({ rules: [] } as any)
    vi.mocked(api.alertsList)
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue({ alerts: [], total: 0 })
    renderMonitor()

    expect(await screen.findByText(/觸發記錄讀取失敗/)).toBeInTheDocument()
    expect(screen.queryByText(EMPTY_TEXT)).not.toBeInTheDocument()
    expect(screen.queryByText('0 筆事件')).not.toBeInTheDocument()
    expect(screen.getByTitle('清空全部觸發記錄')).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument()
    expect(api.alertsList).toHaveBeenCalledTimes(2)
  })

  it('讀取成功且為空時才顯示沒有記錄, 並停用清空', async () => {
    vi.mocked(api.monitorRulesList).mockResolvedValue({ rules: [] } as any)
    vi.mocked(api.alertsList).mockResolvedValue({ alerts: [], total: 0 })
    renderMonitor()

    expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument()
    expect(screen.getByText('0 筆事件')).toBeInTheDocument()
    expect(screen.getByTitle('目前沒有觸發記錄')).toBeDisabled()
  })

  it('以含 days 與 limit 的 QK.alerts 快取 (B286)', async () => {
    vi.mocked(api.monitorRulesList).mockResolvedValue({ rules: [] } as any)
    vi.mocked(api.alertsList).mockResolvedValue({ alerts: [], total: 0 })
    const qc = renderMonitor()

    await screen.findByText(EMPTY_TEXT)
    expect(api.alertsList).toHaveBeenCalledWith({ days: 7, limit: 500 })
    expect(qc.getQueryCache().find({ queryKey: QK.alerts({ days: 7, limit: 500 }), exact: true })).toBeDefined()
  })
})
