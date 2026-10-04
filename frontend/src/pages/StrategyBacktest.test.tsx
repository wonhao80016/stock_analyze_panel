import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

const STRATEGIES = [{
  id: 'momentum',
  name: '動能策略',
  description: 'Momentum strategy',
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
}, {
  id: 'value',
  name: '價值策略',
  description: 'Value strategy',
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
}]

const INSTRUMENTS = [
  { symbol: '2330.TWSE', code: '2330', name: '台積電', asset_type: 'stock', market: 'taiwan' as const },
  { symbol: '2317.TWSE', code: '2317', name: '鴻海', asset_type: 'stock', market: 'taiwan' as const },
  { symbol: '3545.TWSE', code: '3545', name: '敦泰', asset_type: 'stock', market: 'taiwan' as const },
  { symbol: '0050.TWSE', code: '0050', name: '元大台灣50', asset_type: 'etf', market: 'taiwan' as const },
]

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function renderPage(path = '/backtest') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <StrategyBacktest />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function mockStrategies() {
  vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: STRATEGIES, load_errors: [] } as any)
}

beforeEach(() => {
  vi.mocked(api.instrumentSearch).mockImplementation(async query => {
    const normalized = query.trim().toUpperCase()
    return {
      results: INSTRUMENTS.filter(result => (
        result.symbol.toUpperCase().includes(normalized)
        || result.code.toUpperCase().includes(normalized)
        || result.name.toUpperCase().includes(normalized)
      )),
    }
  })
})

function submit() {
  fireEvent.submit(screen.getByRole('button', { name: '執行回測' }).closest('form')!)
}

