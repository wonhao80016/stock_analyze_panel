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
    // The quote's previous close is the exchange reference on an ex-rights or split session.
    const prevClose = typeof live.prev_close === 'number' && Number.isFinite(live.prev_close) && live.prev_close > 0
      ? { prev_close: live.prev_close }
      : {}
    if (last.date === liveDate) {
      patched[sym] = [...arr.slice(0, -1), { ...last, open, high, low, close, ...prevClose }]
    } else if (last.date < liveDate) {
      patched[sym] = [...arr, { date: liveDate, open, high, low, close, ...prevClose }]
    } else {
      patched[sym] = arr
    }
  }
  return patched
}
