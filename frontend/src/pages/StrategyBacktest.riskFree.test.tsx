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

async function renderReadyForm() {
  vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: [STRATEGY], load_errors: [] } as any)
  vi.mocked(api.instrumentSearch).mockResolvedValue({
    results: [{ symbol: '2330.TWSE', code: '2330', name: '台積電', asset_type: 'stock', market: 'taiwan' }],
  } as any)
  renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
  await screen.findByRole('option', { name: '動能策略' }, { timeout: 5000 })
  await waitFor(() => expect(screen.getByRole('button', { name: '執行回測' })).not.toBeDisabled(), { timeout: 5000 })
  return {
    form: screen.getByRole('button', { name: '執行回測' }).closest('form')!,
    input: screen.getByLabelText('無風險利率（年）') as HTMLInputElement,
  }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('策略回測 — 無風險利率欄位', () => {
  it('預設值與萬分位利率都通過瀏覽器的欄位檢查', async () => {
    const { form, input } = await renderReadyForm()

    expect(input).toBeValid()
    expect(form.checkValidity()).toBe(true)

    for (const value of ['0.0175', '0.015', '0.0001', '-0.05', '0.2']) {
      fireEvent.change(input, { target: { value } })
      expect(input.value).toBe(value)
      expect(input.checkValidity()).toBe(true)
      expect(form.checkValidity()).toBe(true)
    }
  })

  it('超出後端接受範圍 [-0.05, 0.2] 的利率不能通過', async () => {
    const { form, input } = await renderReadyForm()

    for (const value of ['-0.0501', '0.2001', '-0.1', '0.25']) {
      fireEvent.change(input, { target: { value } })
      expect(input.checkValidity()).toBe(false)
      expect(form.checkValidity()).toBe(false)
    }
  })
})