async function waitUntilReady() {
  await screen.findByRole('option', { name: '動能策略' })
  await waitFor(() => expect(screen.getByRole('button', { name: '執行回測' })).not.toBeDisabled())
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('策略回測工作區', () => {
  it('從 URL 預填策略、標的與資產類型', async () => {
    mockStrategies()
    renderPage('/backtest?strategy_id=momentum&symbol=0050.TWSE&asset_type=etf')

    expect(await screen.findByLabelText('策略')).toHaveValue('momentum')
    expect(screen.getByLabelText('標的')).toHaveValue('0050.TWSE')
    expect(screen.getByLabelText('資產類型')).toHaveValue('etf')
  })

  it('將裸代號解析為 Security Master canonical symbol 後才送出', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      trades: [],
      stats: {},
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=3545')
    await waitUntilReady()

    submit()

    await waitFor(() => expect(api.strategyBacktestRun).toHaveBeenCalledOnce())
    expect(api.instrumentSearch).toHaveBeenCalledWith('3545', 20, 'stock,etf')
    expect(vi.mocked(api.strategyBacktestRun).mock.calls[0][0].symbols).toEqual(['3545.TWSE'])
    expect(screen.getByRole('status')).toHaveTextContent('已解析：3545.TWSE')
  })

  it('找不到標的時顯示欄位錯誤且不送出回測', async () => {
    mockStrategies()
    renderPage('/backtest?strategy_id=momentum&symbol=9999')
    await waitUntilReady()

    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent('找不到標的「9999」')
    expect(api.strategyBacktestRun).not.toHaveBeenCalled()
  })

  it('多個 exact 結果時要求完整代號且不猜交易所', async () => {
    mockStrategies()
    vi.mocked(api.instrumentSearch).mockResolvedValueOnce({
      results: [
        { symbol: '1234.TWSE', code: '1234', name: '測試一', asset_type: 'stock', market: 'taiwan' },
        { symbol: '1234.TPEX', code: '1234', name: '測試二', asset_type: 'stock', market: 'taiwan' },
      ],
    })
    renderPage('/backtest?strategy_id=momentum&symbol=1234')
    await waitUntilReady()

    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent('1234.TWSE、1234.TPEX')
    expect(screen.getByRole('alert')).toHaveTextContent('請輸入完整代號')
    expect(api.strategyBacktestRun).not.toHaveBeenCalled()
  })

  it('標的資產類型不符時顯示欄位錯誤且不送出回測', async () => {
    mockStrategies()
    renderPage('/backtest?strategy_id=momentum&symbol=3545&asset_type=etf')
    await waitUntilReady()

    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent('標的「3545」是股票，與目前選擇的ETF不符。')
    expect(api.strategyBacktestRun).not.toHaveBeenCalled()
  })

  it('重新送出但標的解析失敗時清除前一次回測結果', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.125, n_trades: 0 },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()
    expect(await screen.findByText('+12.50%')).toBeInTheDocument()

    vi.mocked(api.instrumentSearch).mockRejectedValueOnce(new Error('Security Master unavailable'))
    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent('Security Master unavailable')
    expect(screen.queryByText('+12.50%')).not.toBeInTheDocument()
    expect(api.strategyBacktestRun).toHaveBeenCalledOnce()
  })

  it('送出表單時建立台股回測 payload 且不覆寫交易稅或原始佣金率', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      trades: [],
      stats: {},
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()

    fireEvent.change(screen.getByLabelText('標的'), { target: { value: '2330.TWSE, 2317.TWSE\n2330.TWSE' } })
    fireEvent.change(screen.getByLabelText('開始日期'), { target: { value: '2025-01-02' } })
    fireEvent.change(screen.getByLabelText('結束日期'), { target: { value: '2025-12-31' } })
    fireEvent.change(screen.getByLabelText('成交時點'), { target: { value: 'close_t' } })
    fireEvent.change(screen.getByLabelText('交易單位'), { target: { value: 'odd_lot' } })
    fireEvent.change(screen.getByLabelText('初始資金'), { target: { value: '2000000' } })
    fireEvent.change(screen.getByLabelText('最大持倉數'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('佣金折扣'), { target: { value: '0.6' } })
    fireEvent.change(screen.getByLabelText('最低手續費'), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText('最少持有交易日'), { target: { value: '2' } })
    submit()

    await waitFor(() => expect(api.strategyBacktestRun).toHaveBeenCalledOnce())
    const payload = vi.mocked(api.strategyBacktestRun).mock.calls[0][0]
    expect(payload).toEqual({
      strategy_id: 'momentum',
      symbols: ['2330.TWSE', '2317.TWSE'],
      start: '2025-01-02',
      end: '2025-12-31',
      matching: 'close_t',
      asset_type: 'stock',
      lot_mode: 'odd_lot',
      initial_capital: 2000000,
      max_positions: 5,
      commission_discount: 0.6,
      minimum_commission: 20,
      minimum_holding_sessions: 2,
    })
    expect(payload).not.toHaveProperty('commission_pct')
    expect(payload).not.toHaveProperty('stamp_tax_pct')
  })

  it('執行中停用送出並顯示進度', async () => {
    mockStrategies()
    const pending = deferred<any>()
    vi.mocked(api.strategyBacktestRun).mockReturnValue(pending.promise)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()

    submit()

    expect(await screen.findByRole('button', { name: '回測執行中' })).toBeDisabled()
    expect(screen.getByText('正在計算回測結果…')).toBeInTheDocument()
    pending.resolve({ error: null, trades: [], stats: {} })
  })

  it('拒絕不合法的數字設定且不送出請求', async () => {
    mockStrategies()
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()

    const cases = [
      ['初始資金', '0', '初始資金必須大於 0。', '1000000'],
      ['最大持倉數', '0', '最大持倉數必須是至少 1 的整數。', '10'],
      ['佣金折扣', '1.1', '佣金折扣必須介於 0 到 1。', '1'],
      ['最低手續費', '-1', '最低手續費不可小於 0。', '0'],
      ['最少持有交易日', '0', '最少持有交易日必須是至少 1 的整數。', '1'],
    ] as const

    for (const [label, invalid, message, valid] of cases) {
      fireEvent.change(screen.getByLabelText(label), { target: { value: invalid } })
      submit()
      expect(await screen.findByText(message)).toBeInTheDocument()
      expect(api.strategyBacktestRun).not.toHaveBeenCalled()
      fireEvent.change(screen.getByLabelText(label), { target: { value: valid } })
    }
  })

  it('顯示 HTTP 錯誤與結果內錯誤', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockRejectedValueOnce(new Error('Server unavailable'))
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()
    expect(await screen.findByText('Server unavailable')).toBeInTheDocument()

    vi.mocked(api.strategyBacktestRun).mockResolvedValueOnce({ error: 'No market data', trades: [], stats: {} } as any)
    submit()
    expect(await screen.findByText('No market data')).toBeInTheDocument()
  })

  it('成功但沒有交易時顯示明確空狀態', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0,
        annual_return: 0,
        max_drawdown: 0,
        sharpe: 0,
        win_rate: 0,
        n_trades: 0,
        selection: { strategy_matches: 98, entry_candidates: 98 },
        execution: { buy_lot_size: 96 },
      },
      trades: [],
      equity_curve: [{ date: '2025-01-02', value: 1000000 }],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('回測完成，但期間內沒有產生交易。')).toBeInTheDocument()
    expect(screen.getByText('期間內有 98 次買進候選，其中 96 次因每檔預算不足以買進一張而未成交。')).toBeInTheDocument()
    expect(screen.getByText('請降低最大持倉數、提高初始資金，或將交易單位改為零股。')).toBeInTheDocument()
    expect(screen.getAllByText('0 筆')).toHaveLength(2)
  })

  it('即時顯示每檔可用預算', async () => {
    mockStrategies()
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()

    expect(screen.getByText('每檔預算約 100,000 元')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('最大持倉數'), { target: { value: '2' } })
    expect(screen.getByText('每檔預算約 500,000 元')).toBeInTheDocument()
  })

  it('成功時顯示摘要、曲線與交易紀錄', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        annual_return: 0.1,
        max_drawdown: -0.08,
        sharpe: 1.42,
        win_rate: 0.6,
        n_trades: 1,
      },
      trades: [{
        symbol: '2330.TWSE',
        entry_date: '2025-01-02',
        exit_date: '2025-01-10',
        entry_price: 1000,
        exit_price: 1100,
        shares: 1000,
        lots: 1,
        pnl_amount: 100000,
        pnl_pct: 0.1,
        duration: 6,
        exit_reason: 'signal',
      }],
      equity_curve: [{ date: '2025-01-02', value: 1000000 }],
      drawdown_curve: [{ date: '2025-01-02', value: 0 }],
      elapsed_ms: 123.4,
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('+12.50%')).toBeInTheDocument()
    expect(screen.getAllByText('+10.00%')).toHaveLength(2)
    expect(screen.getByText('-8.00%')).toBeInTheDocument()
    expect(screen.getByText('1.42')).toBeInTheDocument()
    expect(screen.getByText('60.00%')).toBeInTheDocument()
    expect(screen.getAllByText('1 筆')).toHaveLength(2)
    expect(screen.getByTestId('equity-chart')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '2330.TWSE' })).toBeInTheDocument()
    expect(screen.getByText('signal')).toBeInTheDocument()
  })

  it('表單變更後隱藏舊結果並要求重新執行', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.125, n_trades: 0 },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()
    expect(await screen.findByText('+12.50%')).toBeInTheDocument()

    const cases = [
      ['策略', 'value', 'momentum'],
      ['標的', '2317.TWSE', '2330.TWSE'],
      ['開始日期', '2025-01-02', ''],
      ['成交時點', 'close_t', 'open_t+1'],
    ] as const

    for (const [label, changed, original] of cases) {
      fireEvent.change(screen.getByLabelText(label), { target: { value: changed } })
      expect(screen.getByText('設定已變更，請重新執行回測。')).toBeInTheDocument()
      expect(screen.queryByText('+12.50%')).not.toBeInTheDocument()
      fireEvent.change(screen.getByLabelText(label), { target: { value: original } })
      expect(screen.getByText('+12.50%')).toBeInTheDocument()
    }
  })
})
