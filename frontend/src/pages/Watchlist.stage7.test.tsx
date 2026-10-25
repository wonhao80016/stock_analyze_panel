import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Link, useLocation } from 'react-router-dom'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { Watchlist } from './Watchlist'
import { api } from '@/lib/api'
import { setVolumeUnit } from '@/lib/volumeUnit'
import { storage } from '@/lib/storage'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  const fns: Record<string, any> = {}
  return {
    ...actual,
    api: new Proxy(fns, {
      get: (target, key: string) => (target[key] ??= vi.fn().mockResolvedValue({})),
    }),
  }
})
vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: () => null }))
vi.mock('@/extensions/ExtensionSlot', () => ({ ExtensionSlot: () => null }))

const GROUPS = [
  { id: 'g1', name: '核心', color: 'sky' },
  { id: 'g2', name: '觀察', color: 'rose' },
]

function row(symbol: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    symbol, name, asset_type: 'stock', close: 100, prev_close: 99, change_pct: 0.01, change_amount: 1,
    amount: 1e8, turnover_rate: 1.2, amplitude: 0.02, vol_ratio_5d: 1, rsi_14: 50,
    ...overrides,
  }
}

// The filter panel pairs each column label with its min and max inputs.
function filterMinInput(label: string) {
  const span = screen.getAllByText(label).find(el => el.parentElement?.querySelector('input[type="number"]'))
  if (!span) throw new Error(`no filter for ${label}`)
  return span.parentElement!.querySelector('input') as HTMLInputElement
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.search}</div>
}

function renderWatchlist(initialUrl = '/watchlist') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <Link to="/watchlist?group=g1">側欄核心</Link>
        <LocationProbe />
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return queryClient
}

function mockWatchlist(rows: any[], groupIds: Record<string, string[]> = {}) {
  vi.mocked(api.watchlistList).mockResolvedValue({
    symbols: rows.map(r => ({ symbol: r.symbol, added_at: '2026-10-01', name: r.name, group_ids: groupIds[r.symbol] ?? [] })),
  } as any)
  vi.mocked(api.watchlistEnriched).mockResolvedValue({ rows, as_of: '2026-10-23', elapsed_ms: 1 } as any)
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(api.watchlistGroups).mockResolvedValue({ groups: GROUPS } as any)
  vi.mocked(api.watchlistColumns).mockResolvedValue({ columns: null } as any)
  vi.mocked(api.watchlistOcrStatus).mockResolvedValue({ available: false } as any)
  vi.mocked(api.quoteStatus).mockResolvedValue({ running: false } as any)
  vi.mocked(api.preferences).mockResolvedValue({} as any)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('B066 numeric filters use the displayed units', () => {
  it('reads a 3 typed into the change filter as 3%', async () => {
    mockWatchlist([
      row('2330.TWSE', '台積電', { change_pct: 0.05 }),
      row('2317.TWSE', '鴻海', { change_pct: 0.01 }),
    ])
    renderWatchlist()
    await screen.findByText('鴻海')
    fireEvent.click(screen.getByTitle('篩選'))
    fireEvent.change(filterMinInput('漲跌幅'), { target: { value: '3' } })
    await waitFor(() => expect(screen.queryByText('鴻海')).not.toBeInTheDocument())
    expect(screen.getByText('台積電')).toBeInTheDocument()
  })

  it('labels each filter with its unit', async () => {
    mockWatchlist([row('2330.TWSE', '台積電')])
    renderWatchlist()
    await screen.findByText('台積電')
    fireEvent.click(screen.getByTitle('篩選'))
    expect(screen.getByLabelText('漲跌幅 下限 (%)')).toBe(filterMinInput('漲跌幅'))
    expect(screen.getByLabelText('成交額 上限 (億)')).toBeInTheDocument()
  })

  it('keeps turnover in percent and amount in 億', async () => {
    mockWatchlist([
      row('2330.TWSE', '台積電', { turnover_rate: 3.5, amount: 5e9 }),
      row('2317.TWSE', '鴻海', { turnover_rate: 0.8, amount: 5e7 }),
    ])
    renderWatchlist()
    await screen.findByText('鴻海')
    fireEvent.click(screen.getByTitle('篩選'))
    fireEvent.change(filterMinInput('換手率'), { target: { value: '1' } })
    await waitFor(() => expect(screen.queryByText('鴻海')).not.toBeInTheDocument())
    fireEvent.change(filterMinInput('換手率'), { target: { value: '' } })
    await screen.findByText('鴻海')
    fireEvent.change(filterMinInput('成交額'), { target: { value: '1' } })
    await waitFor(() => expect(screen.queryByText('鴻海')).not.toBeInTheDocument())
    expect(screen.getByText('台積電')).toBeInTheDocument()
  })
})

describe('B316 the URL is the selected group', () => {
  it('writes an in-page group switch to the URL', async () => {
    mockWatchlist([row('2330.TWSE', '台積電')], { '2330.TWSE': ['g1'] })
    renderWatchlist()
    fireEvent.click(await screen.findByRole('tab', { name: /核心/ }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('?group=g1'))
    fireEvent.click(screen.getByRole('tab', { name: /全部/ }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe(''))
  })

  it('follows a sidebar link back to the group shown in the URL', async () => {
    mockWatchlist([row('2330.TWSE', '台積電')], { '2330.TWSE': ['g1'] })
    renderWatchlist('/watchlist?group=g1')
    expect(await screen.findByRole('tab', { name: /核心/, selected: true })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /全部/ }))
    await screen.findByRole('tab', { name: /全部/, selected: true })
    fireEvent.click(screen.getByRole('link', { name: '側欄核心' }))
    expect(await screen.findByRole('tab', { name: /核心/, selected: true })).toBeInTheDocument()
  })
})

describe('B317 a failed background refresh keeps the cached rows', () => {
  it('shows the rows with a non-blocking notice', async () => {
    mockWatchlist([row('2330.TWSE', '台積電')])
    const qc = renderWatchlist()
    await screen.findByText('台積電')
    vi.mocked(api.watchlistEnriched).mockRejectedValue(new Error('boom'))
    await qc.refetchQueries({ queryKey: ['watchlist-enriched'] })
    expect(await screen.findByRole('status')).toHaveTextContent('行情更新失敗')
    expect(screen.getByText('台積電')).toBeInTheDocument()
    expect(screen.queryByText('讀取自選行情失敗')).not.toBeInTheDocument()
  })

  it('still reports an error when there is no data yet', async () => {
    vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [{ symbol: '2330.TWSE', added_at: '2026-10-01', group_ids: [] }] } as any)
    vi.mocked(api.watchlistEnriched).mockRejectedValue(new Error('boom'))
    renderWatchlist()
    expect(await screen.findByText('讀取自選行情失敗')).toBeInTheDocument()
  })
})

