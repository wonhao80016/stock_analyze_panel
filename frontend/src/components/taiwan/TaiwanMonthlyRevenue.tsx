import { useMemo } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import type { TaiwanMonthlyRevenuePeriod, TaiwanMonthlyRevenueResponse } from '@/lib/api'
import { cn } from '@/lib/cn'
import { TaiwanMonthlyRevenueChart } from './TaiwanMonthlyRevenueChart'

type Props = {
  symbol: string
  response?: TaiwanMonthlyRevenueResponse
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  onRetry: () => void
}

type Column = {
  key: keyof TaiwanMonthlyRevenuePeriod
  label: string
  kind: 'amount' | 'growth'
}

const COLUMNS: Column[] = [
  { key: 'revenue', label: '營收', kind: 'amount' },
  { key: 'mom_pct', label: '月增', kind: 'growth' },
  { key: 'yoy_pct', label: '年增', kind: 'growth' },
  { key: 'cumulative_revenue', label: '累計營收', kind: 'amount' },
  { key: 'cumulative_yoy_pct', label: '累計年增', kind: 'growth' },
]

const SOURCE_LABELS: Record<string, string> = {
  twse_openapi: '證交所 OpenAPI',
  tpex_openapi: '櫃買中心 OpenAPI',
}

const NOT_APPLICABLE: Record<string, string> = {
  etf: 'ETF 沒有公司月營收，月營收走勢不適用。',
  tdr: 'TDR 的官方營收是海外原股公司的數字，月營收走勢不適用。',
}

// The chart and table keep the latest year; older months stay in the archive.
const SHOWN_MONTHS = 12

function formatNumber(value: number, digits = 2) {
  return value.toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function formatAmount(value: number) {
  const absolute = Math.abs(value)
  if (absolute >= 100_000_000) return `${formatNumber(value / 100_000_000)} 億`
  if (absolute >= 10_000) return `${formatNumber(value / 10_000, 0)} 萬`
  return `${formatNumber(value, 0)} 元`
}

function formatGrowth(value: number) {
  return `${value > 0 ? '+' : ''}${formatNumber(value)}%`
}

function retrievedText(value: string | null) {
  return value ? value.slice(0, 16).replace('T', ' ') : '--'
}

function Missing() {
  return (
    <span className="text-muted" title="官方資料沒有這個欄位" data-testid="monthly-revenue-missing">
      --
    </span>
  )
}

function AmountText({ value }: { value: number | null }) {
  if (value == null) return <Missing />
  return <span className="font-mono text-foreground">{formatAmount(value)}</span>
}

function GrowthText({ value }: { value: number | null }) {
  if (value == null) return <Missing />
  const tone = value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
  return (
    <span
      data-tone={tone}
      className={cn(
        'font-mono',
        tone === 'up' && 'text-bull',
        tone === 'down' && 'text-bear',
        tone === 'flat' && 'text-foreground',
      )}
    >
      {formatGrowth(value)}
    </span>
  )
}

function ValueCell({ month, column }: { month: TaiwanMonthlyRevenuePeriod; column: Column }) {
  const value = month[column.key] as number | null
  return column.kind === 'amount' ? <AmountText value={value} /> : <GrowthText value={value} />
}

function Header({ response }: { response?: TaiwanMonthlyRevenueResponse }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="taiwan-monthly-revenue-heading" className="text-sm font-bold text-foreground">月營收走勢</h3>
        <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">歷史參考・僅供顯示</span>
        {response?.applicable && response.accumulating && (
          <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600">
            累積中（已存 {response.months.length}／{response.target_months} 月）
          </span>
        )}
      </div>
      <p className="mt-1 max-w-3xl text-[11px] leading-5 text-muted">
        回溯取得，可能為更正後數值。從開始存檔起每月保存官方最新一期，不用於回測或選股。
      </p>
    </div>
  )
}

