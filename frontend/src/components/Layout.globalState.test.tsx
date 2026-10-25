import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from './Layout'
import { useStockPreview } from './StockPreviewProvider'
import { usePreferences, useSettings, useQuoteStatus, useVersion } from '@/lib/useSharedQueries'
import { useToggleRealtimeQuotes } from '@/lib/useSharedMutations'
import { useQuoteStream, useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { api } from '@/lib/api'
import { installAudioUnlock } from '@/lib/notificationSound'

const previewDialogModule = vi.hoisted(() => ({ loaded: vi.fn() }))

vi.mock('@/components/StockPreviewDialog', () => {
  previewDialogModule.loaded()
  return {
    StockPreviewDialog: ({ symbol }: { symbol: string | null }) =>
      symbol ? <div data-testid="preview-dialog">{symbol}</div> : null,
  }
})

vi.mock('@/lib/api', () => ({
  api: {
    dataSources: vi.fn(),
    watchlistGroups: vi.fn(),
    watchlistList: vi.fn(),
    watchlistEnriched: vi.fn(),
    alertsList: vi.fn(),
    intradayRefresh: vi.fn(),
    authStatus: vi.fn(),
    authLogout: vi.fn(),
  },
}))

vi.mock('@/lib/useSharedQueries', () => ({
  useSettings: vi.fn(),
  usePreferences: vi.fn(),
  useQuoteStatus: vi.fn(),
  useVersion: vi.fn(),
}))

vi.mock('@/lib/useSharedMutations', () => ({
  useToggleRealtimeQuotes: vi.fn(),
}))

vi.mock('@/lib/useQuoteStream', () => ({
  useQuoteStream: vi.fn(),
  useQuoteStreamStatus: vi.fn(),
}))

vi.mock('@/extensions/registry', () => ({
  getFrontendExtensionNavigation: vi.fn(() => []),
}))

vi.mock('@/extensions/ExtensionSlot', () => ({
  ExtensionSlot: () => null,
}))

vi.mock('@/components/Toast', () => ({
  ToastContainer: () => null,
  toast: vi.fn(),
}))
vi.mock('@/components/AlertToast', () => ({ AlertToastContainer: () => null }))

const audioUnlock = vi.hoisted(() => ({ uninstall: vi.fn() }))
vi.mock('@/lib/notificationSound', () => ({
  installAudioUnlock: vi.fn(() => audioUnlock.uninstall),
}))

function PreviewOpener() {
  const { openPreview } = useStockPreview()
  return <button onClick={() => openPreview('2330.TWSE', '台積電')}>open-preview</button>
}

function renderLayout(path = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<PreviewOpener />} />
            <Route path="watchlist" element={<div>watchlist-page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { qc, ...view }
}

function mockPreferences(prefs: Record<string, unknown>) {
  vi.mocked(usePreferences).mockReturnValue({ data: prefs } as any)
}

function mockQuoteStatus(status: Record<string, unknown> | undefined) {
  vi.mocked(useQuoteStatus).mockReturnValue({ data: status } as any)
}

beforeEach(() => {
  vi.mocked(api.dataSources).mockResolvedValue({ builtin: [], plugins: [], custom: [] } as any)
  vi.mocked(api.watchlistGroups).mockResolvedValue({ groups: [] } as any)
  vi.mocked(api.watchlistList).mockResolvedValue({ symbols: [] } as any)
  vi.mocked(api.watchlistEnriched).mockResolvedValue({ rows: [] } as any)
  vi.mocked(api.alertsList).mockResolvedValue({ alerts: [], total: 0 } as any)
  vi.mocked(api.intradayRefresh).mockResolvedValue({} as any)
  vi.mocked(api.authStatus).mockResolvedValue({ configured: true, authenticated: true } as any)
  vi.mocked(api.authLogout).mockResolvedValue({ ok: true } as any)
  vi.mocked(useSettings).mockReturnValue({ data: undefined } as any)
  vi.mocked(useVersion).mockReturnValue({ data: undefined } as any)
  vi.mocked(useToggleRealtimeQuotes).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as any)
  vi.mocked(useQuoteStream).mockReturnValue(undefined as any)
  vi.mocked(useQuoteStreamStatus).mockReturnValue('connected' as any)
  mockPreferences({})
  mockQuoteStatus(undefined)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('Layout global state', () => {
  it('loads the stock preview dialog only after a preview is opened', async () => {
    renderLayout('/')
    await waitFor(() => expect(api.dataSources).toHaveBeenCalled())
    expect(previewDialogModule.loaded).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('open-preview'))

    expect(await screen.findByTestId('preview-dialog')).toHaveTextContent('2330.TWSE')
    expect(previewDialogModule.loaded).toHaveBeenCalledTimes(1)
  })

  it('shares the watchlist page enriched cache entry for the sidebar group change', async () => {
    mockPreferences({ watchlist_groups_in_nav: true })
    vi.mocked(api.watchlistGroups).mockResolvedValue({
      groups: [{ id: 'g1', name: '半導體', color: 'blue' }],
    } as any)
    const { qc } = renderLayout('/watchlist')

    await waitFor(() => expect(api.watchlistEnriched).toHaveBeenCalled())
    const keys = qc.getQueryCache().findAll({ queryKey: ['watchlist-enriched'] }).map(q => q.queryKey)
    expect(keys).toEqual([['watchlist-enriched', '']])
  })

  it('treats the built-in taiwan realtime provider as built in, not as a custom source id', async () => {
    mockPreferences({ realtime_data_provider: 'taiwan', realtime_quotes_enabled: false })
    mockQuoteStatus({ mode: 'full_market', running: false, is_trading_hours: false })
    renderLayout('/')

    await screen.findByText('即時行情')
    expect(screen.queryByText('taiwan')).not.toBeInTheDocument()
    expect(screen.getByText('全市場')).toBeInTheDocument()
  })

  it('reports realtime as unavailable when the built-in taiwan provider has no realtime mode', async () => {
    mockPreferences({ realtime_data_provider: 'taiwan' })
    mockQuoteStatus({ mode: 'none', running: false, is_trading_hours: false })
    renderLayout('/')

    expect(await screen.findByText('不可用')).toBeInTheDocument()
    expect(screen.queryByText('taiwan')).not.toBeInTheDocument()
  })

  it('arms the alert sound unlock on the first user gesture while mounted', async () => {
    const { unmount } = renderLayout('/')
    await waitFor(() => expect(installAudioUnlock).toHaveBeenCalledTimes(1))
    unmount()
    expect(audioUnlock.uninstall).toHaveBeenCalledTimes(1)
  })

  it('highlights the sidebar group that the ?group= URL selects', async () => {
    mockPreferences({ watchlist_groups_in_nav: true })
    vi.mocked(api.watchlistGroups).mockResolvedValue({
      groups: [
        { id: 'g1', name: '半導體', color: 'blue' },
        { id: 'g2', name: '金融', color: 'green' },
      ],
    } as any)
    renderLayout('/watchlist?group=g2')

    const active = (await screen.findByText('金融')).closest('a')
    const inactive = screen.getByText('半導體').closest('a')
    expect(active?.className).toContain('text-accent')
    expect(inactive?.className).not.toContain('text-accent')
  })
})
