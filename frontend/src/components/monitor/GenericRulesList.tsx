import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Edit2, ListChecks, Power, Trash2 } from 'lucide-react'

import { api, type MonitorRule } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { cnSignal } from '@/lib/signals'

const RULE_TYPE_LABELS: Record<string, string> = {
  strategy: '策略',
  signal: '訊號',
  price: '價格',
  market: '大盤',
  sector: '板塊',
}

const SCOPE_LABELS: Record<string, string> = {
  symbols: '指定標的',
  all: '全市場',
  sector: '板塊',
  watchlist_group: '自選分組',
}

// change_pct / distance_to_* 一律以小數制儲存(0.01 = 1%), 摘要顯示時換算回百分比,
// 否則使用者看到的會是 0.01 這種難以判讀的原始值。
const PERCENT_DECIMAL_FIELDS = new Set(['change_pct', 'distance_to_upper_limit', 'distance_to_lower_limit'])

function formatConditionValue(field: string, value: number | null | undefined): string {
  if (value == null) return ''
  if (PERCENT_DECIMAL_FIELDS.has(field)) {
    const pct = value * 100
    return `${Number(pct.toFixed(2))}%`
  }
  if (field === 'volume') {
    return value >= 1000 ? `${(value / 1000).toLocaleString()}張` : `${value.toLocaleString()}股`
  }
  return String(value)
}

export function conditionSummary(rule: { conditions?: MonitorRule['conditions']; logic?: MonitorRule['logic'] }): string {
  const parts = (rule.conditions || []).map(c => {
    const field = cnSignal(c.field)
    if (c.op === 'truth') return field
    return `${field} ${c.op} ${formatConditionValue(c.field, c.value)}`.trim()
  })
  if (!parts.length) return '無條件'
  return parts.join(rule.logic === 'or' ? ' 或 ' : ' 且 ')
}

interface GenericRulesListProps {
  onEdit: (rule: MonitorRule) => void
}

export function GenericRulesList({ onEdit }: GenericRulesListProps) {
  const qc = useQueryClient()
  const rulesQuery = useQuery({ queryKey: QK.monitorRules, queryFn: api.monitorRulesList })

  const toggleMut = useMutation({
    mutationFn: (rule: MonitorRule) => api.monitorRuleSetEnabled(rule.id, !rule.enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.monitorRules }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.monitorRuleDelete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.monitorRules }),
  })

  const rules = rulesQuery.data?.rules ?? []

  if (rulesQuery.isLoading) {
    return <div className="p-6 text-center text-xs text-muted">載入中...</div>
  }

  if (!rules.length) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-center text-muted">
        <ListChecks className="h-6 w-6 text-muted/60 mb-2" />
        <p className="text-xs font-medium">尚未建立訊號規則</p>
        <p className="text-[10px] text-muted mt-0.5">於個股預覽視窗的「加監控」建立後會列在這裡</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {toggleMut.error && (
        <div className="rounded border border-danger/30 bg-danger/5 px-2 py-1 text-[11px] text-danger">
          {String((toggleMut.error as Error).message ?? toggleMut.error)}
        </div>
      )}
      {rules.map(r => (
        <motion.div
          key={r.id}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn(
            'group relative flex flex-col rounded-xl border p-3 text-xs transition-all shadow-sm',
            r.enabled
              ? 'border-border/80 bg-surface/90 hover:border-accent/40'
              : 'border-border/40 bg-elevated/20 opacity-60'
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate font-semibold text-foreground">{r.name || r.id}</span>
              <span className="shrink-0 rounded bg-elevated px-1.5 py-0.5 text-[10px] text-muted">
                {RULE_TYPE_LABELS[r.type] || r.type}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                onClick={() => toggleMut.mutate(r)}
                title={r.enabled ? '點擊停用' : '點擊啟用'}
                className={cn(
                  'p-1.5 rounded-lg border transition-all cursor-pointer',
                  r.enabled
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                    : 'border-border text-muted hover:bg-elevated'
                )}
              >
                <Power className="h-3 w-3" />
              </button>
              <button
                onClick={() => onEdit(r)}
                title="編輯規則"
                className="p-1.5 rounded-lg border border-border bg-surface text-muted hover:text-accent hover:border-accent/40 transition-colors cursor-pointer"
              >
                <Edit2 className="h-3 w-3" />
              </button>
              <button
                onClick={() => deleteMut.mutate(r.id)}
                title="刪除規則"
                className="p-1.5 rounded-lg border border-border bg-surface text-muted hover:text-danger hover:border-danger/40 transition-colors cursor-pointer"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="rounded bg-elevated px-1.5 py-0.5 font-medium text-foreground/90">
              {SCOPE_LABELS[r.scope] || r.scope}
            </span>
            {r.scope === 'symbols' && r.symbols?.length > 0 && (
              <span className="font-mono text-accent">{r.symbols.slice(0, 4).join(', ')}{r.symbols.length > 4 ? ` +${r.symbols.length - 4}` : ''}</span>
            )}
            <span className="text-muted">{conditionSummary(r)}</span>
            {r.cooldown_seconds > 0 && (
              <span className="rounded bg-surface border border-border/50 px-1 py-0.5 text-[10px] text-muted">
                冷卻 {r.cooldown_seconds}s
              </span>
            )}
            {r.trigger_mode === 'level' && (
              <span className="rounded bg-surface border border-border/50 px-1 py-0.5 text-[10px] text-muted">
                持續觸發
              </span>
            )}
            {r.conditions?.some(c => c.rearm_value != null) && (
              <span className="rounded bg-surface border border-border/50 px-1 py-0.5 text-[10px] text-muted">
                含遲滯重置
              </span>
            )}
          </div>
          {r.runtime_warning && (
            <div className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-600 dark:text-amber-400">
              {r.runtime_warning}
            </div>
          )}
        </motion.div>
      ))}
    </div>
  )
}
