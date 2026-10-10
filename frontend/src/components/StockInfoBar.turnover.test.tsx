// The info bar shows the backend turnover_rate (a percentage) of the latest row as-is; an ETF row
// without one shows no turnover field rather than a figure derived from float shares.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { KlineRow } from '@/lib/api'
import type { ColumnConfig } from '@/lib/list-columns'
import { StockInfoBar } from './StockInfoBar'

vi.mock('@/components/ListColumnCustomizer', () => ({ ListColumnCustomizer: () => null }))

const TURNOVER_FIELD: ColumnConfig = {
  id: 'builtin:turnover',
  source: { type: 'builtin', key: 'turnover' },
  label: '換手',
  visible: true,
}

function rows(turnover_rate: number | null): KlineRow[] {
  return [
    { date: '2026-10-07', open: 50, high: 50, low: 50, close: 50, volume: 100_000 },
    { date: '2026-10-08', open: 50, high: 51, low: 49, close: 50.5, volume: 154_832, turnover_rate },
  ]
}

function renderBar(turnover_rate: number | null) {
  return render(
    <StockInfoBar
      symbol="2434.TWSE"
      stockInfo={{ float_shares: 100_000_000 }}
      rows={rows(turnover_rate)}
      fields={[TURNOVER_FIELD]}
      onFieldsChange={() => {}}
    />,
  )
}

describe('StockInfoBar turnover', () => {
  it('shows the latest row turnover_rate as the backend percentage', () => {
    renderBar(1.6279459)

    expect(screen.getByText('1.63%')).toBeTruthy()
  })

  it('shows no turnover field for a row without turnover_rate', () => {
    renderBar(null)

    expect(screen.queryByText('換手')).toBeNull()
  })
})
