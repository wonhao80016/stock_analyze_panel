import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TaiwanReferenceData } from './TaiwanReferenceData'

function statementResponse() {
  return {
    symbol: '2330.TWSE',
    exchange: 'TWSE',
    security_type: 'stock',
    identity: {},
    sections: {
      financial_statement: {
        dataset: 'financial_statement',
        status: 'data_insufficient',
        usage_scope: 'current_reference',
        historically_eligible: false,
        data: {
          period_start: '2026-01-01',
          period_end: '2026-06-30',
          values: { revenue: 1_773_000_000_000, gross_profit: 1_050_000_000_000, operating_income: 870_000_000_000, net_income: 740_000_000_000, cumulative_eps: 28.5, equity: 4_000_000_000_000 },
        },
      },
    },
  }
}

describe('TaiwanReferenceData financial statement', () => {
  it('shows the statement amounts as year-to-date figures over their accumulation period', () => {
    render(
      <TaiwanReferenceData response={statementResponse() as any} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
    )

    expect(screen.getByText('2026-01-01 ~ 2026-06-30')).toBeInTheDocument()
    expect(screen.getByText('累計營業收入')).toBeInTheDocument()
    expect(screen.getByText('累計營業利益')).toBeInTheDocument()
    expect(screen.getByText('累計本期淨利')).toBeInTheDocument()
    expect(screen.queryByText('營業收入')).not.toBeInTheDocument()
  })

  it('shows the year-to-date gross profit and its margin over revenue', () => {
    render(
      <TaiwanReferenceData response={statementResponse() as any} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
    )

    expect(screen.getByText('累計營業毛利').nextSibling).toHaveTextContent('10,500 億元')
    expect(screen.getByText('累計毛利率').nextSibling).toHaveTextContent('59.22%')
  })

  it.each([
    ['gross profit is missing', { revenue: 1_000, gross_profit: null }],
    ['revenue is zero', { revenue: 0, gross_profit: 500 }],
    ['revenue is missing', { gross_profit: 500 }],
  ])('shows no gross margin when %s', (_case, values) => {
    const response = statementResponse()
    response.sections.financial_statement.data.values = values as any
    render(
      <TaiwanReferenceData response={response as any} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
    )

    expect(screen.getByText('累計毛利率').nextSibling).toHaveTextContent('--')
  })

  it('shows -- for a statement category without a gross profit line', () => {
    const response = statementResponse()
    response.sections.financial_statement.data.values = { revenue: 1_000_000 } as any
    render(
      <TaiwanReferenceData response={response as any} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
    )

    expect(screen.getByText('累計營業毛利').nextSibling).toHaveTextContent('--')
  })

  it('falls back to the period end when no period start is given', () => {
    const response = statementResponse()
    delete (response.sections.financial_statement.data as Record<string, unknown>).period_start
    render(
      <TaiwanReferenceData response={response as any} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
    )

    expect(screen.getByText('截至 2026-06-30')).toBeInTheDocument()
  })
})

describe('TaiwanReferenceData share capital', () => {
  it('has no public float row, since Taiwan publishes no public-float source', () => {
    const response = {
      ...statementResponse(),
      sections: {
        share_capital_record: {
          dataset: 'share_capital_record',
          status: 'ok',
          usage_scope: 'current_reference',
          historically_eligible: false,
          data: { period_end: '2026-09-30', values: { issued_shares: 25_930_380_458, capital_twd: 259_303_804_580 } },
        },
      },
    }
    render(
      <TaiwanReferenceData response={response as any} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
    )

    expect(screen.getByText('已發行普通股數')).toBeInTheDocument()
    expect(screen.queryByText('流通股數')).not.toBeInTheDocument()
  })
})
