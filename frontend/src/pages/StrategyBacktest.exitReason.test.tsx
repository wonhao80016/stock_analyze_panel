import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { StrategyBacktest } from './StrategyBacktest'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      instrumentSearch: vi.fn(),
      screenerStrategies: vi.fn(),
      strategyBacktestRun: vi.fn(),
    },
  }
})

vi.mock('@/components/backtest/BacktestEquityChart', () => ({
  BacktestEquityChart: () => <div data-testid="equity-chart" />,
}))

const STRATEGY = {
  id: 'momentum',
  name: '動能策略',
  description: '',
  tags: [],
  source: 'builtin',
  execution_backend: 'polars_expr',
  asset_types: ['stock', 'etf'],
  timeframes: ['1d'],
  version: '1',
  basic_filter: {},
  params: [],
  params_defaults: {},
  scoring: {},
  scoring_directions: {},
  entry_signals: [],
  exit_signals: [],
  minute_exit_trigger_supported_signals: [],
  stop_loss: null,
  take_profit: null,
  trailing_stop: null,
  trailing_take_profit_activate: null,
  trailing_take_profit_drawdown: null,
  max_hold_days: null,
  order_by: 'score',
  descending: true,
  limit: 10,
}

function trade(index: number, exitReason: string, exitSignalId: string | null = null) {
  return {
    symbol: '2330.TWSE',
    entry_date: `2025-01-0${index + 1}`,
    exit_date: `2025-02-0${index + 1}`,
    entry_price: 1000,
    exit_price: 1010,
    shares: 1000,
    exit_shares: 1000,
    lots: 1,
    pnl_amount: 10,
    pnl_pct: 0.01,
    duration: 5,
    exit_reason: exitReason,
    exit_signal_id: exitSignalId,
  }
}

function renderPage(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <StrategyBacktest />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('策略回測明細 — 出場原因 (B301)', () => {
  it('每一種出場原因都以中文標示, 訊號出場附上訊號名稱', async () => {
    vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: [STRATEGY], load_errors: [] } as any)
    vi.mocked(api.instrumentSearch).mockResolvedValue({
      results: [{ symbol: '2330.TWSE', code: '2330', name: '台積電', asset_type: 'stock', market: 'taiwan' }],
    } as any)
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.01, n_trades: 7 },
      trades: [
        trade(0, 'signal', 'signal_macd_dead'),
        trade(1, 'signal'),
        trade(2, 'stop_loss'),
        trade(3, 'take_profit'),
        trade(4, 'trailing_stop'),
        trade(5, 'trailing_take_profit'),
        trade(6, 'max_hold'),
        trade(7, 'end'),
      ],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await screen.findByRole('option', { name: '動能策略' }, { timeout: 5000 })
    await waitFor(() => expect(screen.getByRole('button', { name: '執行回測' })).not.toBeDisabled(), { timeout: 5000 })

    fireEvent.submit(screen.getByRole('button', { name: '執行回測' }).closest('form')!)

    expect(await screen.findByRole('cell', { name: '訊號出場：MACD死叉' }, { timeout: 5000 })).toBeInTheDocument()
    for (const label of ['訊號出場', '停損', '停利', '移動停損', '移動停利', '達最長持有天數', '回測結束平倉']) {
      expect(screen.getByRole('cell', { name: label })).toBeInTheDocument()
    }
    for (const raw of ['signal', 'stop_loss', 'take_profit', 'trailing_stop', 'trailing_take_profit', 'max_hold', 'end']) {
      expect(screen.queryByRole('cell', { name: raw })).not.toBeInTheDocument()
    }
  })
})
