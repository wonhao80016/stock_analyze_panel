import { AlertTriangle, RefreshCw } from 'lucide-react'
import type {
  TaiwanCurrentSignal,
  TaiwanCurrentSignalsResponse,
  TaiwanSignalDirection,
  TaiwanSignalStatsResponse,
  TaiwanStrategySelection,
} from '@/lib/api'
import { cn } from '@/lib/cn'
import { MarketFilterNote, PickGradeBadge } from '@/components/PickGrade'
import { SignalStatSummary, TaiwanSignalStats, signalName, type SignalStatsQuery } from './TaiwanSignalStats'

type Props = {
  symbol: string
  response?: TaiwanCurrentSignalsResponse
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  onRetry: () => void
  stats?: SignalStatsQuery
}

type SignalsSection = NonNullable<TaiwanCurrentSignalsResponse['signals']>
type StrategiesSection = NonNullable<TaiwanCurrentSignalsResponse['strategies']>

const GROUPS: { direction: TaiwanSignalDirection; label: string; className: string }[] = [
  { direction: 'bullish', label: '偏多', className: 'border-bull/30 bg-bull/10 text-bull' },
  { direction: 'bearish', label: '偏空', className: 'border-bear/30 bg-bear/10 text-bear' },
  { direction: 'neutral', label: '中性', className: 'border-border bg-base text-foreground' },
]

function DateTag({ value }: { value: string | null }) {
  return <span className="text-[11px] font-normal text-muted">資料日期 {value ?? '--'}</span>
}

function SignalGroups({ signals, stats }: { signals: TaiwanCurrentSignal[]; stats?: TaiwanSignalStatsResponse }) {
  return (
    <div className="space-y-2">
      {GROUPS.map(group => {
        const items = signals.filter(signal => signal.direction === group.direction)
        if (items.length === 0) return null
        return (
          <div key={group.direction} className="flex flex-wrap items-center gap-1.5" data-testid={`current-signals-${group.direction}`}>
            <span className="w-8 shrink-0 text-[11px] text-muted">{group.label}</span>
            {items.map(signal => (
              <span key={signal.id} className="inline-flex items-center gap-1">
                <span className={cn('rounded-full border px-2 py-0.5 text-xs', group.className)}>
                  {signalName(signal)}
                </span>
                <SignalStatSummary stat={stats?.signals.find(stat => stat.id === signal.id)} />
              </span>
            ))}
          </div>
        )
      })}
    </div>
  )
}

function SignalsBlock({ section, stats }: { section: SignalsSection; stats?: TaiwanSignalStatsResponse }) {
  let body
  if (section.status === 'unpublished') {
    body = <p className="text-xs text-muted">尚無已發佈的盤後指標資料。</p>
  } else if (section.status === 'no_row') {
    body = <p className="text-xs text-muted">這個交易日的盤後指標沒有這檔的資料（可能停牌或剛上市）。</p>
  } else {
    body = (
      <div className="space-y-2">
        {section.triggered.length > 0 ? (
          <SignalGroups signals={section.triggered} stats={stats} />
        ) : (
          <p className="text-xs text-muted" data-testid="current-signals-none">這個交易日沒有成立的訊號。</p>
        )}
        {section.not_evaluated.length > 0 && (
          <p className="text-[11px] leading-5 text-muted" data-testid="current-signals-not-evaluated">
            未評估：{section.not_evaluated.map(signalName).join('、')}（這天的盤後資料還沒有這些欄位，下次盤後發佈後才會判定）
          </p>
        )}
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 className="text-xs font-semibold text-foreground">技術與量價訊號</h4>
        <DateTag value={section.as_of} />
      </div>
      {body}
    </div>
  )
}

function SelectionRow({ selection, section }: { selection: TaiwanStrategySelection; section: StrategiesSection }) {
  return (
    <li className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="font-medium text-foreground">{selection.strategy_name}</span>
      <span className="text-muted">
        第 {selection.rank}{selection.total != null ? `／${selection.total}` : ''} 名
      </span>
      <PickGradeBadge
        row={{
          symbol: '',
          score: selection.score,
          grade: selection.grade ?? undefined,
          grade_reason: selection.grade_reason,
          limit_up_locked: selection.limit_up_locked,
          missing_scoring_fields: selection.missing_scoring_fields ?? undefined,
          missing_scoring_children: selection.missing_scoring_children ?? undefined,
        }}
        marketState={section.market_state}
        topN={section.grading?.top_n}
      />
      {selection.as_of && selection.as_of !== section.as_of && (
        <span className="text-[11px] text-muted">（盤中 {selection.as_of}）</span>
      )}
    </li>
  )
}

function StrategiesBlock({ section }: { section: StrategiesSection }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 className="text-xs font-semibold text-foreground">目前入選的策略</h4>
        <DateTag value={section.as_of} />
      </div>
      {section.status === 'no_cache' ? (
        <p className="text-xs text-muted" data-testid="current-strategies-no-cache">尚無策略結果，盤後執行策略後才會顯示。</p>
      ) : section.selections.length === 0 ? (
        <p className="text-xs text-muted" data-testid="current-strategies-none">目前沒有策略選中這檔。</p>
      ) : (
        <>
          <ul className="space-y-1.5" data-testid="current-strategies">
            {section.selections.map(selection => (
              <SelectionRow key={selection.strategy_id} selection={selection} section={section} />
            ))}
          </ul>
          <MarketFilterNote
            grading={section.grading ?? undefined}
            marketState={section.market_state}
            resultDates={section.selections.map(selection => selection.as_of)}
          />
        </>
      )}
    </div>
  )
}

export function TaiwanCurrentSignals({ symbol, response, isLoading, isError, isFetching, onRetry, stats }: Props) {
  const current = response?.symbol === symbol ? response : undefined
  const currentStats = stats?.response?.symbol === symbol ? stats.response : undefined
  return (
    <section aria-labelledby="taiwan-current-signals-heading" className="space-y-3 rounded-2xl border border-border bg-surface p-4">
      <div>
        <h3 id="taiwan-current-signals-heading" className="text-sm font-bold text-foreground">目前觸發的訊號</h3>
        <p className="mt-1 text-[11px] leading-5 text-muted">取自盤後發佈的指標與策略結果，只含價量訊號，不含基本面。</p>
      </div>
      {isError ? (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-muted">
          <div className="flex items-center gap-2 font-medium text-rose-500">
            <AlertTriangle className="h-4 w-4" />
            目前訊號暫時讀取失敗
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
        <div className="flex min-h-20 items-center justify-center text-xs text-muted" data-testid="current-signals-loading">
          <RefreshCw className="mr-2 h-4 w-4 animate-spin text-accent" />
          讀取目前訊號中...
        </div>
      ) : !current.applicable || current.signals == null || current.strategies == null ? (
        <p className="text-xs text-muted">指數沒有個股訊號與策略入選。</p>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <SignalsBlock section={current.signals} stats={currentStats} />
            <StrategiesBlock section={current.strategies} />
          </div>
          {stats && <TaiwanSignalStats symbol={symbol} query={stats} />}
        </>
      )}
    </section>
  )
}
