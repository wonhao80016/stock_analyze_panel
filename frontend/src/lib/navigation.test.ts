import { describe, expect, it } from 'vitest'
import * as navigationModule from './navigation'
import { CORE_NAV } from './navigation'

describe('CORE_NAV', () => {
  it('orders pages to match 看市場→找股票→加自選→比較→監控', () => {
    expect(CORE_NAV.map(item => item.to)).toEqual([
      '/',
      '/taiwan-screener',
      '/strategies',
      '/backtest',
      '/watchlist',
      '/stocks/compare',
      '/monitor',
    ])
  })

  it('carries exactly the intended route paths', () => {
    const paths = new Set(CORE_NAV.map(item => item.to))
    expect(paths).toEqual(
      new Set(['/', '/watchlist', '/taiwan-screener', '/strategies', '/backtest', '/stocks/compare', '/monitor']),
    )
  })

  it('is the only exported nav array — CORE_NAV is exactly the 7 Taiwan core items', () => {
    expect(CORE_NAV).toHaveLength(7)
  })

  it('gives every item a label and an icon, so none can render blank in the sidebar', () => {
    for (const item of CORE_NAV) {
      expect(item.label.length, item.to).toBeGreaterThan(0)
      expect(item.icon, item.to).toBeTruthy()
    }
  })
})

describe('ASHARE_LEGACY_NAV removal (Phase 8C-D)', () => {
  it('is no longer exported from navigation.ts', () => {
    expect('ASHARE_LEGACY_NAV' in navigationModule).toBe(false)
  })
})
