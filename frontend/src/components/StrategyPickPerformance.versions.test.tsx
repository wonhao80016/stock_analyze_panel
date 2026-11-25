import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrategyPickPerformance } from './StrategyPickPerformance'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      screenerPickPerformance: vi.fn(),
      screenerPickPerformanceDetail: vi.fn(),
      screenerPickBackfill: vi.fn(),
      screenerPickBackfillStatus: vi.fn(),
    },
  }
})

const METHOD = {
  entry: 'next_session_open', horizons: [1, 5, 10, 20], price_adjustment: 'forward_adjusted_incl_dividends',
  costs_included: false, slippage_included: false, exit_fill_simulated: false, return_unit: 'decimal',
}

function horizon(mean: number, extra: Record<string, unknown> = {}) {
  return {
    sample_count: 4, sample_days: 2, mean_return: mean, median_return: mean, positive_ratio: 0.5,
    adjustment_not_covered: 0, data_incomplete: 0, prices_incomplete: 0, stale_exit_count: 0, pending_count: 0,
    ...extra,
  }
}

function stats(id: string, signals: number, h: ReturnType<typeof horizon>) {
  return {
    strategy_id: id, signal_count: signals, signal_days: 30, filled_count: signals, not_filled_limit_up: 0,
    not_filled_no_trade: 0, limit_check_unknown: 0, pending_entry_count: 0, tracking_count: 0,
    completed_count: signals, entry_prices_incomplete_count: 0, entry_data_incomplete_count: 0, absent_count: 0,
    history_degraded_count: 0, excluded: { manifest_missing: 0, recorded_late: 0, universe_incomplete: 0 },
    horizons: { '1': h, '5': h, '10': h, '20': h },
  }
}

const BENCHMARKED = horizon(0.031, {
  benchmark_sample_count: 4, mean_benchmark_return: 0.012, mean_excess_return: 0.019, beat_benchmark_ratio: 0.75,
})
const LEGACY = horizon(-0.02, {
  benchmark_sample_count: 3, mean_benchmark_return: 0.01, mean_excess_return: -0.03, beat_benchmark_ratio: 0.25,
})

function summary(extra: Record<string, unknown> = {}) {
  return {
    method: { ...METHOD, benchmark: '0050.TWSE' },
    adjustment: { status: 'ok', reason: null, covered_through: '2026-11-24' },
    first_signal_date: '2026-09-02',
    last_signal_date: '2026-11-24',
    prices_through: '2026-11-24',
    strategies: [stats('boll_breakout', 12, BENCHMARKED)],
    legacy_strategies: [stats('boll_breakout', 70, LEGACY)],
    version_counts: { current: 12, legacy: 70 },
    ...extra,
  }
}

const ret = (value: number | null, benchmark: number | null, excess: number | null) =>
  ({ value, reason: null, stale_exit: false, stale_days: 0, benchmark_return: benchmark, excess_return: excess })

function detail() {
  return {
    strategy_id: 'boll_breakout', method: METHOD, adjustment: summary().adjustment, prices_through: '2026-11-24',
    total: 1, signal_days: 1,
    rows: [{
      signal_date: '2026-10-01', symbol: '2330.TWSE', name: '台積電', rank: 1, entry_date: '2026-10-02',
      entry_price_raw: 1000, status: 'completed', limit_check: 'checked', absent_since: null, excluded_reason: null,
      recorded_late: false, manifest_missing: false, universe_complete: true, history_degraded: false,
      version_status: 'current', strategy_version: 'abc',
      returns: {
        '1': ret(0.01, 0.002, 0.008), '5': ret(0.02, 0.03, -0.01),
        '10': ret(0.03, 0.01, 0.02), '20': ret(0.04, 0.015, 0.025),
      },
    }],
  }
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <StrategyPickPerformance nameById={new Map([['boll_breakout', '布林突破']])} onPreview={() => {}} />
    </QueryClientProvider>,
  )
}

