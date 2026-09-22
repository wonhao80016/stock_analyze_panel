// Phase 8C-A — CORE_NAV 順序回歸測試
// Phase 8C-D — Legacy Product Removal: ASHARE_LEGACY_NAV 已隨 /screener,
// /backtest, /mining 產品介面正式移除, 不再匯出; CORE_NAV 是最終且唯一的
// 導覽陣列。
//
// 防止未來意外改回「自選股先於台股選股」的舊順序。Phase 8C-0 產品審查發現
// 現有順序(看板/自選股/台股選股/多股比較/監控中心)與「看市場→找股票→
// 加自選→比較→監控」的核心使用流程不符, 本 Phase 只調整陣列順序(route path
// 不變), 這裡鎖定新順序、並確認 route path 集合本身完全未變。
//
// /strategies(策略庫)其後加在 /taiwan-screener 之後: 它同屬「找股票」這一段,
// 是選股的另一個入口(依策略看, 而非依條件篩), 放在自選股之前才不打斷流程。
import { describe, expect, it } from 'vitest'
import * as navigationModule from './navigation'
import { CORE_NAV } from './navigation'

describe('CORE_NAV', () => {
  it('orders pages to match 看市場→找股票→加自選→比較→監控', () => {
    expect(CORE_NAV.map(item => item.to)).toEqual([
      '/',
      '/taiwan-screener',
      '/strategies',
      '/watchlist',
      '/stocks/compare',
      '/monitor',
    ])
  })

  it('carries exactly the intended route paths', () => {
    const paths = new Set(CORE_NAV.map(item => item.to))
    expect(paths).toEqual(
      new Set(['/', '/watchlist', '/taiwan-screener', '/strategies', '/stocks/compare', '/monitor']),
    )
  })

  it('is the only exported nav array — CORE_NAV is exactly the 6 Taiwan core items', () => {
    expect(CORE_NAV).toHaveLength(6)
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
