import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Onboarding } from './Onboarding'
import { OnboardingGuard } from '@/router'
import { toast } from '@/components/Toast'

// The real api module runs, so a toast raised by the request layer is counted too.
vi.mock('@/components/Toast', () => ({ toast: vi.fn(), ToastContainer: () => null }))

function respond(status: number, body: unknown) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    json: async () => body,
    text: async () => JSON.stringify(body),
  }
}

function stubBackend(completeStatus: number) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/settings/onboarding/complete') {
      return completeStatus < 400
        ? respond(completeStatus, { ok: true, onboarding_completed: true })
        : respond(completeStatus, { detail: '偏好設定檔唯讀' })
    }
    if (url === '/api/settings') return respond(200, { onboarding_completed: false })
    if (url === '/api/taiwan/data-status') return respond(200, { daily_status: 'unavailable' })
    return respond(404, { detail: `unexpected ${url}` })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function LocationSpy() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function renderFlow(initialPath: string, onboarding = <Onboarding />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/onboarding" element={onboarding} />
          <Route path="/" element={<OnboardingGuard><LocationSpy /></OnboardingGuard>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(toast).mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

describe('Onboarding completion when the marker cannot be written', () => {
  it('lets the user in for this session with a single toast that carries the backend reason', async () => {
    const fetchMock = stubBackend(500)
    renderFlow('/onboarding')

    fireEvent.click(await screen.findByText('我已了解,繼續'))
    fireEvent.click(await screen.findByText('稍後再說'))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/settings/onboarding/complete', expect.anything()))
    expect(await screen.findByTestId('location')).toHaveTextContent('/')
    expect(screen.queryByText('我已了解,繼續')).not.toBeInTheDocument()
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/未寫入.*偏好設定檔唯讀/), 'error')
  })

  it('still sends an unfinished onboarding to the wizard when nothing was attempted', async () => {
    stubBackend(200)
    renderFlow('/', <div>wizard</div>)
    expect(await screen.findByText('wizard')).toBeInTheDocument()
  })
})
