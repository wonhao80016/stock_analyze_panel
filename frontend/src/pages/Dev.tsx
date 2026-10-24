import { useState } from 'react'
import { Loader2, Search, AlertTriangle, CheckCircle2, XCircle, Activity } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import { fmtDate } from '@/lib/format'

// ── 分鐘K探測 (遷移自 MinuteDataProbe) ─────────────────
interface ProbeResult {
  date: string
  rows: number
  source: string
  /** Only the full local copy counts; local_partial and live are fallbacks for an incomplete day. */
  complete: boolean
}

/** The latest `count` weekdays up to today, in local calendar dates; exchange holidays are not known here. */
function recentWeekdays(count: number): string[] {
  const dates: string[] = []
  const d = new Date()
  while (dates.length < count) {
    const weekday = d.getDay()
    if (weekday !== 0 && weekday !== 6) dates.push(fmtDate(d))
    d.setDate(d.getDate() - 1)
  }
  return dates
}

function MinuteProbePanel() {
  const [symbol, setSymbol] = useState('2330.TWSE')
  const [days, setDays] = useState(10)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<ProbeResult[]>([])
  const [error, setError] = useState<string | null>(null)

  const runProbe = async () => {
    const sym = symbol.trim().toUpperCase()
    if (!sym) return
    setLoading(true)
    setError(null)
    setResults([])

    const dates = recentWeekdays(days)

    const out: ProbeResult[] = []
    try {
      for (const date of dates) {
        const r = await api.klineMinute(sym, date)
        const rows = r.rows?.length ?? 0
        out.push({
          date,
          rows,
          source: r.source ?? (rows > 0 ? 'local' : 'none'),
          complete: r.source === 'local',
        })
        setResults([...out])
      }
    } catch (e: any) {
      setError(e?.message ?? String(e))
    } finally {
      setLoading(false)
    }
  }

  const total = results.length
  const completeCount = results.filter((r) => r.complete).length
  const missing = results.filter((r) => !r.complete)

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">分鐘K數據探測</h2>
        <p className="mt-1 text-xs text-muted">
          逐日調用 <code className="px-1 rounded bg-elevated text-secondary">/api/kline/minute</code> 接口，
          檢測每隻股票最近若干天的分鐘K數據是否齊全。本地無數據時會自動走內建數據源實時拉取。
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-btn bg-elevated p-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">股票代碼</label>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="2330.TWSE"
            className="w-44 rounded-btn border border-border bg-base px-3 py-1.5 text-sm text-foreground outline-none focus:border-accent"
            onKeyDown={(e) => e.key === 'Enter' && !loading && runProbe()}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">回溯天數</label>
          <input
            type="number"
            min={1}
            max={30}
            value={days}
            onChange={(e) => setDays(Math.max(1, Math.min(30, Number(e.target.value) || 1)))}
            className="w-24 rounded-btn border border-border bg-base px-3 py-1.5 text-sm text-foreground outline-none focus:border-accent"
          />
        </div>
        <button
          onClick={runProbe}
          disabled={loading || !symbol.trim()}
          className="flex items-center gap-1.5 rounded-btn bg-accent px-4 py-1.5 text-sm font-medium text-base hover:bg-accent/90 disabled:opacity-50 cursor-pointer"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          {loading ? '探測中…' : '開始探測'}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-btn border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {total > 0 && (
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-btn bg-elevated p-3">
            <div className="text-xs text-muted">檢測天數</div>
            <div className="mt-1 text-lg font-semibold text-foreground">{total}</div>
          </div>
          <div className="rounded-btn bg-elevated p-3">
            <div className="text-xs text-muted">完整</div>
            <div className="mt-1 text-lg font-semibold text-emerald-400">{completeCount}</div>
          </div>
          <div className="rounded-btn bg-elevated p-3">
            <div className="text-xs text-muted">不完整或缺失</div>
            <div className="mt-1 text-lg font-semibold text-danger">{missing.length}</div>
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div className="overflow-hidden rounded-btn border border-border">
          <table className="w-full text-sm">
            <thead className="bg-elevated text-xs text-muted">
              <tr>
                <th className="px-4 py-2 text-left font-medium">日期</th>
                <th className="px-4 py-2 text-right font-medium">分鐘K條數</th>
                <th className="px-4 py-2 text-left font-medium">數據來源</th>
                <th className="px-4 py-2 text-center font-medium">狀態</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.date} className="border-t border-border/60">
                  <td className="px-4 py-2 text-foreground">{r.date}</td>
                  <td className="px-4 py-2 text-right tabular-nums text-foreground">{r.rows}</td>
                  <td className="px-4 py-2 text-secondary">
                    <span className="rounded bg-elevated px-1.5 py-0.5 text-xs">{r.source}</span>
                  </td>
                  <td className="px-4 py-2 text-center">
                    {r.complete ? (
                      <span className="inline-flex items-center gap-1 text-emerald-400">
                        <CheckCircle2 className="h-4 w-4" /> 完整
                      </span>
                    ) : r.rows > 0 ? (
                      <span className="inline-flex items-center gap-1 text-warning">
                        <AlertTriangle className="h-4 w-4" /> 不完整
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-danger">
                        <XCircle className="h-4 w-4" /> 缺失
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {missing.length > 0 && (
        <div className="rounded-btn border border-warning/40 bg-warning/10 p-3 text-xs text-foreground">
          <div className="mb-1 flex items-center gap-1.5 font-medium text-warning">
            <AlertTriangle className="h-4 w-4" /> 缺失日期的診斷
          </div>
          <p className="leading-relaxed text-secondary">
            已略過週末；缺失日期若為<span className="text-foreground">國定假日或休市日</span>屬正常；
            若為<span className="text-foreground">停牌日</span>（成交量為 0）也屬正常；
            「不完整」表示本機只有部分分鐘K（local_partial）或改由即時來源補上（live）；
            若為<span className="text-foreground">正常交易日</span>（日K有成交量）卻缺失分鐘K，
            則是內建數據源未提供該日分鐘數據。
          </p>
        </div>
      )}
    </div>
  )
}

export function Dev() {
  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="系統工具"
        subtitle="數據診斷"
        right={
          <div className="flex items-center gap-1.5 rounded-btn bg-elevated px-2.5 py-1 text-xs font-medium text-muted">
            <Activity className="h-3.5 w-3.5" />分鐘K探測
          </div>
        }
      />
      <div className="flex-1 overflow-auto px-5 py-4">
        <div className="mx-auto max-w-3xl space-y-4">
          <MinuteProbePanel />
        </div>
      </div>
    </div>
  )
}
