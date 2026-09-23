// Taiwan has no floatShares (docs/taiwan-share-capital-phase-6h.md), so the info-bar
// turnover falls back to the backend-computed latest.turnover_rate only when the
// A-share float-shares formula cannot run — the A-share path itself must not change.
import { describe, expect, it } from 'vitest'
import { resolveTurnoverRate } from './turnoverRate'

describe('resolveTurnoverRate', () => {
  it('uses the float-shares formula when floatShares is known (A-share)', () => {
    const latest = { volume: 500_000, turnover_rate: 999 }
    expect(resolveTurnoverRate(latest, 100_000_000)).toBeCloseTo(500_000 * 100 / 100_000_000 * 100)
  })

  it('ignores a stray backend turnover_rate whenever floatShares is known', () => {
    const latest = { volume: 500_000, turnover_rate: 42 }
    expect(resolveTurnoverRate(latest, 100_000_000)).not.toBe(42)
  })

  it('returns null when floatShares is known but volume is falsy, matching the original A-share formula', () => {
    expect(resolveTurnoverRate({ volume: 0, turnover_rate: 5 }, 100_000_000)).toBeNull()
    expect(resolveTurnoverRate({ volume: undefined, turnover_rate: 5 }, 100_000_000)).toBeNull()
  })

  it('falls back to the backend turnover_rate when floatShares is absent (Taiwan)', () => {
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: 1.23 }, undefined)).toBe(1.23)
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: 1.23 }, null)).toBe(1.23)
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: 1.23 }, 0)).toBe(1.23)
  })

  it('returns null when floatShares is absent and turnover_rate is missing or non-finite', () => {
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: null }, undefined)).toBeNull()
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: undefined }, undefined)).toBeNull()
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: NaN }, undefined)).toBeNull()
  })
})
