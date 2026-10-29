import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { TaiwanMonthlyRevenuePeriod, TaiwanMonthlyRevenueResponse } from '@/lib/api'
import { TaiwanMonthlyRevenue } from './TaiwanMonthlyRevenue'
import { buildMonthlyRevenueOption } from './TaiwanMonthlyRevenueChart'

const setOption = vi.fn()
vi.mock('echarts', () => ({
  init: () => ({ setOption, resize: vi.fn(), dispose: vi.fn() }),
}))

function month(year: number, value: number, overrides: Partial<TaiwanMonthlyRevenuePeriod> = {}): TaiwanMonthlyRevenuePeriod {
  return {
    year,
    month: value,
    period: `${year}-${String(value).padStart(2, '0')}`,
    revenue: 2_500_000_000,
    previous_month_revenue: 2_000_000_000,
    previous_year_revenue: 2_000_000_000,
    cumulative_revenue: 20_000_000_000,
    previous_year_cumulative_revenue: 16_000_000_000,
    mom_pct: 25,
    yoy_pct: 25,
    cumulative_yoy_pct: 25,
    report_date: '2026-10-29',
    source: 'twse_openapi',
    source_url: null,
    first_retrieved_at: '2026-10-29T16:31:00+08:00',
    last_retrieved_at: '2026-10-30T16:31:00+08:00',
    revised_at: null,
    ...overrides,
  }
}

function response(overrides: Partial<TaiwanMonthlyRevenueResponse> = {}): TaiwanMonthlyRevenueResponse {
  return {
    symbol: '2330.TWSE',
    exchange: 'TWSE',
    applicable: true,
    not_applicable_reason: null,
    usage_scope: 'historical_reference',
    display_only: true,
    available_at: null,
    target_months: 12,
    accumulating: true,
    months: [
      month(2026, 9),
      month(2026, 8, { revenue: 1_800_000_000, mom_pct: -4.5, yoy_pct: 0, cumulative_yoy_pct: null }),
      month(2026, 7, { revenue: null, yoy_pct: null }),
    ],
    latest_comparison: {
      period: '2026-09',
      previous_month: '2026-08',
      previous_month_revenue: 2_000_000_000,
      previous_year_month: '2025-09',
      previous_year_revenue: 1_000_000_000,
    },
    ...overrides,
  }
}

function renderCard(props: Partial<Parameters<typeof TaiwanMonthlyRevenue>[0]> = {}) {
  return render(
    <TaiwanMonthlyRevenue
      symbol="2330.TWSE"
      response={response()}
      isLoading={false}
      isError={false}
      isFetching={false}
      onRetry={() => {}}
      {...props}
    />,
  )
}

function tableCell(period: string, column: number) {
  const row = within(screen.getByTestId('monthly-revenue-table')).getByRole('rowheader', { name: period }).closest('tr')!
  return within(row).getAllByRole('cell')[column]
}

