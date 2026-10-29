import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { TaiwanReferenceData } from './TaiwanReferenceData'
import type { TaiwanDataSection } from '@/lib/api'

function renderSections(sections: Record<string, TaiwanDataSection>) {
  const response = { symbol: '2454.TWSE', exchange: 'TWSE', security_type: 'stock', identity: {}, sections }
  render(
    <TaiwanReferenceData response={response as any} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
  )
}

function card(title: string) {
  return within(screen.getByRole('heading', { name: title }).closest('article') as HTMLElement)
}

function capital(overrides: Partial<TaiwanDataSection> = {}): TaiwanDataSection {
  return {
    dataset: 'share_capital_record',
    status: 'data_insufficient',
    usage_scope: 'current_reference',
    historically_eligible: false,
    provider: 'TWSE',
    source: 'twse:share_capital_store',
    data: {
      period_end: '2026-10-28',
      retrieved_at: '2026-10-28T21:56:03+08:00',
      values: {
        issued_shares: 1_600_000_000, capital_twd: null, par_value_twd: null, par_value_raw: null,
        live_error: 'ValueError: official opendata/t187ap03_L: upstream returned non-JSON response (text/html)',
      },
    },
    ...overrides,
  }
}

describe('TaiwanReferenceData status badges', () => {
  it('shows 目前參考 once for a current reference with official figures, plus 官方資料', () => {
    renderSections({ share_capital_record: capital() })

    const section = card('股本資料')
    expect(section.getAllByText('目前參考')).toHaveLength(1)
    expect(section.getByText('官方資料')).toBeInTheDocument()
    expect(section.getByText('不供歷史回測')).toBeInTheDocument()
  })

  it('shows no status badge for a current reference without figures', () => {
    renderSections({
      share_capital_record: capital({ data: { period_end: '', values: { issued_shares: null, reason: 'unavailable' } } }),
    })

    const section = card('股本資料')
    expect(section.getAllByText('目前參考')).toHaveLength(1)
    expect(section.queryByText('官方資料')).not.toBeInTheDocument()
    expect(section.getByText('不供歷史回測')).toBeInTheDocument()
  })

  it.each([
    ['official', '官方資料'],
    ['unsupported', '不適用'],
    ['error', '暫時讀取失敗'],
  ])('keeps the %s badge', (status, label) => {
    renderSections({ share_capital_record: capital({ status, data: null }) })

    const section = card('股本資料')
    expect(section.getByText(label)).toBeInTheDocument()
    expect(section.getAllByText('目前參考')).toHaveLength(1)
  })
})

describe('TaiwanReferenceData share capital source', () => {
  it('says the live fetch failed and names the after-close archive and its refresh time', () => {
    renderSections({ share_capital_record: capital() })

    expect(card('股本資料').getByText('來源：TWSE・即時查詢失敗，改用盤後存檔 2026-10-28 21:56')).toBeInTheDocument()
    expect(card('股本資料').getByText('出表日期').nextSibling).toHaveTextContent('2026-10-28')
    expect(card('股本資料').getByText('實收資本額').nextSibling).toHaveTextContent('--')
    expect(card('股本資料').getByText('每股面額').nextSibling).toHaveTextContent('--')
  })

  it('names a live fetch and its retrieval time', () => {
    renderSections({
      share_capital_record: capital({
        source: 'twse:share_capital_record',
        data: { period_end: '2026-10-28', retrieved_at: '2026-10-29T00:47:10+08:00', values: { issued_shares: 1 } },
      }),
    })

    expect(card('股本資料').getByText('來源：TWSE・即時查詢 2026-10-29 00:47')).toBeInTheDocument()
  })

  it('keeps the plain source when the capital section failed', () => {
    renderSections({ share_capital_record: capital({ status: 'error', provider: null, source: null, data: null }) })

    expect(card('股本資料').getByText('來源：--')).toBeInTheDocument()
  })
})
