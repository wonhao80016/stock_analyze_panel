import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import type { MinuteKlineRow } from '@/lib/api'
import { EChartsIntraday } from './EChartsIntraday'
import { EChartsMultiDayIntraday } from './EChartsMultiDayIntraday'

const setOption = vi.fn()
const handlers = new Map<string, (event?: unknown) => void>()

vi.mock('echarts', () => ({
  init: () => ({
    on: (name: string, handler: (event?: unknown) => void) => handlers.set(name, handler),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
    setOption,
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

afterEach(() => {
  setOption.mockClear()
  handlers.clear()
})

function bar(utcTime: string, close: number, volume: number): MinuteKlineRow {
  return {
    datetime: `2026-10-05T${utcTime}:00`,
    open: close, high: close, low: close, close,
    volume, amount: close * volume,
  }
}

// 09:18 trades above the 51.50 previous close; the day ends at 13:28 below it.
const rows = [bar('01:18', 51.8, 1000), bar('01:19', 51.6, 2000), bar('05:28', 50.4, 3000)]

function lastOption() {
  return setOption.mock.calls[setOption.mock.calls.length - 1][0]
}

describe('EChartsIntraday info bar', () => {
  it('returns to the latest minute when the pointer leaves the chart', () => {
    render(<EChartsIntraday data={rows} prevClose={51.5} />)
    expect(screen.getAllByText('50.40').length).toBeGreaterThan(0)

    act(() => handlers.get('updateAxisPointer')?.({ axesInfo: [{ axisDim: 'x', axisIndex: 0, value: 18 }] }))
    expect(screen.queryAllByText('50.40')).toHaveLength(0)

    act(() => handlers.get('globalout')?.())
    expect(screen.getAllByText('50.40').length).toBeGreaterThan(0)
  })

  it('draws isolated-minute dots in the line colours instead of the default palette', () => {
    render(<EChartsIntraday data={rows} prevClose={51.5} />)

    const series = lastOption().series as { name: string; lineStyle: { color: string }; itemStyle?: { color: string } }[]
    for (const name of ['價格', '均價']) {
      const line = series.find(s => s.name === name)!
      expect(line.itemStyle?.color).toBe(line.lineStyle.color)
    }
  })
})

describe('EChartsMultiDayIntraday dots', () => {
  it('draws isolated-minute dots in each day line colour', () => {
    const sessions = [
      { date: '2026-10-02', prev_close: 51.0, rows: rows.map(r => ({ ...r, datetime: r.datetime.replace('10-05', '10-02') })) },
      { date: '2026-10-05', prev_close: 51.5, rows },
    ]
    render(<EChartsMultiDayIntraday sessions={sessions as never} />)

    const series = lastOption().series as { type: string; lineStyle?: { color: string }; itemStyle?: { color: string } }[]
    const lines = series.filter(s => s.type === 'line')
    expect(lines.length).toBeGreaterThan(0)
    for (const line of lines) expect(line.itemStyle?.color).toBe(line.lineStyle?.color)
  })
})
