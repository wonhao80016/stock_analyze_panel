import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Activity, AlertCircle, Loader2, Play, TrendingUp } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { BacktestEquityChart } from '@/components/backtest/BacktestEquityChart'
import { api, type StrategyBacktestBenchmark, type StrategyBacktestExchangeCoverageWarning, type StrategyBacktestResult, type StrategyBacktestRunPayload } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cnSignal } from '@/lib/signals'
import { cn } from '@/lib/cn'

type AssetType = 'stock' | 'etf'
type Matching = 'close_t' | 'open_t+1'
type LotMode = 'board_lot' | 'odd_lot'
type InstrumentSearchResult = Awaited<ReturnType<typeof api.instrumentSearch>>['results'][number]

const SLIPPAGE_BPS = 5
// Mirrors the backend default_minimum_commission broker floors.
const DEFAULT_MINIMUM_COMMISSION: Record<LotMode, number> = { board_lot: 20, odd_lot: 1 }
// Mirrors the backend bounds; the API rejects values outside them with 400.
const RISK_FREE_RATE_MIN = -0.05
const RISK_FREE_RATE_MAX = 0.2
const inputClass = 'h-9 w-full rounded-md border border-border bg-base px-3 text-xs text-foreground outline-none transition-colors focus:border-accent/60 disabled:cursor-not-allowed disabled:opacity-60'

function parseAssetType(value: string | null): AssetType {
  return value === 'etf' ? 'etf' : 'stock'
}

function parseSymbols(value: string): string[] {
  return [...new Set(value.split(/[\s,，]+/).map(symbol => symbol.trim().toUpperCase()).filter(Boolean))]
}

function exactInstrumentMatches(input: string, results: InstrumentSearchResult[]): InstrumentSearchResult[] {
  const query = input.trim().toUpperCase()
  const matches = results.filter(result => (
    result.symbol.toUpperCase() === query
    || result.code.toUpperCase() === query
    || result.name.trim().toUpperCase() === query
  ))
  return [...new Map(matches.map(result => [result.symbol, result])).values()]
}

// Keys mirror the exit reasons recorded by backend/app/backtest/engine.py.
const EXIT_REASON_LABELS: Record<string, string> = {
  signal: '訊號出場',
  stop_loss: '停損',
  take_profit: '停利',
  trailing_stop: '移動停損',
  trailing_take_profit: '移動停利',
  max_hold: '達最長持有天數',
  end: '回測結束平倉',
  end_unclosed: '期末未平倉（以收盤估值）',
}

function exitReasonLabel(reason: string | undefined, signalId?: string | null): string {
  if (!reason) return '—'
  const label = EXIT_REASON_LABELS[reason] ?? cnSignal(reason)
  return reason === 'signal' && signalId ? `${label}：${cnSignal(signalId)}` : label
}

function assetTypeLabel(assetType: string | undefined): string {
  if (assetType === 'stock') return '股票'
  if (assetType === 'etf') return 'ETF'
  return '未知資產類型'
}

