import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SettingsCustomSignalsPanel } from './CustomSignals'
import { CustomSignalDialog } from '@/components/signals/CustomSignalDialog'
import { api, type CustomSignal } from '@/lib/api'

const SIGNAL: CustomSignal = {
  id: 'low_touches_ma5',
  name: '跌至MA5',
  kind: 'entry',
  enabled: true,
  conditions: [{ left: 'turnover_rate', op: '>', right: '5', leftDays: 0, rightDays: 0 }],
}

vi.mock('@/lib/api', () => ({
  api: {
    customSignalsList: vi.fn(),
    customSignalsOptions: vi.fn(),
    customSignalReferences: vi.fn(),
    customSignalDelete: vi.fn(),
    customSignalUpdate: vi.fn(),
    customSignalCreate: vi.fn(),
    customSignalSave: vi.fn(),
    strategyAiStatus: vi.fn(),
  },
}))

function mockApi() {
  vi.mocked(api.customSignalsList).mockResolvedValue({ signals: [SIGNAL] })
  vi.mocked(api.customSignalsOptions).mockResolvedValue({
    fields: [
      { key: 'turnover_rate', label: '換手率', unit: 'percent' },
      { key: 'change_pct', label: '漲跌幅', unit: 'decimal' },
    ],
    operators: ['>', '<'],
    kinds: [],
    units: { percent: '百分數（5 = 5%）', decimal: '小數（0.05 = 5%）' },
  })
  vi.mocked(api.customSignalReferences).mockResolvedValue({
    monitor_rules: [{ id: 'uses_it', name: '我的監控' }],
    strategies: [],
  })
  vi.mocked(api.customSignalDelete).mockResolvedValue({ ok: true })
  vi.mocked(api.customSignalUpdate).mockResolvedValue({ ok: true, signal: SIGNAL })
  vi.mocked(api.customSignalCreate).mockResolvedValue({ ok: true, signal: SIGNAL })
  vi.mocked(api.strategyAiStatus).mockResolvedValue({ configured: false } as never)
}

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  )
}

async function openCustomTab() {
  wrap(<SettingsCustomSignalsPanel />)
  const tab = screen.getAllByText('自訂訊號').map(el => el.closest('button')).find(Boolean)
  fireEvent.click(tab!)
  await screen.findByText('csg_low_touches_ma5')
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('custom signal references', () => {
  it('lists the monitor rules before deleting a referenced signal and deletes only on explicit confirm', async () => {
    mockApi()
    await openCustomTab()

    fireEvent.click(screen.getByTitle('刪除'))
    fireEvent.click(screen.getByTitle('再次點擊確認刪除'))

    expect(await screen.findByText(/我的監控/)).toBeInTheDocument()
    expect(api.customSignalDelete).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('仍要刪除'))
    await waitFor(() => expect(api.customSignalDelete).toHaveBeenCalledWith('low_touches_ma5', true))
  })

  it('lists the monitor rules before disabling a referenced signal', async () => {
    mockApi()
    await openCustomTab()

    fireEvent.click(screen.getByTitle('停用'))

    expect(await screen.findByText(/我的監控/)).toBeInTheDocument()
    expect(api.customSignalUpdate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('仍要停用'))
    await waitFor(() =>
      expect(api.customSignalUpdate).toHaveBeenCalledWith({ ...SIGNAL, enabled: false }, true),
    )
  })
})

describe('custom signal reference failures and strategies', () => {
  it('lists strategies that use the signal', async () => {
    mockApi()
    vi.mocked(api.customSignalReferences).mockResolvedValue({
      monitor_rules: [],
      strategies: [{ id: 'custom_uses_sig', name: '用到訊號的策略' }],
    })
    await openCustomTab()

    fireEvent.click(screen.getByTitle('停用'))

    expect(await screen.findByText(/用到訊號的策略/)).toBeInTheDocument()
    expect(api.customSignalUpdate).not.toHaveBeenCalled()
  })

  it('shows an error when the reference check fails', async () => {
    mockApi()
    vi.mocked(api.customSignalReferences).mockRejectedValue(new Error('引用查詢失敗'))
    await openCustomTab()

    fireEvent.click(screen.getByTitle('停用'))

    expect(await screen.findByText(/引用查詢失敗/)).toBeInTheDocument()
    expect(api.customSignalUpdate).not.toHaveBeenCalled()
  })

  it('shows an error when saving the enabled state fails', async () => {
    mockApi()
    vi.mocked(api.customSignalReferences).mockResolvedValue({ monitor_rules: [], strategies: [] })
    vi.mocked(api.customSignalUpdate).mockRejectedValue(new Error('儲存失敗了'))
    await openCustomTab()

    fireEvent.click(screen.getByTitle('停用'))

    expect(await screen.findByText(/儲存失敗了/)).toBeInTheDocument()
  })
})

describe('CustomSignalDialog', () => {
  it('saves an edited signal through the update call', async () => {
    mockApi()
    wrap(<CustomSignalDialog open signal={SIGNAL} onClose={() => {}} />)

    fireEvent.click(await screen.findByText('儲存'))

    await waitFor(() => expect(api.customSignalUpdate).toHaveBeenCalled())
    expect(api.customSignalCreate).not.toHaveBeenCalled()
  })

  it('refuses to create a signal whose id is already taken', async () => {
    mockApi()
    wrap(<CustomSignalDialog open onClose={() => {}} />)
    await waitFor(() => expect(api.customSignalsList).toHaveBeenCalled())

    fireEvent.change(screen.getByPlaceholderText('如 low_touches_ma5'), { target: { value: 'low_touches_ma5' } })
    fireEvent.change(screen.getByPlaceholderText('如 跌至MA5'), { target: { value: '新訊號' } })
    fireEvent.click(screen.getByText('儲存'))

    expect(await screen.findByText(/已存在/)).toBeInTheDocument()
    expect(api.customSignalCreate).not.toHaveBeenCalled()
  })

  it('shows the unit of the left field beside a constant threshold', async () => {
    mockApi()
    wrap(<CustomSignalDialog open signal={SIGNAL} onClose={() => {}} />)

    expect(await screen.findByText(/百分數（5 = 5%）/)).toBeInTheDocument()
  })
})
