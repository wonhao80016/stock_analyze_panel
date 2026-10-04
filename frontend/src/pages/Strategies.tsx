import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { BarChart3, Loader2, Target } from 'lucide-react'
import { api, type StrategyDetail } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { StrategyRule, strategyRuleText } from '@/components/StrategyRule'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { StrategyPickPerformance } from '@/components/StrategyPickPerformance'
import { MarketFilterNote, PickGradeBadge } from '@/components/PickGrade'
import { cn } from '@/lib/cn'

const TABS = [
  { key: 'list', label: '策略清單' },
  { key: 'performance', label: '事後統計' },
] as const

type TabKey = (typeof TABS)[number]['key']

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

const MIN_HITS_OPTIONS = [2, 3, 4, 5] as const

/** The cap a run actually applied: a saved display_limit overrides the module's. */
function effectiveLimit(detail: StrategyDetail): number | null {
  const value = detail.display_limit ?? detail.limit
  return typeof value === 'number' && value > 0 ? value : null
}

export function Strategies() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab') as TabKey | null
  const activeTab = TABS.find(t => t.key === tabParam) ?? TABS[0]
  const isList = activeTab.key === 'list'
  const [expanded, setExpanded] = useState<string | null>(null)
  const [minHits, setMinHits] = useState<number>(2)
  // Strategies a stock must ALL be picked by. Empty means no constraint, so a
  // strategy added later never silently narrows what was already on screen.
  const [required, setRequired] = useState<Set<string>>(new Set())
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

  // A stock several strategies agree on is the thing a per-strategy list cannot
  // show: each list holds it once, and nothing says the others hold it too.
  const nameById = useMemo(
    () => new Map(((strategies.data?.presets ?? []) as StrategyDetail[]).map(s => [s.id, s.name || s.id])),
    [strategies.data],
  )

  /** Strategies that picked something, so the chips never offer an empty one. */
  const detailById = useMemo(
    () => new Map(((strategies.data?.presets ?? []) as StrategyDetail[]).map(s => [s.id, s])),
    [strategies.data],
  )

  const contributors = useMemo(
    () => Object.entries(results)
      .filter(([, r]) => ((r as any)?.rows?.length ?? 0) > 0)
      .map(([sid]) => sid)
      .sort((a, b) => (nameById.get(a) ?? a).localeCompare(nameById.get(b) ?? b, 'zh-Hant')),
    [results, nameById],
  )

  const resonance = useMemo(() => {
    const bySymbol = new Map<string, { row: any; ids: string[]; strategies: string[] }>()
    for (const [sid, result] of Object.entries(results)) {
      for (const row of ((result as any)?.rows ?? []) as any[]) {
        const hit = bySymbol.get(row.symbol) ?? { row, ids: [], strategies: [] }
        hit.ids.push(sid)
        hit.strategies.push(nameById.get(sid) ?? sid)
        bySymbol.set(row.symbol, hit)
      }
    }
    const pinned = [...required]
    return [...bySymbol.values()]
      // Every pinned strategy must have picked it, so pinning narrows towards the
      // intersection; with nothing pinned this is the plain multi-hit list.
      .filter(h => pinned.every(sid => h.ids.includes(sid)))
      .filter(h => h.strategies.length >= 2)
      .sort((a, b) => b.strategies.length - a.strategies.length
        || (b.row.change_pct ?? 0) - (a.row.change_pct ?? 0))
  }, [results, nameById, required])

  const resonanceShown = resonance.filter(h => h.strategies.length >= minHits)

  // A strategy at its cap published its top N by score, not everything that
  // matched, so a stock it ranked below the cut is missing from the tally below.
  const cappedNames = rows
    .filter(r => r.ran && effectiveLimit(r.detail) != null && r.total === effectiveLimit(r.detail))
    .map(r => r.detail.name)

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
          {isList && cached.data?.as_of && (
            <span className="font-mono text-[11px] text-muted">選股基準日 {cached.data.as_of}</span>
          )}
        </div>
        {isList && <div className="flex flex-wrap gap-1">
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
        </div>}
      </div>
      {isList && cached.data && (
        <MarketFilterNote
          grading={cached.data.grading}
          marketState={cached.data.market_state}
          resultDates={Object.values(results).map(r => r?.as_of)}
          className="-mt-2 mb-3"
        />
      )}

      <div role="tablist" aria-label="策略庫分頁" className="mb-3 flex gap-1 border-b border-border">
        {TABS.map(t => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={activeTab.key === t.key}
            onClick={() => setSearchParams({ tab: t.key }, { replace: true })}
            className={cn(
              '-mb-px border-b-2 px-3 py-1 text-[12px] transition-colors cursor-pointer',
              activeTab.key === t.key
                ? 'border-accent text-accent'
                : 'border-transparent text-muted hover:text-foreground',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {isList && contributors.length >= 2 && (
        <section className="mb-3 rounded-card border border-accent/30 bg-surface p-3">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <div className="flex items-baseline gap-2">
              <h2 className="text-[13px] font-medium text-foreground">多策略共振</h2>
              <span className="text-[11px] text-muted">
                {resonanceShown.length} 檔同時被 {minHits} 支以上策略選中
              </span>
            </div>
            <div className="flex gap-1">
              {MIN_HITS_OPTIONS.map(v => {
                const count = resonance.filter(h => h.strategies.length >= v).length
                return (
                  <button
                    key={v}
                    type="button"
                    disabled={count === 0}
                    onClick={() => setMinHits(v)}
                    aria-pressed={minHits === v}
                    className={cn(
                      'rounded-md border px-2 py-0.5 text-[11px] transition-colors',
                      count === 0
                        ? 'cursor-not-allowed border-border/50 text-muted/40'
                        : minHits === v
                          ? 'cursor-pointer border-accent/60 bg-accent/10 text-accent'
                          : 'cursor-pointer border-border text-muted hover:text-foreground',
                    )}
                  >
                    ≥{v} 支 {count}
                  </button>
                )
              })}
            </div>
          </div>
          <div className="mb-2 rounded-md border border-border/60 bg-base/40 p-2">
            <div className="mb-1 flex items-baseline justify-between gap-2">
              <span className="text-[11px] text-secondary">
                指定必須命中的策略
                <span className="ml-1 text-[10px] text-muted">
                  {required.size === 0 ? '未指定，列出所有重複命中' : `已指定 ${required.size} 支，需全部命中`}
                </span>
              </span>
              <button
                type="button"
                onClick={() => setRequired(new Set())}
                disabled={required.size === 0}
                className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted enabled:cursor-pointer enabled:hover:text-foreground disabled:opacity-40"
              >
                清除
              </button>
            </div>
            <div className="flex flex-wrap gap-1">
              {contributors.map(sid => {
                const on = required.has(sid)
                return (
                  <button
                    key={sid}
                    type="button"
                    role="checkbox"
                    aria-checked={on}
                    aria-label={nameById.get(sid) ?? sid}
                    title={(() => {
                      const d = detailById.get(sid)
                      return d ? strategyRuleText(d) : (nameById.get(sid) ?? sid)
                    })()}
                    onClick={() => setRequired(prev => {
                      const next = new Set(prev)
                      if (next.has(sid)) next.delete(sid)
                      else next.add(sid)
                      return next
                    })}
                    className={cn(
                      'rounded border px-1.5 py-0.5 text-[10px] transition-colors cursor-pointer',
                      on
                        ? 'border-accent/60 bg-accent/15 text-accent'
                        : 'border-border text-muted hover:text-foreground',
                    )}
                  >
                    {on && <span className="mr-0.5">✓</span>}
                    {nameById.get(sid) ?? sid}
                    <span className="ml-1 font-mono text-[9px] opacity-70">
                      {(results as any)[sid]?.rows?.length ?? 0}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          <p className="mb-2 text-[10px] leading-4 text-muted">
            數字是這檔股票<span className="text-secondary">出現在幾支策略的選股清單中</span>，
            不等於它符合幾支策略的條件。
            {cappedNames.length > 0 && (
              <>
                {' '}
                <span className="text-amber-400/90">
                  {cappedNames.join('、')} 已達各自的選股上限
                </span>
                ，排在上限之外的標的不會計入。
              </>
            )}
          </p>
          {resonanceShown.length === 0 && (
            <p className="py-3 text-center text-[11px] text-muted">
              {required.size > 0
                ? '沒有個股同時被指定的這幾支策略選中。'
                : '目前的門檻下沒有被重複命中的個股。'}
            </p>
          )}
          <div className="max-h-96 space-y-1 overflow-y-auto pr-0.5">
            {resonanceShown.map(({ row, strategies: names }) => (
              <button
                key={row.symbol}
                type="button"
                onClick={() => setPreview({ symbol: row.symbol, name: row.name ?? undefined })}
                aria-label={`查看 ${row.name || row.symbol} 走勢`}
                className="flex w-full items-center gap-3 rounded-md border border-transparent bg-elevated/40 px-2 py-1.5 text-left transition-colors hover:border-border/60 hover:bg-elevated"
              >
                <span className="w-7 shrink-0 text-center font-mono text-[13px] font-bold text-accent">
                  {names.length}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-1.5">
                    <span className="truncate text-[12px] text-foreground">{row.name || row.symbol}</span>
                    <span className="shrink-0 font-mono text-[9px] text-muted">{row.symbol}</span>
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {names.map(name => {
                      const d = [...detailById.values()].find(x => (x.name || x.id) === name)
                      return (
                        <span
                          key={name}
                          title={d ? strategyRuleText(d) : name}
                          className="rounded border border-border px-1 text-[9px] text-muted"
                        >
                          {name}
                        </span>
                      )
                    })}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="font-mono text-[11px] text-foreground">{fmtPrice(row.close)}</div>
                  <div className={`font-mono text-[10px] font-semibold ${pctClass(row.change_pct)}`}>
                    {fmtPct(row.change_pct)}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      {!isList ? (
        <StrategyPickPerformance nameById={nameById} onPreview={setPreview} />
      ) : strategies.isLoading ? (
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
                      <>
                        <span className="text-accent">{total} 檔</span>
                        {total === effectiveLimit(detail) && (
                          <span className="ml-1 text-amber-400/90">已達上限</span>
                        )}
                      </>
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
                    <div className="mt-2 flex justify-end">
                      <Link
                        to={`/backtest?${new URLSearchParams({ strategy_id: detail.id })}`}
                        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-elevated px-2 py-1 text-[11px] font-medium text-secondary transition-colors hover:border-accent/50 hover:text-accent"
                      >
                        <BarChart3 className="h-3.5 w-3.5" />
                        回測此策略
                      </Link>
                    </div>
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
                              <div className="flex min-w-0 items-center gap-1">
                                <span className="truncate text-[11px] text-foreground">{r.name || r.symbol}</span>
                                <PickGradeBadge
                                  row={r}
                                  marketState={cached.data?.market_state}
                                  topN={cached.data?.grading?.top_n}
                                />
                              </div>
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
