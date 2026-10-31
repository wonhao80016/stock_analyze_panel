import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type {
  TaiwanCurrentSignalsResponse,
  TaiwanSignalHorizonStats,
  TaiwanSignalStat,
  TaiwanSignalStatsResponse,
} from '@/lib/api'
import { TaiwanCurrentSignals } from './TaiwanCurrentSignals'
import type { SignalStatsQuery } from './TaiwanSignalStats'

function horizon(k: number, overrides: Partial<TaiwanSignalHorizonStats> = {}): TaiwanSignalHorizonStats {
  return {
    horizon: k,
    event_count: 8,
    completed_count: 6,
    pending_count: 1,
    not_filled_limit_up: 1,
    not_filled_no_trade: 0,
    data_excluded_count: 2,
    sufficient: true,
    mean_return: 0.0512,
    median_return: 0.031,
    up_ratio: 4 / 6,
    benchmark_count: 5,
    benchmark_sufficient: true,
    excess_mean: 0.012,
    beat_ratio: 0.6,
    ...overrides,
  }
}

function stat(id: string, overrides: Partial<TaiwanSignalStat> = {}): TaiwanSignalStat {
  return {
    id,
    source: 'builtin',
    direction: 'bullish',
    name: null,
    event_kind: 'cross',
    evaluated: true,
    current: true,
    first_event_date: '2024-03-01',
    last_event_date: '2026-10-28',
    horizons: [horizon(20), horizon(60)],
    ...overrides,
  }
}

function statsResponse(overrides: Partial<TaiwanSignalStatsResponse> = {}): TaiwanSignalStatsResponse {
  return {
    symbol: '2330.TWSE',
    exchange: 'TWSE',
    applicable: true,
    not_applicable_reason: null,
    status: 'ok',
    as_of: '2026-10-28',
    history_from: '2024-01-02',
    prices_through: '2026-10-28',
    institutional_from: '2025-11-01',
    adjustment_status: 'ok',
    adjustment_reason: null,
    method: { horizons: [20, 60], min_samples: 5, state_gap_sessions: 20, new_high_sessions: 252, foreign_streak_sessions: 5 },
    benchmark: { index_name: '發行量加權股價報酬指數', covered_from: '2026-10-29', covered_through: '2026-10-29' },
    signals: [
      stat('signal_macd_golden'),
      stat('signal_ma20_breakdown', {
        direction: 'bearish',
        horizons: [horizon(20, { sufficient: false, completed_count: 3, mean_return: null, median_return: null, up_ratio: null }),
          horizon(60, { sufficient: false, completed_count: 2, mean_return: null, median_return: null, up_ratio: null })],
      }),
      stat('foreign_buy_5d', { source: 'computed', name: '外資連5買', event_kind: 'state',
        horizons: [horizon(20, { benchmark_sufficient: false, benchmark_count: 1, excess_mean: null, beat_ratio: null }), horizon(60)] }),
      stat('signal_n_day_high', { event_kind: 'state', current: false }),
      stat('vp_low', { source: 'volume_pattern', direction: 'neutral', evaluated: false, current: null, horizons: [] }),
    ],
    revenue: { status: 'ok', holds: true, period: '2026-09', yoy_pct: 25.0, threshold_pct: 20, history_stats: false },
    ...overrides,
  }
}

function signalsResponse(symbol = '2330.TWSE'): TaiwanCurrentSignalsResponse {
  return {
    symbol,
    exchange: 'TWSE',
    applicable: true,
    not_applicable_reason: null,
    signals: {
      status: 'ok',
      as_of: '2026-10-28',
      triggered: [
        { id: 'signal_macd_golden', source: 'builtin', direction: 'bullish', name: null },
        { id: 'signal_ma20_breakdown', source: 'builtin', direction: 'bearish', name: null },
      ],
      not_evaluated: [],
    },
    strategies: { status: 'no_cache', as_of: null, updated_at: null, market_state: null, grading: null, selections: [] },
  }
}

function query(overrides: Partial<SignalStatsQuery> = {}): SignalStatsQuery {
  return { response: statsResponse(), isLoading: false, isError: false, isFetching: false, onRetry: () => {}, ...overrides }
}

function renderCard(stats: SignalStatsQuery = query(), symbol = '2330.TWSE') {
  return render(
    <TaiwanCurrentSignals
      symbol={symbol}
      response={signalsResponse(symbol)}
      isLoading={false}
      isError={false}
      isFetching={false}
      onRetry={() => {}}
      stats={stats}
    />,
  )
}

