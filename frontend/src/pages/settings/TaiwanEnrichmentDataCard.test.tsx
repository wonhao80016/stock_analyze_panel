import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApiError, api, type TaiwanEnrichmentBackfillJobState } from '@/lib/api'
import { toast } from '@/components/Toast'
import { TaiwanEnrichmentDataCard } from './TaiwanEnrichmentDataCard'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: {
    taiwanEnrichmentStatus: vi.fn(),
    taiwanEnrichmentBackfillRun: vi.fn(),
    taiwanEnrichmentBackfillJob: vi.fn(),
  },
}))

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

const dataset = { has_data: true, day_count: 60, earliest: '2026-07-01', latest: '2026-10-23' }
const stats = { dates_requested: 1, dates_fetched: 1, dates_skipped: 0, total_rows_written: 10, failed_dates: [] }

const finishedJob: TaiwanEnrichmentBackfillJobState = {
  job_id: 'job-1',
  status: 'success',
  stage: '舊任務階段',
  progress: 100,
  current_dataset: null,
  current_date: null,
  day_index: 60,
  total_days: 60,
  message: '舊任務完成',
  result: { institutional: stats, margin: stats },
}

const notFound = () => new ApiError('job not found', 404, undefined, '/api/taiwan/enrichment/backfill/job-1')

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TaiwanEnrichmentDataCard />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.taiwanEnrichmentStatus).mockResolvedValue({ institutional: dataset, margin: dataset })
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('TaiwanEnrichmentDataCard backfill progress', () => {
  it('does not show the previous job while a new backfill is starting', async () => {
    vi.mocked(api.taiwanEnrichmentBackfillRun)
      .mockResolvedValueOnce({ job_id: 'job-1', status: 'pending' })
      .mockResolvedValueOnce({ job_id: 'job-2', status: 'pending' })
    vi.mocked(api.taiwanEnrichmentBackfillJob).mockImplementation((jobId: string) =>
      jobId === 'job-1' ? Promise.resolve(finishedJob) : new Promise(() => {}),
    )
    renderCard()

    fireEvent.click(await screen.findByRole('button', { name: '開始回補' }, { timeout: 5000 }))
    expect(await screen.findByText('上次回補結果', {}, { timeout: 5000 })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '開始回補' }))
    await waitFor(() => expect(api.taiwanEnrichmentBackfillJob).toHaveBeenCalledWith('job-2', { quiet: true }))

    expect(screen.getByText(/正在啟動回補任務/)).toBeInTheDocument()
    expect(screen.queryByText('舊任務完成')).toBeNull()
    expect(screen.queryByText('舊任務階段')).toBeNull()
  })

  it('stops polling a job the server no longer knows', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(api.taiwanEnrichmentBackfillRun).mockResolvedValue({ job_id: 'job-1', status: 'pending' })
    vi.mocked(api.taiwanEnrichmentBackfillJob).mockRejectedValue(notFound())
    renderCard()

    fireEvent.click(await screen.findByRole('button', { name: '開始回補' }, { timeout: 5000 }))

    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining('任務已遺失'), 'error'))
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(api.taiwanEnrichmentBackfillJob).toHaveBeenCalledTimes(1)
    expect(api.taiwanEnrichmentStatus).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: '開始回補' })).toBeEnabled()
  })

  it('gives up after repeated transient failures and reports once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(api.taiwanEnrichmentBackfillRun).mockResolvedValue({ job_id: 'job-1', status: 'pending' })
    vi.mocked(api.taiwanEnrichmentBackfillJob).mockRejectedValue(new ApiError('502 Bad Gateway', 502, undefined, '/api/taiwan/enrichment/backfill/job-1'))
    renderCard()

    fireEvent.click(await screen.findByRole('button', { name: '開始回補' }, { timeout: 5000 }))
    await waitFor(() => expect(api.taiwanEnrichmentBackfillJob).toHaveBeenCalled())
    await act(() => vi.advanceTimersByTimeAsync(60_000))

    const calls = vi.mocked(api.taiwanEnrichmentBackfillJob).mock.calls
    expect(calls.length).toBeGreaterThan(1)
    expect(calls.length).toBeLessThanOrEqual(6)
    expect(calls.every(([, opts]) => opts?.quiet === true)).toBe(true)
    const errorToasts = vi.mocked(toast).mock.calls.filter(([, kind]) => kind === 'error')
    expect(errorToasts).toHaveLength(1)
  })
})
