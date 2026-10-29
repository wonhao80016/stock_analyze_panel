import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { TaiwanCurrentSignalsResponse, TaiwanStrategySelection } from '@/lib/api'
import { TaiwanCurrentSignals } from './TaiwanCurrentSignals'

function selection(overrides: Partial<TaiwanStrategySelection> = {}): TaiwanStrategySelection {
  return {
    strategy_id: 'trend',
    strategy_name: '趨勢突破',
    as_of: '2026-10-28',
    rank: 2,
    total: 12,
    score: 8.5,
    grade: 'buy',
    grade_reason: null,
    limit_up_locked: false,
    missing_scoring_fields: null,
    missing_scoring_children: null,
    ...overrides,
  }
}

function response(overrides: Partial<TaiwanCurrentSignalsResponse> = {}): TaiwanCurrentSignalsResponse {
  return {
    symbol: '2330.TWSE',
    exchange: 'TWSE',
    applicable: true,
    not_applicable_reason: null,
    signals: {
      status: 'ok',
      as_of: '2026-10-28',
      triggered: [
        { id: 'signal_macd_golden', source: 'builtin', direction: 'bullish', name: null },
        { id: 'signal_ma20_breakdown', source: 'builtin', direction: 'bearish', name: null },
        { id: 'vp_double', source: 'volume_pattern', direction: 'neutral', name: null },
        { id: 'csg_mine', source: 'custom', direction: 'neutral', name: '我的訊號' },
      ],
      not_evaluated: [],
    },
    strategies: {
      status: 'ok',
      as_of: '2026-10-28',
      updated_at: 1,
      market_state: null,
      grading: { top_n: 5, market_filter: 'passed' },
      selections: [selection()],
    },
    ...overrides,
  }
}

function renderCard(props: Partial<Parameters<typeof TaiwanCurrentSignals>[0]> = {}) {
  return render(
    <TaiwanCurrentSignals
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

describe('TaiwanCurrentSignals', () => {
  it('groups triggered signals by direction with their names and data date', () => {
    renderCard()
    expect(within(screen.getByTestId('current-signals-bullish')).getByText('MACD金叉')).toHaveClass('text-bull')
    expect(within(screen.getByTestId('current-signals-bearish')).getByText('跌破MA20')).toHaveClass('text-bear')
    const neutral = screen.getByTestId('current-signals-neutral')
    expect(within(neutral).getByText('倍量')).toBeInTheDocument()
    expect(within(neutral).getByText('我的訊號')).toBeInTheDocument()
    expect(screen.getAllByText('資料日期 2026-10-28')).toHaveLength(2)
  })

  it('lists strategy picks with their rank and grade', () => {
    renderCard()
    const list = screen.getByTestId('current-strategies')
    expect(within(list).getByText('趨勢突破')).toBeInTheDocument()
    expect(within(list).getByText('第 2／12 名')).toBeInTheDocument()
    expect(within(list).getByText('買進')).toBeInTheDocument()
    expect(screen.getByText('大盤：未跌破 20 日均線')).toBeInTheDocument()
  })

  it('marks a realtime pick dated after the post-close basis', () => {
    const strategies = { ...response().strategies!, selections: [selection({ as_of: '2026-10-29', grade: 'watch', grade_reason: 'market_below_ma20' })] }
    renderCard({ response: response({ strategies }) })
    expect(screen.getByText('（盤中 2026-10-29）')).toBeInTheDocument()
    expect(screen.getByText('觀察・大盤')).toBeInTheDocument()
  })

  it('shows not evaluated signals apart from the triggered ones', () => {
    const signals = {
      ...response().signals!,
      triggered: [],
      not_evaluated: [
        { id: 'vp_low', source: 'volume_pattern' as const, direction: 'neutral' as const, name: null },
        { id: 'csg_fresh', source: 'custom' as const, direction: 'neutral' as const, name: '剛新增' },
      ],
    }
    renderCard({ response: response({ signals }) })
    expect(screen.getByTestId('current-signals-none')).toBeInTheDocument()
    expect(screen.getByTestId('current-signals-not-evaluated')).toHaveTextContent('未評估：低量、剛新增')
    expect(screen.queryByTestId('current-signals-neutral')).not.toBeInTheDocument()
  })

  it('explains empty sections', () => {
    renderCard({
      response: response({
        signals: { status: 'unpublished', as_of: null, triggered: [], not_evaluated: [] },
        strategies: { ...response().strategies!, status: 'no_cache', as_of: null, selections: [] },
      }),
    })
    expect(screen.getByText('尚無已發佈的盤後指標資料。')).toBeInTheDocument()
    expect(screen.getByTestId('current-strategies-no-cache')).toBeInTheDocument()
  })

  it('says when no strategy picks the symbol', () => {
    renderCard({ response: response({ strategies: { ...response().strategies!, selections: [] } }) })
    expect(screen.getByTestId('current-strategies-none')).toBeInTheDocument()
  })

  it('shows a loading state', () => {
    renderCard({ isLoading: true, response: undefined })
    expect(screen.getByTestId('current-signals-loading')).toBeInTheDocument()
  })

  it('does not show the previous symbol while the new one loads, then replaces it', () => {
    const view = renderCard({ symbol: '2317.TWSE' })
    expect(screen.getByTestId('current-signals-loading')).toBeInTheDocument()
    expect(screen.queryByText('MACD金叉')).not.toBeInTheDocument()
    const next = response({
      symbol: '2317.TWSE',
      signals: { status: 'ok', as_of: '2026-10-28', triggered: [{ id: 'signal_n_day_low', source: 'builtin', direction: 'bearish', name: null }], not_evaluated: [] },
    })
    view.rerender(
      <TaiwanCurrentSignals symbol="2317.TWSE" response={next} isLoading={false} isError={false} isFetching={false} onRetry={() => {}} />,
    )
    expect(screen.getByText('60日收盤新低')).toBeInTheDocument()
    expect(screen.queryByText('MACD金叉')).not.toBeInTheDocument()
  })

  it('shows an error state with a retry', () => {
    const onRetry = vi.fn()
    renderCard({ isError: true, onRetry })
    expect(screen.getByText('目前訊號暫時讀取失敗')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(onRetry).toHaveBeenCalledOnce()
    expect(screen.queryByTestId('current-strategies')).not.toBeInTheDocument()
  })
})
