import { useMemo, useState } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import type {
  TaiwanQuarterlyFigures,
  TaiwanQuarterlyFinancialsResponse,
  TaiwanQuarterlyPeriod,
} from '@/lib/api'
import { cn } from '@/lib/cn'
import { TaiwanQuarterlyMarginChart } from './TaiwanQuarterlyMarginChart'

type Mode = 'single' | 'cumulative'

type Props = {
  symbol: string
  response?: TaiwanQuarterlyFinancialsResponse
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  onRetry: () => void
}

type MetricKind = 'amount' | 'eps' | 'ratio'
type Metric = { key: keyof TaiwanQuarterlyFigures; label: string; kind: MetricKind; singleOnly?: boolean }

const METRICS: Metric[] = [
  { key: 'revenue', label: '營業收入', kind: 'amount' },
  { key: 'gross_profit', label: '營業毛利', kind: 'amount' },
  { key: 'operating_income', label: '營業利益', kind: 'amount' },
  { key: 'net_income', label: '稅後淨利', kind: 'amount' },
  { key: 'parent_net_income', label: '歸屬母公司淨利', kind: 'amount' },
  { key: 'eps', label: 'EPS', kind: 'eps' },
  { key: 'gross_margin_pct', label: '毛利率', kind: 'ratio' },
  { key: 'operating_margin_pct', label: '營業利益率', kind: 'ratio' },
  { key: 'net_margin_pct', label: '稅後淨利率', kind: 'ratio' },
  { key: 'roe_pct', label: 'ROE（單季，未年化）', kind: 'ratio', singleOnly: true },
]

const SOURCE_LABELS: Record<string, string> = {
  twse_openapi: '證交所 OpenAPI',
  tpex_openapi: '櫃買中心 OpenAPI',
}

const NOT_APPLICABLE: Record<string, string> = {
  etf: 'ETF 沒有公司財報，季度財報不適用。',
  tdr: 'TDR 的官方財報是海外原股公司的數字，季度財報不適用。',
}

// The table keeps the latest two years; older quarters stay in the archive.
const SHOWN_QUARTERS = 8

const quarterLabel = (period: { fiscal_year: number; fiscal_quarter: number }) =>
  `${period.fiscal_year} Q${period.fiscal_quarter}`

