import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { TaiwanQuarterlyFigures, TaiwanQuarterlyFinancialsResponse, TaiwanQuarterlyPeriod } from '@/lib/api'
import { TaiwanQuarterlyFinancials } from './TaiwanQuarterlyFinancials'
import { buildQuarterlyMarginOption } from './TaiwanQuarterlyMarginChart'

const setOption = vi.fn()
vi.mock('echarts', () => ({
  init: () => ({ setOption, resize: vi.fn(), dispose: vi.fn() }),
}))

function figures(values: Partial<TaiwanQuarterlyFigures>): TaiwanQuarterlyFigures {
  return {
    revenue: null, gross_profit: null, operating_income: null, net_income: null, parent_net_income: null,
    eps: null, gross_margin_pct: null, operating_margin_pct: null, net_margin_pct: null, roe_pct: null,
    ...values,
  }
}

function period(year: number, quarter: number, overrides: Partial<TaiwanQuarterlyPeriod> = {}): TaiwanQuarterlyPeriod {
  return {
    fiscal_year: year,
    fiscal_quarter: quarter,
    period_end: `${year}-0${quarter * 3}-30`,
    accounting_category: 'ci',
    cumulative: figures({ revenue: 2_000_000_000, eps: 5, gross_margin_pct: 42 }),
    single: figures({ revenue: 1_000_000_000, eps: 2.5, gross_margin_pct: 45, roe_pct: 3.1 }),
    single_missing_reason: null,
    roe_missing_reason: null,
    balance: { assets: null, liabilities: null, equity: null, parent_equity: null },
    year_ago: null,
    source: 'twse_openapi',
    source_url: null,
    first_retrieved_at: '2026-10-29T16:31:00+08:00',
    last_retrieved_at: '2026-10-30T16:31:00+08:00',
    revised_at: null,
    ...overrides,
  }
}

function response(overrides: Partial<TaiwanQuarterlyFinancialsResponse> = {}): TaiwanQuarterlyFinancialsResponse {
  const yearAgo = period(2025, 2, {
    single: figures({ revenue: 800_000_000, eps: 2.8, gross_margin_pct: 40, roe_pct: 3.1 }),
    cumulative: figures({ revenue: 1_500_000_000, eps: 4, gross_margin_pct: 44 }),
  })
  const latest = period(2026, 2, {
    year_ago: { fiscal_year: 2025, fiscal_quarter: 2, single: yearAgo.single, cumulative: yearAgo.cumulative },
  })
  return {
    symbol: '2330.TWSE',
    exchange: 'TWSE',
    applicable: true,
    not_applicable_reason: null,
    usage_scope: 'historical_reference',
    display_only: true,
    available_at: null,
    target_quarters: 8,
    accumulating: true,
    quarters: [yearAgo, period(2025, 4), latest],
    annual_eps: [{ fiscal_year: 2025, eps: 9.5 }],
    ...overrides,
  }
}

