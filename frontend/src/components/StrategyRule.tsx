import type { StrategyDetail } from '@/lib/api'
import { cnSignal } from '@/lib/signals'
import { cn } from '@/lib/cn'

/** The pieces of a strategy's rule, so the panel and the tooltip cannot diverge.
 *
 * Params come from params_defaults, which /api/strategies has already merged the
 * user's saved overrides into, so this shows the values a run would use rather
 * than the module constants.
 */
export function strategyRuleParts(detail: StrategyDetail) {
  return {
    description: detail.description || '此策略未提供說明。',
    params: (detail.params ?? []).map(p => {
      const v = detail.params_defaults?.[p.id] ?? p.default
      return `${p.label ?? p.id}: ${v ?? '—'}`
    }),
    entry: (detail.entry_signals ?? []).map(s => cnSignal(s)),
    exit: (detail.exit_signals ?? []).map(s => cnSignal(s)),
    risk: [
      detail.stop_loss != null ? `停損 ${(detail.stop_loss * 100).toFixed(1)}%` : null,
      detail.take_profit != null ? `停利 ${(detail.take_profit * 100).toFixed(1)}%` : null,
      detail.trailing_stop != null ? `移動停損 ${(detail.trailing_stop * 100).toFixed(1)}%` : null,
      detail.max_hold_days != null ? `最多持有 ${detail.max_hold_days} 天` : null,
    ].filter(Boolean) as string[],
  }
}

/** Plain-text form for a native tooltip, where markup is not available. */
export function strategyRuleText(detail: StrategyDetail): string {
  const p = strategyRuleParts(detail)
  return [
    detail.name,
    p.description,
    p.entry.length ? `進場訊號：${p.entry.join('、')}` : null,
    p.exit.length ? `出場訊號：${p.exit.join('、')}` : null,
    p.risk.length ? `風控：${p.risk.join('、')}` : null,
    p.params.length ? p.params.join('　') : null,
  ].filter(Boolean).join('\n')
}

export function StrategyRule({ detail, className }: { detail: StrategyDetail; className?: string }) {
  const { description, params, entry, exit, risk } = strategyRuleParts(detail)

  return (
    <div className={cn('space-y-1 rounded-md border border-border/60 bg-base/60 p-2', className)}>
      <p className="text-[11px] leading-5 text-secondary">{description}</p>
      {entry.length > 0 && (
        <p className="text-[10px] leading-4 text-muted">
          <span className="text-foreground">進場訊號</span>：{entry.join('、')}
        </p>
      )}
      {exit.length > 0 && (
        <p className="text-[10px] leading-4 text-muted">
          <span className="text-foreground">出場訊號</span>：{exit.join('、')}
        </p>
      )}
      {risk.length > 0 && (
        <p className="text-[10px] leading-4 text-muted">
          <span className="text-foreground">風控</span>：{risk.join('、')}
        </p>
      )}
      {params.length > 0 && (
        <p className="font-mono text-[10px] leading-4 text-amber-400/90">{params.join('　')}</p>
      )}
      {(detail.tags ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 pt-0.5">
          {detail.tags.map(t => (
            <span key={t} className="rounded border border-border px-1 text-[9px] text-muted">{t}</span>
          ))}
        </div>
      )}
    </div>
  )
}
