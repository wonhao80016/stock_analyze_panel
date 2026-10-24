import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createElement } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { toast } from '@/components/Toast'
import { QK } from '@/lib/queryKeys'
import { TaiwanHistoryDataCard } from './TaiwanHistoryDataCard'

vi.mock('@/lib/api', () => ({
  api: {
    taiwanHistoryStatus: vi.fn(),
    taiwanUpdateLatest: vi.fn(),
    taiwanBootstrapRun: vi.fn(),
    taiwanBootstrapJob: vi.fn(),
  },
}))

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

const read = (relativePath: string) => readFileSync(resolve(__dirname, relativePath), 'utf-8')

describe('Taiwan History Daily Data Bootstrap & UI Card', () => {
  it('renders history status, 31 MiB package info, date range, and action buttons in TaiwanHistoryDataCard', () => {
    const cardCode = read('TaiwanHistoryDataCard.tsx')
    expect(cardCode).toContain('台股歷史日 K 資料庫')
    expect(cardCode).toContain('資料已就緒')
    expect(cardCode).toContain('缺少歷史日 K')
    expect(cardCode).toContain('最早交易日')
    expect(cardCode).toContain('最新交易日')
    expect(cardCode).toContain('涵蓋交易天數')
    expect(cardCode).toContain('更新到最新')
    expect(cardCode).toContain('一鍵下載官方歷史日 K 資料包')
    expect(cardCode).toContain('2024-01-02 ~ 2026-09-10')
    expect(cardCode).toContain('taiwanHistoryStatus')
    expect(cardCode).toContain('taiwanBootstrapRun')
    expect(cardCode).toContain('taiwanUpdateLatest')
    expect(cardCode).toContain('taiwanBootstrapJob')
  })

  it('embeds TaiwanHistoryDataCard inside TaiwanOfficialDetail in DataSources.tsx', () => {
    const dataSourcesCode = read('DataSources.tsx')
    expect(dataSourcesCode).toContain("import { TaiwanHistoryDataCard } from './TaiwanHistoryDataCard'")
    expect(dataSourcesCode).toContain('<TaiwanHistoryDataCard />')
    expect(dataSourcesCode).toContain('台灣官方資料源 (TWSE/TPEx)')
  })

  it('exposes typed API methods and data contracts in api.ts and queryKeys.ts', () => {
    const apiCode = read('../../lib/api.ts')
    expect(apiCode).toContain('taiwanHistoryStatus:')
    expect(apiCode).toContain('taiwanBootstrapRun:')
    expect(apiCode).toContain('taiwanBootstrapJob:')
    expect(apiCode).toContain('taiwanUpdateLatest:')
    expect(apiCode).toContain('interface TaiwanHistoryStatus')
    expect(apiCode).toContain('interface TaiwanBootstrapJobState')

    const qkCode = read('../../lib/queryKeys.ts')
    expect(qkCode).toContain('taiwanHistoryStatus:')
  })
})

