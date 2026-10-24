import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GenericRulesList } from './GenericRulesList'
import { api, type MonitorRule } from '@/lib/api'

const RULE: MonitorRule = {
  id: 'r_intraday',
  name: '分時規則',
  enabled: true,
  type: 'signal',
  asset_type: 'stock',
  scope: 'watchlist_group',
  symbols: [],
  group_id: 'deleted_group',
  direction: 'entry',
  conditions: [{ field: 'signal_intraday_avg_cross_up', op: 'truth' }],
  logic: 'and',
  cooldown_seconds: 3600,
  severity: 'info',
  message: '',
  runtime_warning: '台股沒有分鐘資料源, 分時訊號需要設定分鐘資料插件',
}

vi.mock('@/lib/api', () => ({
  api: {
    monitorRulesList: vi.fn(),
    monitorRuleSave: vi.fn().mockResolvedValue({ ok: true, rule: {} }),
    monitorRuleSetEnabled: vi.fn().mockResolvedValue({ ok: true, rule: {} }),
    monitorRuleDelete: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

function renderList() {
  vi.mocked(api.monitorRulesList).mockResolvedValue({ rules: [RULE] })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <GenericRulesList onEdit={() => {}} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('GenericRulesList', () => {
  it('shows the runtime warning of a rule', async () => {
    renderList()
    expect(await screen.findByText(RULE.runtime_warning!)).toBeTruthy()
  })

  it('disables a rule through the enabled-only endpoint', async () => {
    renderList()
    fireEvent.click(await screen.findByTitle('點擊停用'))

    await waitFor(() => expect(api.monitorRuleSetEnabled).toHaveBeenCalledWith('r_intraday', false))
    expect(api.monitorRuleSave).not.toHaveBeenCalled()
  })
})
