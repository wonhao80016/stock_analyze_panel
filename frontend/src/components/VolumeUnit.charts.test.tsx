import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { MinuteKlineRow, MinuteKlineSession } from '@/lib/api'
import { EChartsCandlestick, type OHLC } from './EChartsCandlestick'
import { EChartsIntraday } from './EChartsIntraday'
import { EChartsMultiDayIntraday } from './EChartsMultiDayIntraday'
import { VolumeUnitToggle } from './VolumeUnitToggle'

const setOption = vi.fn()

vi.mock('echarts', () => ({
  init: () => ({
    on: vi.fn(),
    off: vi.fn(),
    getZr: () => ({ on: vi.fn(), off: vi.fn() }),
    setOption,
    getOption: () => ({}),
    dispatchAction: vi.fn(),
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

function lastFullOption() {
  const calls = setOption.mock.calls.filter(call => call[1] === true)
  return calls[calls.length - 1][0]
}

function minuteBar(volume: number): MinuteKlineRow {
  return { datetime: '2026-10-08T01:01:00', open: 50, high: 50, low: 50, close: 50, volume, amount: 50 * volume }
}

function dailyBar(date: string, volume: number): OHLC {
  return { date, open: 50, high: 51, low: 49, close: 50.5, volume }
}

describe('EChartsIntraday volume unit', () => {
  it('shows the minute volume in lots by default and in shares after switching', () => {
    render(<EChartsIntraday data={[minuteBar(625_123)]} prevClose={50} />)

    expect(screen.getByRole('button', { name: '張' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('625張')).toBeInTheDocument()
    expect(lastFullOption().yAxis[1].axisPointer.label.formatter({ value: 1_500 })).toBe('1.5張')

    fireEvent.click(screen.getByRole('button', { name: '股' }))

    expect(screen.getByText('63萬股')).toBeInTheDocument()
    expect(screen.queryByText('625張')).not.toBeInTheDocument()
    expect(lastFullOption().yAxis[1].axisPointer.label.formatter({ value: 1_500 })).toBe('1500股')
    expect(lastFullOption().series.find((s: { name: string }) => s.name === '成交量').data.find(Boolean).value).toBe(625_123)
  })

  it('keeps the chosen unit across a remount', () => {
    const first = render(<EChartsIntraday data={[minuteBar(625_123)]} prevClose={50} />)
    fireEvent.click(screen.getByRole('button', { name: '股' }))
    first.unmount()

    render(<EChartsIntraday data={[minuteBar(625_123)]} prevClose={50} />)

    expect(screen.getByRole('button', { name: '股' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('63萬股')).toBeInTheDocument()
  })
})

describe('EChartsMultiDayIntraday volume unit', () => {
  it('follows the shared setting in its info bar', () => {
    const sessions: MinuteKlineSession[] = [{ date: '2026-10-08', prev_close: 50, rows: [minuteBar(1_500)] }]
    render(<EChartsMultiDayIntraday sessions={sessions} />)

    expect(screen.getByText('1.5張')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '股' }))

    expect(screen.getByText('1500股')).toBeInTheDocument()
  })
})

describe('EChartsCandlestick volume unit', () => {
  const rows = Array.from({ length: 10 }, (_, index) => dailyBar(`2026-09-${String(index + 21).padStart(2, '0')}`, 625_123))

  function volumeGraphics() {
    const graphic = lastFullOption().graphic as { id: string; style: { text: string } }[]
    return {
      title: graphic.find(item => item.id === 'sub-label-vol')!.style.text,
      values: graphic.find(item => item.id === 'sub-val-vol')!.style.text,
    }
  }

  it('switches the volume panel title, info bar, legends and axis between lots and shares', () => {
    render(
      <>
        <VolumeUnitToggle />
        <EChartsCandlestick data={rows} activeIndicators={['vol']} volumeCompare={{ enabled: false, days: 1 }} />
      </>,
    )

    expect(volumeGraphics().title).toBe('成交量(張)')
    expect(volumeGraphics().values).toContain('量:625張')
    expect(volumeGraphics().values).toContain('VOL5:625張')
    expect(volumeGraphics().values).toContain('VOL10:625張')
    expect(lastFullOption().yAxis[1].axisLabel.formatter(3_000_000)).toBe('3000')
    expect(lastFullOption().yAxis[1].axisPointer.label.formatter({ value: 3_000_000 })).toBe('3000張')

    fireEvent.click(screen.getByRole('button', { name: '股' }))

    expect(volumeGraphics().title).toBe('成交量(股)')
    expect(volumeGraphics().values).toContain('量:63萬股')
    expect(volumeGraphics().values).toContain('VOL5:63萬股')
    expect(lastFullOption().yAxis[1].axisLabel.formatter(3_000_000)).toBe('300萬')
    expect(lastFullOption().series.find((s: { name: string }) => s.name === '成交量').data[0].value).toBe(625_123)
  })
})
