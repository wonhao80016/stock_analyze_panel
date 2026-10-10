// The backend's turnover_rate is already a percentage (1.23 means 1.23%) computed with the
// market's own volume unit, so the frontend shows it as-is and never recomputes it.
import { describe, expect, it } from 'vitest'
import { resolveTurnoverRate } from './turnoverRate'

describe('resolveTurnoverRate', () => {
  it('shows the backend turnover_rate as a percentage without scaling it', () => {
    expect(resolveTurnoverRate({ volume: 6_425_491, turnover_rate: 0.400354 })).toBe(0.400354)
  })

  it('does not recompute turnover from volume', () => {
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: 1.23 })).toBe(1.23)
  })

  it('returns null when turnover_rate is missing or non-finite', () => {
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: null })).toBeNull()
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: undefined })).toBeNull()
    expect(resolveTurnoverRate({ volume: 500_000, turnover_rate: NaN })).toBeNull()
  })
})
