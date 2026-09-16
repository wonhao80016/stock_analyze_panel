import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { TrendingDown, TrendingUp } from 'lucide-react'
import { api, type TaiwanRealtimeQuote } from '@/lib/api'

import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { DataQualityBadge, formatQuoteTime } from '@/components/taiwan/TaiwanDataQuality'

// MIS channel codes for the two benchmarks: tse_t00.tw and otc_o00.tw.
const INDEX_SYMBOLS = ['t00.TWSE', 'o00.TPEX']

const FALLBACK_NAMES: Record<string, string> = {
  't00.TWSE': '加權指數',
  'o00.TPEX': '櫃買指數',
}

function formatPoints(value: number | null | undefined): string {
  if (value == null) return '--'
  return value.toLocaleString('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function IndexCard({ symbol, quote }: { symbol: string; quote?: TaiwanRealtimeQuote }) {
  const change = quote?.change ?? 0
  const isUp = change > 0
  const isDown = change < 0
  const colorCls = isUp
    ? 'text-rose-500 dark:text-rose-400'
    : isDown
      ? 'text-emerald-500 dark:text-emerald-400'
      : 'text-foreground'
  const bgCls = isUp ? 'bg-rose-500/10' : isDown ? 'bg-emerald-500/10' : 'bg-surface'

  return (
    <Link
      to={`/stocks/${encodeURIComponent(symbol)}`}
      aria-label={`查看 ${quote?.name || FALLBACK_NAMES[symbol] || symbol} 研究頁`}
      className={cn(
        'flex flex-1 min-w-[180px] flex-col rounded-xl border border-border/80 p-3 transition-colors hover:border-primary/60',
        bgCls,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-bold text-foreground">
          {quote?.name || FALLBACK_NAMES[symbol] || symbol}
        </span>
        {quote && (
          <DataQualityBadge meta={quote.source_meta} quoteTime={quote.quote_time} />
        )}
      </div>

      <div className="mt-1 flex items-baseline gap-2">
        <span className={cn('font-mono text-xl font-bold tabular-nums', colorCls)}>
          {formatPoints(quote?.last_price)}
        </span>
        {isUp && <TrendingUp className={cn('h-4 w-4', colorCls)} />}
        {isDown && <TrendingDown className={cn('h-4 w-4', colorCls)} />}
      </div>

      <div className={cn('mt-0.5 flex items-center gap-2 font-mono text-xs', colorCls)}>
        <span>
          {change > 0 ? '+' : ''}
          {formatPoints(quote?.change)}
        </span>
        <span>
          {(quote?.change_pct ?? 0) > 0 ? '+' : ''}
          {quote?.change_pct != null ? `${quote.change_pct.toFixed(2)}%` : '--'}
        </span>
      </div>

      <div className="mt-1 text-[10px] text-muted">
        {quote ? formatQuoteTime(quote.quote_time) : '載入中'}
      </div>
    </Link>
  )
}

export function TaiwanIndexBar() {
  const indicesQuery = useQuery({
    queryKey: QK.taiwanQuotes(INDEX_SYMBOLS.join(',')),
    queryFn: () => api.taiwanQuotes(INDEX_SYMBOLS),
    staleTime: 3000,
  })

  const quotes = indicesQuery.data?.quotes || []

  if (indicesQuery.isError) {
    return (
      <div className="rounded-xl border border-border/80 bg-surface p-3 text-xs text-muted">
        大盤指數暫時無法取得
      </div>
    )
  }

  return (
    <div className="flex flex-wrap gap-3">
      {INDEX_SYMBOLS.map(sym => (
        <IndexCard key={sym} symbol={sym} quote={quotes.find(q => q.symbol === sym)} />
      ))}
    </div>
  )
}
