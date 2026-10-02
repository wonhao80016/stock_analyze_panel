import { Fragment, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2 } from 'lucide-react'
import {
  api,
  type PickHorizon,
  type PickHorizonReturn,
  type PickPerformanceSummary,
  type PickRecord,
  type PickStrategyStats,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { fmtPct, fmtPrice, priceColorClass } from '@/lib/format'

const HORIZONS: readonly PickHorizon[] = ['1', '5', '10', '20']
const DETAIL_LIMIT = 200
// Same-day picks are highly correlated, so the effective sample size tracks signal days, not rows.
const LOW_SAMPLE_SIGNAL_DAYS = 20
const STALE_TIME = 300_000

const ADJUSTMENT_REASONS: Record<string, string> = {
  events_missing: '除權息資料檔不存在',
  events_unreadable: '除權息資料讀取失敗',
  coverage_missing: '除權息資料缺少涵蓋區間',
}

const STATUS_LABELS: Record<string, string> = {
  pending_entry: '待 T+1 開盤',
  tracking: '追蹤中',
  completed: '已滿 20 日',
  not_filled_limit_up: '未成交：開盤漲停',
  not_filled_no_trade: '未成交：無成交／停牌',
  prices_incomplete: '價格資料缺漏',
  data_incomplete: '市場資料不完整',
}

const EXCLUDED_LABELS: Record<string, string> = {
  recorded_late: '延遲紀錄',
  universe_incomplete: '市場不完整',
  manifest_missing: '清單缺漏',
}

const RETURN_REASONS: Record<string, { short: string; full: string }> = {
  pending: { short: '未滿期', full: '尚未到達這個持有期' },
  not_filled_limit_up: { short: '未成交', full: 'T+1 開盤漲停，無法以開盤價成交' },
  not_filled_no_trade: { short: '未成交', full: 'T+1 無成交或停牌' },
  adjustment_not_covered: { short: '除權息未涵蓋', full: '除權息資料尚未涵蓋這段期間' },
  data_incomplete: { short: '資料不完整', full: '這段期間的市場資料不完整' },
  prices_incomplete: { short: '價格缺漏', full: '這段期間有日 K 分區讀取失敗' },
  events_missing: { short: '除權息不可用', full: ADJUSTMENT_REASONS.events_missing },
  events_unreadable: { short: '除權息不可用', full: ADJUSTMENT_REASONS.events_unreadable },
  coverage_missing: { short: '除權息不可用', full: ADJUSTMENT_REASONS.coverage_missing },
}

function fmtRatio(v: number | null | undefined) {
  return v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(1)}%`
}

function errorText(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message : ''
  if (message.includes('pick_history_unreadable')) return '選股紀錄目前無法讀取，請稍後再試。'
  if (message.includes('pick_prices_unreadable')) return '日 K 價格資料目前無法讀取，請稍後再試。'
  return fallback
}

function sampleTotal(strategies: PickStrategyStats[], horizon: PickHorizon) {
  return strategies.reduce((sum, s) => sum + (s.horizons[horizon]?.sample_count ?? 0), 0)
}

/** T+20 when it has samples, else the longest horizon that does, else T+1. */
export function defaultPickHorizon(strategies: PickStrategyStats[]): PickHorizon {
  for (let i = HORIZONS.length - 1; i >= 0; i--) {
    if (sampleTotal(strategies, HORIZONS[i]) > 0) return HORIZONS[i]
  }
  return HORIZONS[0]
}

function excludedTotal(s: PickStrategyStats) {
  return s.excluded.recorded_late + s.excluded.universe_incomplete + s.excluded.manifest_missing
}

type Preview = { symbol: string; name?: string }

interface Props {
  nameById: Map<string, string>
  onPreview: (preview: Preview) => void
}

export function StrategyPickPerformance({ nameById, onPreview }: Props) {
  const [chosen, setChosen] = useState<PickHorizon | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const summary = useQuery({
    queryKey: QK.screenerPickPerformance,
    queryFn: () => api.screenerPickPerformance(),
    staleTime: STALE_TIME,
  })

  const strategies = useMemo(
    () => [...(summary.data?.strategies ?? [])].sort((a, b) =>
      (nameById.get(a.strategy_id) ?? a.strategy_id)
        .localeCompare(nameById.get(b.strategy_id) ?? b.strategy_id, 'zh-Hant')),
    [summary.data, nameById],
  )

  if (summary.isLoading) {
    return (
      <div className="flex items-center gap-2 py-6 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取事後統計…
      </div>
    )
  }
  if (!summary.data) {
    return (
      <p className="py-6 text-xs text-muted">
        {errorText(summary.error, '目前無法讀取事後統計。')}
      </p>
    )
  }

  const data = summary.data
  const enabled = new Set(HORIZONS.filter(h => sampleTotal(strategies, h) > 0))
  const horizon = chosen && enabled.has(chosen) ? chosen : defaultPickHorizon(strategies)

  return (
    <div className="space-y-3">
      <MethodNote data={data} />
      <HistoricalBackfill />

      {data.adjustment.status !== 'ok' && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded border border-amber-900/40 bg-amber-950/20 px-2 py-1.5 text-[11px] leading-5 text-amber-200/80"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            除權息資料目前無法使用
            {data.adjustment.reason && `（${ADJUSTMENT_REASONS[data.adjustment.reason] ?? data.adjustment.reason}）`}
            ，報酬暫不計算，以免除權息日的價格跳動被算成漲跌。
          </span>
        </div>
      )}

      {strategies.length === 0 ? (
        <p className="py-6 text-center text-xs text-muted">
          尚無選股紀錄。每個交易日盤後的策略選股會記錄下來，T+1 開盤後才開始計算報酬。
        </p>
      ) : (
        <section className="rounded-card border border-border bg-surface p-3">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-[11px] text-secondary">持有期</span>
            <div className="flex gap-1">
              {HORIZONS.map(h => {
                const count = sampleTotal(strategies, h)
                return (
                  <button
                    key={h}
                    type="button"
                    disabled={count === 0}
                    onClick={() => setChosen(h)}
                    aria-pressed={horizon === h}
                    title={`樣本 ${count} 筆`}
                    className={cn(
                      'rounded-md border px-2 py-0.5 font-mono text-[11px] transition-colors',
                      count === 0
                        ? 'cursor-not-allowed border-border/50 text-muted/40'
                        : horizon === h
                          ? 'cursor-pointer border-accent/60 bg-accent/10 text-accent'
                          : 'cursor-pointer border-border text-muted hover:text-foreground',
                    )}
                  >
                    T+{h}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-[11px]">
              <thead>
                <tr className="border-b border-border text-left text-[10px] text-muted">
                  <th className="py-1 pr-2 font-normal">策略名稱</th>
                  <th className="py-1 pr-2 text-right font-normal">訊號筆數（訊號日數）</th>
                  <th className="py-1 pr-2 text-right font-normal">未成交（開盤漲停／無成交）</th>
                  <th className="py-1 pr-2 text-right font-normal">樣本數</th>
                  <th className="py-1 pr-2 text-right font-normal">平均報酬</th>
                  <th className="py-1 pr-2 text-right font-normal">中位數報酬</th>
                  <th className="py-1 text-right font-normal">報酬為正比例</th>
                </tr>
              </thead>
              <tbody>
                {strategies.map(s => (
                  <StrategyRow
                    key={s.strategy_id}
                    stats={s}
                    name={nameById.get(s.strategy_id) ?? s.strategy_id}
                    horizon={horizon}
                    open={expanded === s.strategy_id}
                    onToggle={() => setExpanded(prev => (prev === s.strategy_id ? null : s.strategy_id))}
                    onPreview={onPreview}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted">
            訊號日數少於 {LOW_SAMPLE_SIGNAL_DAYS} 日的策略標為「樣本少」並以灰字顯示，因為同一天的選股彼此高度相關。連續多日入選的標的每天各算一筆。點一列可展開最近 {DETAIL_LIMIT} 筆紀錄。
          </p>
        </section>
      )}
    </div>
  )
}

function MethodNote({ data }: { data: PickPerformanceSummary }) {
  const range = data.first_signal_date
    ? `訊號日 ${data.first_signal_date} ～ ${data.last_signal_date ?? '—'}`
    : '尚無訊號日'
  return (
    <div className="space-y-0.5 text-[11px] leading-5 text-secondary">
      <p>
        以 T+1 開盤價為計算起點，已含除權息調整，未扣交易成本
        <span className="ml-2 font-mono text-[10px] text-muted">
          資料區間：{range}；價格資料至 {data.prices_through ?? '—'}
          {data.adjustment.status === 'ok' && data.adjustment.covered_through
            && `；除權息資料涵蓋至 ${data.adjustment.covered_through}`}
        </span>
      </p>
      <p className="text-[10px] text-muted">
        成交是以訊號次一交易日開盤價模擬買入；開盤漲停或無成交會列為未成交，並非帳戶的實際成交。
        T+k 報酬＝訊號後第 k 個交易日的復權收盤價 ÷ 次日復權開盤價 − 1；T+1 是次日開盤到收盤。
        報酬為毛報酬：未扣交易成本、未計滑點、未模擬出場可成交性。
        以下為過去選股紀錄的統計結果，計算方式固定，不構成買賣建議，也不代表未來表現。
      </p>
    </div>
  )
}

function HistoricalBackfill() {
  const client = useQueryClient()
  const [days, setDays] = useState('5')
  const [jobId, setJobId] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: () => api.screenerPickBackfill(Number(days)),
    onSuccess: job => {
      client.setQueryData(QK.screenerPickBackfill(job.job_id), job)
      setJobId(job.job_id)
      void client.invalidateQueries({ queryKey: QK.screenerPickBackfill(job.job_id) })
    },
  })
  const status = useQuery({
    queryKey: QK.screenerPickBackfill(jobId ?? ''),
    queryFn: () => api.screenerPickBackfillStatus(jobId!),
    enabled: jobId !== null,
    refetchInterval: query => query.state.data?.status === 'running' ? 1500 : false,
  })
  const job = status.data ?? (jobId ? mutation.data : undefined)
  const active = mutation.isPending || (job?.status === 'running' && !status.isError)
  const valid = Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 250
  useEffect(() => {
    if (job && job.status !== 'running') {
      void client.invalidateQueries({ queryKey: QK.screenerPickPerformanceAll })
    }
  }, [client, job?.job_id, job?.status])
  return (
    <section className="space-y-1 rounded border border-border p-2 text-[11px] text-secondary">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="pick-backfill-days">歷史回算交易日數</label>
        <input id="pick-backfill-days" type="number" min={1} max={250} value={days}
          disabled={active} onChange={event => setDays(event.target.value)}
          className="w-20 rounded border border-border bg-base px-2 py-1" />
        <button type="button" disabled={active || !valid} onClick={() => mutation.mutate()}
          className="rounded border border-border px-2 py-1 disabled:opacity-50">
          {active ? '正在補算…' : '補算歷史訊號'}
        </button>
        {active && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      </div>
      <p className="text-[10px] text-muted">
        補算最新完整日 K 以前 {valid ? days : 'N'} 個本機行情交易日（不含最新日，最多 250 日），只補缺少的策略日。
        依目前策略、參數與股票池重建歷史，並非當時已記錄的訊號；零訊號的計算日不增加訊號日數。
      </p>
      {job && (
        <p role="status">
          {job.status === 'running' ? '計算進度' : job.status === 'completed' ? (job.failures.length ? '補算結束（部分未能計算）' : '補算完成') : '補算失敗'}：
          {job.completed_days}／{job.dates.length} 日，新增 {job.written_runs} 策略日、保留 {job.existing_runs} 策略日，
          其中零訊號 {job.zero_signal_runs} 策略日。
          {job.dates.length < job.requested_days && ` 本機僅有 ${job.dates.length} 個可回算交易日。`}
          {!!job.scoring_unavailable_runs && ` 另 ${job.scoring_unavailable_runs} 策略日缺評分資料，保留完整候選但不顯示分數。`}
        </p>
      )}
      {!!job?.failures.length && (
        <details className="text-amber-400">
          <summary>部分尚未補算：有 {job.failures.length} 個策略日未能計算</summary>
          <ul className="max-h-40 overflow-auto">
            {job.failures.map((failure, i) => <li key={i}>
              {failure.date} {failure.strategy_id}：{BACKFILL_REASONS[failure.reason] ?? '計算失敗'}
              {failure.missing_fields?.length ? `（${fieldLabels(failure.missing_fields)}）` : ''}
            </li>)}
          </ul>
        </details>
      )}
      {(mutation.isError || status.isError || job?.status === 'failed') && (
        <p role="alert" className="text-amber-400">歷史回算暫時無法完成，已寫入的歷史會保留。請確認行情資料後重試。</p>
      )}
    </section>
  )
}

const BACKFILL_REASONS: Record<string, string> = {
  daily_incomplete: '當日行情不完整', fingerprint_unavailable: '無法確認策略版本',
  input_unavailable: '策略所需資料不足', future_input: '歷史資料日期超出訊號日',
  universe_incomplete: '策略所需市場資料不完整',
  strategy_failed: '策略執行失敗', as_of_mismatch: '訊號日期不符',
  invalid_result_rows: '策略結果含不符訊號日或格式錯誤的資料',
  scoring_selection_unavailable: '評分資料不足，無法可靠決定候選',
}

function fieldLabels(fields: string[]) {
  const labels: Record<string, string> = { turnover_rate: '換手率', float_shares: '歷史股本', total_shares: '歷史股本', close: '收盤價', volume: '成交量', history: '歷史行情' }
  return fields.map(field => labels[field] ?? '必要指標').join('、')
}

interface RowProps {
  stats: PickStrategyStats
  name: string
  horizon: PickHorizon
  open: boolean
  onToggle: () => void
  onPreview: (preview: Preview) => void
}

function StrategyRow({ stats: s, name, horizon, open, onToggle, onPreview }: RowProps) {
  const h = s.horizons[horizon]
  const faint = s.signal_days < LOW_SAMPLE_SIGNAL_DAYS
  const excluded = excludedTotal(s)
  const horizonExcluded = h ? h.adjustment_not_covered + h.data_incomplete + h.prices_incomplete : 0
  const entryNotes = [
    s.pending_entry_count > 0 && `待 T+1 開盤 ${s.pending_entry_count}`,
    s.entry_prices_incomplete_count + s.entry_data_incomplete_count > 0
      && `起點資料不完整 ${s.entry_prices_incomplete_count + s.entry_data_incomplete_count}`,
  ].filter(Boolean)

  return (
    <Fragment>
      <tr
        onClick={onToggle}
        className={cn(
          'cursor-pointer border-b border-border/50 align-top transition-colors hover:bg-elevated/60',
          faint ? 'text-muted' : 'text-foreground',
        )}
      >
        <td className="py-1.5 pr-2">
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${name} 的選股紀錄`}
            className="flex items-baseline gap-1.5 text-left"
          >
            <span className="text-[12px]">{name}</span>
            <span className="font-mono text-[9px] text-muted">{s.strategy_id}</span>
            {faint && (
              <span className="rounded border border-border px-1 text-[9px] text-muted">樣本少</span>
            )}
            <span className="text-muted">{open ? '▴' : '▾'}</span>
          </button>
          {excluded > 0 && (
            <div
              className="mt-0.5 text-[10px] text-muted"
              title={`延遲紀錄 ${s.excluded.recorded_late}、市場不完整 ${s.excluded.universe_incomplete}、清單缺漏 ${s.excluded.manifest_missing}`}
            >
              延遲紀錄／市場不完整／清單缺漏，未計入 {excluded} 筆
            </div>
          )}
          {s.history_degraded_count > 0 && (
            <div className="mt-0.5 text-[10px] text-amber-400/90">
              其中 {s.history_degraded_count} 筆選股產生時歷史資料讀取不完整，結果可能與資料完整時不同
            </div>
          )}
          {!!s.reconstructed_count && (
            <div className="mt-0.5 text-[10px] text-muted">依目前策略重建歷史 {s.reconstructed_count} 筆</div>
          )}
          {!!s.scoring_unavailable_count && (
            <div className="mt-0.5 text-[10px] text-amber-400/90">
              {s.scoring_unavailable_count} 筆缺評分資料；保留完整候選與報酬，列序不代表有效評分排名
            </div>
          )}
          {!!s.input_unavailable_days && (
            <div className="mt-0.5 text-[10px] text-amber-400/90">{s.input_unavailable_days} 日缺必要篩選資料，未能計算歷史訊號</div>
          )}
        </td>
        <td className="py-1.5 pr-2 text-right font-mono">
          {s.signal_count}（{s.signal_days} 日）
          {entryNotes.length > 0 && (
            <div className="text-[10px] text-muted">{entryNotes.join('、')}</div>
          )}
          {s.absent_count > 0 && (
            <div
              className="text-[10px] text-muted"
              title="至最新價格日仍無資料（可能停牌或下市），已計入統計，報酬沿用最近一筆收盤價"
            >
              缺席 {s.absent_count}
            </div>
          )}
        </td>
        <td className="py-1.5 pr-2 text-right font-mono">
          {s.not_filled_limit_up}／{s.not_filled_no_trade}
          {s.limit_check_unknown > 0 && (
            <div className="text-[10px] text-muted" title="漲停價資料不足，無法判斷 T+1 開盤是否漲停">
              漲停未判定 {s.limit_check_unknown}
            </div>
          )}
        </td>
        <td className="py-1.5 pr-2 text-right font-mono">
          {h?.sample_count ?? 0}
          {h && h.sample_count > 0 && (
            <span className="ml-1 text-[10px] text-muted">（{h.sample_days} 日）</span>
          )}
          {h && horizonExcluded > 0 && (
            <div
              className="text-[10px] text-muted"
              title={`除權息未涵蓋 ${h.adjustment_not_covered}、市場資料不完整 ${h.data_incomplete}、價格缺漏 ${h.prices_incomplete}`}
            >
              另 {horizonExcluded} 筆未計入
            </div>
          )}
          {h && h.pending_count > 0 && (
            <div className="text-[10px] text-muted">未滿期 {h.pending_count}</div>
          )}
          {h && h.stale_exit_count > 0 && (
            <div className="text-[10px] text-muted" title="T+k 當日無成交，沿用最近一筆收盤價">
              沿用前收 {h.stale_exit_count}
            </div>
          )}
        </td>
        <td className={cn('py-1.5 pr-2 text-right font-mono', faint ? 'text-muted' : priceColorClass(h?.mean_return))}>
          {fmtPct(h?.mean_return)}
        </td>
        <td className={cn('py-1.5 pr-2 text-right font-mono', faint ? 'text-muted' : priceColorClass(h?.median_return))}>
          {fmtPct(h?.median_return)}
        </td>
        <td className="py-1.5 text-right font-mono">{fmtRatio(h?.positive_ratio)}</td>
      </tr>
      {open && (
        <tr className="border-b border-border/50">
          <td colSpan={7} className="bg-base/40 px-2 py-2">
            <PickRecords strategyId={s.strategy_id} horizon={horizon} onPreview={onPreview} />
          </td>
        </tr>
      )}
    </Fragment>
  )
}

