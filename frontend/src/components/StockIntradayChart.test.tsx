import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StockIntradayChart } from './StockIntradayChart'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { klineMinute: vi.fn(), syncMinuteSingle: vi.fn() },
}))

vi.mock('@/components/EChartsIntraday', () => ({
  EChartsIntraday: ({ prevClose }: { prevClose?: number }) => (
    <div data-testid="intraday-chart" data-prev-close={prevClose == null ? 'none' : String(prevClose)} />
  ),
}))

const minuteRow = { datetime: '2025-06-17 09:01:00', open: 188, high: 188.5, low: 187.5, close: 188.2, volume: 10, amount: 1882 }

function renderChart(prevClose?: number) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <StockIntradayChart symbol="0050.TWSE" date="2025-06-17" prevClose={prevClose} />
    </QueryClientProvider>,
  )
}

async function drawnPrevClose(): Promise<string | null> {
  return (await screen.findByTestId('intraday-chart')).getAttribute('data-prev-close')
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('StockIntradayChart previous close', () => {
  it("prefers the minute endpoint's previous close, which is on the minute bars' scale", async () => {
    vi.mocked(api.klineMinute).mockResolvedValue({
      symbol: '0050.TWSE', date: '2025-06-17', rows: [minuteRow], prev_close: 188.0,
    } as any)
    renderChart(47.0)

    expect(await drawnPrevClose()).toBe('188')
  })

  it('falls back to the given previous close when the endpoint has none (regression guard)', async () => {
    vi.mocked(api.klineMinute).mockResolvedValue({
      symbol: '0050.TWSE', date: '2025-06-17', rows: [minuteRow], prev_close: null,
    } as any)
    renderChart(188.0)

    expect(await drawnPrevClose()).toBe('188')
  })
})
