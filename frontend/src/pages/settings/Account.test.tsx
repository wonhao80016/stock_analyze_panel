import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SettingsAccountPanel } from './Account'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    authStatus: vi.fn(),
    authLogout: vi.fn().mockResolvedValue({ ok: true }),
    authChangePassword: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SettingsAccountPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function fillForm(
  container: HTMLElement,
  values: { oldPassword: string; newPassword: string; confirmPassword: string },
) {
  const inputs = container.querySelectorAll<HTMLInputElement>('input[type="password"]')
  fireEvent.change(inputs[0], { target: { value: values.oldPassword } })
  fireEvent.change(inputs[1], { target: { value: values.newPassword } })
  fireEvent.change(inputs[2], { target: { value: values.confirmPassword } })
}

beforeEach(() => {
  // The success path hard-navigates; jsdom cannot perform it, so window.location is a plain object here.
  vi.stubGlobal('location', { href: '' })
})

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('SettingsAccountPanel', () => {
  it('renders the three password fields when a panel password is configured', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: true, authenticated: true })
    const { container } = renderPanel()

    expect(await screen.findByText('舊密碼')).toBeInTheDocument()
    expect(screen.getByText('新密碼')).toBeInTheDocument()
    expect(screen.getByText('確認新密碼')).toBeInTheDocument()
    expect(container.querySelectorAll('input[type="password"]')).toHaveLength(3)
  })

  it('stays usable while authenticated is false (no-password local mode gates on configured)', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: true, authenticated: false })
    const { container } = renderPanel()

    expect(await screen.findByText('舊密碼')).toBeInTheDocument()
    expect(container.querySelectorAll('input[type="password"]')).toHaveLength(3)
  })

  it('renders the empty state and no password input when no password is configured', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: false, authenticated: false })
    const { container } = renderPanel()

    expect(await screen.findByText(/尚未設定面板存取密碼/)).toBeInTheDocument()
    expect(container.querySelectorAll('input[type="password"]')).toHaveLength(0)
    expect(screen.queryByText('舊密碼')).not.toBeInTheDocument()
  })

  it('offers the logout control when a password is configured', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: true, authenticated: true })
    renderPanel()

    expect(await screen.findByRole('button', { name: '登出' })).toBeInTheDocument()
  })

  it('hides the logout control when no password is configured (logging out would trap the user on the setup form)', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: false, authenticated: false })
    renderPanel()

    await screen.findByText(/尚未設定面板存取密碼/)
    expect(screen.queryByRole('button', { name: '登出' })).not.toBeInTheDocument()
  })

  it('drops the stale cookie by logging out after a successful password change', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: true, authenticated: true })
    const { container } = renderPanel()
    await screen.findByText('舊密碼')

    fillForm(container, {
      oldPassword: 'old-secret',
      newPassword: 'new-secret',
      confirmPassword: 'new-secret',
    })
    fireEvent.click(screen.getByRole('button', { name: '更新密碼' }))

    await waitFor(() => expect(api.authChangePassword).toHaveBeenCalledWith('old-secret', 'new-secret'))
    await waitFor(() => expect(api.authLogout).toHaveBeenCalledTimes(1))
    expect(window.location.href).toBe('/login?changed=1')
  })

  it('rejects a mismatched confirmation without calling the API', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: true, authenticated: true })
    const { container } = renderPanel()
    await screen.findByText('舊密碼')

    fillForm(container, {
      oldPassword: 'old-secret',
      newPassword: 'new-secret',
      confirmPassword: 'new-secrets',
    })
    fireEvent.click(screen.getByRole('button', { name: '更新密碼' }))

    expect(await screen.findByText('兩次密碼不一致')).toBeInTheDocument()
    expect(api.authChangePassword).not.toHaveBeenCalled()
    expect(api.authLogout).not.toHaveBeenCalled()
  })

  it('rejects a too-short new password without calling the API', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: true, authenticated: true })
    const { container } = renderPanel()
    await screen.findByText('舊密碼')

    fillForm(container, {
      oldPassword: 'old-secret',
      newPassword: 'abc',
      confirmPassword: 'abc',
    })
    fireEvent.click(screen.getByRole('button', { name: '更新密碼' }))

    expect(await screen.findByText('密碼至少 6 位')).toBeInTheDocument()
    expect(api.authChangePassword).not.toHaveBeenCalled()
    expect(api.authLogout).not.toHaveBeenCalled()
  })

  it('surfaces a wrong old password instead of leaving the form silent', async () => {
    vi.mocked(api.authStatus).mockResolvedValue({ configured: true, authenticated: true })
    vi.mocked(api.authChangePassword).mockRejectedValueOnce(new Error('旧密码错误'))
    const { container } = renderPanel()
    await screen.findByText('舊密碼')

    fillForm(container, {
      oldPassword: 'wrong-secret',
      newPassword: 'new-secret',
      confirmPassword: 'new-secret',
    })
    fireEvent.click(screen.getByRole('button', { name: '更新密碼' }))

    expect(await screen.findByText('舊密碼錯誤')).toBeInTheDocument()
    expect(api.authLogout).not.toHaveBeenCalled()
  })
})
