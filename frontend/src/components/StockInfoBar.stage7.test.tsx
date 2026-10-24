import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BOARD_LOT_VOLUME_NOTE, type KlineRow } from '@/lib/api'
import type { ColumnConfig } from '@/lib/list-columns'
import { StockInfoBar } from './StockInfoBar'

vi.mock('@/components/ListColumnCustomizer', () => ({ ListColumnCustomizer: () => null }))

const VOLUME_FIELD: ColumnConfig = {
  id: 'builtin:volume',
  source: { type: 'builtin', key: 'volume' },
  label: '成交量',
  visible: true,
}

function renderBar(boardLot: boolean) {
  const rows: KlineRow[] = [
    { date: '2026-10-07', open: 50, high: 50, low: 50, close: 50, volume: 100_000 },
    { date: '2026-10-08', open: 50, high: 51, low: 49, close: 50.5, volume: 625_000, ...(boardLot ? { board_lot_volume: true } : {}) },
  ]
  return render(<StockInfoBar symbol="2330.TWSE" rows={rows} fields={[VOLUME_FIELD]} onFieldsChange={() => {}} />)
}

describe('StockInfoBar volume convention', () => {
  it('labels an intraday candle volume as board lots only', () => {
    renderBar(true)
    expect(screen.getByText(new RegExp(BOARD_LOT_VOLUME_NOTE.replace(/[()（）]/g, '.')))).toBeInTheDocument()
  })

  it('leaves a published daily volume unlabelled', () => {
    renderBar(false)
    expect(screen.queryByText(new RegExp(BOARD_LOT_VOLUME_NOTE.replace(/[()（）]/g, '.')))).not.toBeInTheDocument()
  })
})
