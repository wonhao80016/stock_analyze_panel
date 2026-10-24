import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type Preferences } from '@/lib/api'
import { usePreferences } from '@/lib/useSharedQueries'
import { SettingsDataSourcesPanel } from './DataSources'

vi.mock('@/lib/api', () => ({
  api: {
    dataSources: vi.fn(),
    dataSource: vi.fn(),
    updateDataProviders: vi.fn().mockResolvedValue({}),
    reloadDataSources: vi.fn(),
    deleteDataSource: vi.fn(),
    installPlugin: vi.fn(),
    uninstallPlugin: vi.fn(),
    taiwanHistoryStatus: vi.fn(() => new Promise(() => {})),
    taiwanEnrichmentStatus: vi.fn(() => new Promise(() => {})),
  },
}))

vi.mock('@/lib/useSharedQueries', () => ({
  usePreferences: vi.fn(),
}))

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

const SOURCES = {
  builtin: [{ name: 'taiwan', display_name: '台灣官方資料源 (TWSE/TPEx)', datasets: ['daily', 'realtime', 'financial'] }],
  plugins: [],
  custom: [{ name: 'mysrc', display_name: 'My Source', datasets: ['daily', 'adj_factor', 'realtime'] }],
  errors: [],
  config_dir: '/tmp/data_sources',
}

function renderPanel(prefs: Partial<Preferences> = {}) {
  vi.mocked(api.dataSources).mockResolvedValue(SOURCES as never)
  vi.mocked(usePreferences).mockReturnValue({
    data: {
      daily_data_provider: 'taiwan',
      adj_factor_provider: 'same_as_daily',
      realtime_data_provider: 'taiwan',
      minute_data_provider: 'taiwan',
      ...prefs,
    },
  } as ReturnType<typeof usePreferences>)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsDataSourcesPanel />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('built-in Taiwan source card', () => {
  it('lists the financial dataset once', async () => {
    renderPanel()

    const name = await screen.findByText('台灣官方資料源 (TWSE/TPEx)', { selector: 'span' })
    const card = name.closest('div.relative') as HTMLElement
    expect(within(card).getAllByText('財務')).toHaveLength(1)
  })
})

describe('daily and adjustment factor routing in Taiwan mode', () => {
  it('offers no provider choice for daily K or adjustment factors and says why', async () => {
    renderPanel()

    await screen.findByText('My Source', { selector: 'span' })
    expect(screen.queryByTitle('「日K」由 My Source 提供')).toBeNull()
    expect(screen.queryByTitle('「除權因子」由 My Source 提供')).toBeNull()
    expect(screen.queryByText('跟隨日K')).toBeNull()
    expect(screen.getByTitle('「即時行情」由 My Source 提供')).toBeInTheDocument()
    expect(screen.getAllByText(/台股日K與除權因子固定由台灣官方資料源提供/).length).toBeGreaterThan(0)
  })

  it('keeps daily K and adjustment factors on the official source when a custom source is applied', async () => {
    renderPanel()

    const name = await screen.findByText('My Source', { selector: 'span' })
    const card = name.closest('div.relative') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: '使用' }))

    await waitFor(() => expect(api.updateDataProviders).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.updateDataProviders).mock.calls[0][0]).toMatchObject({
      daily_data_provider: 'taiwan',
      adj_factor_provider: 'same_as_daily',
      realtime_data_provider: 'mysrc',
    })
  })

  it('never marks a custom source as serving daily K, even from a stale legacy preference', async () => {
    renderPanel({ daily_data_provider: 'mysrc', adj_factor_provider: 'mysrc' })

    const name = await screen.findByText('My Source', { selector: 'span' })
    const card = name.closest('div.relative') as HTMLElement
    expect(within(card).queryByText('服務中')).toBeNull()
    expect(screen.queryByRole('button', { name: '恢復預設' })).toBeNull()
  })

  it('cannot apply a source that only offers daily K or adjustment factors, and says why', async () => {
    vi.mocked(api.dataSources).mockResolvedValue({
      ...SOURCES,
      custom: [{ name: 'dailyonly', display_name: 'Daily Only', datasets: ['daily', 'adj_factor'] }],
    } as never)
    vi.mocked(usePreferences).mockReturnValue({ data: {} } as ReturnType<typeof usePreferences>)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <SettingsDataSourcesPanel />
      </QueryClientProvider>,
    )

    const name = await screen.findByText('Daily Only', { selector: 'span' })
    const card = name.closest('div.relative') as HTMLElement
    const use = within(card).getByRole('button', { name: '使用' })
    expect(use).toBeDisabled()
    expect(use).toHaveAttribute('title', expect.stringContaining('台股日K與除權因子固定由台灣官方資料源提供'))
    fireEvent.click(use)
    expect(api.updateDataProviders).not.toHaveBeenCalled()
  })
})
