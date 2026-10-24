import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Auth } from './Auth'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    authStatus: vi.fn(),
    authSetup: vi.fn(),
    authLogin: vi.fn(),
  },
}))

function LocationSpy() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function renderAuth() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<Auth />} />
          <Route path="/" element={<LocationSpy />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('Auth page', () => {
  it('signs in right after the first password is set, so the panel does not bounce back to /login', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: false, authenticated: false })
    vi.mocked(api.authSetup).mockResolvedValue({ ok: true })
    vi.mocked(api.authLogin).mockResolvedValue({ ok: true })
    renderAuth()

    fireEvent.change(await screen.findByPlaceholderText('訪問密碼'), { target: { value: 'secret1' } })
    fireEvent.change(screen.getByPlaceholderText('再次輸入密碼'), { target: { value: 'secret1' } })
    fireEvent.click(screen.getByRole('button', { name: '設置並進入' }))

    await waitFor(() => expect(api.authLogin).toHaveBeenCalledWith('secret1'))
    expect(api.authSetup).toHaveBeenCalledWith('secret1')
    expect(await screen.findByTestId('location')).toHaveTextContent('/')
  })

  it('shows the status error with a retry instead of the first-time setup form', async () => {
    vi.mocked(api.authStatus)
      .mockRejectedValueOnce(new Error('503 Service Unavailable'))
      .mockResolvedValueOnce({ configured: true, authenticated: false })
    renderAuth()

    expect(await screen.findByText(/503 Service Unavailable/)).toBeInTheDocument()
    expect(screen.queryByText('設置訪問密碼')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('再次輸入密碼')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重試' }))

    expect(await screen.findByText('登錄訪問')).toBeInTheDocument()
    expect(api.authStatus).toHaveBeenCalledTimes(2)
  })
})
