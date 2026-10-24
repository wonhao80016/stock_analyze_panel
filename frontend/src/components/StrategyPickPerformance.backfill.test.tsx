import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrategyPickPerformance } from './StrategyPickPerformance'
import { api, ApiError } from '@/lib/api'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      screenerPickPerformance: vi.fn(),
      screenerPickBackfill: vi.fn(),
      screenerPickBackfillStatus: vi.fn(),
    },
  }
})

const SUMMARY = {
  method: {
    entry: 'next_session_open', horizons: [1, 5, 10, 20], price_adjustment: 'forward_adjusted_incl_dividends',
    costs_included: false, slippage_included: false, exit_fill_simulated: false, return_unit: 'decimal',
  },
  adjustment: { status: 'ok', reason: null, covered_through: '2026-09-30' },
  first_signal_date: '2026-09-01',
  last_signal_date: '2026-10-01',
  prices_through: '2026-10-02',
  strategies: [],
}

const RUNNING = {
  job_id: 'history-lost', status: 'running', requested_days: 5, dates: ['2026-09-23'],
  completed_days: 0, written_runs: 0, existing_runs: 0, zero_signal_runs: 0, failures: [], error: null,
}

function notFound() {
  return new ApiError('Historical reconstruction job was not found', 404, undefined, '/api/screener/pick-performance/backfill/history-lost')
}

function renderPanel() {
  // Production default: three retries per failed fetch.
  const qc = new QueryClient({ defaultOptions: { queries: { retryDelay: 10 } } })
  render(
    <QueryClientProvider client={qc}>
      <StrategyPickPerformance nameById={new Map()} onPreview={() => {}} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('事後統計 — 歷史回算工作遺失 (B266)', () => {
  it('狀態查詢失敗 (後端重啟後 404) 時停止輪詢, 不依舊的 running 資料無限重查', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(api.screenerPickPerformance).mockResolvedValue(SUMMARY as any)
    vi.mocked(api.screenerPickBackfill).mockResolvedValue({ ...RUNNING } as any)
    vi.mocked(api.screenerPickBackfillStatus)
      .mockResolvedValueOnce({ ...RUNNING } as any)
      .mockRejectedValue(notFound())
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: '補算歷史訊號' }))
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toBeInTheDocument()
    const callsAtError = vi.mocked(api.screenerPickBackfillStatus).mock.calls.length

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })

    expect(vi.mocked(api.screenerPickBackfillStatus).mock.calls.length).toBe(callsAtError)
    expect(callsAtError).toBeLessThanOrEqual(3)
    expect(screen.getByRole('button', { name: '補算歷史訊號' })).toBeEnabled()
  })

  it('其他錯誤照常重試, 重試用完後仍依 running 繼續輪詢', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(api.screenerPickPerformance).mockResolvedValue(SUMMARY as any)
    vi.mocked(api.screenerPickBackfill).mockResolvedValue({ ...RUNNING } as any)
    const serverError = new ApiError('Internal Server Error', 500, undefined, '/api/screener/pick-performance/backfill/history-lost')
    vi.mocked(api.screenerPickBackfillStatus)
      .mockResolvedValueOnce({ ...RUNNING } as any)
      .mockRejectedValueOnce(serverError)
      .mockRejectedValueOnce(serverError)
      .mockRejectedValueOnce(serverError)
      .mockRejectedValueOnce(serverError)
      .mockResolvedValue({ ...RUNNING, status: 'completed', completed_days: 1, dates: [...RUNNING.dates] } as any)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: '補算歷史訊號' }))
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toBeInTheDocument()
    // One failed poll plus its three retries.
    expect(api.screenerPickBackfillStatus).toHaveBeenCalledTimes(5)

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })

    expect(await screen.findByText(/補算完成：1／1 日/)).toBeInTheDocument()
    expect(api.screenerPickBackfillStatus).toHaveBeenCalledTimes(6)
  })
})
