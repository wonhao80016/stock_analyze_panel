// Minute amounts are in TWD; 1 億 is 1e8, so the info bars must not divide by 1e9.
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { MinuteKlineRow } from '@/lib/api'
import { EChartsIntraday } from './EChartsIntraday'
import { EChartsMultiDayIntraday } from './EChartsMultiDayIntraday'

vi.mock('echarts', () => ({
  init: () => ({
    on: vi.fn(),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
    setOption: vi.fn(),
    getOption: () => ({}),
    dispatchAction: vi.fn(),
    clear: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    containPixel: () => false,
  }),
}))

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

function bar(amount: number): MinuteKlineRow {
  return { datetime: '2026-10-05T05:30:00', open: 1000, high: 1000, low: 1000, close: 1000, volume: 1000, amount }
}

describe('intraday amount in 億', () => {
  it('shows 5 billion TWD as 50.00億 on the intraday info bar', () => {
    render(<EChartsIntraday data={[bar(5_000_000_000)]} prevClose={1000} />)
    expect(screen.getByText('50.00億')).toBeInTheDocument()
  })

  it('shows 500 million TWD as 5.00億 rather than 50000萬', () => {
    render(<EChartsIntraday data={[bar(500_000_000)]} prevClose={1000} />)
    expect(screen.getByText('5.00億')).toBeInTheDocument()
    expect(screen.queryByText('50000萬')).not.toBeInTheDocument()
  })

  it('shows the same scale on the multi-day info bar', () => {
    const sessions = [{ date: '2026-10-05', prev_close: 1000, rows: [bar(5_000_000_000)] }]
    render(<EChartsMultiDayIntraday sessions={sessions as never} />)
    expect(screen.getByText('50.00億')).toBeInTheDocument()
  })
})
