import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Target } from 'lucide-react'
import { api, type StrategyDetail } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { StrategyRule } from '@/components/StrategyRule'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { cn } from '@/lib/cn'

function n(v: number | null | undefined) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function fmtPrice(v: number | null | undefined) {
  const x = n(v)
  return x == null ? '—' : x.toFixed(2)
}

function fmtPct(v: number | null | undefined) {
  const x = n(v)
  if (x == null) return '—'
  return `${x >= 0 ? '+' : ''}${(x * 100).toFixed(2)}%`
}

function pctClass(v: number | null | undefined) {
  const x = n(v)
  if (x == null) return 'text-muted'
  return x > 0 ? 'text-red-400' : x < 0 ? 'text-emerald-400' : 'text-secondary'
}

type Filter = 'all' | 'hits' | 'empty' | 'unrun'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'hits', label: '今日有選股' },
  { key: 'empty', label: '今日零命中' },
  { key: 'unrun', label: '未執行' },
]

export function Strategies() {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ symbol: string; name?: string } | null>(null)
  const [filter, setFilter] = useState<Filter>('all')

  const strategies = useQuery({
    queryKey: QK.screenerStrategies('stock'),
    queryFn: () => api.screenerStrategies('stock'),
    staleTime: 300_000,
  })
  const cached = useQuery({
    queryKey: QK.screenerCached(),
    queryFn: () => api.screenerCached(),
    staleTime: 60_000,
  })

  const results = cached.data?.results ?? {}

  const rows = useMemo(() => {
    const presets = (strategies.data?.presets ?? []) as StrategyDetail[]
    return presets
      .map(detail => {
        const result = results[detail.id]
        return {
          detail,
          // A strategy absent from the cache did not run at all, which is not the
          // same as running and finding nothing; the card only ever showed hits,
          // so both cases were invisible.
          ran: result != null,
          total: result?.total ?? 0,
          picks: (result?.rows ?? []) as any[],
        }
      })
      .sort((a, b) => Number(b.ran) - Number(a.ran) || b.total - a.total)
  }, [strategies.data, results])

  const shown = rows.filter(r =>
    filter === 'all' ? true
    : filter === 'hits' ? r.ran && r.total > 0
    : filter === 'empty' ? r.ran && r.total === 0
    : !r.ran,
  )

  const counts = {
    all: rows.length,
    hits: rows.filter(r => r.ran && r.total > 0).length,
    empty: rows.filter(r => r.ran && r.total === 0).length,
    unrun: rows.filter(r => !r.ran).length,
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-3 py-3">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-center gap-2">
          <Target className="h-4 w-4 text-accent" />
          <h1 className="text-sm font-semibold text-foreground">策略庫</h1>
          {cached.data?.as_of && (
            <span className="font-mono text-[11px] text-muted">選股基準日 {cached.data.as_of}</span>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          {FILTERS.map(f => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              aria-pressed={filter === f.key}
              className={cn(
                'rounded-md border px-2 py-0.5 text-[11px] transition-colors cursor-pointer',
                filter === f.key
                  ? 'border-accent/60 bg-accent/10 text-accent'
                  : 'border-border text-muted hover:text-foreground',
              )}
            >
              {f.label} {counts[f.key]}
            </button>
          ))}
        </div>
      </div>

      {strategies.isLoading ? (
        <div className="flex items-center gap-2 py-6 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取策略清單…
        </div>
      ) : strategies.isError ? (
        <p className="py-6 text-xs text-muted">目前無法讀取策略清單。</p>
      ) : shown.length === 0 ? (
        <p className="py-6 text-center text-xs text-muted">此分類沒有策略。</p>
      ) : (
        <div className="space-y-2">
          {shown.map(({ detail, ran, total, picks }) => {
            const open = expanded === detail.id
            return (
              <section key={detail.id} className="rounded-card border border-border bg-surface p-3">
                <button
                  type="button"
                  onClick={() => setExpanded(open ? null : detail.id)}
                  aria-expanded={open}
                  aria-label={`${detail.name} 的細節`}
                  className="flex w-full items-baseline justify-between gap-3 text-left cursor-pointer"
                >
                  <div className="flex min-w-0 items-baseline gap-2">
                    <span className="truncate text-[13px] font-medium text-foreground">{detail.name}</span>
                    <span className="shrink-0 font-mono text-[10px] text-muted">{detail.id}</span>
                  </div>
                  <span className="shrink-0 font-mono text-[11px]">
                    {!ran ? (
                      <span className="text-amber-400/90">未執行</span>
                    ) : total > 0 ? (
                      <span className="text-accent">{total} 檔</span>
                    ) : (
                      <span className="text-muted">0 檔</span>
                    )}
                    <span className="ml-1 text-muted">{open ? '▴' : '▾'}</span>
                  </span>
                </button>

                {!ran && (
                  <p className="mt-1.5 rounded border border-amber-900/40 bg-amber-950/20 px-2 py-1 text-[11px] leading-5 text-amber-200/80">
                    這支策略本輪沒有執行，因此沒有結果可看 — 通常是它需要的欄位在台股沒有資料
                    （例如換手率需要流通股數，台灣官方不公布）。詳情記在後端日誌。
                  </p>
                )}

                {open && (
                  <>
                    <StrategyRule detail={detail} className="mt-2" />
                    {ran && total > 0 && (
                      <div className="mt-2 max-h-80 space-y-1 overflow-y-auto pr-0.5">
                        {picks.map(r => (
                          <button
                            key={r.symbol}
                            type="button"
                            onClick={() => setPreview({ symbol: r.symbol, name: r.name ?? undefined })}
                            aria-label={`查看 ${r.name || r.symbol} 走勢`}
                            className="flex w-full items-center justify-between gap-2 rounded-md border border-transparent bg-elevated/40 px-2 py-1.5 text-left transition-colors hover:border-border/60 hover:bg-elevated"
                          >
                            <div className="min-w-0">
                              <div className="truncate text-[11px] text-foreground">{r.name || r.symbol}</div>
                              <div className="font-mono text-[9px] text-muted">{r.symbol}</div>
                            </div>
                            <div className="shrink-0 text-right">
                              <div className="font-mono text-[11px] text-foreground">{fmtPrice(r.close)}</div>
                              <div className={`font-mono text-[10px] font-semibold ${pctClass(r.change_pct)}`}>
                                {fmtPct(r.change_pct)}
                              </div>
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                    {ran && total === 0 && (
                      <p className="mt-2 text-[11px] text-muted">基準日沒有任何標的符合這支策略的條件。</p>
                    )}
                  </>
                )}
              </section>
            )
          })}
        </div>
      )}

      {preview && (
        <StockPreviewDialog
          symbol={preview.symbol}
          name={preview.name}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  )
}
