import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { MinuteKlineRow, PriceLimitInfo } from '@/lib/api'
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

const rows: MinuteKlineRow[] = [
  { datetime: '2026-10-06 09:01:00', open: 5180, high: 5200, low: 5170, close: 5190, volume: 10, amount: 51900 },
]

function drawnLines(): number[] {
  const option = setOption.mock.calls[setOption.mock.calls.length - 1][0]
  return (option.series[0].markLine?.data ?? []).map((line: { yAxis: number }) => line.yAxis)
}

function renderInLimitMode(priceLimit: PriceLimitInfo | undefined, prevClose = 5180) {
  render(<EChartsIntraday data={rows} prevClose={prevClose} priceLimit={priceLimit} />)
  fireEvent.click(screen.getByRole('button', { name: '漲跌停' }))
}

describe('EChartsIntraday limit lines', () => {
  it('draws the limit prices the backend computed', () => {
    renderInLimitMode({ rate: 0.1, limit_up: 5695, limit_down: 4665, source: 'model' })

    expect(drawnLines()).toEqual([5180, 5695, 4665])
  })

  it('does not compute limit prices from the rate on its own', () => {
    renderInLimitMode({ rate: 0.1, limit_up: null, limit_down: null, source: 'rule' })

    expect(drawnLines()).toEqual([5180])
  })

  it('draws no lines and says so for an instrument without limits', () => {
    renderInLimitMode({ rate: null, limit_up: null, limit_down: null, source: 'no_limit' }, 30)

    expect(drawnLines()).toEqual([30])
    expect(screen.getByText('無漲跌幅限制')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '漲跌停' })).toBeDisabled()
  })

  it('draws no lines and flags an unknown rule instead of assuming 10%', () => {
    renderInLimitMode({ rate: null, limit_up: null, limit_down: null, source: 'unknown' }, 20)

    expect(drawnLines()).toEqual([20])
    expect(screen.getByText('漲跌幅規則未知')).toBeInTheDocument()
  })

  it('drops the lines and disables limit mode when the next symbol has no limit', () => {
    const { rerender } = render(
      <EChartsIntraday
        data={rows}
        prevClose={5180}
        priceLimit={{ rate: 0.1, limit_up: 5695, limit_down: 4665, source: 'model' }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: '漲跌停' }))
    expect(drawnLines()).toEqual([5180, 5695, 4665])

    rerender(
      <EChartsIntraday
        data={rows}
        prevClose={5180}
        priceLimit={{ rate: null, limit_up: null, limit_down: null, source: 'no_limit' }}
      />,
    )

    expect(drawnLines()).toEqual([5180])
    expect(screen.getByRole('button', { name: '漲跌停' })).toBeDisabled()
    expect(screen.getByText('無漲跌幅限制')).toBeInTheDocument()
  })

  it('draws no lines without any price-limit info', () => {
    renderInLimitMode(undefined)

    expect(drawnLines()).toEqual([5180])
  })
})
