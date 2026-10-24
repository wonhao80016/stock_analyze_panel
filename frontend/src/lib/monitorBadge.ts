import { useSyncExternalStore } from 'react'

/**
 * Unread badge of the monitor center: alerts newer than the last seen alert ts, persisted in localStorage.
 * The alert store prunes by age and record cap, so its total is not a monotonic counter.
 */

const STORAGE_KEY = 'monitor_last_seen_ts'

/** Recent alerts fetched per poll; the badge caps its display at 99+. */
export const RECENT_ALERT_LIMIT = 100

let recentTs: number[] = []
let loaded = false
let lastSeenTs = readSeen()
let onMonitorPage = false    // 當前是否在監控中心頁面
const listeners = new Set<() => void>()

function readSeen(): number | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === null) return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

function writeSeen(v: number) {
  try { localStorage.setItem(STORAGE_KEY, String(v)) } catch { /* ignore */ }
}

function newestTs(): number {
  return recentTs.reduce((max, ts) => (ts > max ? ts : max), 0)
}

function syncSeen() {
  const newest = Math.max(newestTs(), lastSeenTs ?? 0)
  if (lastSeenTs !== newest) {
    lastSeenTs = newest
    writeSeen(newest)
  }
}

function emit() {
  listeners.forEach(fn => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

export function getUnreadAlertCount(): number {
  const seen = lastSeenTs
  if (seen == null) return 0
  return recentTs.filter(ts => ts > seen).length
}

/** Called by the Layout poll with the ts of the most recent alerts. */
export function setRecentAlertTimestamps(timestamps: number[]): void {
  recentTs = timestamps.filter(ts => Number.isFinite(ts))
  loaded = true
  // Without a stored cursor, alerts that already exist on the first load count as read.
  if (lastSeenTs == null || onMonitorPage) syncSeen()
  emit()
}

/** 進入監控頁時調用。 */
export function markSeen(): void {
  onMonitorPage = true
  if (loaded) {
    syncSeen()
    emit()
  }
}

/** 離開監控頁時調用 (停止同步, 之後新增才計入未讀)。 */
export function leaveMonitorPage(): void {
  onMonitorPage = false
}

/** 記錄被清空時調用。 */
export function resetBadge(): void {
  syncSeen()
  recentTs = []
  emit()
}

/** 讀取當前未讀數。 */
export function useUnreadAlerts(): number {
  return useSyncExternalStore(subscribe, getUnreadAlertCount, () => 0)
}