async function resolveSymbols(inputs: string[], assetType: AssetType): Promise<string[]> {
  const resolved: string[] = []
  for (const input of inputs) {
    const response = await api.instrumentSearch(input, 20, 'stock,etf')
    const exactMatches = exactInstrumentMatches(input, response.results)
    if (exactMatches.length === 0) {
      throw new Error(`找不到標的「${input}」，請輸入台股代號、完整代號或名稱。`)
    }
    if (exactMatches.length > 1) {
      const candidates = exactMatches.map(result => result.symbol).join('、')
      throw new Error(`標的「${input}」有多個精確符合：${candidates}。請輸入完整代號。`)
    }
    const match = exactMatches[0]
    if (match.asset_type !== assetType) {
      throw new Error(`標的「${input}」是${assetTypeLabel(match.asset_type)}，與目前選擇的${assetTypeLabel(assetType)}不符。`)
    }
    resolved.push(match.symbol)
  }
  return [...new Set(resolved)]
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatPercent(value: unknown, signed = true): string {
  const number = finiteNumber(value)
  if (number == null) return '—'
  return `${signed && number > 0 ? '+' : ''}${(number * 100).toFixed(2)}%`
}

function formatNumber(value: unknown, digits = 2): string {
  const number = finiteNumber(value)
  return number == null ? '—' : number.toLocaleString('zh-TW', { maximumFractionDigits: digits })
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '回測執行失敗，請稍後再試。'
}

function returnTone(value: number | null): 'positive' | 'negative' | undefined {
  if (value == null) return undefined
  return value >= 0 ? 'positive' : 'negative'
}

function benchmarkName(benchmark: StrategyBacktestBenchmark): string {
  return benchmark.name?.trim() || '加權指數'
}

function benchmarkNote(benchmark: StrategyBacktestBenchmark, benchmarkReturn: number | null): string {
  const name = benchmarkName(benchmark)
  const basis = `${name}為價格指數，不含息`
  if (benchmarkReturn != null) {
    const base = benchmark.base_date ? `，以 ${benchmark.base_date} 收盤為起點` : ''
    return `${basis}${base}；超額報酬＝總報酬－${name}報酬。策略報酬含股利而基準不含，相較含息基準，超額報酬每年約高估大盤的股利殖利率。`
  }
  if (benchmark.coverage_start && benchmark.coverage_end) {
    return `${basis}。${name}缺少回測首日或末日的收盤（期間內找到的資料為 ${benchmark.coverage_start} 至 ${benchmark.coverage_end}），無法計算${name}報酬與超額報酬。`
  }
  return `${basis}。回測期間內沒有${name}資料，無法計算${name}報酬與超額報酬。`
}

const EXCLUSION_REASON_LABELS: Record<string, string> = {
  security_master_missing: '證券主檔查無',
  price_limit_unknown: '漲跌幅規則未確認',
  listing_date_unknown: '上市日不明',
}

function exclusionNote(excluded: { symbol: string; reason: string }[]): string {
  const items = excluded.map(item => `${item.symbol}（${EXCLUSION_REASON_LABELS[item.reason] ?? '原因不明'}）`)
  return `以下標的未納入回測：${items.join('、')}`
}

/** Rounded down as the backend's coverage refusals print it, so a share below a threshold never shows as it. */
function wholePercent(share: number): string {
  return `${Math.floor(Math.round(share * 100 * 1e6) / 1e6)}%`
}

function coverageNote(coverage: unknown): string | null {
  if (!coverage || typeof coverage !== 'object') return null
  const partial = Object.entries(coverage as Record<string, unknown>)
    .map(([field, value]) => [field, finiteNumber(value)] as const)
    .filter((entry): entry is readonly [string, number] => entry[1] != null && entry[1] < 1)
  if (partial.length === 0) return null
  const items = partial.map(([field, share]) => `${cnSignal(field)} ${wholePercent(share)}`)
  return `以下欄位資料不完整（資料覆蓋率）：${items.join('、')}。缺少評分資料的候選排在最後，缺少過濾資料的列不會進場。`
}

const EXCHANGE_LABELS: Record<string, string> = { TWSE: '上市', TPEX: '上櫃' }

function exchangeCoverageNotes(warnings: StrategyBacktestExchangeCoverageWarning[] | undefined): string[] {
  if (!Array.isArray(warnings)) return []
  return warnings.flatMap((warning) => {
    if (!warning || typeof warning !== 'object') return []
    const { field, exchange, threshold, sessions, session_count: sessionCount } = warning
    if (typeof field !== 'string' || typeof exchange !== 'string' || !Array.isArray(sessions) || sessions.length === 0) return []
    const label = EXCHANGE_LABELS[exchange] ?? exchange
    const count = finiteNumber(sessionCount) ?? sessions.length
    const more = count > sessions.length ? ` 等 ${count} 個交易日` : ''
    const minimum = finiteNumber(threshold)
    return [`${label}股票的${cnSignal(field)}資料覆蓋率低於 ${minimum == null ? '—' : wholePercent(minimum)}：${sessions.join('、')}${more}。這些交易日的${label}股票缺少該欄位，多半不會進場。`]
  })
}

function MetricCard({ label, value, tone }: { label: string; value: string; tone?: 'positive' | 'negative' }) {
  return (
    <div className="rounded-md border border-border/70 bg-base/50 px-3 py-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className={cn(
        'mt-1 font-mono text-base font-semibold tabular-nums text-foreground',
        tone === 'positive' && 'text-danger',
        tone === 'negative' && 'text-bear',
      )}>
        {value}
      </div>
    </div>
  )
}

