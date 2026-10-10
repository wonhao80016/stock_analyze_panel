import type { MarketFilter, MarketState, PickGradeReason, PickGrading, ScreenerCachedRow } from '@/lib/api'
import { INVESTMENT_DISCLAIMER } from '@/lib/disclaimer'
import { cn } from '@/lib/cn'
import { cnSignal } from '@/lib/signals'

const MARKET_FILTER_TEXT: Record<MarketFilter, string> = {
  applied: '大盤：跌破 20 日均線，買進已降為觀察',
  passed: '大盤：未跌破 20 日均線',
  unavailable: '大盤狀態無法判定，濾鏡未套用',
}

const UNAVAILABLE_REASON_TEXT: Record<string, string> = {
  index_missing: '加權指數缺少基準日收盤',
  insufficient_history: '交易日不足 20 天',
  index_gap: '加權指數近 20 個交易日有缺漏',
}

const PRICE_INDEX_NOTE = '（價格指數，不含股利）'

// Worded for both an intraday price and a close: the flag is live intraday, published after the close.
const LIMIT_UP_LOCKED_TITLE = '價格已在漲停價，買單難以成交'

const WATCH_BADGE_TEXT: Partial<Record<PickGradeReason, string>> = {
  market_below_ma20: '觀察・大盤',
  score_unavailable: '觀察・資料不足',
}

function fmtIndex(v: number | null | undefined) {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(2) : '—'
}

function fieldLabels(fields: string[]) {
  return fields.map(field => cnSignal(field)).join('、')
}

function missingScoringTitle(row: ScreenerCachedRow) {
  const children = row.missing_scoring_children ?? []
  if (children.length > 0) {
    return `評分欄位缺值：${children.map(child => `${child.name}（${fieldLabels(child.fields)}）`).join('；')}`
  }
  const fields = row.missing_scoring_fields ?? []
  return fields.length > 0 ? `評分欄位缺值：${fieldLabels(fields)}` : undefined
}

function watchTitle(row: ScreenerCachedRow, marketState?: MarketState | null, topN?: number) {
  switch (row.grade_reason) {
    case 'market_below_ma20':
      return marketState
        ? `加權指數 ${marketState.as_of} 收盤 ${fmtIndex(marketState.close)} 低於 20 日均線 ${fmtIndex(marketState.ma)}${PRICE_INDEX_NOTE}`
        : '加權指數收盤低於 20 日均線'
    case 'score_unavailable':
      return missingScoringTitle(row) ?? '評分資料不足（分數 —）'
    case 'outside_top_n':
      return topN ? `排名在前 ${topN} 名之外` : '排名在前段之外'
    default:
      return undefined
  }
}

export function PickGradeBadge({
  row,
  marketState,
  topN,
}: {
  row: ScreenerCachedRow
  marketState?: MarketState | null
  topN?: number
}) {
  if (row.grade !== 'buy' && row.grade !== 'watch') return null
  const buy = row.grade === 'buy'
  return (
    <>
      <span
        title={buy ? undefined : watchTitle(row, marketState, topN)}
        className={cn(
          'shrink-0 rounded px-1 py-px text-[9px] font-medium leading-tight',
          buy ? 'bg-danger/15 text-danger' : 'bg-muted/15 text-muted',
        )}
      >
        {buy ? '買進' : (row.grade_reason && WATCH_BADGE_TEXT[row.grade_reason]) || '觀察'}
      </span>
      {row.limit_up_locked && (
        <span
          title={LIMIT_UP_LOCKED_TITLE}
          className="shrink-0 rounded bg-amber-400/15 px-1 py-px text-[9px] font-medium leading-tight text-amber-400"
        >
          漲停鎖死
        </span>
      )}
    </>
  )
}

export function MarketFilterNote({
  grading,
  marketState,
  resultDates,
  className,
}: {
  grading?: PickGrading
  marketState?: MarketState | null
  resultDates?: (string | null | undefined)[]
  className?: string
}) {
  if (!grading) return null
  const basis = marketState?.as_of
  const laterResults = !!basis && (resultDates ?? []).some(d => !!d && d !== basis)
  const title = marketState?.status === 'ok'
    ? `加權指數 ${basis} 收盤 ${fmtIndex(marketState.close)}，20 日均線 ${fmtIndex(marketState.ma)}${PRICE_INDEX_NOTE}`
    : marketState?.reason
      ? UNAVAILABLE_REASON_TEXT[marketState.reason]
      : undefined
  return (
    <p className={cn('text-[10px] leading-4 text-muted', className)}>
      <span
        title={title}
        className={grading.market_filter === 'applied' ? 'text-amber-400/90' : undefined}
      >
        {MARKET_FILTER_TEXT[grading.market_filter]}
      </span>
      {laterResults && <span>（大盤狀態依 {basis} 收盤）</span>}
      <span className="ml-1">{INVESTMENT_DISCLAIMER}</span>
    </p>
  )
}
