import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TaiwanAlertsList } from './TaiwanAlertsList'
import { api, type AlertEvent } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    alertDelete: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

const openPreview = vi.fn()
vi.mock('@/components/StockPreviewProvider', () => ({
  useStockPreview: () => ({ openPreview, closePreview: vi.fn() }),
}))

const TS = 1_792_800_000_000

function makeAlert(id: string, symbol: string): AlertEvent {
  return {
    id,
    ts: TS,
    rule_id: 'r1',
    source: 'signal',
    type: 'signal',
    symbol,
    message: `${symbol} hit`,
  }
}

function renderList(alerts: AlertEvent[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <TaiwanAlertsList alerts={alerts} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.mocked(api.alertDelete).mockClear()
  openPreview.mockClear()
})

describe('TaiwanAlertsList alerts sharing one ts', () => {
  it('renders every alert without duplicate React keys', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    renderList([makeAlert('a1', '2330.TWSE'), makeAlert('a2', '2317.TWSE')])

    expect(screen.getByText('2330.TWSE hit')).toBeTruthy()
    expect(screen.getByText('2317.TWSE hit')).toBeTruthy()
    const keyWarnings = errors.mock.calls.filter((args) =>
      args.some((a) => typeof a === 'string' && a.includes('same key')),
    )
    expect(keyWarnings).toEqual([])
  })

  it('deletes the clicked alert by its id', async () => {
    renderList([makeAlert('a1', '2330.TWSE'), makeAlert('a2', '2317.TWSE')])

    const buttons = screen.getAllByTitle('刪除這筆記錄')
    expect(buttons).toHaveLength(2)
    fireEvent.click(buttons[1])

    await waitFor(() => expect(api.alertDelete).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.alertDelete).mock.calls[0][0]).toBe('a2')
  })
})

describe('TaiwanAlertsList trigger time (B273)', () => {
  it('shows the date and time in Asia/Taipei', () => {
    // 2026-10-19 17:02:03 UTC is 2026-10-20 01:02:03 in Taipei.
    renderList([{ ...makeAlert('a1', '2330.TWSE'), ts: Date.UTC(2026, 9, 19, 17, 2, 3) }])

    const time = screen.getByText('10/20 01:02:03')
    expect(time.closest('[title]')).toHaveAttribute('title', '台灣時間 (Asia/Taipei)')
  })
})

describe('TaiwanAlertsList symbol button (B274)', () => {
  it('opens the stock preview for the clicked alert', () => {
    renderList([{ ...makeAlert('a1', '2330.TWSE'), name: '台積電' }])

    fireEvent.click(screen.getByRole('button', { name: /2330\.TWSE/ }))

    expect(openPreview).toHaveBeenCalledWith('2330.TWSE', '台積電')
  })
})