function MonthTable({ months }: { months: TaiwanMonthlyRevenuePeriod[] }) {
  return (
    <div className="hidden overflow-x-auto md:block">
      <table className="w-full text-xs" data-testid="monthly-revenue-table">
        <thead>
          <tr className="border-b border-border text-muted">
            <th className="py-1.5 pr-3 text-left font-medium">月份</th>
            {COLUMNS.map(column => (
              <th key={column.key} className="px-2 py-1.5 text-right font-medium">{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {months.map(month => (
            <tr key={month.period} className="border-b border-border/50 last:border-0">
              <th scope="row" className="whitespace-nowrap py-1.5 pr-3 text-left font-normal text-muted">{month.period}</th>
              {COLUMNS.map(column => (
                <td key={column.key} className="whitespace-nowrap px-2 py-1.5 text-right">
                  <ValueCell month={month} column={column} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MonthCards({ months }: { months: TaiwanMonthlyRevenuePeriod[] }) {
  return (
    <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:hidden" data-testid="monthly-revenue-cards">
      {months.map(month => (
        <li key={month.period} className="min-w-0 rounded-xl border border-border/80 bg-base p-3">
          <div className="mb-1.5 text-xs font-semibold text-foreground">{month.period}</div>
          <dl className="space-y-1 text-xs">
            {COLUMNS.map(column => (
              <div key={column.key} className="flex items-center justify-between gap-2">
                <dt className="min-w-0 truncate text-muted">{column.label}</dt>
                <dd className="shrink-0 text-right"><ValueCell month={month} column={column} /></dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ul>
  )
}

function LatestComparison({ comparison }: { comparison: NonNullable<TaiwanMonthlyRevenueResponse['latest_comparison']> }) {
  return (
    <div className="rounded-xl border border-border/80 bg-base p-3 text-xs" data-testid="monthly-revenue-comparison">
      <div className="mb-1 font-medium text-foreground">對照（取自 {comparison.period} 報表）</div>
      <dl className="grid grid-cols-1 gap-1 sm:grid-cols-2">
        <div className="flex items-center justify-between gap-2">
          <dt className="min-w-0 truncate text-muted">上月（{comparison.previous_month}）</dt>
          <dd className="shrink-0"><AmountText value={comparison.previous_month_revenue} /></dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="min-w-0 truncate text-muted">去年同月（{comparison.previous_year_month}）</dt>
          <dd className="shrink-0"><AmountText value={comparison.previous_year_revenue} /></dd>
        </div>
      </dl>
      <p className="mt-1 text-[11px] text-muted">這兩個值是最新一期報表內官方附帶的比較數字，不是另外存檔的月份。</p>
    </div>
  )
}

function SourceLine({ month }: { month: TaiwanMonthlyRevenuePeriod }) {
  const source = month.source ? SOURCE_LABELS[month.source] ?? month.source : '--'
  return (
    <p className="text-[11px] leading-5 text-muted">
      {month.period} 來源：{source}；出表日期 {month.report_date ?? '--'}；首次取得 {retrievedText(month.first_retrieved_at)}，最後確認 {retrievedText(month.last_retrieved_at)}
      {month.revised_at && `；官方值於 ${retrievedText(month.revised_at)} 有更動，已改用新值`}
    </p>
  )
}

function Body({ response }: { response: TaiwanMonthlyRevenueResponse }) {
  const shown = useMemo(() => response.months.slice(0, SHOWN_MONTHS), [response.months])
  const points = useMemo(
    () => [...shown].reverse().map(month => ({ label: month.period, revenue: month.revenue, yoyPct: month.yoy_pct })),
    [shown],
  )
  if (!response.applicable) {
    return <p className="text-xs text-muted">{NOT_APPLICABLE[response.not_applicable_reason ?? ''] ?? '月營收走勢不適用。'}</p>
  }
  if (shown.length === 0) {
    return (
      <p className="text-xs text-muted" data-testid="monthly-revenue-accumulating">
        累積中：尚未存到任何月份。盤後排程會在官方公布新月份後自動保存。
      </p>
    )
  }
  return (
    <div className="space-y-3">
      <TaiwanMonthlyRevenueChart points={points} />
      {shown.length === 1 && response.latest_comparison && <LatestComparison comparison={response.latest_comparison} />}
      <p className="text-[11px] text-muted">月增、年增與累計年增為官方公布的百分比，紅色為成長、綠色為衰退。</p>
      <MonthTable months={shown} />
      <MonthCards months={shown} />
      <SourceLine month={shown[0]} />
    </div>
  )
}

export function TaiwanMonthlyRevenue({ symbol, response, isLoading, isError, isFetching, onRetry }: Props) {
  const current = response?.symbol === symbol ? response : undefined
  return (
    <section aria-labelledby="taiwan-monthly-revenue-heading" className="space-y-3 rounded-2xl border border-border bg-surface p-4">
      <Header response={isError ? undefined : current} />
      {isError ? (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-muted">
          <div className="flex items-center gap-2 font-medium text-rose-500">
            <AlertTriangle className="h-4 w-4" />
            月營收暫時讀取失敗
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
        <div className="flex min-h-20 items-center justify-center text-xs text-muted" data-testid="monthly-revenue-loading">
          <RefreshCw className="mr-2 h-4 w-4 animate-spin text-accent" />
          讀取月營收中...
        </div>
      ) : (
        <Body response={current} />
      )}
    </section>
  )
}
