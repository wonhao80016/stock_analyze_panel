import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type {
  TaiwanIndustryRankMetric,
  TaiwanIndustryRanking,
  TaiwanPositioningDimension,
  TaiwanPositioningMetric,
  TaiwanPositioningResponse,
} from '@/lib/api'
import { TaiwanPositioning } from './TaiwanPositioning'

function metric(overrides: Partial<TaiwanPositioningMetric> & Pick<TaiwanPositioningMetric, 'id'>): TaiwanPositioningMetric {
  return {
    status: 'ranked',
    reason: null,
    value: 10,
    unit: 'percent',
    lower_is_better: false,
    percentile: 50,
    score: 50,
    grade: 'good',
    tier: 2,
    compared: 1800,
    as_of: '2026-10-29',
    period: null,
    basis: null,
    ...overrides,
  }
}

function dimensions(): TaiwanPositioningDimension[] {
  return [
    {
      id: 'valuation', score: 29.2, grade: 'pricey', tier: 1, segments: 2,
      metrics: [
        metric({ id: 'pe', value: 20, unit: 'times', lower_is_better: true, percentile: 75, score: 25, grade: 'pricey', tier: 1, compared: 950 }),
        metric({ id: 'pb', value: 5, unit: 'times', lower_is_better: true, percentile: 87.5, score: 12.5, grade: 'expensive', tier: 0 }),
        metric({ id: 'dividend_yield', value: 2.36 }),
      ],
    },
    {
      id: 'profitability', score: 62.5, grade: 'good', tier: 2, segments: 4,
      metrics: [
        metric({ id: 'roe', value: 20, period: '2026Q2', basis: 'ytd_annualized' }),
        metric({ id: 'operating_margin', status: 'not_ranked', reason: 'not_general_industry', value: null, percentile: null, score: null, grade: null, tier: null, compared: 1500, period: '2026Q2', basis: 'ytd' }),
      ],
    },
    {
      id: 'growth', score: 75, grade: 'good', tier: 2, segments: 4,
      metrics: [metric({ id: 'revenue_yoy', value: 25, period: '2026-09', compared: 1700 })],
    },
    {
      id: 'momentum', score: 60, grade: 'good', tier: 2, segments: 3,
      metrics: [
        metric({ id: 'return_60d', value: -5.5 }),
        metric({ id: 'return_240d', status: 'not_ranked', reason: 'insufficient_history', value: null, percentile: null, score: null, grade: null, tier: null }),
      ],
    },
    {
      id: 'chips', score: null, grade: null, tier: null, segments: null,
      metrics: [metric({ id: 'foreign_flow_20d', status: 'unavailable', reason: 'incomplete_window', value: null, percentile: null, score: null, grade: null, tier: null, compared: null })],
    },
  ]
}

function rankMetric(overrides: Partial<TaiwanIndustryRankMetric> & Pick<TaiwanIndustryRankMetric, 'id'>): TaiwanIndustryRankMetric {
  return {
    status: 'ranked',
    reason: null,
    value: 10,
    unit: 'percent',
    lower_is_better: false,
    rank: 3,
    compared: 25,
    percentile: 90,
    as_of: '2026-10-29',
    period_mixed: false,
    ...overrides,
  }
}

function industry(overrides: Partial<TaiwanIndustryRanking> = {}): TaiwanIndustryRanking {
  return {
    applicable: true,
    not_applicable_reason: null,
    industry: '半導體業',
    members: 26,
    metrics: [
      rankMetric({ id: 'return_20d', value: 12.5 }),
      rankMetric({ id: 'foreign_flow_20d', value: -1.2, rank: 20, percentile: 22 }),
      rankMetric({ id: 'trust_flow_20d', status: 'not_ranked', reason: 'insufficient_peers', value: 0, rank: null, percentile: null, compared: 2 }),
      rankMetric({ id: 'pe', value: 20, unit: 'times', lower_is_better: true, rank: 1, compared: 18, percentile: 97.2 }),
      rankMetric({ id: 'pb', value: 5, unit: 'times', lower_is_better: true, rank: 12, compared: 20, percentile: 42.5 }),
      rankMetric({ id: 'dividend_yield', value: 2.36, rank: 4, compared: 21, percentile: 83.3 }),
    ],
    peers_metric: 'return_20d',
    peers: [
      { symbol: '5274.TPEX', name: '信驊', value: 30 },
      { symbol: '2303.TWSE', name: '聯電', value: 20 },
      { symbol: '2454.TWSE', name: '聯發科', value: 15 },
    ],
    ...overrides,
  }
}

