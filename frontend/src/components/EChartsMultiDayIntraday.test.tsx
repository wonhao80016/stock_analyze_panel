import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { MinuteKlineRow, MinuteKlineSession } from '@/lib/api'
import { EChartsMultiDayIntraday } from './EChartsMultiDayIntraday'

const setOption = vi.fn()
const chartHandlers = new Map<string, () => void>()
let currentZoom = { start: 0, end: 100 }

vi.mock('echarts', () => ({
  init: () => ({
    on: (event: string, handler: () => void) => chartHandlers.set(event, handler),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
    getOption: () => ({ dataZoom: [currentZoom] }),
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
  chartHandlers.clear()
  currentZoom = { start: 0, end: 100 }
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

  describe('zoom', () => {
    function lastZoom() {
      const { start, end } = lastOption().dataZoom[0]
      return { start, end }
    }

    function zoomTo(start: number, end: number) {
      currentZoom = { start, end }
      chartHandlers.get('dataZoom')!()
    }

    it('keeps the user zoom when the volume unit switches', () => {
      render(<EChartsMultiDayIntraday sessions={[session('2026-10-07'), session('2026-10-08')]} />)
      expect(lastZoom()).toEqual({ start: 0, end: 100 })

      zoomTo(40, 80)
      fireEvent.click(screen.getByRole('button', { name: '股' }))

      expect(screen.getByRole('button', { name: '股' })).toHaveAttribute('aria-pressed', 'true')
      expect(lastZoom()).toEqual({ start: 40, end: 80 })
    })

    it('keeps the user zoom while the latest session updates', () => {
      const first = [session('2026-10-07'), session('2026-10-08')]
      const view = render(<EChartsMultiDayIntraday sessions={first} />)
      zoomTo(40, 80)

      const updated = [first[0], { ...first[1], rows: [...first[1].rows, bar('2026-10-08', '02:00', 102, 300)] }]
      view.rerender(<EChartsMultiDayIntraday sessions={updated} />)

      expect(lastZoom()).toEqual({ start: 40, end: 80 })
    })

    it('shows the whole range again when the trading days change', () => {
      const view = render(<EChartsMultiDayIntraday sessions={[session('2026-10-07'), session('2026-10-08')]} />)
      zoomTo(40, 80)

      view.rerender(<EChartsMultiDayIntraday sessions={[session('2026-10-08'), session('2026-10-09')]} />)

      expect(lastZoom()).toEqual({ start: 0, end: 100 })
    })
  })
})
