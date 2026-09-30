import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { renderBuiltinDataCell } from './primitives'
import type { ColumnConfig } from '@/lib/list-columns'

function col(key: string): ColumnConfig {
  return { id: `builtin:${key}`, source: { type: 'builtin', key }, label: key, visible: true, align: 'center' }
}

function renderCell(row: Record<string, unknown>, key: string) {
  const { container } = render(
    <table><tbody><tr>{renderBuiltinDataCell(row, col(key))}</tr></tbody></table>,
  )
  return container.querySelector('td')!
}

describe('renderBuiltinDataCell indicator date', () => {
  it('marks indicator cells with the indicator date', () => {
    const td = renderCell({ rsi_14: 58.8, indicator_date: '2026-09-30' }, 'rsi14')
    expect(td.getAttribute('title')).toBe('指標日期 2026-09-30')
  })

  it('renders the 60D momentum column from the watchlist registry', async () => {
    const { BUILTIN_COLUMNS } = await import('@/lib/watchlist-columns')
    const registered = BUILTIN_COLUMNS.find(c => c.id === 'builtin:momentum_60d')!
    const { container } = render(
      <table><tbody><tr>{renderBuiltinDataCell({ momentum_60d: 0.1234 }, registered)}</tr></tbody></table>,
    )
    expect(container.querySelector('td')!.textContent).not.toBe('—')
  })

  it('leaves price cells unmarked', () => {
    const td = renderCell({ close: 100, indicator_date: '2026-09-30' }, 'price')
    expect(td.getAttribute('title')).toBeNull()
  })

  it('leaves rows without an indicator date unmarked', () => {
    const td = renderCell({ rsi_14: 58.8, indicator_date: null }, 'rsi14')
    expect(td.getAttribute('title')).toBeNull()
  })
})
