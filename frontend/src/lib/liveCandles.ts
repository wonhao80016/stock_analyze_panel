import type { KlineRow } from '@/lib/api'

// live_date comes only from a realtime quote; a missing/stale live_date must never patch a prior session's close onto today.
export function patchLiveCandles(
  base: Record<string, KlineRow[]>,
  liveRows: any[],
): Record<string, KlineRow[]> {
  if (!Array.isArray(liveRows) || liveRows.length === 0) return base
  const liveBySymbol = new Map<string, any>(liveRows.map((r: any) => [r.symbol, r]))
  const patched: Record<string, KlineRow[]> = {}
  for (const sym of Object.keys(base)) {
    const arr = base[sym]
    if (!Array.isArray(arr) || arr.length === 0) { patched[sym] = arr; continue }
    const live = liveBySymbol.get(sym)
    const liveDate = live?.live_date
    const { open, high, low, close } = live ?? {}
    if (!liveDate || open == null || high == null || low == null || close == null) {
      patched[sym] = arr
      continue
    }
    const last = arr[arr.length - 1]
    if (last.date === liveDate) {
      patched[sym] = [...arr.slice(0, -1), { ...last, open, high, low, close }]
    } else if (last.date < liveDate) {
      patched[sym] = [...arr, { date: liveDate, open, high, low, close }]
    } else {
      patched[sym] = arr
    }
  }
  return patched
}