function ReturnCell({ ret, selected }: { ret: PickHorizonReturn | undefined; selected: boolean }) {
  const value = ret?.value ?? null
  if (value == null || Number.isNaN(value)) {
    const reason = ret?.reason ? RETURN_REASONS[ret.reason] : undefined
    return (
      <td className="py-1 pr-2 text-right text-[10px] text-muted" title={reason?.full}>
        {reason?.short ?? '—'}
      </td>
    )
  }
  return (
    <td
      className={cn('py-1 pr-2 text-right font-mono', priceColorClass(value), selected && 'font-semibold')}
      title={ret?.stale_exit ? `當日無成交，沿用 ${ret.stale_days} 個交易日前的收盤價` : undefined}
    >
      {fmtPct(value)}
      {ret?.stale_exit && '*'}
    </td>
  )
}

function recordNote(r: PickRecord) {
  if (r.excluded_reason) return `未計入：${EXCLUDED_LABELS[r.excluded_reason] ?? r.excluded_reason}`
  if (r.absent_since) return `${r.absent_since} 起無資料`
  if (r.calculation_status === 'scoring_unavailable') return `缺評分資料（${fieldLabels(r.missing_fields ?? [])}）；列序不代表有效排名`
  if (r.provenance === 'reconstructed') return '依目前策略重建歷史'
  return null
}