describe('TaiwanMonthlyRevenue', () => {
  it('shows a loading state', () => {
    renderCard({ response: undefined, isLoading: true })
    expect(screen.getByTestId('monthly-revenue-loading')).toHaveTextContent('讀取月營收中')
  })

  it('does not show the previous symbol while the new one loads', () => {
    renderCard({ symbol: '2317.TWSE' })
    expect(screen.getByTestId('monthly-revenue-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('monthly-revenue-table')).not.toBeInTheDocument()
    expect(screen.queryByText(/累積中（已存/)).not.toBeInTheDocument()
  })

  it('replaces the previous symbol once the new answer arrives', () => {
    const view = renderCard()
    expect(tableCell('2026-09', 0)).toHaveTextContent('25.00 億')
    const next = response({ symbol: '2317.TWSE', months: [month(2026, 9, { revenue: 300_000_000 })] })
    view.rerender(
      <TaiwanMonthlyRevenue symbol="2317.TWSE" response={next} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
    )
    expect(tableCell('2026-09', 0)).toHaveTextContent('3.00 億')
    expect(screen.queryByRole('rowheader', { name: '2026-08' })).not.toBeInTheDocument()
  })

  it('shows an error state with a retry', () => {
    const onRetry = vi.fn()
    renderCard({ isError: true, onRetry })
    expect(screen.getByText('月營收暫時讀取失敗')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(onRetry).toHaveBeenCalledOnce()
    expect(screen.queryByTestId('monthly-revenue-table')).not.toBeInTheDocument()
    expect(screen.queryByText(/累積中（已存/)).not.toBeInTheDocument()
  })

  it('keeps the section visible while no month is stored yet', () => {
    renderCard({ response: response({ months: [], latest_comparison: null }) })
    expect(screen.getByTestId('monthly-revenue-accumulating')).toHaveTextContent('累積中')
    expect(screen.getByText('累積中（已存 0／12 月）')).toBeInTheDocument()
    expect(screen.getByText(/回溯取得，可能為更正後數值/)).toBeInTheDocument()
    expect(screen.queryByTestId('monthly-revenue-table')).not.toBeInTheDocument()
  })

  it('marks ETFs and TDRs as not applicable', () => {
    const view = renderCard({ response: response({ applicable: false, not_applicable_reason: 'etf', months: [] }) })
    expect(screen.getByText(/ETF 沒有公司月營收/)).toBeInTheDocument()
    view.rerender(
      <TaiwanMonthlyRevenue
        symbol="2330.TWSE"
        response={response({ applicable: false, not_applicable_reason: 'tdr', months: [] })}
        isLoading={false}
        isError={false}
        isFetching={false}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/TDR 的官方營收是海外原股公司的數字/)).toBeInTheDocument()
    expect(screen.queryByText(/累積中/)).not.toBeInTheDocument()
  })

  it('lists months newest first with the six columns and the accumulation count', () => {
    renderCard()
    const headers = within(screen.getByTestId('monthly-revenue-table')).getAllByRole('columnheader')
    expect(headers.map(header => header.textContent)).toEqual(['月份', '營收', '月增', '年增', '累計營收', '累計年增'])
    const rows = within(screen.getByTestId('monthly-revenue-table')).getAllByRole('rowheader')
    expect(rows.map(row => row.textContent)).toEqual(['2026-09', '2026-08', '2026-07'])
    expect(tableCell('2026-09', 3)).toHaveTextContent('200.00 億')
    expect(screen.getByText('累積中（已存 3／12 月）')).toBeInTheDocument()
    expect(screen.queryByTestId('monthly-revenue-comparison')).not.toBeInTheDocument()
  })

  it('colours growth by sign: red up, green down, neutral when flat', () => {
    renderCard()
    const up = within(tableCell('2026-09', 2)).getByText('+25.00%')
    expect(up).toHaveClass('text-bull')
    const down = within(tableCell('2026-08', 1)).getByText('-4.50%')
    expect(down).toHaveClass('text-bear')
    const flat = within(tableCell('2026-08', 2)).getByText('0.00%')
    expect(flat).toHaveClass('text-foreground')
    expect(flat).not.toHaveClass('text-bull')
    expect(flat).not.toHaveClass('text-bear')
  })

  it('shows -- for a figure the official list left blank', () => {
    renderCard()
    const revenue = within(tableCell('2026-07', 0)).getByTestId('monthly-revenue-missing')
    expect(revenue).toHaveTextContent('--')
    expect(revenue).toHaveAttribute('title', '官方資料沒有這個欄位')
    expect(within(tableCell('2026-07', 2)).getByTestId('monthly-revenue-missing')).toHaveTextContent('--')
    expect(within(tableCell('2026-08', 4)).getByTestId('monthly-revenue-missing')).not.toHaveClass('text-bull')
  })

  it('shows the official previous-month and year-ago figures beside a single stored month', () => {
    setOption.mockClear()
    renderCard({ response: response({ months: [month(2026, 9)] }) })
    const option = setOption.mock.calls.at(-1)![0]
    expect(option.xAxis.data).toEqual(['2026-09'])
    expect(option.series[0].data).toEqual([25])
    const comparison = screen.getByTestId('monthly-revenue-comparison')
    expect(comparison).toHaveTextContent('對照（取自 2026-09 報表）')
    expect(within(comparison).getByText('上月（2026-08）').nextSibling).toHaveTextContent('20.00 億')
    expect(within(comparison).getByText('去年同月（2025-09）').nextSibling).toHaveTextContent('10.00 億')
    expect(comparison).toHaveTextContent('最新一期報表')
  })

  it('shows -- in the comparison when the latest report omits a figure', () => {
    renderCard({
      response: response({
        months: [month(2026, 9)],
        latest_comparison: {
          period: '2026-09', previous_month: '2026-08', previous_month_revenue: null,
          previous_year_month: '2025-09', previous_year_revenue: 1_000_000_000,
        },
      }),
    })
    const comparison = screen.getByTestId('monthly-revenue-comparison')
    expect(within(comparison).getByTestId('monthly-revenue-missing')).toHaveTextContent('--')
  })

  it('renders one card per month for narrow screens without a scrolling table', () => {
    renderCard()
    const cards = screen.getByTestId('monthly-revenue-cards')
    expect(cards).toHaveClass('md:hidden')
    expect(cards.className).not.toMatch(/overflow-x/)
    const items = within(cards).getAllByRole('listitem')
    expect(items.map(item => within(item).getByText(/^\d{4}-\d{2}$/).textContent)).toEqual(['2026-09', '2026-08', '2026-07'])
    expect(within(items[1]).getByText('-4.50%')).toHaveClass('text-bear')
    expect(within(items[2]).getAllByTestId('monthly-revenue-missing')).toHaveLength(2)
    expect(screen.getByTestId('monthly-revenue-table').parentElement).toHaveClass('hidden', 'md:block')
  })

  it('names the source and retrieval times of the latest month', () => {
    renderCard({ response: response({ months: [month(2026, 9, { revised_at: '2026-11-02T16:31:00+08:00' })] }) })
    expect(
      screen.getByText(/2026-09 來源：證交所 OpenAPI；出表日期 2026-10-29；首次取得 2026-10-29 16:31，最後確認 2026-10-30 16:31/),
    ).toHaveTextContent('官方值於 2026-11-02 16:31 有更動')
  })

  it('charts at most twelve months oldest first with revenue in hundred millions', () => {
    setOption.mockClear()
    const months = Array.from({ length: 14 }, (_, index) => month(2026 - Math.floor(index / 12), 12 - (index % 12)))
    renderCard({ response: response({ months, accumulating: false }) })
    const option = setOption.mock.calls.at(-1)![0]
    expect(option.xAxis.data).toHaveLength(12)
    expect(option.xAxis.data[0]).toBe('2026-01')
    expect(option.xAxis.data.at(-1)).toBe('2026-12')
    expect(screen.queryByText(/累積中/)).not.toBeInTheDocument()
    expect(within(screen.getByTestId('monthly-revenue-table')).getAllByRole('rowheader')).toHaveLength(12)
  })
})

describe('buildMonthlyRevenueOption', () => {
  it('keeps missing revenue and growth as gaps rather than zero', () => {
    const theme = {
      text: '#aaa', textStrong: '#fff', grid: '#333', border: '#444', crosshair: '#555',
      crosshairLabelBg: '#111', tooltipBg: '#111', tooltipBorder: '#444', tooltipText: '#fff',
      infoBarBg: '#111', zoomFill: '#222', fillSubtle: '#222',
    }
    const option = buildMonthlyRevenueOption(
      [
        { label: '2026-08', revenue: 150_000_000, yoyPct: -3 },
        { label: '2026-09', revenue: null, yoyPct: null },
      ],
      theme,
    )
    const series = option.series as { name: string; type: string; data: (number | null)[]; yAxisIndex?: number }[]
    expect(series.map(item => [item.name, item.type])).toEqual([['營收（億）', 'bar'], ['年增率', 'line']])
    expect(series[0].data).toEqual([1.5, null])
    expect(series[1].data).toEqual([-3, null])
    expect(series[1].yAxisIndex).toBe(1)
  })
})
