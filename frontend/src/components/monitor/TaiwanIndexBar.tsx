import { useQuery } from '@tanstack/react-query'
import { TrendingDown, TrendingUp } from 'lucide-react'
import { api, type TaiwanRealtimeQuote } from '@/lib/api'

import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { useStockPreview } from '@/components/StockPreviewProvider'
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
  const { openPreview } = useStockPreview()
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
    <button
      type="button"
      onClick={() => openPreview(symbol, quote?.name || FALLBACK_NAMES[symbol])}
      aria-label={`查看 ${quote?.name || FALLBACK_NAMES[symbol] || symbol}`}
      className={cn(
        'flex flex-1 min-w-[220px] items-center gap-3 rounded-lg border border-border/80 px-3 py-1.5 text-left transition-colors hover:border-primary/60 cursor-pointer',
        bgCls,
      )}
    >
      <span className="shrink-0 text-xs font-bold text-foreground">
        {quote?.name || FALLBACK_NAMES[symbol] || symbol}
      </span>
      <span className={cn('font-mono text-base font-bold tabular-nums', colorCls)}>
        {formatPoints(quote?.last_price)}
      </span>
      <span className={cn('font-mono text-[11px]', colorCls)}>
        {change > 0 ? '+' : ''}{formatPoints(quote?.change)}
        {' '}
        {(quote?.change_pct ?? 0) > 0 ? '+' : ''}
        {quote?.change_pct != null ? `${quote.change_pct.toFixed(2)}%` : '--'}
      </span>
      {isUp && <TrendingUp className={cn('h-3.5 w-3.5 shrink-0', colorCls)} />}
      {isDown && <TrendingDown className={cn('h-3.5 w-3.5 shrink-0', colorCls)} />}
      <span className="ml-auto flex shrink-0 items-center gap-2">
        {quote && <DataQualityBadge meta={quote.source_meta} quoteTime={quote.quote_time} />}
        <span className="text-[10px] text-muted">{quote ? formatQuoteTime(quote.quote_time) : '載入中'}</span>
      </span>
    </button>
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
    <div className="flex flex-wrap gap-2">
      {INDEX_SYMBOLS.map(sym => (
        <IndexCard key={sym} symbol={sym} quote={quotes.find(q => q.symbol === sym)} />
      ))}
    </div>
  )
}