describe('TaiwanCurrentSignals with signal statistics', () => {
  it('puts a 60-day summary beside each triggered tag', () => {
    renderCard()
    const bullish = screen.getByTestId('current-signals-bullish')
    expect(within(bullish).getByText('MACD金叉')).toHaveClass('text-bull')
    expect(within(bullish).getByTestId('signal-stat-summary')).toHaveTextContent('60日 上漲比例 67%・樣本 6')
    expect(within(screen.getByTestId('current-signals-bearish')).getByTestId('signal-stat-summary'))
      .toHaveTextContent('60日 樣本不足（2）')
  })

  it('lists the currently true signals in the 20 and 60 day table', () => {
    renderCard()
    const table = screen.getByTestId('signal-stats-current')
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(1 + 3 * 2)
    expect(within(rows[1]).getByText('MACD金叉')).toBeInTheDocument()
    expect(rows[1]).toHaveTextContent('20 日')
    expect(rows[1]).toHaveTextContent('+5.1%')
    expect(rows[1]).toHaveTextContent('67%')
    expect(rows[1]).toHaveTextContent('+1.2%')
    expect(rows[1]).toHaveTextContent('60%')
    expect(rows[1]).toHaveTextContent('漲停 1・其他 2')
    expect(rows[2]).toHaveTextContent('60 日')
    expect(within(table).getByText('外資連5買')).toBeInTheDocument()
    expect(within(table).queryByText('60日收盤新高')).not.toBeInTheDocument()
  })

  it('shows 樣本不足 and 基準不足 instead of numbers', () => {
    renderCard()
    const table = screen.getByTestId('signal-stats-current')
    expect(within(table).getAllByText('樣本不足')).toHaveLength(2)
    expect(within(table).getByText('基準不足（1）')).toBeInTheDocument()
  })

  it('keeps signals that are not true now in a collapsed list and skips unevaluated ones', () => {
    renderCard()
    const others = screen.getByTestId('signal-stats-others')
    expect(within(others).getByText('60日收盤新高')).toBeInTheDocument()
    expect(screen.queryByText('低量')).not.toBeInTheDocument()
  })

  it('shows the revenue condition without history statistics', () => {
    renderCard()
    const revenue = screen.getByTestId('signal-stats-revenue')
    expect(revenue).toHaveTextContent('月營收年增 ≥ 20%：目前成立（2026-09 年增 25%）')
    expect(revenue).toHaveTextContent('歷史統計不提供')
  })

  it('states entry, adjustment, costs, dedup, overlap and benchmark notes', () => {
    renderCard()
    const notes = screen.getByTestId('signal-stats-notes')
    expect(notes).toHaveTextContent('下一個交易日開盤進場')
    expect(notes).toHaveTextContent('前復權含息，不扣交易成本')
    expect(notes).toHaveTextContent('少於 5 次顯示「樣本不足」')
    expect(notes).toHaveTextContent('不足 20 個交易日不重算')
    expect(notes).toHaveTextContent('持有期間可能重疊')
    expect(notes).toHaveTextContent('上櫃股也用這個指數')
    expect(notes).toHaveTextContent('多一個隔夜')
    expect(notes).toHaveTextContent('自 2026-10-29 起累積')
    expect(notes).toHaveTextContent('法人資料最早日 2025-11-01')
  })

  it('says when nothing holds now and when nothing is published', () => {
    const view = renderCard(query({ response: statsResponse({ signals: [stat('signal_macd_golden', { current: false })] }) }))
    expect(screen.getByTestId('signal-stats-none')).toBeInTheDocument()
    view.unmount()
    renderCard(query({ response: statsResponse({ status: 'unpublished', signals: [], revenue: null }) }))
    expect(within(screen.getByTestId('signal-stats')).getByText('尚無已發佈的盤後指標資料。')).toBeInTheDocument()
  })

  it('shows a loading state and keeps tags without a summary meanwhile', () => {
    renderCard(query({ isLoading: true, response: undefined }))
    expect(screen.getByTestId('signal-stats-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('signal-stat-summary')).not.toBeInTheDocument()
    expect(screen.getByText('MACD金叉')).toBeInTheDocument()
  })

  it('shows an error with a retry', () => {
    const onRetry = vi.fn()
    renderCard(query({ isError: true, onRetry }))
    expect(screen.getByText('訊號統計暫時讀取失敗')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(onRetry).toHaveBeenCalledOnce()
    expect(screen.queryByTestId('signal-stats-current')).not.toBeInTheDocument()
  })

  it('does not show the previous symbol statistics after a switch', () => {
    renderCard(query(), '2317.TWSE')
    expect(screen.getByTestId('signal-stats-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('signal-stat-summary')).not.toBeInTheDocument()
    expect(screen.queryByTestId('signal-stats-current')).not.toBeInTheDocument()
  })
})