function Results({ result, lotMode }: { result: StrategyBacktestResult; lotMode: LotMode }) {
  if (result.error) {
    return <ErrorPanel message={result.error} />
  }

  const tradeCount = finiteNumber(result.stats.n_trades) ?? result.trades.length
  const totalReturn = finiteNumber(result.stats.total_return)
  const annualReturn = finiteNumber(result.stats.annual_return)
  const maxDrawdown = finiteNumber(result.stats.max_drawdown)
  const winRate = finiteNumber(result.stats.win_rate)
  const riskFreeRate = finiteNumber(result.stats.risk_free_rate)
  const entryCandidates = finiteNumber(result.stats.selection?.entry_candidates)
  const lotSizeRejections = finiteNumber(result.stats.execution?.buy_lot_size)
  const unclosedAtEnd = finiteNumber(result.stats.execution?.unclosed_at_end)
  const unclosedPnl = finiteNumber(result.stats.unclosed_at_end?.pnl_amount)
  const benchmark = result.stats.benchmark
  const benchmarkReturn = finiteNumber(result.stats.benchmark_return)
  const excessReturn = finiteNumber(result.stats.excess)
  const excludedSymbols = result.stats.excluded_symbols ?? []
  const fieldCoverageNote = coverageNote(result.stats.field_coverage)
  const exchangeNotes = exchangeCoverageNotes(result.stats.exchange_coverage_warnings)

  return (
    <div className="space-y-3">
      <section className="rounded-card border border-border bg-surface p-3">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-semibold text-foreground">回測摘要</h2>
          </div>
          <span className="font-mono text-[11px] text-muted">耗時 {formatNumber(result.elapsed_ms, 1)} ms</span>
        </div>
        {excludedSymbols.length > 0 && (
          <div role="status" className="mb-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] leading-relaxed text-warning">
            {exclusionNote(excludedSymbols)}
          </div>
        )}
        {fieldCoverageNote && (
          <div role="status" className="mb-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] leading-relaxed text-warning">
            {fieldCoverageNote}
          </div>
        )}
        {unclosedAtEnd != null && unclosedAtEnd > 0 && (
          <div role="status" className="mb-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] leading-relaxed text-warning">
            另有 {formatNumber(unclosedAtEnd, 0)} 筆候選到資料結尾仍未平倉，以最後收盤價估值列於交易紀錄{unclosedPnl != null && `（估值損益 ${formatNumber(unclosedPnl)}）`}，不計入交易數、勝率與平均報酬。
          </div>
        )}
        {exchangeNotes.map(note => (
          <div key={note} role="status" className="mb-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] leading-relaxed text-warning">
            {note}
          </div>
        ))}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
          <MetricCard label="總報酬" value={formatPercent(totalReturn)} tone={totalReturn == null ? undefined : totalReturn >= 0 ? 'positive' : 'negative'} />
          <MetricCard label="年化報酬" value={formatPercent(annualReturn)} tone={annualReturn == null ? undefined : annualReturn >= 0 ? 'positive' : 'negative'} />
          <MetricCard label="最大回撤" value={formatPercent(maxDrawdown)} tone="negative" />
          <MetricCard label="Sharpe" value={formatNumber(result.stats.sharpe)} />
          <MetricCard label="勝率" value={formatPercent(winRate, false)} />
          <MetricCard label="交易數" value={`${formatNumber(tradeCount, 0)} 筆`} />
          {benchmark && (
            <>
              <MetricCard label="基準指數報酬" value={formatPercent(benchmarkReturn)} tone={returnTone(benchmarkReturn)} />
              <MetricCard label="超額報酬" value={formatPercent(excessReturn)} tone={returnTone(excessReturn)} />
            </>
          )}
        </div>
        {benchmark && (
          <p className="mt-2 text-[10px] leading-relaxed text-muted">{benchmarkNote(benchmark, benchmarkReturn)}</p>
        )}
        <p className="mt-2 text-[10px] leading-relaxed text-muted">
          年化報酬依回測期間的實際日曆天數以複利換算；Sharpe 先從每期報酬扣除無風險利率，再乘以每年報酬期數的平方根。每年報酬期數＝報酬期數 ÷（日曆天數 ÷ 365.25），無風險年利率依 (1＋年利率)^(1÷每年報酬期數)－1 換算為每期利率{riskFreeRate != null && `，本次使用 ${formatPercent(riskFreeRate, false)}`}。回測少於 60 個交易日時不計算年化報酬與 Sharpe。交易紀錄的價格與股數為當時實際成交價與股數，持有期間遇分割或減資時，以「→」列出出場股數；損益與報酬率以前復權價計算，含持有期間的股利，因此可能與（出場價－進場價）×股數不同。配股增加的股數不在交易紀錄中追蹤；減資退還股款時，出場股數為近似值。
        </p>
      </section>

      {result.equity_curve.length > 0 && (
        <section className="rounded-card border border-border bg-surface p-3">
          <h2 className="mb-2 text-sm font-semibold text-foreground">資金與回撤曲線</h2>
          <BacktestEquityChart
            equityCurve={result.equity_curve}
            drawdownCurve={result.drawdown_curve}
          />
        </section>
      )}

      <section className="rounded-card border border-border bg-surface p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-foreground">交易紀錄</h2>
          <span className="font-mono text-[11px] text-muted">{result.trades.length} 筆</span>
        </div>
        {result.trades.length === 0 ? (
          <div className="rounded-md border border-dashed border-border px-4 py-6 text-center text-xs text-muted">
            <p>回測完成，但期間內沒有產生交易。</p>
            {entryCandidates != null && lotSizeRejections != null && lotSizeRejections > 0 && (
              <div className="mx-auto mt-3 max-w-xl rounded-md border border-warning/30 bg-warning/10 px-3 py-2 leading-relaxed text-warning">
                <p>期間內有 {formatNumber(entryCandidates, 0)} 次買進候選，其中 {formatNumber(lotSizeRejections, 0)} 次因每檔預算不足以買進一張而未成交。</p>
                <p className="mt-1">請降低最大持倉數、提高初始資金，或將交易單位改為零股。</p>
              </div>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[900px] w-full text-left text-xs">
              <thead className="border-b border-border text-[11px] text-muted">
                <tr>
                  <th className="px-2 py-2 font-medium">標的</th>
                  <th className="px-2 py-2 font-medium">進場</th>
                  <th className="px-2 py-2 font-medium">出場</th>
                  <th className="px-2 py-2 text-right font-medium">股數</th>
                  {lotMode === 'board_lot' && <th className="px-2 py-2 text-right font-medium">張數</th>}
                  <th className="px-2 py-2 text-right font-medium">損益</th>
                  <th className="px-2 py-2 text-right font-medium">報酬率</th>
                  <th className="px-2 py-2 font-medium">出場原因</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {result.trades.map((trade, index) => {
                  const pnl = finiteNumber(trade.pnl_amount)
                  const returnValue = finiteNumber(trade.pnl_pct)
                  const exitShares = finiteNumber(trade.exit_shares)
                  const lots = finiteNumber(trade.lots)
                  const sharesChanged = exitShares != null && exitShares !== trade.shares
                  const entryShares = finiteNumber(trade.shares)
                  const exitLots = sharesChanged && lots != null && entryShares ? exitShares * lots / entryShares : null
                  return (
                    <tr key={`${trade.symbol}-${trade.entry_date}-${trade.exit_date}-${index}`} className="text-secondary">
                      <td className="whitespace-nowrap px-2 py-2 font-mono text-foreground">{trade.symbol}</td>
                      <td className="whitespace-nowrap px-2 py-2 font-mono">{trade.entry_date}<span className="ml-1 text-muted">@ {formatNumber(trade.entry_price)}</span></td>
                      <td className="whitespace-nowrap px-2 py-2 font-mono">{trade.exit_date}<span className="ml-1 text-muted">@ {formatNumber(trade.exit_price)}</span></td>
                      <td className="whitespace-nowrap px-2 py-2 text-right font-mono">
                        {formatNumber(trade.shares, 0)}
                        {sharesChanged && <span className="ml-1 text-muted">→ {formatNumber(exitShares, 0)}</span>}
                      </td>
                      {lotMode === 'board_lot' && (
                        <td className="whitespace-nowrap px-2 py-2 text-right font-mono">
                          {formatNumber(lots)}
                          {exitLots != null && <span className="ml-1 text-muted">→ {formatNumber(exitLots)}</span>}
                        </td>
                      )}
                      <td className={cn('px-2 py-2 text-right font-mono', pnl != null && (pnl >= 0 ? 'text-danger' : 'text-bear'))}>{formatNumber(pnl)}</td>
                      <td className={cn('px-2 py-2 text-right font-mono', returnValue != null && (returnValue >= 0 ? 'text-danger' : 'text-bear'))}>{formatPercent(returnValue)}</td>
                      <td className="whitespace-nowrap px-2 py-2">{exitReasonLabel(trade.exit_reason, trade.exit_signal_id)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function ErrorPanel({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-card border border-danger/30 bg-danger/10 p-3 text-xs text-danger">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  )
}

export function StrategyBacktest() {
  const [searchParams] = useSearchParams()
  const [assetType, setAssetType] = useState<AssetType>(() => parseAssetType(searchParams.get('asset_type')))
  const [strategyId, setStrategyId] = useState(() => searchParams.get('strategy_id') ?? '')
  const [symbolsText, setSymbolsText] = useState(() => searchParams.get('symbol') ?? '')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [matching, setMatching] = useState<Matching>('open_t+1')
  const [lotMode, setLotMode] = useState<LotMode>('board_lot')
  const [initialCapital, setInitialCapital] = useState(1_000_000)
  const [maxPositions, setMaxPositions] = useState(10)
  const [commissionDiscount, setCommissionDiscount] = useState(1)
  const [minimumCommission, setMinimumCommission] = useState(DEFAULT_MINIMUM_COMMISSION.board_lot)
  const [minimumHoldingSessions, setMinimumHoldingSessions] = useState(1)
  const [riskFreeRate, setRiskFreeRate] = useState(0)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [symbolError, setSymbolError] = useState<string | null>(null)
  const [resolvedSymbols, setResolvedSymbols] = useState<string[]>([])
  const [isResolvingSymbols, setIsResolvingSymbols] = useState(false)
  const [submittedFingerprint, setSubmittedFingerprint] = useState<string | null>(null)

  const strategies = useQuery({
    queryKey: QK.screenerStrategies(assetType),
    queryFn: () => api.screenerStrategies(assetType),
    staleTime: 300_000,
  })
  const presets = useMemo(() => strategies.data?.presets ?? [], [strategies.data])
  const currentFingerprint = useMemo(() => JSON.stringify({
    strategyId,
    symbols: parseSymbols(symbolsText),
    start: start || null,
    end: end || null,
    matching,
    assetType,
    lotMode,
    initialCapital,
    maxPositions,
    commissionDiscount,
    minimumCommission,
    minimumHoldingSessions,
    riskFreeRate,
  }), [
    assetType,
    commissionDiscount,
    end,
    initialCapital,
    lotMode,
    matching,
    maxPositions,
    minimumCommission,
    minimumHoldingSessions,
    riskFreeRate,
    start,
    strategyId,
    symbolsText,
  ])

  useEffect(() => {
    if (!strategyId && presets.length > 0) setStrategyId(presets[0].id)
  }, [presets, strategyId])

  const run = useMutation({ mutationFn: (payload: StrategyBacktestRunPayload) => api.strategyBacktestRun(payload) })
  const isBusy = isResolvingSymbols || run.isPending
  const positionBudget = Number.isFinite(initialCapital) && Number.isInteger(maxPositions) && maxPositions > 0
    ? initialCapital / maxPositions
    : null

  function handleLotMode(next: LotMode) {
    if (minimumCommission === DEFAULT_MINIMUM_COMMISSION[lotMode]) {
      setMinimumCommission(DEFAULT_MINIMUM_COMMISSION[next])
    }
    setLotMode(next)
  }

  function handleAssetType(next: AssetType) {
    setAssetType(next)
    setStrategyId('')
    setSymbolError(null)
    setResolvedSymbols([])
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const symbolInputs = parseSymbols(symbolsText)
    if (!strategyId) {
      setValidationError('請選擇策略。')
      return
    }
    if (symbolInputs.length === 0) {
      setSymbolError('請至少輸入一個回測標的。')
      return
    }
    if (start && end && start > end) {
      setValidationError('開始日期不可晚於結束日期。')
      return
    }
    if (!Number.isFinite(initialCapital) || initialCapital <= 0) {
      setValidationError('初始資金必須大於 0。')
      return
    }
    if (!Number.isInteger(maxPositions) || maxPositions < 1) {
      setValidationError('最大持倉數必須是至少 1 的整數。')
      return
    }
    if (!Number.isFinite(commissionDiscount) || commissionDiscount < 0 || commissionDiscount > 1) {
      setValidationError('佣金折扣必須介於 0 到 1。')
      return
    }
    if (!Number.isFinite(minimumCommission) || minimumCommission < 0) {
      setValidationError('最低手續費不可小於 0。')
      return
    }
    if (!Number.isInteger(minimumHoldingSessions) || minimumHoldingSessions < 1) {
      setValidationError('最少持有交易日必須是至少 1 的整數。')
      return
    }
    if (!Number.isFinite(riskFreeRate) || riskFreeRate < RISK_FREE_RATE_MIN || riskFreeRate > RISK_FREE_RATE_MAX) {
      setValidationError('無風險利率必須介於 -0.05 到 0.2（-5% 到 20%）。')
      return
    }

    run.reset()
    setValidationError(null)
    setSymbolError(null)
    setResolvedSymbols([])
    setIsResolvingSymbols(true)
    let symbols: string[]
    try {
      symbols = await resolveSymbols(symbolInputs, assetType)
    } catch (error) {
      setSymbolError(errorMessage(error))
      return
    } finally {
      setIsResolvingSymbols(false)
    }
    setResolvedSymbols(symbols)
    setSubmittedFingerprint(currentFingerprint)
    run.mutate({
      strategy_id: strategyId,
      symbols,
      start: start || null,
      end: end || null,
      matching,
      asset_type: assetType,
      lot_mode: lotMode,
      initial_capital: initialCapital,
      max_positions: maxPositions,
      commission_discount: commissionDiscount,
      minimum_commission: minimumCommission,
      minimum_holding_sessions: minimumHoldingSessions,
      risk_free_rate: riskFreeRate,
      slippage_bps: SLIPPAGE_BPS,
    })
  }

  return (
    <div className="mx-auto w-full max-w-7xl px-3 py-3">
      <div className="mb-3 flex items-center gap-2">
        <Activity className="h-4 w-4 text-accent" />
        <div>
          <h1 className="text-sm font-semibold text-foreground">策略回測</h1>
          <p className="text-[11px] text-muted">以台股交易規則驗證策略的歷史表現</p>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-[320px_minmax(0,1fr)]">
        <form onSubmit={handleSubmit} className="self-start rounded-card border border-border bg-surface p-3 lg:sticky lg:top-3">
          <h2 className="mb-3 text-sm font-semibold text-foreground">回測設定</h2>
          <div className="space-y-3">
            <label className="block text-[11px] text-secondary">
              資產類型
              <select aria-label="資產類型" value={assetType} onChange={event => handleAssetType(event.target.value as AssetType)} disabled={isBusy} className={`${inputClass} mt-1`}>
                <option value="stock">股票</option>
                <option value="etf">ETF</option>
              </select>
            </label>

            <label className="block text-[11px] text-secondary">
              策略
              <select aria-label="策略" value={strategyId} onChange={event => setStrategyId(event.target.value)} disabled={strategies.isLoading || isBusy} className={`${inputClass} mt-1`}>
                <option value="">{strategies.isLoading ? '正在讀取策略…' : '請選擇策略'}</option>
                {strategyId && !presets.some(strategy => strategy.id === strategyId) && <option value={strategyId}>{strategyId}</option>}
                {presets.map(strategy => <option key={strategy.id} value={strategy.id}>{strategy.name || strategy.id}</option>)}
              </select>
            </label>
            {strategies.isError && <div className="text-[11px] text-danger">策略清單讀取失敗，請稍後重試。</div>}

            <label className="block text-[11px] text-secondary">
              標的
              <textarea
                aria-label="標的"
                value={symbolsText}
                onChange={event => {
                  setSymbolsText(event.target.value)
                  setSymbolError(null)
                  setResolvedSymbols([])
                }}
                disabled={isBusy}
                rows={3}
                placeholder="2330.TWSE, 2317.TWSE"
                className="mt-1 w-full resize-y rounded-md border border-border bg-base px-3 py-2 font-mono text-xs text-foreground outline-none transition-colors focus:border-accent/60 disabled:cursor-not-allowed disabled:opacity-60"
              />
              <span className="mt-1 block text-[10px] text-muted">可輸入代號、完整代號或名稱，並用逗號、空白或換行分隔。</span>
              {symbolError && <span role="alert" className="mt-1 block text-[10px] leading-relaxed text-danger">{symbolError}</span>}
              {!symbolError && resolvedSymbols.length > 0 && (
                <span role="status" className="mt-1 block text-[10px] leading-relaxed text-accent">已解析：{resolvedSymbols.join('、')}</span>
              )}
            </label>

            <div className="grid grid-cols-2 gap-2">
              <label className="block text-[11px] text-secondary">開始日期<input aria-label="開始日期" type="date" value={start} onChange={event => setStart(event.target.value)} disabled={isBusy} className={`${inputClass} mt-1`} /></label>
              <label className="block text-[11px] text-secondary">結束日期<input aria-label="結束日期" type="date" value={end} onChange={event => setEnd(event.target.value)} disabled={isBusy} className={`${inputClass} mt-1`} /></label>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="block text-[11px] text-secondary">
                成交時點
                <select aria-label="成交時點" value={matching} onChange={event => setMatching(event.target.value as Matching)} disabled={isBusy} className={`${inputClass} mt-1`}>
                  <option value="open_t+1">次日開盤</option>
                  <option value="close_t">當日收盤</option>
                </select>
              </label>
              <label className="block text-[11px] text-secondary">
                交易單位
                <select aria-label="交易單位" value={lotMode} onChange={event => handleLotMode(event.target.value as LotMode)} disabled={isBusy} className={`${inputClass} mt-1`}>
                  <option value="board_lot">整股</option>
                  <option value="odd_lot">零股</option>
                </select>
              </label>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="block text-[11px] text-secondary">初始資金<input aria-label="初始資金" type="number" min="1" step="1" value={initialCapital} onChange={event => setInitialCapital(Number(event.target.value))} disabled={isBusy} className={`${inputClass} mt-1 font-mono`} /></label>
              <label className="block text-[11px] text-secondary">最大持倉數<input aria-label="最大持倉數" type="number" min="1" step="1" value={maxPositions} onChange={event => setMaxPositions(Number(event.target.value))} disabled={isBusy} className={`${inputClass} mt-1 font-mono`} /></label>
              <label className="block text-[11px] text-secondary">佣金折扣<input aria-label="佣金折扣" type="number" min="0" max="1" step="0.01" value={commissionDiscount} onChange={event => setCommissionDiscount(Number(event.target.value))} disabled={isBusy} className={`${inputClass} mt-1 font-mono`} /></label>
              <label className="block text-[11px] text-secondary">最低手續費<input aria-label="最低手續費" type="number" min="0" step="1" value={minimumCommission} onChange={event => setMinimumCommission(Number(event.target.value))} disabled={isBusy} className={`${inputClass} mt-1 font-mono`} /></label>
            </div>
            {positionBudget != null && (
              <div className="rounded-md border border-border/70 bg-base/40 px-3 py-2 font-mono text-[10px] text-secondary">
                每檔預算約 {formatNumber(positionBudget, 0)} 元
              </div>
            )}

            <label className="block text-[11px] text-secondary">最少持有交易日<input aria-label="最少持有交易日" type="number" min="1" step="1" value={minimumHoldingSessions} onChange={event => setMinimumHoldingSessions(Number(event.target.value))} disabled={isBusy} className={`${inputClass} mt-1 font-mono`} /></label>

            <label className="block text-[11px] text-secondary">
              無風險利率（年）
              <input aria-label="無風險利率（年）" type="number" min={RISK_FREE_RATE_MIN} max={RISK_FREE_RATE_MAX} step="0.005" value={riskFreeRate} onChange={event => setRiskFreeRate(Number(event.target.value))} disabled={isBusy} className={`${inputClass} mt-1 font-mono`} />
              <span className="mt-1 block text-[10px] text-muted">以小數輸入，例如 0.015 代表 1.5%；用於 Sharpe 與 Sortino。</span>
            </label>

            <div className="rounded-md border border-border/70 bg-base/40 px-3 py-2 text-[10px] leading-relaxed text-muted">
              佣金基準、交易稅與每檔交易單位由台股市場規則接管；此處只設定券商折扣與最低手續費；最低手續費預設整股 20 元、零股 1 元，未自訂時會隨交易單位切換。買進與賣出各另計 {SLIPPAGE_BPS / 100}% 滑價。
            </div>

            {validationError && <ErrorPanel message={validationError} />}

            <button type="submit" disabled={isBusy || strategies.isLoading} aria-label={isResolvingSymbols ? '標的解析中' : run.isPending ? '回測執行中' : '執行回測'} className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-md bg-accent px-4 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
              {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {isResolvingSymbols ? '標的解析中' : run.isPending ? '回測執行中' : '執行回測'}
            </button>
          </div>
        </form>

        <main className="min-w-0">
          {run.isPending && (
            <div className="flex min-h-56 items-center justify-center gap-2 rounded-card border border-border bg-surface text-xs text-muted">
              <Loader2 className="h-4 w-4 animate-spin text-accent" />正在計算回測結果…
            </div>
          )}
          {run.isError && <ErrorPanel message={errorMessage(run.error)} />}
          {run.isSuccess && run.data && submittedFingerprint === currentFingerprint && <Results result={run.data} lotMode={lotMode} />}
          {run.isSuccess && run.data && submittedFingerprint !== currentFingerprint && (
            <div role="status" className="flex min-h-56 items-center justify-center rounded-card border border-warning/30 bg-warning/10 px-4 text-center text-xs text-warning">
              設定已變更，請重新執行回測。
            </div>
          )}
          {run.isIdle && (
            <div className="flex min-h-56 flex-col items-center justify-center rounded-card border border-dashed border-border bg-surface/50 px-6 text-center">
              <TrendingUp className="mb-3 h-7 w-7 text-muted" />
              <div className="text-sm font-medium text-secondary">尚未執行回測</div>
              <p className="mt-1 max-w-md text-[11px] leading-relaxed text-muted">選擇策略、輸入標的並設定交易條件後，即可查看績效摘要、資金曲線與交易明細。</p>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
