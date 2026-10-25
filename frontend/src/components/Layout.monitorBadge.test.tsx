import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from './Layout'
import { usePreferences, useSettings, useQuoteStatus, useVersion } from '@/lib/useSharedQueries'
import { useToggleRealtimeQuotes } from '@/lib/useSharedMutations'
import { useQuoteStream, useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { api } from '@/lib/api'

vi.mock('@/components/StockPreviewDialog', () => ({ StockPreviewDialog: () => null }))

vi.mock('@/lib/api', () => ({
  api: {
    dataSources: vi.fn().mockResolvedValue({ builtin: [], plugins: [], custom: [] }),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    watchlistEnriched: vi.fn().mockResolvedValue({ rows: [] }),
    alertsList: vi.fn(),
    intradayRefresh: vi.fn().mockResolvedValue({}),
    authStatus: vi.fn().mockResolvedValue({ configured: true, authenticated: true }),
    authLogout: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

vi.mock('@/lib/useSharedQueries', () => ({
  useSettings: vi.fn(),
  usePreferences: vi.fn(),
  useQuoteStatus: vi.fn(),
  useVersion: vi.fn(),
}))
vi.mock('@/lib/useSharedMutations', () => ({ useToggleRealtimeQuotes: vi.fn() }))
vi.mock('@/lib/useQuoteStream', () => ({ useQuoteStream: vi.fn(), useQuoteStreamStatus: vi.fn() }))
vi.mock('@/extensions/registry', () => ({ getFrontendExtensionNavigation: vi.fn(() => []) }))
vi.mock('@/extensions/ExtensionSlot', () => ({ ExtensionSlot: () => null }))
vi.mock('@/components/Toast', () => ({ ToastContainer: () => null, toast: vi.fn() }))
vi.mock('@/components/AlertToast', () => ({ AlertToastContainer: () => null }))

function alerts(...ts: number[]) {
  return ts.map(t => ({ id: `a${t}`, ts: t, source: 'monitor', type: 'price', message: 'm' }))
}

beforeEach(() => {
  vi.mocked(useSettings).mockReturnValue({ data: undefined } as any)
  vi.mocked(usePreferences).mockReturnValue({ data: {} } as any)
  vi.mocked(useQuoteStatus).mockReturnValue({ data: undefined } as any)
  vi.mocked(useVersion).mockReturnValue({ data: undefined } as any)
  vi.mocked(useToggleRealtimeQuotes).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as any)
  vi.mocked(useQuoteStream).mockReturnValue(undefined as any)
  vi.mocked(useQuoteStreamStatus).mockReturnValue('connected' as any)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('Layout monitor badge', () => {
  it('counts alerts newer than the last seen one even after the store pruned old records', async () => {
    // The store is at its record cap: every new alert pushes one old record out, so the total stays flat.
    vi.mocked(api.alertsList)
      .mockResolvedValueOnce({ alerts: alerts(3000, 2000, 1000), total: 5000 } as any)
      .mockResolvedValue({ alerts: alerts(6000, 5000, 4000, 3000), total: 5000 } as any)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/']}>
          <Layout />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const monitorLink = (await screen.findByText('監控中心')).closest('a')!
    await waitFor(() => expect(api.alertsList).toHaveBeenCalledTimes(1))
    expect(monitorLink).not.toHaveTextContent('3')

    await act(async () => {
      await qc.refetchQueries({ queryKey: ['alerts-total'] })
    })

    await waitFor(() => expect(monitorLink).toHaveTextContent('監控中心3'))
  })
})