function strategyRow() {
  return screen.getByRole('button', { name: '布林突破 的選股紀錄' }).closest('tr')!
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('事後統計 — 策略版本與大盤比較', () => {
  it('預設只顯示目前版本的彙總，切換後改看舊版', async () => {
    vi.mocked(api.screenerPickPerformance).mockResolvedValue(summary() as any)
    renderPanel()

    const current = await screen.findByRole('button', { name: '目前版本（12 筆）' })
    expect(current).toHaveAttribute('aria-pressed', 'true')
    expect(within(strategyRow()).getByText('12（30 日）')).toBeInTheDocument()
    expect(within(strategyRow()).getAllByText('+3.10%').length).toBe(2)

    fireEvent.click(screen.getByRole('button', { name: '舊版（70 筆）' }))

    expect(screen.getByRole('button', { name: '舊版（70 筆）' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(strategyRow()).getByText('70（30 日）')).toBeInTheDocument()
    expect(within(strategyRow()).queryByText('+3.10%')).not.toBeInTheDocument()
    expect(within(strategyRow()).getAllByText('-2.00%').length).toBe(2)
  })

  it('彙總列出大盤平均報酬、平均超額報酬與勝過大盤比例，紅漲綠跌', async () => {
    vi.mocked(api.screenerPickPerformance).mockResolvedValue(summary() as any)
    renderPanel()

    await screen.findByRole('button', { name: '布林突破 的選股紀錄' })
    for (const name of ['大盤平均報酬', '平均超額報酬', '勝過大盤比例']) {
      expect(screen.getByRole('columnheader', { name })).toHaveAttribute('title', '大盤＝0050 含息')
    }
    const row = within(strategyRow())
    expect(row.getByText('+1.20%')).toHaveClass('text-bull')
    expect(row.getByText('+1.90%')).toHaveClass('text-bull')
    expect(row.getByText('75.0%')).toBeInTheDocument()
    expect(screen.getAllByText(/大盤＝0050 含息/).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: '舊版（70 筆）' }))
    expect(within(strategyRow()).getByText('-3.00%')).toHaveClass('text-bear')
    expect(within(strategyRow()).getByText('比較 3 筆')).toBeInTheDocument()
  })

  it('展開紀錄依所選版本查詢，並顯示所選持有期的大盤與超額報酬', async () => {
    vi.mocked(api.screenerPickPerformance).mockResolvedValue(summary() as any)
    vi.mocked(api.screenerPickPerformanceDetail).mockResolvedValue(detail() as any)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: '布林突破 的選股紀錄' }))
    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(api.screenerPickPerformanceDetail).toHaveBeenLastCalledWith('boll_breakout', 200, 'current')
    expect(screen.getByRole('columnheader', { name: '大盤報酬 T+20' })).toBeInTheDocument()
    expect(screen.getByText('+1.50%')).toHaveClass('text-bull')
    expect(screen.getByText('+2.50%')).toHaveClass('text-bull')

    fireEvent.click(screen.getByRole('button', { name: 'T+5' }))
    expect(screen.getByRole('columnheader', { name: '超額報酬 T+5' })).toBeInTheDocument()
    expect(screen.getByText('-1.00%')).toHaveClass('text-bear')

    fireEvent.click(screen.getByRole('button', { name: '舊版（70 筆）' }))
    fireEvent.click(screen.getByRole('button', { name: '布林突破 的選股紀錄' }))
    await screen.findByText('台積電')
    expect(api.screenerPickPerformanceDetail).toHaveBeenLastCalledWith('boll_breakout', 200, 'legacy')
  })

  it('舊版後端沒有版本與大盤欄位時照常顯示，缺值以 — 呈現', async () => {
    const old = summary({ method: METHOD, strategies: [stats('boll_breakout', 12, horizon(0.031))] })
    delete (old as Record<string, unknown>).legacy_strategies
    delete (old as Record<string, unknown>).version_counts
    vi.mocked(api.screenerPickPerformance).mockResolvedValue(old as any)
    renderPanel()

    expect(await screen.findByRole('button', { name: '目前版本' })).toBeInTheDocument()
    expect(within(strategyRow()).getAllByText('—').length).toBe(3)

    fireEvent.click(screen.getByRole('button', { name: '舊版' }))
    expect(screen.getByText('沒有舊版策略的選股紀錄。')).toBeInTheDocument()
  })
})
