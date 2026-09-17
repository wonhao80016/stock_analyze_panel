import { useState, useRef, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, ListChecks, BellRing, Globe, Trash2 } from 'lucide-react'
import { api, type MonitorRule, type TaiwanRealtimeQuote } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { markSeen, leaveMonitorPage } from '@/lib/monitorBadge'
import { TaiwanIndexBar } from '@/components/monitor/TaiwanIndexBar'
import { TaiwanQuotePanel } from '@/components/monitor/TaiwanQuotePanel'
import { TaiwanAlertsList } from '@/components/monitor/TaiwanAlertsList'
import { GenericRulesList } from '@/components/monitor/GenericRulesList'
import { RuleEditor } from '@/components/monitor/RuleEditor'
import { Modal } from '@/components/Modal'

// Phase 8C-D — Legacy Product Removal: 「A 股策略監控」分頁與其專屬觸發記錄/
// 規則管理 UI (含行業/概念標籤設定) 已隨產品介面正式移除。strategy_alert SSE、
// 通用告警串流 (alertsQuery/api.alertsList)、通用監控規則 API
// (monitorRuleSave/monitorRuleDelete/monitorRulesList, 由 components/monitor/
// RuleEditor.tsx 經 StockPreviewDialog 等處繼續重用)、Taiwan quote/monitor
// merge 完全未動。Monitor 最終只剩台股即時監控一種畫面, 不再需要市場切換分頁。
//
// 監控規則合併(2026-09): 原本並排的「台股即時規則」「訊號規則」兩組列表/編輯器
// 已合併為單一 GenericRulesList + RuleEditor —— 兩者過去欄位/後端儲存不同,
// 使用者無從判斷該用哪個; 通用引擎(monitor.py)現已能表達台股引擎原本的全部
// 規則型態(價格/漲跌幅/量能/漲跌停距離/訊號), 不再需要兩套。
export function Monitor() {
  const qc = useQueryClient()
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<MonitorRule | null>(null)
  const [rulePreset, setRulePreset] = useState<Partial<MonitorRule> | undefined>(undefined)

  const rulesQuery = useQuery({
    queryKey: QK.monitorRules,
    queryFn: () => api.monitorRulesList(),
  })
  const ruleCount = rulesQuery.data?.rules?.length ?? 0

  const openNewRule = (preset?: Partial<MonitorRule>) => {
    setEditingRule(null)
    setRulePreset(preset)
    setEditorOpen(true)
  }
  const openEditRule = (rule: MonitorRule) => {
    setEditingRule(rule)
    setRulePreset(undefined)
    setEditorOpen(true)
  }
  const closeEditor = () => {
    setEditorOpen(false)
    setEditingRule(null)
    setRulePreset(undefined)
  }

  // 台股觸發記錄 (與 strategy_alert SSE 共用同一份 alertsQuery/api.alertsList,
  // 只是 Monitor 頁面級的兜底輪詢 — 未讀徽標/即時刷新走既有 monitorBadge/SSE)
  const alertsQuery = useQuery({
    queryKey: QK.alerts(undefined),
    queryFn: () => api.alertsList({ days: 7, limit: 500 }),
    refetchInterval: 10000,
  })
  const total = alertsQuery.data?.total ?? 0

  // Clearing only removes the stored alert history; rule cooldown and dedup state
  // live in the engine's memory and are intentionally left untouched.
  const clearAlertsMut = useMutation({
    mutationFn: () => api.alertsClear(),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.alerts(undefined) }),
  })

  // 進入監控頁: 清零未讀徽標 + 記錄"進入時刻", 之後新增的記錄會閃爍
  // 離開監控頁: 停止同步, 之後新增才計入未讀
  const enterTsRef = useRef<number>(Date.now())
  useEffect(() => {
    enterTsRef.current = Date.now()
    markSeen()
    return () => leaveMonitorPage()
  }, [])

  return (
    <div className="flex flex-col h-full">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-3 pb-1 border-b border-border/40 bg-surface/50">
        <div>
          <h1 className="text-lg font-bold text-foreground flex items-center gap-2">
            <BellRing className="h-5 w-5 text-accent" />
            即時監控中心
          </h1>
          <p className="text-xs text-muted">台股盤中即時行情、深度五檔與邊緣規則觸發</p>
        </div>
        <div className="flex items-center gap-1.5 rounded-xl bg-elevated/60 px-3 py-1.5 border border-border/60 text-xs font-semibold text-accent">
          <Globe className="h-3.5 w-3.5" />
          台股即時監控 (TWSE / TPEx)
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-auto px-5 py-4">
        {/* ===== 台股即時監控區塊 ===== */}
        <div className="mx-auto flex min-h-full max-w-7xl flex-col gap-4">
          {/* Benchmarks: TWSE weighted index and TPEx index */}
          <TaiwanIndexBar />

          {/* 上部: 台股即時行情 + 五檔盤口 */}
          <div className="h-auto">
            <TaiwanQuotePanel
              onAddRuleForSymbol={(q: TaiwanRealtimeQuote) => openNewRule({
                name: `${q.name} 監控`,
                type: 'price',
                scope: 'symbols',
                symbols: [q.symbol],
              })}
            />
          </div>

          {/* 下部: 左右雙欄 (左: 警報觸發記錄, 右: 監控規則列表) */}
          {/* min-h keeps both panels usable once the index cards take vertical space. */}
          <div className="flex min-h-[300px] flex-1 flex-col gap-4 lg:flex-row">
            {/* 左欄: 台股告警觸發紀錄 */}
            <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-surface/40 shadow-lg shadow-black/5">
              <div className="flex items-center justify-between border-b border-border/60 bg-surface/60 px-4 py-2.5">
                <SectionHeader icon={BellRing} title="台股觸發記錄" />
                <div className="flex items-center gap-2">
                  <span className="rounded-md bg-elevated/50 px-1.5 py-0.5 text-[10px] font-medium text-muted">
                    {total} 筆事件
                  </span>
                  <button
                    onClick={() => {
                      if (window.confirm(`確定清空全部 ${total} 筆觸發記錄?\n\n此操作無法復原。規則本身與冷卻狀態不受影響。`)) {
                        clearAlertsMut.mutate()
                      }
                    }}
                    disabled={total === 0 || clearAlertsMut.isPending}
                    title={total === 0 ? '目前沒有觸發記錄' : '清空全部觸發記錄'}
                    className="inline-flex h-6 w-6 items-center justify-center rounded-lg border border-border/60 bg-surface text-muted transition-all hover:border-danger/40 hover:text-danger hover:shadow-sm cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-border/60 disabled:hover:text-muted"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-3.5">
                <TaiwanAlertsList alerts={alertsQuery.data?.alerts || []} />
              </div>
            </section>

            {/* 右欄: 監控規則管理 — 統一列表 + 統一編輯器, 不再區分「台股/訊號」兩組引擎 */}
            <section className="flex min-h-0 w-full flex-col overflow-hidden rounded-xl border border-border bg-surface/40 shadow-lg shadow-black/5 lg:w-[420px] lg:shrink-0">
              <div className="flex items-center justify-between border-b border-border/60 bg-surface/60 px-4 py-2.5">
                <div className="flex items-center gap-1.5">
                  <SectionHeader icon={ListChecks} title="監控規則" />
                  <span className="rounded-md bg-elevated/50 px-1.5 py-0.5 text-[10px] font-medium text-muted">
                    {ruleCount} 條
                  </span>
                </div>
                <button
                  onClick={() => openNewRule()}
                  title="新建監控規則"
                  className="inline-flex h-6 w-6 items-center justify-center rounded-lg border border-border/60 bg-surface text-muted transition-all hover:border-accent/40 hover:text-accent hover:shadow-sm cursor-pointer"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-3.5">
                <GenericRulesList onEdit={openEditRule} />
              </div>
            </section>
          </div>
        </div>
      </div>

      {editorOpen && (
        <Modal
          onClose={closeEditor}
          ariaLabel={editingRule ? '編輯監控規則' : '新建監控規則'}
          panelClassName="w-[92vw] max-w-2xl max-h-[90vh] overflow-y-auto"
        >
          <RuleEditor
            rule={editingRule}
            preset={rulePreset}
            onClose={closeEditor}
            onSaved={closeEditor}
          />
        </Modal>
      )}
    </div>
  )
}

function SectionHeader({ icon: Icon, title }: { icon: any; title: string }) {
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <Icon className="h-4 w-4 text-accent" />
      <h2 className="text-sm font-semibold text-foreground whitespace-nowrap">{title}</h2>
    </div>
  )
}
