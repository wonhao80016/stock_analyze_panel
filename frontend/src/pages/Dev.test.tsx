import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Dev } from './Dev'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { klineMinute: vi.fn() },
}))

vi.mock('@/components/PageHeader', () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}))

const row = { datetime: '', open: 1, high: 1, low: 1, close: 1, volume: 1, amount: 1 }

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  // Tuesday 00:30 local time: the UTC calendar date is still Monday in Taipei.
  vi.setSystemTime(new Date(2026, 9, 20, 0, 30))
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

async function probe(days: number) {
  render(<Dev />)
  fireEvent.change(screen.getByDisplayValue('10'), { target: { value: String(days) } })
  fireEvent.click(screen.getByText('開始探測'))
  await waitFor(() => expect(screen.getByText('開始探測')).toBeInTheDocument())
}

describe('Dev minute probe', () => {
  it('walks back local calendar dates and skips weekends', async () => {
    vi.mocked(api.klineMinute).mockResolvedValue({ rows: [row], source: 'local' } as any)
    await probe(4)

    expect(vi.mocked(api.klineMinute).mock.calls.map(call => call[1])).toEqual([
      '2026-10-20',
      '2026-10-19',
      '2026-10-16',
      '2026-10-15',
    ])
  })

  it('counts a day as complete only when the backend served the full local copy', async () => {
    vi.mocked(api.klineMinute)
      .mockResolvedValueOnce({ rows: [row], source: 'local' } as any)
      .mockResolvedValueOnce({ rows: [row], source: 'local_partial' } as any)
      .mockResolvedValueOnce({ rows: [row], source: 'live' } as any)
      .mockResolvedValueOnce({ rows: [], source: 'none' } as any)
    await probe(4)

    const complete = screen.getByText('完整', { selector: 'div' }).parentElement!
    expect(within(complete).getByText('1')).toBeInTheDocument()
    const incomplete = screen.getByText('不完整或缺失', { selector: 'div' }).parentElement!
    expect(within(incomplete).getByText('3')).toBeInTheDocument()
  })
})
