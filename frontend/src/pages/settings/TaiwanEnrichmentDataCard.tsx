import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  RefreshCw,
  Calendar,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Users,
  Layers,
  XCircle,
} from 'lucide-react'
import {
  api,
  type TaiwanEnrichmentStatus,
  type TaiwanEnrichmentBackfillJobState,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'

const RANGE_OPTIONS = [
  { days: 20, label: '近 20 個交易日' },
  { days: 60, label: '近 60 個交易日' },
  { days: 120, label: '近 120 個交易日' },
] as const

const DATASET_LABEL: Record<string, string> = {
  institutional: '三大法人',
  margin: '融資融券',
}

function DatasetStatusTile({
  icon,
  label,
  status,
}: {
  icon: React.ReactNode
  label: string
  status: { has_data: boolean; day_count: number; earliest: string | null; latest: string | null }
}) {
  return (
    <div className="rounded-lg border border-border/50 bg-elevated/30 p-3">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5 text-muted text-xs">
          {icon}
          <span>{label}</span>
        </div>
        {status.has_data ? (
          <span className="inline-flex items-center gap-1 text-[9px] text-emerald-500 bg-emerald-500/10 px-1.5 py-0.5 rounded font-medium">
            <CheckCircle2 className="h-2.5 w-2.5" /> 有資料
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-[9px] text-amber-500 bg-amber-500/10 px-1.5 py-0.5 rounded font-medium">
            <AlertCircle className="h-2.5 w-2.5" /> 缺資料
          </span>
        )}
      </div>
      <div className="text-sm font-semibold text-foreground">
        {status.day_count.toLocaleString()} 天
      </div>
      <div className="text-[11px] text-muted mt-0.5 font-mono">
        {status.earliest && status.latest ? `${status.earliest} ~ ${status.latest}` : '無資料'}
      </div>
    </div>
  )
}

export function TaiwanEnrichmentDataCard() {
  const qc = useQueryClient()
  const [selectedDays, setSelectedDays] = useState<number>(60)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [activeJob, setActiveJob] = useState<TaiwanEnrichmentBackfillJobState | null>(null)

  const {
    data: status,
    isLoading: isStatusLoading,
    refetch: refetchStatus,
  } = useQuery<TaiwanEnrichmentStatus>({
    queryKey: QK.taiwanEnrichmentStatus,
    queryFn: api.taiwanEnrichmentStatus,
    staleTime: 30_000,
  })

  const backfillMutation = useMutation({
    mutationFn: (days: number) => api.taiwanEnrichmentBackfillRun(days),
    onSuccess: (res) => {
      setActiveJobId(res.job_id)
      toast('三大法人與融資融券回補任務已啟動', 'success')
    },
    onError: (err: any) => {
      toast(`啟動回補失敗: ${err.message || err}`, 'error')
    },
  })

  useEffect(() => {
    if (!activeJobId) return

    let cancelled = false
    let timerId: any = null

    const checkProgress = async () => {
      try {
        const job = await api.taiwanEnrichmentBackfillJob(activeJobId)
        if (cancelled) return
        setActiveJob(job)

        if (job.status === 'success') {
          toast(job.message || '回補完成', 'success')
          qc.invalidateQueries({ queryKey: QK.taiwanEnrichmentStatus })
          qc.invalidateQueries({ queryKey: QK.taiwanDataStatus })
          setActiveJobId(null)
          return
        }

        if (job.status === 'failed') {
          toast(job.error || job.message || '回補失敗', 'error')
          setActiveJobId(null)
          return
        }

        timerId = setTimeout(checkProgress, 800)
      } catch (err: any) {
        if (!cancelled) {
          timerId = setTimeout(checkProgress, 2000)
        }
      }
    }

    checkProgress()

    return () => {
      cancelled = true
      if (timerId) clearTimeout(timerId)
    }
  }, [activeJobId, qc])

  const isJobRunning =
    activeJobId !== null ||
    (activeJob !== null && activeJob.status !== 'success' && activeJob.status !== 'failed')

  const bothReady = status?.institutional.has_data && status?.margin.has_data
  const result = activeJob?.status === 'success' ? activeJob.result : null

  return (
    <section className="rounded-card border border-border bg-surface p-6">
      <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
            <Users className="h-5 w-5 text-accent" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-foreground">
                三大法人與融資融券資料
              </h3>
              {isStatusLoading ? null : bothReady ? (
                <span className="inline-flex items-center gap-1 text-[10px] text-emerald-500 bg-emerald-500/10 px-2 py-0.5 rounded font-medium">
                  <CheckCircle2 className="h-3 w-3" /> 資料已就緒
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[10px] text-amber-500 bg-amber-500/10 px-2 py-0.5 rounded font-medium">
                  <AlertCircle className="h-3 w-3" /> 需回補
                </span>
              )}
            </div>
            <p className="text-xs text-secondary mt-1 leading-relaxed">
              歷史資料包僅包含日 K 線，三大法人與融資融券只能透過每日排程逐日累積。
              首次使用或排程曾經中斷時，這兩類資料會大量缺漏，請用下方按鈕手動回補歷史區間。
            </p>
          </div>
        </div>

        <button
          type="button"
          disabled={isStatusLoading}
          onClick={() => refetchStatus()}
          title="重新檢查本地資料狀態"
          className="p-1.5 text-muted hover:text-foreground rounded hover:bg-elevated/60 transition-colors"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isStatusLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {isStatusLoading ? (
        <div className="py-6 flex items-center justify-center gap-2 text-xs text-muted">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          正在檢查本地覆蓋狀況...
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {status && (
              <>
                <DatasetStatusTile
                  icon={<Users className="h-3.5 w-3.5" />}
                  label="三大法人"
                  status={status.institutional}
                />
                <DatasetStatusTile
                  icon={<Layers className="h-3.5 w-3.5" />}
                  label="融資融券"
                  status={status.margin}
                />
              </>
            )}
          </div>

          {isJobRunning && activeJob ? (
            <div className="rounded-lg border border-accent/20 bg-accent/5 p-4 space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-accent flex items-center gap-2">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {activeJob.stage}
                  {activeJob.current_dataset && (
                    <span className="text-muted">
                      ({DATASET_LABEL[activeJob.current_dataset] ?? activeJob.current_dataset})
                    </span>
                  )}
                </span>
                <span className="font-mono text-muted">
                  {activeJob.total_days > 0 ? `${activeJob.day_index}/${activeJob.total_days} 天 · ` : ''}
                  {activeJob.progress}%
                </span>
              </div>
              <div className="w-full bg-border rounded-full h-2 overflow-hidden">
                <div
                  className="bg-accent h-2 rounded-full transition-all duration-300 ease-out"
                  style={{ width: `${Math.max(5, activeJob.progress)}%` }}
                />
              </div>
              <p className="text-[11px] text-secondary font-mono">
                {activeJob.current_date ? `目前處理: ${activeJob.current_date} · ` : ''}
                {activeJob.message}
              </p>
            </div>
          ) : (
            <div className="flex items-center gap-3 flex-wrap pt-1">
              <div className="inline-flex shrink-0 items-center rounded border border-border bg-elevated p-0.5" aria-label="回補區間">
                {RANGE_OPTIONS.map(opt => (
                  <button
                    key={opt.days}
                    type="button"
                    aria-pressed={selectedDays === opt.days}
                    onClick={() => setSelectedDays(opt.days)}
                    className={`h-6 rounded px-2 text-[11px] font-medium transition-colors ${
                      selectedDays === opt.days
                        ? 'bg-accent/20 text-accent'
                        : 'text-muted hover:text-secondary'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              <button
                type="button"
                disabled={backfillMutation.isPending}
                onClick={() => backfillMutation.mutate(selectedDays)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-btn bg-accent text-white text-xs font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
              >
                {backfillMutation.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" />
                )}
                {backfillMutation.isPending ? '正在啟動回補...' : '開始回補'}
              </button>
            </div>
          )}

          {result && (
            <div className="rounded-lg border border-border/50 bg-elevated/30 p-3 space-y-2 text-[11px]">
              <div className="font-medium text-foreground">上次回補結果</div>
              {(['institutional', 'margin'] as const).map(key => {
                const stats = result[key]
                if (!stats) return null
                return (
                  <div key={key} className="flex items-center justify-between text-muted">
                    <span>{DATASET_LABEL[key]}</span>
                    <span className="font-mono">
                      成功 {stats.dates_fetched} · 跳過 {stats.dates_skipped} · 失敗{' '}
                      <span className={stats.failed_dates.length > 0 ? 'text-danger' : undefined}>
                        {stats.failed_dates.length}
                      </span>
                    </span>
                  </div>
                )
              })}
              {(['institutional', 'margin'] as const).some(key => result[key]?.failed_dates.length) && (
                <div className="pt-1 border-t border-border/40 space-y-1">
                  {(['institutional', 'margin'] as const).map(key =>
                    result[key]?.failed_dates.map(fd => (
                      <div key={`${key}-${fd.date}`} className="flex items-center gap-1.5 text-danger">
                        <XCircle className="h-3 w-3 shrink-0" />
                        <span className="font-mono">{DATASET_LABEL[key]} {fd.date}</span>
                        <span className="text-muted truncate">{fd.error}</span>
                      </div>
                    )),
                  )}
                </div>
              )}
            </div>
          )}

          <div className="flex items-center gap-1.5 text-[11px] text-muted pt-2 border-t border-border/40">
            <Calendar className="h-3 w-3" />
            回補範圍依台股交易日曆自動往回推算，已存在的日期會自動跳過，不會重複下載。
          </div>
        </div>
      )}
    </section>
  )
}
