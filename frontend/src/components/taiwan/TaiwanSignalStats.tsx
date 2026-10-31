import { AlertTriangle, RefreshCw } from 'lucide-react'
import type { TaiwanSignalHorizonStats, TaiwanSignalStat, TaiwanSignalStatsResponse } from '@/lib/api'
import { cn } from '@/lib/cn'
import { fmtPct, priceColorClass } from '@/lib/format'
import { VOLUME_PATTERN_DEFINITIONS, cnSignal } from '@/lib/signals'

export type SignalStatsQuery = {
  response?: TaiwanSignalStatsResponse
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  onRetry: () => void
}

const VOLUME_PATTERN_NAMES: Record<string, string> = Object.fromEntries(
  VOLUME_PATTERN_DEFINITIONS.map(pattern => [pattern.id, pattern.name]),
)

const SUMMARY_HORIZON = 60

export function signalName(signal: { id: string; name: string | null }) {
  return signal.name ?? VOLUME_PATTERN_NAMES[signal.id] ?? cnSignal(signal.id)
}

function fmtRatio(value: number | null) {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function horizonOf(stat: TaiwanSignalStat, horizon: number) {
  return stat.horizons.find(item => item.horizon === horizon)
}

export function SignalStatSummary({ stat }: { stat?: TaiwanSignalStat }) {
  const stats = stat?.evaluated ? horizonOf(stat, SUMMARY_HORIZON) : undefined
  if (stats == null) return null
  return (
    <span className="text-[11px] text-muted" data-testid="signal-stat-summary">
      {stats.sufficient
        ? `${SUMMARY_HORIZON}日 上漲比例 ${fmtRatio(stats.up_ratio)}・樣本 ${stats.completed_count}`
        : `${SUMMARY_HORIZON}日 樣本不足（${stats.completed_count}）`}
    </span>
  )
}

function ReturnValue({ value }: { value: number | null }) {
  return <span className={priceColorClass(value)}>{fmtPct(value, 1)}</span>
}

function HorizonRow({ stat, stats, first }: { stat: TaiwanSignalStat; stats: TaiwanSignalHorizonStats; first: boolean }) {
  return (
    <tr className="border-t border-border/60">
      {first && (
        <td rowSpan={stat.horizons.length} className="whitespace-nowrap py-1.5 pr-3 align-top font-medium text-foreground">
          {signalName(stat)}
        </td>
      )}
      <td className="whitespace-nowrap py-1.5 pr-3 text-muted">{stats.horizon} 日</td>
      <td className="py-1.5 pr-3 tabular-nums">{stats.event_count}</td>
      <td className="py-1.5 pr-3 tabular-nums">{stats.completed_count}</td>
      {stats.sufficient ? (
        <>
          <td className="py-1.5 pr-3 tabular-nums"><ReturnValue value={stats.mean_return} /></td>
          <td className="py-1.5 pr-3 tabular-nums"><ReturnValue value={stats.median_return} /></td>
          <td className="py-1.5 pr-3 tabular-nums">{fmtRatio(stats.up_ratio)}</td>
        </>
      ) : (
        <td colSpan={3} className="py-1.5 pr-3 text-muted">樣本不足</td>
      )}
      {stats.sufficient && stats.benchmark_sufficient ? (
        <>
          <td className="py-1.5 pr-3 tabular-nums"><ReturnValue value={stats.excess_mean} /></td>
          <td className="py-1.5 pr-3 tabular-nums">{fmtRatio(stats.beat_ratio)}</td>
        </>
      ) : (
        <td colSpan={2} className="py-1.5 pr-3 text-muted">
          {stats.sufficient ? `基準不足（${stats.benchmark_count}）` : '—'}
        </td>
      )}
      <td className="whitespace-nowrap py-1.5 pr-3 tabular-nums text-muted">
        漲停 {stats.not_filled_limit_up}・其他 {stats.not_filled_no_trade + stats.data_excluded_count}
      </td>
    </tr>
  )
}

function StatsTable({ signals, testId }: { signals: TaiwanSignalStat[]; testId: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-xs" data-testid={testId}>
        <thead className="text-[11px] text-muted">
          <tr>
            <th className="py-1 pr-3 font-normal">訊號</th>
            <th className="py-1 pr-3 font-normal">期間</th>
            <th className="py-1 pr-3 font-normal">事件數</th>
            <th className="py-1 pr-3 font-normal">已結算</th>
            <th className="py-1 pr-3 font-normal">平均報酬</th>
            <th className="py-1 pr-3 font-normal">中位數</th>
            <th className="py-1 pr-3 font-normal">上漲比例</th>
            <th className="py-1 pr-3 font-normal">平均超額</th>
            <th className="py-1 pr-3 font-normal">贏大盤比例</th>
            <th className="py-1 pr-3 font-normal">排除</th>
          </tr>
        </thead>
        <tbody>
          {signals.map(stat =>
            stat.horizons.map((stats, index) => (
              <HorizonRow key={`${stat.id}-${stats.horizon}`} stat={stat} stats={stats} first={index === 0} />
            )),
          )}
        </tbody>
      </table>
    </div>
  )
}

function RevenueLine({ revenue }: { revenue: NonNullable<TaiwanSignalStatsResponse['revenue']> }) {
  const state = revenue.status === 'no_data' || revenue.holds == null
    ? '尚無資料'
    : revenue.holds ? `目前成立（${revenue.period} 年增 ${revenue.yoy_pct}%）` : `目前不成立（${revenue.period} 年增 ${revenue.yoy_pct}%）`
  return (
    <p className="text-xs text-foreground" data-testid="signal-stats-revenue">
      月營收年增 ≥ {revenue.threshold_pct}%：{state}
      <span className="ml-2 text-[11px] text-muted">歷史統計不提供（營收沒有可用時間）</span>
    </p>
  )
}

function Notes({ response }: { response: TaiwanSignalStatsResponse }) {
  const method = response.method
  const from = response.benchmark?.covered_from
  return (
    <ul className="list-disc space-y-0.5 pl-4 text-[11px] leading-5 text-muted" data-testid="signal-stats-notes">
      <li>以訊號日的下一個交易日開盤進場，持有 20／60 個交易日後以收盤計；價格為前復權含息，不扣交易成本。</li>
      <li>上漲比例為報酬大於 0 的比例；已結算樣本少於 {method?.min_samples ?? 5} 次顯示「樣本不足」。開盤漲停買不到的事件不計入，列在排除數。</li>
      <li>
        交叉型訊號每次成立都算；其他訊號只算由不成立轉為成立的第一天，距上次計入不足 {method?.state_gap_sessions ?? 20} 個交易日不重算。事件的持有期間可能重疊。
      </li>
      <li>
        超額報酬以發行量加權股價報酬指數（含息）計，上櫃股也用這個指數；基準期間是訊號日收盤到第 20／60 日收盤，比個股多一個隔夜。
        報酬指數{from ? `自 ${from} 起` : '尚未'}累積，涵蓋的樣本少於 {method?.min_samples ?? 5} 次顯示「基準不足」。
      </li>
      <li>
        252日新高以前復權收盤計；外資連5買只能回溯到法人資料最早日{response.institutional_from ? ` ${response.institutional_from}` : '（尚無資料）'}。
      </li>
    </ul>
  )
}

function StatsBody({ response }: { response: TaiwanSignalStatsResponse }) {
  if (!response.applicable) return null
  if (response.status === 'unpublished') {
    return <p className="text-xs text-muted">尚無已發佈的盤後指標資料。</p>
  }
  const evaluated = response.signals.filter(stat => stat.evaluated)
  const current = evaluated.filter(stat => stat.current === true)
  const others = evaluated.filter(stat => stat.current !== true)
  return (
    <div className="space-y-2">
      {response.adjustment_status != null && response.adjustment_status !== 'ok' && (
        <p className="text-[11px] text-amber-500">除權息資料不可用，報酬暫不計算。</p>
      )}
      {current.length > 0 ? (
        <StatsTable signals={current} testId="signal-stats-current" />
      ) : (
        <p className="text-xs text-muted" data-testid="signal-stats-none">目前沒有成立的訊號。</p>
      )}
      {response.revenue && <RevenueLine revenue={response.revenue} />}
      {others.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted">其他訊號的歷史統計（{others.length}）</summary>
          <div className="mt-2">
            <StatsTable signals={others} testId="signal-stats-others" />
          </div>
        </details>
      )}
      <Notes response={response} />
    </div>
  )
}

export function TaiwanSignalStats({ symbol, query }: { symbol: string; query: SignalStatsQuery }) {
  const current = query.response?.symbol === symbol ? query.response : undefined
  return (
    <div className="space-y-2 border-t border-border pt-3" data-testid="signal-stats">
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 className="text-xs font-semibold text-foreground">技術訊號歷史統計</h4>
        {current?.as_of && <span className="text-[11px] font-normal text-muted">資料日期 {current.as_of}</span>}
      </div>
      {query.isError ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="flex items-center gap-1.5 text-rose-500">
            <AlertTriangle className="h-3.5 w-3.5" />
            訊號統計暫時讀取失敗
          </span>
          <button
            type="button"
            onClick={query.onRetry}
            disabled={query.isFetching}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-base px-2.5 py-1 text-xs text-muted hover:text-foreground disabled:opacity-60"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', query.isFetching && 'animate-spin')} />
            重試
          </button>
        </div>
      ) : query.isLoading || current == null ? (
        <p className="flex items-center text-xs text-muted" data-testid="signal-stats-loading">
          <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin text-accent" />
          計算訊號統計中...
        </p>
      ) : (
        <StatsBody response={current} />
      )}
    </div>
  )
}