function renderCard(props: Partial<Parameters<typeof TaiwanQuarterlyFinancials>[0]> = {}) {
  return render(
    <TaiwanQuarterlyFinancials
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

function tableCell(rowLabel: string, column: number) {
  const row = within(screen.getByTestId('quarterly-table')).getByRole('rowheader', { name: rowLabel }).closest('tr')!
  return within(row).getAllByRole('cell')[column]
}

describe('TaiwanQuarterlyFinancials', () => {
  it('shows a loading state', () => {
    renderCard({ response: undefined, isLoading: true })
    expect(screen.getByTestId('quarterly-loading')).toHaveTextContent('讀取季度財報中')
  })

  it('does not show the previous symbol while the new one loads', () => {
    renderCard({ symbol: '2317.TWSE' })
    expect(screen.getByTestId('quarterly-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('quarterly-table')).not.toBeInTheDocument()
  })

  it('shows an error state with a retry', () => {
    const onRetry = vi.fn()
    renderCard({ isError: true, onRetry })
    expect(screen.getByText('季度財報暫時讀取失敗')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(onRetry).toHaveBeenCalledOnce()
    expect(screen.queryByTestId('quarterly-table')).not.toBeInTheDocument()
  })

  it('keeps the section visible while no quarter is stored yet', () => {
    renderCard({ response: response({ quarters: [], annual_eps: [] }) })
    expect(screen.getByTestId('quarterly-accumulating')).toHaveTextContent('累積中')
    expect(screen.getByText('累積中（已存 0／8 季）')).toBeInTheDocument()
    expect(screen.getByText(/回溯取得，可能為更正後數值/)).toBeInTheDocument()
  })

  it('marks ETFs as not applicable', () => {
    renderCard({ response: response({ applicable: false, not_applicable_reason: 'etf', quarters: [] }) })
    expect(screen.getByText(/ETF 沒有公司財報/)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: '單季或累計' })).not.toBeInTheDocument()
  })

  it('switches between single-quarter and cumulative figures', () => {
    renderCard()
    expect(tableCell('營業收入', 0)).toHaveTextContent('10.00 億')
    expect(tableCell('ROE（單季，未年化）', 0)).toHaveTextContent('3.10%')

    fireEvent.click(screen.getByRole('button', { name: '累計' }))

    expect(screen.getByRole('button', { name: '累計' })).toHaveAttribute('aria-pressed', 'true')
    expect(tableCell('營業收入', 0)).toHaveTextContent('20.00 億')
    expect(tableCell('ROE（單季，未年化）', 0)).toHaveTextContent('--')
  })

  it('colours a figure against the same quarter a year earlier', () => {
    renderCard()
    const margin = within(tableCell('毛利率', 0)).getByText('45.00%')
    expect(margin).toHaveClass('text-bull')
    expect(margin).toHaveAttribute('title', '較去年同季（2025 Q2）+5.00 個百分點')
    const eps = within(tableCell('EPS', 0)).getByText('2.50 元')
    expect(eps).toHaveClass('text-bear')
    expect(eps.getAttribute('title')).toBe('較去年同季（2025 Q2）-10.71%')
    const flat = within(tableCell('ROE（單季，未年化）', 0)).getByText('3.10%')
    expect(flat).not.toHaveClass('text-bull')
    expect(flat).not.toHaveClass('text-bear')
    expect(within(tableCell('毛利率', 1)).getByText('45.00%')).not.toHaveAttribute('title')
  })

  it('shows -- with the reason when a single quarter cannot be derived', () => {
    const missing = period(2026, 3, { single: figures({}), single_missing_reason: 'previous_quarter_missing' })
    renderCard({ response: response({ quarters: [missing] }) })
    const cell = within(tableCell('營業收入', 0)).getByTestId('quarterly-missing')
    expect(cell).toHaveTextContent('--')
    expect(cell).toHaveAttribute('title', '缺上一季累計值，無法推算單季')
  })

  it('explains a missing closing equity behind the ROE --', () => {
    const missing = period(2026, 2, { single: figures({ revenue: 1 }), roe_missing_reason: 'closing_equity_missing' })
    renderCard({ response: response({ quarters: [missing] }) })
    const cell = within(tableCell('ROE（單季，未年化）', 0)).getByTestId('quarterly-missing')
    expect(cell).toHaveAttribute('title', '官方資料沒有期末母公司權益，無法計算')
  })

  it('renders one card per quarter for narrow screens without a scrolling table', () => {
    renderCard()
    const cards = screen.getByTestId('quarterly-cards')
    expect(cards).toHaveClass('md:hidden')
    expect(cards.className).not.toMatch(/overflow-x/)
    const items = within(cards).getAllByRole('listitem')
    expect(items.map(item => within(item).getByText(/^\d{4} Q\d$/).textContent)).toEqual(['2026 Q2', '2025 Q4', '2025 Q2'])
    expect(within(items[0]).getByText('45.00%')).toHaveClass('text-bull')
    expect(screen.getByTestId('quarterly-table').parentElement).toHaveClass('hidden', 'md:block')
  })

  it('lists the full-year EPS and the source', () => {
    renderCard()
    expect(screen.getByText('歷年全年 EPS（第 4 季累計）')).toBeInTheDocument()
    expect(screen.getByText('9.50 元')).toBeInTheDocument()
    expect(screen.getByText(/2026 Q2 來源：證交所 OpenAPI；首次取得 2026-10-29 16:31/)).toBeInTheDocument()
  })

  it('charts the three margins of the shown mode', () => {
    setOption.mockClear()
    renderCard()
    const option = setOption.mock.calls.at(-1)![0]
    expect(option.series.map((series: { name: string }) => series.name)).toEqual(['毛利率', '營業利益率', '稅後淨利率'])
    expect(option.series[0].data).toEqual([40, 45, 45])
  })
})

describe('buildQuarterlyMarginOption', () => {
  it('keeps missing margins as gaps rather than zero', () => {
    const theme = {
      text: '#aaa', textStrong: '#fff', grid: '#333', border: '#444', crosshair: '#555',
      crosshairLabelBg: '#111', tooltipBg: '#111', tooltipBorder: '#444', tooltipText: '#fff',
      infoBarBg: '#111', zoomFill: '#222', fillSubtle: '#222',
    }
    const option = buildQuarterlyMarginOption(
      [
        { label: '2026 Q1', figures: figures({ gross_margin_pct: 50, net_margin_pct: 20 }) },
        { label: '2026 Q2', figures: figures({ net_margin_pct: 18 }) },
      ],
      theme,
    )
    expect(option.xAxis).toMatchObject({ data: ['2026 Q1', '2026 Q2'] })
    const series = option.series as { name: string; data: (number | null)[] }[]
    expect(series[0].data).toEqual([50, null])
    expect(series[1].data).toEqual([null, null])
    expect(series[2].data).toEqual([20, 18])
  })
})
