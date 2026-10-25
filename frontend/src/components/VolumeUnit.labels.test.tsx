import { describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import type { KlineRow } from '@/lib/api'
import type { ColumnConfig } from '@/lib/list-columns'
import { setVolumeUnit } from '@/lib/volumeUnit'
import { StockInfoBar } from './StockInfoBar'
import { renderBuiltinDataCell } from './stock-table/primitives'

vi.mock('@/components/ListColumnCustomizer', () => ({ ListColumnCustomizer: () => null }))

const VOLUME_FIELD: ColumnConfig = {
  id: 'builtin:volume',
  source: { type: 'builtin', key: 'volume' },
  label: '成交量',
  visible: true,
}

function dailyRows(volume: number): KlineRow[] {
  return [
    { date: '2026-10-07', open: 50, high: 50, low: 50, close: 50, volume: 100_000 },
    { date: '2026-10-08', open: 50, high: 51, low: 49, close: 50.5, volume },
  ]
}

function column(key: string): ColumnConfig {
  return { id: `builtin:${key}`, source: { type: 'builtin', key }, label: key, visible: true, align: 'center' }
}

describe('StockInfoBar volume unit', () => {
  it('labels the latest volume in lots by default and in shares after switching', () => {
    render(
      <StockInfoBar
        symbol="2330.TWSE"
        rows={dailyRows(12_345_678)}
        fields={[VOLUME_FIELD]}
        onFieldsChange={() => {}}
      />,
    )

    expect(screen.getByText('1.2萬張')).toBeInTheDocument()

    act(() => setVolumeUnit('shares'))

    expect(screen.getByText('1235萬股')).toBeInTheDocument()
  })
})

describe('stock table volume averages', () => {
  function renderCell(row: Record<string, unknown>, key: string) {
    const { container } = render(
      <table><tbody><tr>{renderBuiltinDataCell(row, column(key))}</tr></tbody></table>,
    )
    return container.querySelector('td')!
  }

  it('labels 量MA5 and 量MA10 with the shared volume unit', () => {
    const ma5 = renderCell({ vol_ma5: 12_345_678 }, 'vol_ma5')
    const ma10 = renderCell({ vol_ma10: 3_456_000 }, 'vol_ma10')

    expect(ma5.textContent).toBe('1.2萬張')
    expect(ma10.textContent).toBe('3456張')

    act(() => setVolumeUnit('shares'))

    expect(ma5.textContent).toBe('1235萬股')
    expect(ma10.textContent).toBe('346萬股')
  })

  it('keeps the indicator date title and the missing-value dash', () => {
    expect(renderCell({ vol_ma5: 5_000, indicator_date: '2026-09-30' }, 'vol_ma5').getAttribute('title'))
      .toBe('指標日期 2026-09-30')
    expect(renderCell({ vol_ma10: null }, 'vol_ma10').textContent).toBe('—')
  })
})