function PickRecords({ strategyId, horizon, onPreview }: {
  strategyId: string
  horizon: PickHorizon
  onPreview: (preview: Preview) => void
}) {
  const detail = useQuery({
    queryKey: QK.screenerPickPerformanceDetail(strategyId, DETAIL_LIMIT),
    queryFn: () => api.screenerPickPerformanceDetail(strategyId, DETAIL_LIMIT),
    staleTime: STALE_TIME,
  })

  if (detail.isLoading) {
    return (
      <div className="flex items-center gap-2 py-2 text-[11px] text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />正在讀取選股紀錄…
      </div>
    )
  }
  if (!detail.data) {
    return <p className="py-2 text-[11px] text-muted">{errorText(detail.error, '目前無法讀取這支策略的選股紀錄。')}</p>
  }
  const { rows, total, signal_days, calculation_issues = [] } = detail.data
  if (rows.length === 0) {
    return <div className="py-2 text-[11px] text-muted">
      {calculation_issues.filter(issue => issue.status === 'input_unavailable').map(issue => (
        <p key={issue.date}>{issue.date} 缺必要篩選資料（{fieldLabels(issue.missing_fields)}），未能計算，並非零訊號。</p>
      ))}
      <p>這支策略沒有選股紀錄。</p>
    </div>
  }

  return (
    <div>
      {calculation_issues.filter(issue => issue.status === 'input_unavailable').map(issue => (
        <p key={issue.date} className="mb-1 text-[10px] text-amber-400">{issue.date} 缺必要篩選資料（{fieldLabels(issue.missing_fields)}），未能計算，並非零訊號。</p>
      ))}
      <p className="mb-1 text-[10px] text-muted">
        最近 {rows.length} 筆，共 {total} 筆、{signal_days} 個訊號日。
      </p>
      <div className="max-h-96 overflow-auto">
        <table className="w-full min-w-[640px] text-[11px]">
          <thead>
            <tr className="border-b border-border text-left text-[10px] text-muted">
              <th className="py-1 pr-2 font-normal">訊號日</th>
              <th className="py-1 pr-2 font-normal">標的</th>
              <th className="py-1 pr-2 text-right font-normal">T+1 開盤價</th>
              <th className="py-1 pr-2 font-normal">狀態</th>
              {HORIZONS.map(h => (
                <th
                  key={h}
                  className={cn('py-1 pr-2 text-right font-normal', h === horizon && 'text-accent')}
                >
                  T+{h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const note = recordNote(r)
              return (
                <tr
                  key={`${r.signal_date}-${r.symbol}`}
                  className={cn('border-b border-border/30', r.excluded_reason ? 'text-muted' : 'text-foreground')}
                >
                  <td className="py-1 pr-2 font-mono">{r.signal_date}</td>
                  <td className="py-1 pr-2">
                    <button
                      type="button"
                      onClick={() => onPreview({ symbol: r.symbol, name: r.name ?? undefined })}
                      aria-label={`查看 ${r.name || r.symbol} 走勢`}
                      className="flex items-baseline gap-1 text-left hover:text-accent"
                    >
                      <span>{r.name || r.symbol}</span>
                      <span className="font-mono text-[9px] text-muted">{r.symbol}</span>
                    </button>
                  </td>
                  <td className="py-1 pr-2 text-right font-mono">{fmtPrice(r.entry_price_raw)}</td>
                  <td className="py-1 pr-2 text-[10px]">
                    {STATUS_LABELS[r.status] ?? r.status}
                    {note && <div className="text-muted">{note}</div>}
                  </td>
                  {HORIZONS.map(h => (
                    <ReturnCell key={h} ret={r.returns?.[h]} selected={h === horizon} />
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[10px] text-muted">
        T+1 開盤價為未調整的原始價。標 * 的報酬表示該日無成交，沿用最近一筆收盤價。
      </p>
    </div>
  )
}