function formatNumber(value: number, digits = 2) {
  return value.toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function formatValue(value: number, kind: MetricKind) {
  if (kind === 'ratio') return `${formatNumber(value)}%`
  if (kind === 'eps') return `${formatNumber(value)} 元`
  const absolute = Math.abs(value)
  if (absolute >= 100_000_000) return `${formatNumber(value / 100_000_000)} 億`
  if (absolute >= 10_000) return `${formatNumber(value / 10_000, 0)} 萬`
  return `${formatNumber(value, 0)} 元`
}

function signed(value: number, digits = 2) {
  return `${value > 0 ? '+' : ''}${formatNumber(value, digits)}`
}

function missingReason(period: TaiwanQuarterlyPeriod, metric: Metric, mode: Mode) {
  if (metric.singleOnly && mode === 'cumulative') return 'ROE 只算單季'
  if (mode === 'single' && period.single_missing_reason === 'previous_quarter_missing') {
    return '缺上一季累計值，無法推算單季'
  }
  if (metric.key === 'roe_pct' && period.roe_missing_reason === 'opening_equity_missing') {
    return '缺期初母公司權益（上一季未存檔），無法計算'
  }
  if (metric.key === 'roe_pct' && period.roe_missing_reason === 'closing_equity_missing') {
    return '官方資料沒有期末母公司權益，無法計算'
  }
  return '官方資料沒有這個欄位或分母不可用'
}

function compareWithYearAgo(period: TaiwanQuarterlyPeriod, metric: Metric, mode: Mode) {
  const current = period[mode][metric.key]
  const yearAgo = period.year_ago
  const previous = yearAgo?.[mode][metric.key]
  if (current == null || yearAgo == null || previous == null) return null
  const difference = current - previous
  const tone = difference > 0 ? 'up' : difference < 0 ? 'down' : 'flat'
  const base = `較去年同季（${quarterLabel(yearAgo)}）`
  if (metric.kind === 'ratio') return { tone, title: `${base}${signed(difference)} 個百分點` }
  if (previous === 0) return { tone, title: `${base}${difference === 0 ? '持平' : '由 0 變動'}` }
  return { tone, title: `${base}${signed(difference / Math.abs(previous) * 100)}%` }
}

function ValueCell({ period, metric, mode }: { period: TaiwanQuarterlyPeriod; metric: Metric; mode: Mode }) {
  const value = metric.singleOnly && mode === 'cumulative' ? null : period[mode][metric.key]
  if (value == null) {
    return (
      <span className="text-muted" title={missingReason(period, metric, mode)} data-testid="quarterly-missing">
        --
      </span>
    )
  }
  const comparison = compareWithYearAgo(period, metric, mode)
  return (
    <span
      title={comparison?.title}
      data-tone={comparison?.tone ?? 'none'}
      className={cn(
        'font-mono',
        comparison?.tone === 'up' && 'text-bull',
        comparison?.tone === 'down' && 'text-bear',
        (comparison == null || comparison.tone === 'flat') && 'text-foreground',
      )}
    >
      {formatValue(value, metric.kind)}
    </span>
  )
}

function retrievedText(value: string | null) {
  return value ? value.slice(0, 16).replace('T', ' ') : '--'
}

function Header({ mode, onMode, response }: {
  mode: Mode
  onMode: (mode: Mode) => void
  response?: TaiwanQuarterlyFinancialsResponse
}) {
  const showToggle = response?.applicable && response.quarters.length > 0
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id="taiwan-quarterly-heading" className="text-sm font-bold text-foreground">季度財報</h3>
          <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">歷史參考・僅供顯示</span>
          {response?.applicable && response.accumulating && (
            <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600">
              累積中（已存 {response.quarters.length}／{response.target_quarters} 季）
            </span>
          )}
        </div>
        <p className="mt-1 max-w-3xl text-[11px] leading-5 text-muted">
          回溯取得，可能為更正後數值。從開始存檔起每季保存官方最新一季，不用於回測或選股。
        </p>
      </div>
      {showToggle && (
        <div role="group" aria-label="單季或累計" className="flex rounded-lg border border-border bg-base p-0.5 text-xs">
          {(['single', 'cumulative'] as const).map(value => (
            <button
              key={value}
              type="button"
              aria-pressed={mode === value}
              onClick={() => onMode(value)}
              className={cn(
                'rounded-md px-2.5 py-1 transition-colors',
                mode === value ? 'bg-accent/15 font-semibold text-accent' : 'text-muted hover:text-foreground',
              )}
            >
              {value === 'single' ? '單季' : '累計'}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function QuarterTable({ periods, mode }: { periods: TaiwanQuarterlyPeriod[]; mode: Mode }) {
  return (
    <div className="hidden overflow-x-auto md:block">
      <table className="w-full text-xs" data-testid="quarterly-table">
        <thead>
          <tr className="border-b border-border text-muted">
            <th className="py-1.5 pr-3 text-left font-medium">項目</th>
            {periods.map(period => (
              <th key={quarterLabel(period)} className="px-2 py-1.5 text-right font-medium">{quarterLabel(period)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {METRICS.map(metric => (
            <tr key={metric.key} className="border-b border-border/50 last:border-0">
              <th scope="row" className="whitespace-nowrap py-1.5 pr-3 text-left font-normal text-muted">{metric.label}</th>
              {periods.map(period => (
                <td key={quarterLabel(period)} className="whitespace-nowrap px-2 py-1.5 text-right">
                  <ValueCell period={period} metric={metric} mode={mode} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function QuarterCards({ periods, mode }: { periods: TaiwanQuarterlyPeriod[]; mode: Mode }) {
  return (
    <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:hidden" data-testid="quarterly-cards">
      {periods.map(period => (
        <li key={quarterLabel(period)} className="min-w-0 rounded-xl border border-border/80 bg-base p-3">
          <div className="mb-1.5 flex items-baseline justify-between gap-2">
            <span className="text-xs font-semibold text-foreground">{quarterLabel(period)}</span>
            <span className="text-[10px] text-muted">期末 {period.period_end}</span>
          </div>
          <dl className="space-y-1 text-xs">
            {METRICS.map(metric => (
              <div key={metric.key} className="flex items-center justify-between gap-2">
                <dt className="min-w-0 truncate text-muted">{metric.label}</dt>
                <dd className="shrink-0 text-right"><ValueCell period={period} metric={metric} mode={mode} /></dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ul>
  )
}

function AnnualEps({ response }: { response: TaiwanQuarterlyFinancialsResponse }) {
  return (
    <div className="text-xs">
      <div className="mb-1 font-medium text-foreground">歷年全年 EPS（第 4 季累計）</div>
      {response.annual_eps.length === 0 ? (
        <p className="text-muted">尚未存到任何年度的第 4 季，累積中。</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {response.annual_eps.map(item => (
            <li key={item.fiscal_year} className="rounded-lg border border-border/80 bg-base px-2 py-1">
              <span className="text-muted">{item.fiscal_year}</span>{' '}
              <span className="font-mono text-foreground">{item.eps == null ? '--' : formatValue(item.eps, 'eps')}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SourceLine({ period }: { period: TaiwanQuarterlyPeriod }) {
  const source = period.source ? SOURCE_LABELS[period.source] ?? period.source : '--'
  return (
    <p className="text-[11px] leading-5 text-muted">
      {quarterLabel(period)} 來源：{source}；首次取得 {retrievedText(period.first_retrieved_at)}，最後確認 {retrievedText(period.last_retrieved_at)}
      {period.revised_at && `；官方值於 ${retrievedText(period.revised_at)} 有更動，已改用新值`}
    </p>
  )
}

function Body({ response, mode }: { response: TaiwanQuarterlyFinancialsResponse; mode: Mode }) {
  const shown = useMemo(() => response.quarters.slice(-SHOWN_QUARTERS), [response.quarters])
  const newestFirst = useMemo(() => [...shown].reverse(), [shown])
  const points = useMemo(
    () => shown.map(period => ({ label: quarterLabel(period), figures: period[mode] })),
    [shown, mode],
  )
  if (!response.applicable) {
    return <p className="text-xs text-muted">{NOT_APPLICABLE[response.not_applicable_reason ?? ''] ?? '季度財報不適用。'}</p>
  }
  if (shown.length === 0) {
    return (
      <p className="text-xs text-muted" data-testid="quarterly-accumulating">
        累積中：尚未存到任何一季。盤後排程會在官方公布新一季後自動保存。
      </p>
    )
  }
  return (
    <div className="space-y-3">
      <TaiwanQuarterlyMarginChart points={points} />
      <p className="text-[11px] text-muted">
        {mode === 'single'
          ? '單季值為本季累計減上一季累計（第 1 季即累計）；三率以單季金額除以單季營收。'
          : '累計值為年初至該季的官方累計數字。'}
        顏色依去年同季比較，滑過數值可看差距。
      </p>
      <QuarterTable periods={newestFirst} mode={mode} />
      <QuarterCards periods={newestFirst} mode={mode} />
      <AnnualEps response={response} />
      <SourceLine period={newestFirst[0]} />
    </div>
  )
}

export function TaiwanQuarterlyFinancials({ symbol, response, isLoading, isError, isFetching, onRetry }: Props) {
  const [mode, setMode] = useState<Mode>('single')
  const current = response?.symbol === symbol ? response : undefined
  return (
    <section aria-labelledby="taiwan-quarterly-heading" className="space-y-3 rounded-2xl border border-border bg-surface p-4">
      <Header mode={mode} onMode={setMode} response={isError ? undefined : current} />
      {isError ? (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-muted">
          <div className="flex items-center gap-2 font-medium text-rose-500">
            <AlertTriangle className="h-4 w-4" />
            季度財報暫時讀取失敗
          </div>
          <button
            type="button"
            onClick={onRetry}
            disabled={isFetching}
            className="mt-2 flex items-center gap-1.5 rounded-lg border border-border bg-base px-2.5 py-1 text-xs text-muted hover:text-foreground disabled:opacity-60"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isFetching && 'animate-spin')} />
            重試
          </button>
        </div>
      ) : isLoading || current == null ? (
        <div className="flex min-h-20 items-center justify-center text-xs text-muted" data-testid="quarterly-loading">
          <RefreshCw className="mr-2 h-4 w-4 animate-spin text-accent" />
          讀取季度財報中...
        </div>
      ) : (
        <Body response={current} mode={mode} />
      )}
    </section>
  )
}
