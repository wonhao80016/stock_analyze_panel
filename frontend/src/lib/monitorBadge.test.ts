import { beforeEach, describe, expect, it, vi } from 'vitest'

type Badge = typeof import('./monitorBadge')

async function freshBadge(): Promise<Badge> {
  vi.resetModules()
  return import('./monitorBadge')
}

function unread(badge: Badge): number {
  return badge.getUnreadAlertCount()
}

beforeEach(() => {
  localStorage.clear()
})

describe('monitor badge cursor', () => {
  it('treats the alerts present on the first load as read', async () => {
    const badge = await freshBadge()
    badge.setRecentAlertTimestamps([3000, 2000, 1000])
    expect(unread(badge)).toBe(0)
  })

  it('counts alerts newer than the last seen alert, independent of the stored total', async () => {
    const badge = await freshBadge()
    badge.setRecentAlertTimestamps([3000, 2000, 1000])
    badge.setRecentAlertTimestamps([5000, 4000, 3000])
    expect(unread(badge)).toBe(2)
  })

  it('clears on the monitor page and counts again after leaving it', async () => {
    const badge = await freshBadge()
    badge.setRecentAlertTimestamps([1000])
    badge.setRecentAlertTimestamps([2000, 1000])
    badge.markSeen()
    expect(unread(badge)).toBe(0)

    badge.setRecentAlertTimestamps([3000, 2000, 1000])
    expect(unread(badge)).toBe(0)

    badge.leaveMonitorPage()
    badge.setRecentAlertTimestamps([4000, 3000, 2000])
    expect(unread(badge)).toBe(1)
  })

  it('keeps the last seen alert across a reload', async () => {
    const first = await freshBadge()
    first.setRecentAlertTimestamps([1000])

    const reloaded = await freshBadge()
    reloaded.setRecentAlertTimestamps([2000, 1000])
    expect(unread(reloaded)).toBe(1)
  })

  it('marks alerts seen when the monitor page opens before the first load', async () => {
    const badge = await freshBadge()
    badge.setRecentAlertTimestamps([1000])
    badge.leaveMonitorPage()
    const reloaded = await freshBadge()
    reloaded.markSeen()
    reloaded.setRecentAlertTimestamps([2000, 1000])
    expect(unread(reloaded)).toBe(0)
  })
})
