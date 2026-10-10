import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { StrategyDetail } from '@/lib/api'
import { StrategyRule, strategyRuleParts, strategyRuleText } from './StrategyRule'

function detail(overrides: Partial<StrategyDetail>): StrategyDetail {
  return {
    id: 'bullish_alignment',
    name: '均線多頭',
    description: 'MA5>MA10>MA20>MA60多頭排列 + 短期動量為正',
    tags: [],
    params: [],
    params_defaults: {},
    entry_signals: [],
    exit_signals: [],
    stop_loss: null,
    take_profit: null,
    trailing_stop: null,
    max_hold_days: null,
    ...overrides,
  } as StrategyDetail
}

describe('StrategyRule — strategy-specific entry signals', () => {
  it.each([
    ['bullish_alignment', 'signal_bullish_alignment_entry', '均線多頭排列'],
    ['pullback_ma20_bounce', 'signal_pullback_ma20_bounce_entry', '回踩MA20反彈'],
    ['pullback_to_support', 'signal_pullback_to_support_entry', '縮量回踩MA20'],
  ])('%s shows its own entry condition, not a MA5/MA20 golden cross', (id, signal, label) => {
    const d = detail({ id, entry_signals: [signal], exit_signals: ['signal_ma20_breakdown'] })

    expect(strategyRuleParts(d).entry).toEqual([label])
    expect(strategyRuleText(d)).toContain(`進場訊號：${label}`)
    expect(strategyRuleText(d)).not.toContain('MA5上穿MA20')

    render(<StrategyRule detail={d} />)
    expect(screen.getByText(content => content.includes(label))).toBeInTheDocument()
    expect(screen.queryByText(content => content.includes('MA5上穿MA20'))).toBeNull()
  })

  it('labels the shared strategy exit id instead of showing the raw id', () => {
    const d = detail({ id: 'pullback_resume', exit_signals: ['signal_ma20_break'] })
    expect(strategyRuleParts(d).exit).toEqual(['收盤低於MA20'])
  })
})
