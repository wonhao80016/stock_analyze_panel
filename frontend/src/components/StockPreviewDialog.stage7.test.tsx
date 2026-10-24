import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StockPreviewDialog } from './StockPreviewDialog'

const panelProps = vi.hoisted(() => [] as Record<string, unknown>[])
const shared = vi.hoisted(() => ({
  prefs: { minute_intraday_refresh: true, minute_intraday_refresh_interval: 6 } as Record<string, unknown>,
  running: true,
}))

vi.mock('@/lib/api', () => ({
  api: {
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    monitorRulesList: vi.fn().mockResolvedValue({ rules: [] }),
  },
}))
vi.mock('@/lib/useSharedQueries', () => ({
  usePreferences: () => ({ data: shared.prefs }),
  useQuoteStatus: () => ({ data: { running: shared.running } }),
}))
vi.mock('@/lib/useQuoteStream', () => ({ setFocusSymbol: vi.fn(), clearFocusSymbol: vi.fn() }))
vi.mock('@/components/StockPanel', async () => ({
  getDefaultRange: (await vi.importActual<typeof import('@/components/StockDailyKChart')>('@/components/StockDailyKChart')).getDefaultRange,
  StockPanel: (props: Record<string, unknown>) => {
    panelProps.push(props)
    return null
  },
}))
vi.mock('@/components/StockMultiDayIntradayChart', () => ({ StockMultiDayIntradayChart: () => null }))
vi.mock('@/components/monitor/RuleEditor', () => ({ RuleEditor: () => null }))
vi.mock('@/components/stock-analysis/PriceAlertDialog', () => ({ PriceAlertDialog: () => null }))
vi.mock('@/extensions/ExtensionSlot', () => ({ ExtensionSlot: () => null }))
vi.mock('@/components/WatchlistAddMenu', () => ({ WatchlistAddMenu: () => null }))
vi.mock('@/pages/TaiwanStockDetail', () => ({ TaiwanStockDetail: () => null }))
vi.mock('@/components/DatePicker', () => ({
  DatePicker: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <input aria-label="date" value={value} onChange={e => onChange(e.target.value)} />
  ),
}))

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname + location.search}</div>
}

function dialog(symbol: string | null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/watchlist']}>
        <StockPreviewDialog symbol={symbol} onClose={() => {}} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function lastDailyPanel(): Record<string, unknown> {
  const daily = panelProps.filter(p => p.showIntraday)
  return daily[daily.length - 1]
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  panelProps.length = 0
  shared.running = true
  localStorage.clear()
})

describe('StockPreviewDialog daily range', () => {
  it('ends the default range on the Taipei date, not the UTC date', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-23T17:30:00Z'))

    render(dialog('2330.TWSE'))

    expect(lastDailyPanel().dateRange).toEqual({ start: '2026-04-24', end: '2026-10-24' })
  })

  it('recomputes the range when the long-lived dialog opens again on a later day', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-22T02:00:00Z'))
    const { rerender } = render(dialog('2330.TWSE'))
    rerender(dialog(null))

    vi.setSystemTime(new Date('2026-10-26T02:00:00Z'))
    rerender(dialog('2317.TWSE'))

    expect(lastDailyPanel().dateRange).toEqual({ start: '2026-04-26', end: '2026-10-26' })
  })
})

describe('StockPreviewDialog daily intraday polling', () => {
  it('polls the daily tab intraday chart while realtime refresh is on', () => {
    render(dialog('2330.TWSE'))
    expect(lastDailyPanel().refetchIntervalMs).toBe(6000)
  })

  it('leaves it static when the range ends before the Taipei today', () => {
    render(dialog('2330.TWSE'))

    fireEvent.change(screen.getAllByLabelText('date')[1], { target: { value: '2026-09-30' } })

    expect(lastDailyPanel().dateRange).toMatchObject({ end: '2026-09-30' })
    expect(lastDailyPanel().refetchIntervalMs).toBeUndefined()
  })

  it('leaves it static while realtime quotes are stopped', () => {
    shared.running = false
    render(dialog('2330.TWSE'))
    expect(lastDailyPanel().refetchIntervalMs).toBeUndefined()
  })
})

describe('StockPreviewDialog add to compare', () => {
  const FULL = ['1101.TWSE', '1216.TWSE', '2317.TWSE', '2454.TWSE', '2881.TWSE']

  it('asks before replacing the earliest symbol of a full list', () => {
    localStorage.setItem('tw_compare:last_symbols', JSON.stringify(FULL))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(dialog('2330.TWSE'))

    fireEvent.click(screen.getByRole('button', { name: '將 2330.TWSE 加入比較' }))

    expect(confirm).toHaveBeenCalledTimes(1)
    const target = new URL(`http://x${screen.getByTestId('location').textContent}`)
    expect(target.searchParams.get('symbols')?.split(',')).toEqual(['1216.TWSE', '2317.TWSE', '2454.TWSE', '2881.TWSE', '2330.TWSE'])
  })

  it('stays put when the user declines', () => {
    localStorage.setItem('tw_compare:last_symbols', JSON.stringify(FULL))
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(dialog('2330.TWSE'))

    fireEvent.click(screen.getByRole('button', { name: '將 2330.TWSE 加入比較' }))

    expect(screen.getByTestId('location').textContent).toBe('/watchlist')
  })
})
