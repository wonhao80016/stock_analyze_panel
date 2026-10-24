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
    fireEvent.change(screen.getByLabelText('無風險利率（年）'), { target: { value: '0.015' } })
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
      risk_free_rate: 0.015,
      slippage_bps: 5,
    })
    expect(payload).not.toHaveProperty('commission_pct')
    expect(payload).not.toHaveProperty('stamp_tax_pct')
  })

  it('未調整最低手續費時預設以 20 元送出', async () => {
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

    expect(screen.getByLabelText('最低手續費')).toHaveValue(20)
    submit()

    await waitFor(() => expect(api.strategyBacktestRun).toHaveBeenCalledOnce())
    expect(vi.mocked(api.strategyBacktestRun).mock.calls[0][0].minimum_commission).toBe(20)
  })

  it('切換交易單位時未自訂的最低手續費改用該模式預設', async () => {
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

    fireEvent.change(screen.getByLabelText('交易單位'), { target: { value: 'odd_lot' } })
    expect(screen.getByLabelText('最低手續費')).toHaveValue(1)
    fireEvent.change(screen.getByLabelText('交易單位'), { target: { value: 'board_lot' } })
    expect(screen.getByLabelText('最低手續費')).toHaveValue(20)
    fireEvent.change(screen.getByLabelText('交易單位'), { target: { value: 'odd_lot' } })
    submit()

    await waitFor(() => expect(api.strategyBacktestRun).toHaveBeenCalledOnce())
    const payload = vi.mocked(api.strategyBacktestRun).mock.calls[0][0]
    expect(payload.lot_mode).toBe('odd_lot')
    expect(payload.minimum_commission).toBe(1)
  })

  it('切換交易單位時保留使用者自訂的最低手續費', async () => {
    mockStrategies()
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()

    fireEvent.change(screen.getByLabelText('最低手續費'), { target: { value: '15' } })
    fireEvent.change(screen.getByLabelText('交易單位'), { target: { value: 'odd_lot' } })
    expect(screen.getByLabelText('最低手續費')).toHaveValue(15)
    fireEvent.change(screen.getByLabelText('交易單位'), { target: { value: 'board_lot' } })
    expect(screen.getByLabelText('最低手續費')).toHaveValue(15)
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
      ['無風險利率（年）', '0.25', '無風險利率必須介於 -0.05 到 0.2（-5% 到 20%）。', '0'],
      ['無風險利率（年）', '-0.06', '無風險利率必須介於 -0.05 到 0.2（-5% 到 20%）。', '0'],
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
        risk_free_rate: 0.015,
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
    expect(screen.getByRole('cell', { name: '訊號出場' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '張數' })).toBeInTheDocument()
    expect(screen.getByText(/年化報酬依回測期間的實際日曆天數以複利換算/)).toBeInTheDocument()
    expect(screen.getByText(/Sharpe 先從每期報酬扣除無風險利率/)).toBeInTheDocument()
    expect(screen.getByText(/每年報酬期數＝報酬期數 ÷（日曆天數 ÷ 365.25）/)).toBeInTheDocument()
    expect(screen.getByText(/本次使用 1.50%/)).toBeInTheDocument()
    expect(screen.getByText(/回測少於 60 個交易日時不計算年化報酬與 Sharpe/)).toBeInTheDocument()
    expect(screen.queryByText(/252 個交易日/)).not.toBeInTheDocument()
    expect(screen.getByText(/交易紀錄的價格與股數為當時實際成交價與股數/)).toBeInTheDocument()
    expect(screen.getByText(/損益與報酬率以前復權價計算，含持有期間的股利/)).toBeInTheDocument()
    expect(screen.queryByText(/交易紀錄的價格為前復權價/)).not.toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '1,000' })).toBeInTheDocument()
  })

  it('持有期間分割時列出出場股數', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.01, n_trades: 1 },
      trades: [{
        symbol: '0050.TWSE',
        entry_date: '2025-06-10',
        exit_date: '2025-06-19',
        entry_price: 188.65,
        exit_price: 47.1,
        entry_price_adj: 47.1625,
        exit_price_adj: 47.1,
        shares: 5000,
        exit_shares: 20000,
        lots: 5,
        pnl_amount: -4878.48,
        pnl_pct: -0.005165,
        duration: 2,
        exit_reason: 'signal',
      }],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByRole('cell', { name: '0050.TWSE' })).toBeInTheDocument()
    expect(screen.getByText(/@ 188\.65/)).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: /^5,000\s*→\s*20,000$/ })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: /^5\s*→\s*20$/ })).toBeInTheDocument()
  })

  it('期末未平倉的候選以中文標示並另列數量', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.01,
        n_trades: 0,
        execution: { unclosed_at_end: 1 },
        unclosed_at_end: { count: 1, avg_return: -0.1, pnl_amount: -100, valuation: 'last_close' },
      },
      trades: [{
        symbol: '2330.TWSE',
        entry_date: '2025-01-02',
        exit_date: '2025-01-10',
        entry_price: 1000,
        exit_price: 900,
        shares: 1000,
        exit_shares: 1000,
        lots: 1,
        pnl_amount: -100,
        pnl_pct: -0.1,
        duration: 5,
        exit_reason: 'end_unclosed',
      }],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByRole('cell', { name: '期末未平倉（以收盤估值）' })).toBeInTheDocument()
    expect(screen.getByText(/1 筆候選到資料結尾仍未平倉/)).toBeInTheDocument()
  })

  it('出場股數未變時股數與張數欄不顯示箭頭', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.01, n_trades: 1 },
      trades: [{
        symbol: '2330.TWSE',
        entry_date: '2025-01-02',
        exit_date: '2025-01-10',
        entry_price: 1000,
        exit_price: 1010,
        shares: 3000,
        exit_shares: 3000,
        lots: 3,
        pnl_amount: 21000,
        pnl_pct: 0.007,
        duration: 6,
        exit_reason: 'signal',
      }],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByRole('cell', { name: '2330.TWSE' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '3,000' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '3' })).toBeInTheDocument()
    expect(screen.getAllByRole('cell').filter((cell) => cell.textContent?.includes('→'))).toHaveLength(0)
  })

  it('零股模式的交易紀錄不顯示張數欄', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.01, n_trades: 1 },
      trades: [{
        symbol: '2330.TWSE',
        entry_date: '2025-01-02',
        exit_date: '2025-01-10',
        entry_price: 1000,
        exit_price: 1010,
        shares: 98,
        lots: 98,
        pnl_amount: 980,
        pnl_pct: 0.01,
        duration: 6,
        exit_reason: 'signal',
      }],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    fireEvent.change(screen.getByLabelText('交易單位'), { target: { value: 'odd_lot' } })
    submit()

    expect(await screen.findByRole('cell', { name: '2330.TWSE' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '股數' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '張數' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('cell', { name: '98' })).toHaveLength(1)
  })

  it('表單說明揭露固定滑價', async () => {
    mockStrategies()
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()

    expect(screen.getByText(/買進與賣出各另計 0\.05% 滑價/)).toBeInTheDocument()
  })

  it('期間不足時年化報酬與 Sharpe 顯示為不可計算', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.03,
        annual_return: null,
        max_drawdown: -0.01,
        sharpe: null,
        win_rate: 1,
        n_trades: 1,
        risk_free_rate: 0,
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('+3.00%')).toBeInTheDocument()
    expect(screen.getByText('年化報酬').nextElementSibling).toHaveTextContent('—')
    expect(screen.getByText('Sharpe').nextElementSibling).toHaveTextContent('—')
    expect(screen.getByText(/本次使用 0.00%/)).toBeInTheDocument()
  })

  it('顯示加權指數報酬與超額報酬並標示不含息', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        n_trades: 0,
        benchmark_return: 0.05,
        excess: 0.075,
        benchmark: {
          symbol: 't00.TWSE',
          name: '發行量加權股價指數',
          price_basis: 'price_index',
          includes_dividends: false,
          base_date: '2025-01-02',
          coverage_start: '2025-01-02',
          coverage_end: '2025-06-30',
          missing_sessions: 0,
        },
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('基準指數報酬')).toBeInTheDocument()
    expect(screen.getByText('基準指數報酬').nextElementSibling).toHaveTextContent('+5.00%')
    expect(screen.getByText('基準指數報酬').nextElementSibling).toHaveClass('text-danger')
    expect(screen.getByText('超額報酬').nextElementSibling).toHaveTextContent('+7.50%')
    expect(screen.getByText(/發行量加權股價指數為價格指數，不含息，以 2025-01-02 收盤為起點；超額報酬＝總報酬－發行量加權股價指數報酬。/)).toBeInTheDocument()
    expect(screen.getByText(/策略報酬含股利而基準不含，相較含息基準，超額報酬每年約高估大盤的股利殖利率/)).toBeInTheDocument()
    expect(screen.queryByText(/無法計算/)).not.toBeInTheDocument()
  })

  it('基準與超額報酬為負時以下跌色顯示', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: -0.07,
        n_trades: 0,
        benchmark_return: -0.05,
        excess: -0.02,
        benchmark: {
          symbol: 't00.TWSE',
          name: '發行量加權股價指數',
          price_basis: 'price_index',
          includes_dividends: false,
          base_date: '2025-01-02',
          coverage_start: '2025-01-02',
          coverage_end: '2025-06-30',
          missing_sessions: 0,
        },
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    const benchmarkValue = (await screen.findByText('基準指數報酬')).nextElementSibling
    expect(benchmarkValue).toHaveTextContent('-5.00%')
    expect(benchmarkValue).toHaveClass('text-bear')
    const excessValue = screen.getByText('超額報酬').nextElementSibling
    expect(excessValue).toHaveTextContent('-2.00%')
    expect(excessValue).toHaveClass('text-bear')
  })

  it('沒有基期日與指數名稱時省略起點並退回加權指數', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        n_trades: 0,
        benchmark_return: 0.05,
        excess: 0.075,
        benchmark: {
          symbol: 't00.TWSE',
          price_basis: 'price_index',
          includes_dividends: false,
          base_date: null,
          coverage_start: '2025-01-02',
          coverage_end: '2025-06-30',
          missing_sessions: 0,
        },
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect((await screen.findByText('基準指數報酬')).nextElementSibling).toHaveTextContent('+5.00%')
    expect(screen.getByText(/加權指數為價格指數，不含息；超額報酬＝總報酬－加權指數報酬。/)).toBeInTheDocument()
    expect(screen.queryByText(/收盤為起點/)).not.toBeInTheDocument()
  })

  it('指數資料覆蓋不足時基準與超額報酬顯示為不可計算並列出實際涵蓋期間', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        n_trades: 0,
        benchmark_return: null,
        excess: null,
        benchmark: {
          symbol: 't00.TWSE',
          name: '發行量加權股價指數',
          price_basis: 'price_index',
          includes_dividends: false,
          base_date: '2025-01-02',
          coverage_start: '2025-03-03',
          coverage_end: '2025-06-30',
          missing_sessions: 40,
        },
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('基準指數報酬')).toBeInTheDocument()
    expect(screen.getByText('基準指數報酬').nextElementSibling).toHaveTextContent('—')
    expect(screen.getByText('超額報酬').nextElementSibling).toHaveTextContent('—')
    expect(screen.getByText(/價格指數，不含息/)).toBeInTheDocument()
    expect(screen.getByText(/發行量加權股價指數缺少回測首日或末日的收盤（期間內找到的資料為 2025-03-03 至 2025-06-30），無法計算發行量加權股價指數報酬與超額報酬/)).toBeInTheDocument()
    expect(screen.queryByText(/未涵蓋整段/)).not.toBeInTheDocument()
  })

  it('回測期間沒有指數資料時說明無法計算', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        n_trades: 0,
        benchmark_return: null,
        excess: null,
        benchmark: {
          symbol: 't00.TWSE',
          name: '發行量加權股價指數',
          price_basis: 'price_index',
          includes_dividends: false,
          base_date: '2025-01-02',
          coverage_start: null,
          coverage_end: null,
          missing_sessions: 120,
        },
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText(/回測期間內沒有發行量加權股價指數資料，無法計算發行量加權股價指數報酬與超額報酬/)).toBeInTheDocument()
  })

  it('說明配股與減資退還股款對交易紀錄股數的影響', async () => {
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

    expect(await screen.findByText(/配股增加的股數不在交易紀錄中追蹤；減資退還股款時，出場股數為近似值/)).toBeInTheDocument()
    expect(screen.queryByText(/指數報酬/)).not.toBeInTheDocument()
  })

  it('列出未納入回測的標的與中文原因', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        n_trades: 0,
        excluded_symbols: [
          { symbol: '0000.TWSE', reason: 'security_master_missing' },
          { symbol: '00998A.TWSE', reason: 'price_limit_unknown' },
        ],
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('以下標的未納入回測：0000.TWSE（證券主檔查無）、00998A.TWSE（漲跌幅規則未確認）')).toBeInTheDocument()
    expect(screen.queryByText(/security_master_missing|price_limit_unknown/)).not.toBeInTheDocument()
  })

  it('上市日不明的排除標的顯示中文原因', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        n_trades: 0,
        excluded_symbols: [{ symbol: '7799.TWSE', reason: 'listing_date_unknown' }],
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('以下標的未納入回測：7799.TWSE（上市日不明）')).toBeInTheDocument()
    expect(screen.queryByText(/listing_date_unknown|原因不明/)).not.toBeInTheDocument()
  })

  it('沒有排除標的時不顯示排除提示', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.125, n_trades: 0, excluded_symbols: [] },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('+12.50%')).toBeInTheDocument()
    expect(screen.queryByText(/未納入回測/)).not.toBeInTheDocument()
  })

  it('欄位資料覆蓋率不足 100% 時顯示警示', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        n_trades: 0,
        field_coverage: { amount: 1, turnover_rate: 0.7299 },
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('以下欄位資料不完整（資料覆蓋率）：換手率 72%。缺少評分資料的候選排在最後，缺少過濾資料的列不會進場。')).toBeInTheDocument()
    expect(screen.queryByText(/amount|成交額/)).not.toBeInTheDocument()
  })

  it('欄位資料完整時不顯示覆蓋率警示', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.125, n_trades: 0, field_coverage: { turnover_rate: 1 } },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('+12.50%')).toBeInTheDocument()
    expect(screen.queryByText(/資料覆蓋率/)).not.toBeInTheDocument()
  })

  it('單一交易所覆蓋率不足的交易日顯示交易所與日期', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: {
        total_return: 0.125,
        n_trades: 0,
        exchange_coverage_warnings: [
          { field: 'turnover_rate', exchange: 'TPEX', threshold: 0.5, sessions: ['2026-10-08'], session_count: 1 },
          {
            field: 'turnover_rate',
            exchange: 'TWSE',
            threshold: 0.5,
            sessions: ['2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07'],
            session_count: 8,
          },
        ],
      },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('上櫃股票的換手率資料覆蓋率低於 50%：2026-10-08。這些交易日的上櫃股票缺少該欄位，多半不會進場。')).toBeInTheDocument()
    expect(screen.getByText('上市股票的換手率資料覆蓋率低於 50%：2026-10-01、2026-10-02、2026-10-05、2026-10-06、2026-10-07 等 8 個交易日。這些交易日的上市股票缺少該欄位，多半不會進場。')).toBeInTheDocument()
  })

  it('沒有交易所覆蓋率警示時不顯示', async () => {
    mockStrategies()
    vi.mocked(api.strategyBacktestRun).mockResolvedValue({
      error: null,
      stats: { total_return: 0.125, n_trades: 0, exchange_coverage_warnings: [] },
      trades: [],
      equity_curve: [],
      drawdown_curve: [],
    } as any)
    renderPage('/backtest?strategy_id=momentum&symbol=2330.TWSE')
    await waitUntilReady()
    submit()

    expect(await screen.findByText('+12.50%')).toBeInTheDocument()
    expect(screen.queryByText(/資料覆蓋率低於/)).not.toBeInTheDocument()
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
      ['無風險利率（年）', '0.015', '0'],
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