describe('B318 Taiwan rows show the published signals', () => {
  it('renders signal tags from the enriched row', async () => {
    mockWatchlist([row('2330.TWSE', '台積電', { signal_macd_golden: true })])
    renderWatchlist()
    const name = await screen.findByText('台積電')
    const tr = name.closest('tr') as HTMLElement
    expect(within(tr).getByText('MACD金叉')).toBeInTheDocument()
  })
})

describe('F2 signal cells carry the indicator date', () => {
  it('titles the signal cell with the partition date', async () => {
    mockWatchlist([row('2330.TWSE', '台積電', { signal_macd_golden: true, indicator_date: '2026-10-22' })])
    renderWatchlist()
    const tag = await screen.findByText('MACD金叉')
    expect(tag.closest('td')).toHaveAttribute('title', '指標日期 2026-10-22')
  })
})

describe('F8 volume filter unit follows the setting', () => {
  it('labels the volume MA filter in shares when shares are shown', async () => {
    setVolumeUnit('shares')
    mockWatchlist([
      row('2330.TWSE', '台積電', { vol_ma5: 5000 }),
      row('2317.TWSE', '鴻海', { vol_ma5: 500 }),
    ])
    renderWatchlist()
    await screen.findByText('鴻海')
    fireEvent.click(screen.getByTitle('篩選'))
    fireEvent.change(screen.getByLabelText('量MA5 下限 (股)'), { target: { value: '1000' } })
    await waitFor(() => expect(screen.queryByText('鴻海')).not.toBeInTheDocument())
    expect(screen.getByText('台積電')).toBeInTheDocument()
  })
})

describe('F2 flags from an earlier session are dated on screen', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    // 10:00 in Taipei on 2026-10-23.
    vi.setSystemTime(new Date('2026-10-23T02:00:00Z'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function withLimitColumn() {
    vi.mocked(api.watchlistColumns).mockResolvedValue({
      columns: [{ id: 'builtin:limit_ups', source: { type: 'builtin', key: 'limit_ups' }, label: '連續漲停', visible: true, align: 'center' }],
    } as any)
  }

  it('tags the signal and limit-streak headers with the previous session date', async () => {
    withLimitColumn()
    mockWatchlist([row('2330.TWSE', '台積電', { signal_macd_golden: true, consecutive_limit_ups: 2, indicator_date: '2026-10-22' })])
    renderWatchlist()
    await screen.findByText('MACD金叉')
    const signalHeader = screen.getByRole('columnheader', { name: /訊號/ })
    expect(signalHeader).toHaveTextContent('10-22')
    expect(await screen.findByRole('columnheader', { name: /連續漲停/ })).toHaveTextContent('10-22')
  })

  it('shows no tag when the flags are from today in Taipei', async () => {
    mockWatchlist([row('2330.TWSE', '台積電', { signal_macd_golden: true, indicator_date: '2026-10-23' })])
    renderWatchlist()
    await screen.findByText('MACD金叉')
    expect(screen.getByRole('columnheader', { name: /訊號/ })).not.toHaveTextContent('10-2')
  })

  it('dates the card limit-streak and signal badges', async () => {
    storage.watchlistView.set('card')
    mockWatchlist([row('2330.TWSE', '台積電', { signal_macd_golden: true, consecutive_limit_ups: 2, indicator_date: '2026-10-22' })])
    renderWatchlist()
    const streak = await screen.findByText('2連')
    expect(streak.parentElement).toHaveTextContent('2連10-22')
    expect(screen.getByText('MACD金叉').parentElement).toHaveTextContent('10-22')
  })
})
