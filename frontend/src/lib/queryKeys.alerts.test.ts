import { describe, expect, it } from 'vitest'
import { QK } from './queryKeys'

describe('QK.alerts', () => {
  it('puts every request parameter into the key', () => {
    expect(QK.alerts({ days: 7, limit: 10 })).toEqual([
      'alerts',
      { days: 7, limit: 10, source: null, type: null, extColumns: null },
    ])
    expect(QK.alerts({ days: 7, limit: 500, source: 'price', type: 'cross', extColumns: 'a.b' })).toEqual([
      'alerts',
      { days: 7, limit: 500, source: 'price', type: 'cross', extColumns: 'a.b' },
    ])
  })

  it('keeps the dashboard and monitor lists in separate caches under one prefix', () => {
    const dashboard = QK.alerts({ days: 7, limit: 10 })
    const monitor = QK.alerts({ days: 7, limit: 500 })

    expect(dashboard).not.toEqual(monitor)
    expect(QK.alertsAll).toEqual(['alerts'])
    expect(dashboard[0]).toBe(QK.alertsAll[0])
    expect(monitor[0]).toBe(QK.alertsAll[0])
  })
})