describe('TaiwanHistoryDataCard update to latest', () => {
  it('invalidates the cached screener results even when no new dates were fetched', async () => {
    vi.mocked(api.taiwanHistoryStatus).mockResolvedValue({
      has_data: true,
      earliest_date: '2024-01-02',
      latest_date: '2026-10-02',
      trading_days: 670,
      needs_bootstrap: false,
    })
    vi.mocked(api.taiwanUpdateLatest).mockResolvedValue({
      ok: true,
      already_current: true,
      message: 'already current',
      dates_fetched: 0,
      benchmark_index_latest_date: '2026-10-02',
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(QK.screenerCached(), { results: [] })
    render(createElement(QueryClientProvider, { client: queryClient }, createElement(TaiwanHistoryDataCard)))

    fireEvent.click(await screen.findByRole('button', { name: '更新到最新' }))

    await waitFor(() => expect(api.taiwanUpdateLatest).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(queryClient.getQueryState(QK.screenerCached())?.isInvalidated).toBe(true))
  })

  it('invalidates the dashboard market panels when only chip data caught up', async () => {
    vi.mocked(api.taiwanHistoryStatus).mockResolvedValue({
      has_data: true,
      earliest_date: '2024-01-02',
      latest_date: '2026-10-21',
      trading_days: 690,
      needs_bootstrap: false,
    })
    const stats = (dates_fetched: number) => ({
      status: 'success' as const,
      dates_requested: dates_fetched,
      dates_fetched,
      dates_skipped: 0,
      failed_dates: [],
      rows_written: dates_fetched,
      note: null,
    })
    vi.mocked(api.taiwanUpdateLatest).mockResolvedValue({
      ok: true,
      already_current: true,
      message: '本地資料已是最新 (2026-10-21), 籌碼資料: 大盤法人金額至 2026-10-21',
      dates_fetched: 0,
      chip_data: { institutional: stats(0), market_institutional: stats(3), margin: stats(0) },
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(['taiwanMarketIntelligence'], { institutional_amounts: null })
    render(createElement(QueryClientProvider, { client: queryClient }, createElement(TaiwanHistoryDataCard)))

    fireEvent.click(await screen.findByRole('button', { name: '更新到最新' }))

    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining('籌碼資料'), 'success'))
    await waitFor(() =>
      expect(queryClient.getQueryState(['taiwanMarketIntelligence'])?.isInvalidated).toBe(true),
    )
  })
})

const READY_STATUS = {
  has_data: true,
  earliest_date: '2024-01-02',
  latest_date: '2026-10-21',
  trading_days: 690,
  needs_bootstrap: false,
}

const errorToasts = () => vi.mocked(toast).mock.calls.filter(([, kind]) => kind === 'error')

describe('TaiwanHistoryDataCard failures and bundle import', () => {
  it('leaves the error toast of a refused update to the request helper', async () => {
    vi.mocked(toast).mockClear()
    vi.mocked(api.taiwanHistoryStatus).mockResolvedValue(READY_STATUS)
    vi.mocked(api.taiwanUpdateLatest).mockRejectedValue(new Error('另一個台股資料更新正在執行, 請稍後再試'))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(createElement(QueryClientProvider, { client: queryClient }, createElement(TaiwanHistoryDataCard)))

    fireEvent.click(await screen.findByRole('button', { name: '更新到最新' }))

    await waitFor(() => expect(api.taiwanUpdateLatest).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(errorToasts()).toEqual([])
  })

  it('still reports a network failure that never reached the server', async () => {
    vi.mocked(toast).mockClear()
    vi.mocked(api.taiwanHistoryStatus).mockResolvedValue(READY_STATUS)
    vi.mocked(api.taiwanUpdateLatest).mockRejectedValue(new TypeError('Failed to fetch'))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(createElement(QueryClientProvider, { client: queryClient }, createElement(TaiwanHistoryDataCard)))

    fireEvent.click(await screen.findByRole('button', { name: '更新到最新' }))

    await waitFor(() => expect(errorToasts()).toHaveLength(1))
  })

  it('invalidates every query once a bundle import succeeds', async () => {
    vi.mocked(api.taiwanHistoryStatus).mockResolvedValue({
      has_data: false,
      earliest_date: null,
      latest_date: null,
      trading_days: 0,
      needs_bootstrap: true,
    })
    vi.mocked(api.taiwanBootstrapRun).mockResolvedValue({ job_id: 'job-1' } as never)
    vi.mocked(api.taiwanBootstrapJob).mockResolvedValue({
      job_id: 'job-1',
      status: 'success',
      stage: '已完成',
      progress: 100,
      message: 'done',
      error: null,
      bytes_downloaded: 0,
      total_bytes: 0,
      updated_at: '2026-10-24T08:00:00Z',
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(['taiwanMarketIntelligence'], { institutional_amounts: null })
    render(createElement(QueryClientProvider, { client: queryClient }, createElement(TaiwanHistoryDataCard)))

    fireEvent.click(await screen.findByRole('button', { name: /一鍵下載歷史資料包/ }))

    await waitFor(() => expect(api.taiwanBootstrapJob).toHaveBeenCalledWith('job-1'))
    await waitFor(() =>
      expect(queryClient.getQueryState(['taiwanMarketIntelligence'])?.isInvalidated).toBe(true),
    )
  })
})
