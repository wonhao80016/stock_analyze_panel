import type { MarketBreadthStats } from './api'

export function breadthBasisNote(stats: Pick<MarketBreadthStats, 'untraded_count' | 'uncompared_count'>): string {
  return `僅計有成交；未成交 ${stats.untraded_count ?? 0}、無前收 ${stats.uncompared_count}`
}
