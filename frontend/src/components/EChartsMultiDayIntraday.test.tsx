import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { MinuteKlineRow, MinuteKlineSession } from '@/lib/api'
import { EChartsMultiDayIntraday } from './EChartsMultiDayIntraday'

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

function bar(date: string, utcTime: string, close: number, volume: number, unattributed?: number): MinuteKlineRow {
  return {
    datetime: `${date}T${utcTime}:00`,
    open: close, high: close, low: close, close,
    volume, amount: close * volume,
    unattributed_volume: unattributed ?? null,
  }
}

function session(date: string, unattributed?: number): MinuteKlineSession {
  return {
    date,
    prev_close: 100,
    rows: [bar(date, '01:00', 100, 300, unattributed), bar(date, '01:30', 101, 300)],
  }
}

function lastOption() {
  return setOption.mock.calls[setOption.mock.calls.length - 1][0]
}

describe('EChartsMultiDayIntraday', () => {
  it('leaves recording gaps unconnected and marks isolated minutes', () => {
    render(<EChartsMultiDayIntraday sessions={[session('2026-10-07')]} />)

    const lines = lastOption().series.filter((s: { type: string }) => s.type === 'line')
    expect(lines.length).toBeGreaterThan(0)
    for (const line of lines) expect(line.connectNulls).toBe(false)
    expect(lines[0].data.slice(0, 3)).toEqual([{ value: 100, symbol: 'circle', symbolSize: 3 }, null, null])
  })

  it('drops the average line of a session whose bars place under 80% of its volume', () => {
    render(
      <EChartsMultiDayIntraday
        sessions={[session('2026-10-07', 100), session('2026-10-08', 400)]}
      />,
    )

    const names = lastOption().series.map((s: { name: string }) => s.name)
    expect(names).toContain('2026-10-07 均價')
    expect(names).not.toContain('2026-10-08 均價')
    expect(screen.getByText('均價覆蓋 60%')).toBeInTheDocument()
  })
})
