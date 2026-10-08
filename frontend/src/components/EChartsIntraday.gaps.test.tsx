import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { MinuteKlineRow } from '@/lib/api'
import { EChartsIntraday } from './EChartsIntraday'

const setOption = vi.fn()

vi.mock('echarts', () => ({
  init: () => ({
    on: vi.fn(),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
    setOption,
    clear: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
}))

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

afterEach(() => {
  setOption.mockClear()
})

function bar(utcTime: string, close: number, volume: number, unattributed?: number): MinuteKlineRow {
  return {
    datetime: `2026-10-08T${utcTime}:00`,
    open: close, high: close, low: close, close,
    volume, amount: close * volume,
    unattributed_volume: unattributed ?? null,
  }
}

// 09:00, 09:01 and, after a 44-minute recording gap, 09:45 Taipei time.
const rows = [bar('01:00', 100, 200), bar('01:01', 101, 100), bar('01:45', 102, 200)]

function lastOption() {
  return setOption.mock.calls[setOption.mock.calls.length - 1][0]
}

function lineSeries(name: string) {
  return lastOption().series.find((s: { name: string }) => s.name === name)
}

describe('EChartsIntraday recording gaps', () => {
  it('leaves recording gaps unconnected and marks the isolated minute', () => {
    render(<EChartsIntraday data={rows} prevClose={100} />)

    const price = lineSeries('價格')
    expect(price.connectNulls).toBe(false)
    expect(price.data.slice(0, 3)).toEqual([100, 101, null])
    expect(price.data[45]).toEqual({ value: 102, symbol: 'circle', symbolSize: 3 })
    expect(lineSeries('均價').connectNulls).toBe(false)
  })

  it('shows the 13:30 closing bar after the auction minutes without bars', () => {
    const closing = [bar('05:23', 100, 200), bar('05:24', 101, 100), bar('05:30', 102, 5000)]
    render(<EChartsIntraday data={closing} prevClose={100} />)

    const price = lineSeries('價格')
    expect(price.data[264]).toBe(101)
    expect(price.data[270]).toEqual({ value: 102, symbol: 'circle', symbolSize: 3 })
  })
})

describe('EChartsIntraday average-price coverage', () => {
  it('measures coverage against board-lot volume, so a complete day keeps its average line', () => {
    render(<EChartsIntraday data={rows} prevClose={100} />)

    expect(screen.getByText('均價覆蓋 100%')).toBeInTheDocument()
    expect(lineSeries('均價')).toBeDefined()
  })

  it('counts unattributed volume against the coverage and keeps the line from 80%', () => {
    const gapped = [bar('01:00', 100, 200, 100), bar('01:01', 101, 300)]
    render(<EChartsIntraday data={gapped} prevClose={100} />)

    expect(screen.getByText('均價覆蓋 83%')).toBeInTheDocument()
    expect(lineSeries('均價')).toBeDefined()
  })

  it('hides the average line below 80% coverage', () => {
    const gapped = [bar('01:00', 100, 200, 500), bar('01:01', 101, 300)]
    render(<EChartsIntraday data={gapped} prevClose={100} />)

    expect(screen.getByText('均價覆蓋 50%')).toBeInTheDocument()
    expect(lineSeries('均價')).toBeUndefined()
  })

  it('keeps the average line without a coverage label when no volume was recorded', () => {
    render(<EChartsIntraday data={[bar('01:00', 100, 0)]} prevClose={100} />)

    expect(screen.queryByText(/均價覆蓋/)).toBeNull()
    expect(lineSeries('均價')).toBeDefined()
  })
})
