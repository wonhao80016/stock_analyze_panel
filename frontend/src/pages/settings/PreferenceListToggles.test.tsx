import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { SettingsMenuSettingsPanel } from './MenuSettings'
import { SettingsMonitoringPanel } from './Monitoring'

const delayedEcho = <K extends string>(key: K) => (value: string[]) =>
  new Promise<Record<K, string[]>>(resolve => setTimeout(() => resolve({ [key]: value } as Record<K, string[]>), 20))

vi.mock('@/lib/api', () => ({
  api: {
    preferences: vi.fn(),
    saveNavOrder: vi.fn(),
    saveNavHidden: vi.fn(),
    quoteStatus: vi.fn().mockResolvedValue({ mode: 'watchlist', running: false, is_trading_hours: false, paused: false }),
    quoteInterval: vi.fn().mockResolvedValue({ interval: 6, min_interval: 6, max_interval: 60 }),
    watchlistList: vi.fn().mockResolvedValue([]),
    updateWebhookDefaultChannels: vi.fn(),
  },
}))

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

function renderWithClient(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('menu visibility toggles', () => {
  it('keeps both items hidden when two rows are hidden in quick succession', async () => {
    vi.mocked(api.preferences).mockResolvedValue({ nav_order: [], nav_hidden: [] } as never)
    vi.mocked(api.saveNavHidden).mockImplementation(delayedEcho('nav_hidden') as never)
    renderWithClient(<SettingsMenuSettingsPanel />)

    await screen.findByText('台股選股')
    const hideButtons = screen.getAllByTitle('隱藏')
    fireEvent.click(hideButtons[0])
    fireEvent.click(hideButtons[1])

    await waitFor(() => expect(api.saveNavHidden).toHaveBeenCalledTimes(2))
    const [first, second] = vi.mocked(api.saveNavHidden).mock.calls.map(([hidden]) => hidden)
    expect(first).toHaveLength(1)
    expect(second).toHaveLength(2)
    expect(second).toEqual(expect.arrayContaining(first))
  })
})

describe('default notification channel toggles', () => {
  it('keeps both channels when LINE and Telegram are ticked in quick succession', async () => {
    vi.mocked(api.preferences).mockResolvedValue({ webhook_default_channels: [] } as never)
    vi.mocked(api.updateWebhookDefaultChannels).mockImplementation(delayedEcho('webhook_default_channels') as never)
    renderWithClient(<SettingsMonitoringPanel />)

    await waitFor(() => expect(screen.getAllByTitle('作為新建規則的預設推播管道')).toHaveLength(2))
    const [line, telegram] = screen.getAllByTitle('作為新建規則的預設推播管道')
    await waitFor(() => expect(api.preferences).toHaveBeenCalled())
    fireEvent.click(line)
    fireEvent.click(telegram)

    await waitFor(() => expect(api.updateWebhookDefaultChannels).toHaveBeenCalledTimes(2))
    const calls = vi.mocked(api.updateWebhookDefaultChannels).mock.calls.map(([channels]) => channels)
    expect(calls[0]).toEqual(['line'])
    expect([...calls[1]].sort()).toEqual(['line', 'telegram'])
  })
})
