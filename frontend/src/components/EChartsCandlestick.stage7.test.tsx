import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { EChartsCandlestick, type ChartMarker, type OHLC } from './EChartsCandlestick'

const setOption = vi.fn()
const handlers = new Map<string, (event?: unknown) => void>()
const zoom = { start: 0, end: 100 }

vi.mock('echarts', () => ({
  init: () => ({
    on: (name: string, handler: (event?: unknown) => void) => handlers.set(name, handler),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
    setOption,
    getOption: () => ({ dataZoom: [zoom] }),
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

afterEach(() => {
  setOption.mockClear()
  handlers.clear()
  zoom.start = 0
  zoom.end = 100
})

const bars: OHLC[] = Array.from({ length: 100 }, (_, i) => {
  const day = new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10)
  return { date: day, open: 50, high: 51, low: 49, close: 50.5, volume: 1000 }
})

function fullSetOptionCount(): number {
  return setOption.mock.calls.filter(call => call[1] === true).length
}

describe('EChartsCandlestick option rebuilds', () => {
  it('does not rebuild the full option when the parent re-renders with the default props', () => {
    const { rerender } = render(<EChartsCandlestick data={bars} />)
    const before = fullSetOptionCount()

    rerender(<EChartsCandlestick data={bars} />)

    expect(fullSetOptionCount()).toBe(before)
  })
})

describe('EChartsCandlestick compact markers', () => {
  it('redraws the current markers when a zoom crosses the compact threshold', () => {
    const first: ChartMarker[] = [{ date: bars[10].date, kind: 'buy', above: true, label: 'OLD' }]
    const second: ChartMarker[] = [{ date: bars[20].date, kind: 'buy', above: true, label: 'NEW' }]
    const { rerender } = render(<EChartsCandlestick data={bars} markers={first} />)
    rerender(<EChartsCandlestick data={bars} markers={second} />)
    setOption.mockClear()

    act(() => handlers.get('dataZoom')?.())

    const update = setOption.mock.calls.map(call => call[0]).find(opt => opt.series?.some((s: { name: string }) => s.name === 'K'))
    const kSeries = update.series.find((s: { name: string }) => s.name === 'K')
    expect(kSeries.markPoint.data.map((p: { name: string }) => p.name)).toEqual([bars[20].date])
  })
})

describe('EChartsCandlestick linked price label', () => {
  function linkedLabel(): string | undefined {
    const full = setOption.mock.calls.filter(call => call[1] === true)
    const option = full[full.length - 1][0]
    const k = option.series.find((s: { name: string }) => s.name === 'K')
    const line = (k.markLine?.data ?? []).find((d: { lineStyle?: { color?: string } }) => d.lineStyle?.color === '#3B82F6')
    return line?.label?.formatter
  }

  it('shows the traded price next to the adjusted one', () => {
    render(<EChartsCandlestick data={bars} linkedPrice={{ value: 47, traded: 188 }} />)
    expect(linkedLabel()).toBe('47.00（成交 188.00）')
  })

  it('shows one price when the session is not adjusted', () => {
    render(<EChartsCandlestick data={bars} linkedPrice={{ value: 50.5, traded: 50.5 }} />)
    expect(linkedLabel()).toBe('50.50')
  })
})
