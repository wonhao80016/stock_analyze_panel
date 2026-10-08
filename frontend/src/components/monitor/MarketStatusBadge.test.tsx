import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MarketStatusBadge } from './TaiwanQuotePanel'

vi.mock('@/lib/api', () => ({
  api: {},
  BOARD_LOT_VOLUME_NOTE: '盤中為整股量（不含零股、鉅額、盤後定價）',
}))

describe('MarketStatusBadge', () => {
  it('labels the 13:25-13:30 closing auction', () => {
    render(<MarketStatusBadge status="closing_auction" />)
    expect(screen.getByText('收盤競價')).toBeInTheDocument()
  })

  it('keeps the existing session labels', () => {
    render(
      <>
        <MarketStatusBadge status="pre_open" />
        <MarketStatusBadge status="open" />
        <MarketStatusBadge status="post_close" />
      </>,
    )
    expect(screen.getByText('盤前')).toBeInTheDocument()
    expect(screen.getByText('開盤')).toBeInTheDocument()
    expect(screen.getByText('盤後')).toBeInTheDocument()
  })
})
