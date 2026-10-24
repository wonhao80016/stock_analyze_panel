import { describe, expect, it } from 'vitest'
import * as signals from './signals'
import { ABNORMAL_SIGNAL_DEFINITIONS, BUILTIN_SIGNAL_DEFINITIONS } from './signals'

// Subtype strings emitted by backend/app/taiwan/abnormal_diagnostics.py.
const BACKEND_SUBTYPES: Record<string, string[]> = {
  PRICE_MOVE: ['UP', 'DOWN'],
  FOREIGN_FLOW_SPIKE: ['BUY', 'SELL'],
  TRUST_FLOW_SPIKE: ['BUY', 'SELL'],
  MARGIN_SURGE: ['INCREASE', 'DECREASE'],
  SHORT_SURGE: ['INCREASE', 'DECREASE'],
  PRICE_FLOW_DIVERGENCE: ['PRICE_UP_FOREIGN_SELL', 'PRICE_DOWN_FOREIGN_BUY'],
  RELATIVE_STRENGTH_OUTLIER: ['LEADER', 'LAGGARD'],
}

describe('B063 abnormalSignalLabel', () => {
  const label = (signals as any).abnormalSignalLabel as (type: string, subtype?: string | null) => string

  it('names a short-balance drop as a drop', () => {
    expect(label('SHORT_SURGE', 'DECREASE')).toBe('融券大減')
    expect(label('SHORT_SURGE', 'INCREASE')).toBe('融券激增')
  })

  it('gives both directions of every two-sided signal distinct labels', () => {
    for (const [type, subtypes] of Object.entries(BACKEND_SUBTYPES)) {
      const names = subtypes.map(subtype => label(type, subtype))
      expect(new Set(names).size, type).toBe(subtypes.length)
      const def = ABNORMAL_SIGNAL_DEFINITIONS.find(sig => sig.id === type)!
      for (const name of names) expect(name, type).not.toBe(def.name)
    }
  })

  it('falls back to the definition name, then to the raw type', () => {
    expect(label('TURNOVER_SPIKE', null)).toBe('成交額放大')
    expect(label('NOT_A_SIGNAL', 'X')).toBe('NOT_A_SIGNAL')
  })

  it('keeps the filter names direction-neutral for the signals that fire both ways', () => {
    const byId = (id: string) => ABNORMAL_SIGNAL_DEFINITIONS.find(sig => sig.id === id)!
    expect(byId('MARGIN_SURGE').name).not.toMatch(/激增|大減/)
    expect(byId('SHORT_SURGE').name).not.toMatch(/激增|大減/)
  })
})

describe('B306 abnormalSignalEvidence', () => {
  const evidence = (signals as any).abnormalSignalEvidence as (sig: Record<string, unknown>) => string

  it('reads the divergence baseline as the foreign net', () => {
    expect(evidence({ type: 'PRICE_FLOW_DIVERGENCE', observed: 4.2, baseline: -300000 }))
      .toBe('漲跌幅 4.2%，外資買賣超 -300,000 股')
  })

  it('reports a zero median instead of a ratio of zero', () => {
    expect(evidence({ type: 'SHORT_SURGE', observed: -150000, baseline: 0, ratio: null }))
      .toBe('融券增減 -150,000 股，前 20 日 |增減| 中位數為 0，倍數無法計算')
  })

  it('drops the meaningless zero baseline of a price move', () => {
    expect(evidence({ type: 'PRICE_MOVE', observed: -6.1, baseline: 0 })).toBe('漲跌幅 -6.1%')
  })
})

describe('B287 limit-down recovery name matches its definition', () => {
  it('describes 跌停翻紅 as closing above the previous close', () => {
    const sig = BUILTIN_SIGNAL_DEFINITIONS.find(s => s.id === 'signal_limit_down_recovery')!
    expect(sig.name).toBe('跌停翻紅')
    expect(sig.description).toBe('盤中觸及跌停價，收盤高於昨收。')
  })
})