function response(overrides: Partial<TaiwanPositioningResponse> = {}): TaiwanPositioningResponse {
  return {
    symbol: '2330.TWSE',
    exchange: 'TWSE',
    applicable: true,
    not_applicable_reason: null,
    usage_scope: 'current_reference',
    available_at: null,
    universe_size: 1850,
    valuation_dates: { TWSE: '2026-10-29', TPEX: '2026-10-29' },
    valuation_period_mixed: false,
    dimensions: dimensions(),
    industry: industry(),
    ...overrides,
  }
}

function renderCard(props: Partial<Parameters<typeof TaiwanPositioning>[0]> = {}) {
  return render(
    <MemoryRouter>
      <TaiwanPositioning
        symbol="2330.TWSE"
        response={response()}
        isLoading={false}
        isError={false}
        isFetching={false}
        onRetry={() => {}}
        {...props}
      />
    </MemoryRouter>,
  )
}

describe('TaiwanPositioning', () => {
  it('labels the card as current reference with the universe size', () => {
    renderCard()
    expect(screen.getByRole('heading', { name: '體質與估值定位' })).toBeInTheDocument()
    expect(screen.getByText('目前參考')).toBeInTheDocument()
    expect(screen.getByText(/共 1,850 檔/)).toBeInTheDocument()
    expect(screen.getByText(/回溯取得，可能為更正後數值/)).toBeInTheDocument()
  })

  it('renders dimension grades and five-segment bars', () => {
    renderCard()
    const valuation = screen.getByTestId('positioning-dimension-valuation')
    expect(within(valuation).getAllByText('偏貴')[0]).toBeInTheDocument()
    const bar = screen.getByTestId('positioning-segments-valuation')
    expect(bar).toHaveAttribute('data-filled', '2')
    expect(bar.children).toHaveLength(5)
    expect(within(valuation).getByText('29.2 分')).toBeInTheDocument()
    expect(screen.getByTestId('positioning-segments-profitability')).toHaveAttribute('data-filled', '4')
    expect(within(screen.getByTestId('positioning-metric-pb')).getByText('昂貴')).toBeInTheDocument()
  })

  it('shows values in their units with percentiles, denominators and periods', () => {
    renderCard()
    const pe = screen.getByTestId('positioning-metric-pe')
    expect(within(pe).getByText('20.00 倍')).toBeInTheDocument()
    expect(within(pe).getByText('百分位 75.0')).toBeInTheDocument()
    expect(within(pe).getByText('950 檔比較')).toBeInTheDocument()
    expect(within(pe).getByText('（越低越便宜）')).toBeInTheDocument()
    expect(within(screen.getByTestId('positioning-metric-dividend_yield')).getByText('2.36%')).toBeInTheDocument()
    expect(within(screen.getByTestId('positioning-metric-roe')).getByText('年初至今累計年化・2026Q2')).toBeInTheDocument()
    expect(within(screen.getByTestId('positioning-metric-revenue_yoy')).getByText('單月・資料年月 2026-09')).toBeInTheDocument()
    expect(within(screen.getByTestId('positioning-metric-revenue_yoy')).getByText('+25.00%')).toHaveClass('text-bull')
    expect(within(screen.getByTestId('positioning-metric-return_60d')).getByText('-5.50%')).toHaveClass('text-bear')
  })

  it('explains not-ranked and unavailable metrics', () => {
    renderCard()
    expect(screen.getByTestId('positioning-reason-operating_margin')).toHaveTextContent('不排名：此產業類別不適用（只比一般業）（1,500 檔比較）')
    expect(screen.getByTestId('positioning-reason-return_240d')).toHaveTextContent('交易日數不足')
    expect(screen.getByTestId('positioning-reason-foreign_flow_20d')).toHaveTextContent('無法計算：期間內法人資料不完整')
    expect(within(screen.getByTestId('positioning-dimension-chips')).getByText('無法計算')).toBeInTheDocument()
  })

  it('marks loss-making PE', () => {
    const [valuation, ...rest] = dimensions()
    valuation.metrics[0] = metric({ id: 'pe', status: 'not_ranked', reason: 'loss_or_not_applicable', value: null, unit: 'times', lower_is_better: true, percentile: null, score: null, grade: null, tier: null })
    renderCard({ response: response({ dimensions: [valuation, ...rest] }) })
    expect(screen.getByTestId('positioning-reason-pe')).toHaveTextContent('虧損或不適用')
  })

  it('flags mixed valuation dates', () => {
    renderCard({ response: response({ valuation_dates: { TWSE: '2026-10-29', TPEX: '2026-10-28' }, valuation_period_mixed: true }) })
    const dates = screen.getByTestId('positioning-valuation-dates')
    expect(dates).toHaveTextContent('上市 2026-10-29／上櫃 2026-10-28')
    expect(within(dates).getByText('期別混雜')).toBeInTheDocument()
  })

  it('does not flag matching valuation dates', () => {
    renderCard()
    expect(screen.queryByText('期別混雜')).toBeNull()
  })

  it('shows a loading state', () => {
    renderCard({ response: undefined, isLoading: true })
    expect(screen.getByTestId('positioning-loading')).toBeInTheDocument()
  })

  it('shows an empty cross-section as not computable rather than zero', () => {
    const empty = dimensions().map(d => ({
      ...d, score: null, grade: null, tier: null, segments: null,
      metrics: d.metrics.map(m => ({ ...m, status: 'unavailable' as const, reason: 'not_archived' as const, value: null, percentile: null, score: null, grade: null, tier: null, compared: null })),
    }))
    renderCard({ response: response({ dimensions: empty, valuation_dates: {}, industry: null }) })
    expect(screen.getAllByText('無法計算')).toHaveLength(5)
    expect(screen.queryByText(/^百分位 [0-9]/)).toBeNull()
    expect(screen.queryByTestId('positioning-valuation-dates')).toBeNull()
  })

  it('shows an error with a retry', () => {
    const onRetry = vi.fn()
    renderCard({ isError: true, onRetry })
    expect(screen.getByText('定位資料暫時讀取失敗')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('does not show the previous stock after a symbol switch', () => {
    renderCard({ symbol: '2317.TWSE' })
    expect(screen.getByTestId('positioning-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('positioning-dimension-valuation')).toBeNull()
  })

  it.each([
    ['etf', 'ETF 沒有公司財報與本益比'],
    ['tdr', 'TDR 的財報與估值屬海外原股公司'],
  ] as const)('explains why a %s is not ranked', (reason, text) => {
    renderCard({ response: response({ applicable: false, not_applicable_reason: reason, dimensions: [], valuation_dates: {}, industry: null }) })
    expect(screen.getByTestId('positioning-not-applicable')).toHaveTextContent(text)
    expect(screen.queryByTestId('positioning-industry')).toBeNull()
  })
})

describe('TaiwanPositioning industry ranking', () => {
  it('shows rank over the ranked peers with the percentile beside it', () => {
    const metrics = industry().metrics.map(m => (m.id === 'foreign_flow_20d' ? { ...m, period_mixed: true } : m))
    renderCard({ response: response({ industry: industry({ metrics }) }) })
    const block = screen.getByTestId('positioning-industry')
    expect(within(block).getByText('同產業排名')).toBeInTheDocument()
    expect(block).toHaveTextContent('半導體業・上市櫃合併共 26 檔')
    expect(screen.getByTestId('industry-rank-return_20d')).toHaveTextContent('第 3 / 25 名')
    const ret = screen.getByTestId('industry-metric-return_20d')
    expect(within(ret).getByText('百分位 90.0')).toBeInTheDocument()
    expect(within(ret).getByText('+12.50%')).toHaveClass('text-bull')
    const foreign = screen.getByTestId('industry-metric-foreign_flow_20d')
    expect(within(foreign).getByText('-1.20%')).toHaveClass('text-bear')
    expect(within(foreign).getByText('期別混雜')).toBeInTheDocument()
    expect(within(ret).queryByText('期別混雜')).toBeNull()
    expect(block).toHaveTextContent('投信買賣超為 0')
  })

  it('leaves the valuation metrics out of the industry section', () => {
    renderCard()
    const block = screen.getByTestId('positioning-industry')
    for (const id of ['pe', 'pb', 'dividend_yield']) {
      expect(screen.queryByTestId(`industry-metric-${id}`)).toBeNull()
    }
    const rows = within(block).getAllByRole('listitem').map(li => li.getAttribute('data-testid'))
    expect(rows.filter(id => id?.startsWith('industry-metric-'))).toEqual([
      'industry-metric-return_20d', 'industry-metric-foreign_flow_20d', 'industry-metric-trust_flow_20d',
    ])
    expect(block).not.toHaveTextContent('本益比最低')
  })

  it('shows the industry rank under each valuation row without an industry percentile', () => {
    renderCard()
    expect(screen.getByTestId('positioning-industry-rank-pe')).toHaveTextContent('同業第 1 / 18 名（半導體業）')
    expect(screen.getByTestId('positioning-industry-rank-pb')).toHaveTextContent('同業第 12 / 20 名（半導體業）')
    expect(screen.getByTestId('positioning-industry-rank-dividend_yield')).toHaveTextContent('同業第 4 / 21 名（半導體業）')
    const pe = screen.getByTestId('positioning-metric-pe')
    expect(within(pe).getByText('百分位 75.0')).toBeInTheDocument()
    expect(within(pe).queryByText('百分位 97.2')).toBeNull()
    expect(screen.getByTestId('positioning-industry-valuation-note')).toHaveTextContent('第 1 名為同業中最便宜：本益比、淨值比最低，殖利率最高')
    expect(screen.queryByTestId('positioning-industry-rank-roe')).toBeNull()
  })

  it('shows too few peers on a valuation row', () => {
    const metrics = industry().metrics.map(m => m.id === 'pb'
      ? { ...m, status: 'not_ranked' as const, reason: 'insufficient_peers' as const, rank: null, percentile: null, compared: 2 }
      : m)
    renderCard({ response: response({ industry: industry({ metrics }) }) })
    expect(screen.getByTestId('positioning-industry-rank-pb')).toHaveTextContent('同業不足（2 檔）')
    expect(screen.getByTestId('positioning-industry-rank-pe')).toHaveTextContent('同業第 1 / 18 名')
  })

  it('shows other not-ranked reasons on a valuation row', () => {
    const metrics = industry().metrics.map(m => m.id === 'pe'
      ? { ...m, status: 'not_ranked' as const, reason: 'loss_or_not_applicable' as const, value: null, rank: null, percentile: null }
      : m)
    renderCard({ response: response({ industry: industry({ metrics }) }) })
    expect(screen.getByTestId('positioning-industry-rank-pe')).toHaveTextContent('同業不排名：虧損或不適用')
  })

  it.each([
    ['an unclassified stock', industry({ applicable: false, not_applicable_reason: 'unclassified', industry: null, members: null, metrics: [], peers: [] })],
    ['no industry section', null],
  ] as const)('shows no industry rank on valuation rows for %s', (_label, section) => {
    renderCard({ response: response({ industry: section }) })
    for (const id of ['pe', 'pb', 'dividend_yield']) {
      expect(screen.queryByTestId(`positioning-industry-rank-${id}`)).toBeNull()
    }
    expect(screen.queryByTestId('positioning-industry-valuation-note')).toBeNull()
    expect(screen.getByTestId('positioning-metric-pe')).toHaveTextContent('百分位 75.0')
  })

  it('shows too few peers instead of a rank', () => {
    renderCard()
    expect(screen.getByTestId('industry-reason-trust_flow_20d')).toHaveTextContent('同業不足（2 檔）')
    expect(screen.queryByTestId('industry-rank-trust_flow_20d')).toBeNull()
  })

  it('shows other not-ranked reasons', () => {
    const metrics = industry().metrics.map(m => m.id === 'foreign_flow_20d'
      ? { ...m, status: 'not_ranked' as const, reason: 'incomplete_window' as const, value: null, rank: null, percentile: null }
      : m)
    renderCard({ response: response({ industry: industry({ metrics }) }) })
    expect(screen.getByTestId('industry-reason-foreign_flow_20d')).toHaveTextContent('不排名：期間內法人資料不完整')
  })

  it('explains an unclassified stock', () => {
    renderCard({ response: response({ industry: industry({ applicable: false, not_applicable_reason: 'unclassified', industry: null, members: null, metrics: [], peers: [] }) }) })
    expect(screen.getByTestId('industry-not-applicable')).toHaveTextContent('未分類')
    expect(screen.queryByTestId('industry-peers')).toBeNull()
  })

  it('links the top peers and a compare page with the stock first', () => {
    renderCard()
    const peers = screen.getByTestId('industry-peers')
    expect(peers).toHaveTextContent('同業近 20 日漲幅前 3 名')
    expect(within(peers).getByRole('link', { name: /信驊/ })).toHaveAttribute('href', '/stocks/5274.TPEX')
    expect(within(peers).getByRole('link', { name: '加入比較' })).toHaveAttribute(
      'href', `/stocks/compare?symbols=${encodeURIComponent('2330.TWSE,5274.TPEX,2303.TWSE,2454.TWSE')}`,
    )
  })

  it('keeps the compare link within five symbols', () => {
    const peers = ['1101.TWSE', '1102.TWSE', '2303.TWSE', '2454.TWSE', '2330.TWSE', '5274.TPEX'].map(symbol => ({ symbol, name: null, value: 1 }))
    renderCard({ response: response({ industry: industry({ peers }) }) })
    const href = screen.getByRole('link', { name: '加入比較' }).getAttribute('href')!
    const symbols = new URLSearchParams(href.split('?')[1]).get('symbols')!.split(',')
    expect(symbols).toEqual(['2330.TWSE', '1101.TWSE', '1102.TWSE', '2303.TWSE', '2454.TWSE'])
  })

  it('shows no compare link without peers', () => {
    renderCard({ response: response({ industry: industry({ peers: [] }) }) })
    expect(screen.queryByRole('link', { name: '加入比較' })).toBeNull()
  })

  it('switches the peers and compare link with the symbol', () => {
    const { rerender } = renderCard()
    const next = response({
      symbol: '2317.TWSE',
      industry: industry({ industry: '其他電子業', peers: [{ symbol: '6196.TWSE', name: '帆宣', value: 8 }] }),
    })
    rerender(
      <MemoryRouter>
        <TaiwanPositioning symbol="2317.TWSE" response={next} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />
      </MemoryRouter>,
    )
    expect(screen.getByTestId('positioning-industry')).toHaveTextContent('其他電子業')
    expect(screen.queryByText('信驊')).toBeNull()
    expect(screen.getByRole('link', { name: '加入比較' })).toHaveAttribute(
      'href', `/stocks/compare?symbols=${encodeURIComponent('2317.TWSE,6196.TWSE')}`,
    )
  })
})
