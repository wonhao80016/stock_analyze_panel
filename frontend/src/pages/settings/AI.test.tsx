import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type SettingsState } from '@/lib/api'
import { useSettings } from '@/lib/useSharedQueries'
import { SettingsAIPanel } from './AI'

vi.mock('@/lib/api', () => ({
  api: {
    saveAiSettings: vi.fn().mockResolvedValue({ ok: true, ai_provider: 'openai_compat' }),
    clearAiSettings: vi.fn().mockResolvedValue({ ok: true }),
    strategyAiTest: vi.fn().mockResolvedValue({ ok: true }),
    testAiSettings: vi.fn().mockResolvedValue({ ok: false, error: 'AI 服务请求失败(401): invalid key' }),
  },
}))

vi.mock('@/lib/useSharedQueries', () => ({
  useSettings: vi.fn(),
}))

const settings = (overrides: Partial<SettingsState> = {}): SettingsState => ({
  onboarding_completed: true,
  ai_provider: 'openai_compat',
  ai_base_url: '',
  ai_api_key_masked: '',
  has_ai_key: false,
  ai_configured: false,
  ai_model: '',
  ai_user_agent: '',
  ...overrides,
})

function renderPanel(state: SettingsState) {
  vi.mocked(useSettings).mockReturnValue({ data: state } as ReturnType<typeof useSettings>)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsAIPanel />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('SettingsAIPanel quick presets', () => {
  it('shows only Custom and OpenAI', () => {
    renderPanel(settings())

    const presetCard = screen.getByText('快速預設').closest('section')!
    expect(within(presetCard).getAllByRole('button').map(button => button.textContent)).toEqual(['自訂', 'OpenAI'])
  })

  it('loads a legacy provider-specific preset as Custom without rewriting persisted settings', async () => {
    renderPanel(settings({
      ai_base_url: 'https://api.deepseek.com',
      ai_model: 'deepseek-chat',
      ai_api_key_masked: 'sk-a......z',
      has_ai_key: true,
      ai_configured: true,
    }))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('https://api.example.com/v1')).toHaveValue('https://api.deepseek.com')
      expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('deepseek-chat')
    })
    expect(screen.getByRole('button', { name: '自訂' })).toHaveAttribute('aria-pressed', 'true')
    expect(api.saveAiSettings).not.toHaveBeenCalled()
    expect(api.clearAiSettings).not.toHaveBeenCalled()
  })

  it('filters a persisted Codex preset from the UI without deleting its stored configuration', async () => {
    renderPanel(settings({
      ai_provider: 'codex_cli',
      ai_base_url: 'https://saved-compatible.example/v1',
      ai_openai_model: 'saved-compatible-model',
      ai_configured: true,
      ai_codex_model: 'gpt-5.6-sol',
      ai_codex_command: 'codex',
      ai_codex_reasoning_effort: 'xhigh',
    }))

    await waitFor(() => expect(screen.getByRole('button', { name: '自訂' })).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.queryByText(/Codex CLI/)).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('https://api.example.com/v1')).toHaveValue('https://saved-compatible.example/v1')
    expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('saved-compatible-model')
    expect(api.saveAiSettings).not.toHaveBeenCalled()
    expect(api.clearAiSettings).not.toHaveBeenCalled()
  })

  it('saves any OpenAI-compatible API through the generic Custom provider', async () => {
    renderPanel(settings())

    fireEvent.change(screen.getByPlaceholderText('https://api.example.com/v1'), {
      target: { value: 'https://integrate.api.nvidia.com/v1' },
    })
    fireEvent.change(screen.getByPlaceholderText('your-model-id'), {
      target: { value: 'nvidia/llama-3.1-nemotron-ultra-253b-v1' },
    })
    fireEvent.change(screen.getByPlaceholderText('sk-...'), {
      target: { value: 'test-key' },
    })
    fireEvent.click(screen.getByRole('button', { name: '儲存設定' }))

    await waitFor(() => expect(api.saveAiSettings).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(api.saveAiSettings).mock.calls[0][0]
    expect(payload).toMatchObject({
      provider: 'openai_compat',
      base_url: 'https://integrate.api.nvidia.com/v1',
      api_key: 'test-key',
      model: 'nvidia/llama-3.1-nemotron-ultra-253b-v1',
    })
    expect(payload).not.toHaveProperty('codex_command')
    expect(payload).not.toHaveProperty('codex_reasoning_effort')
  })
})

describe('SettingsAIPanel empty API address', () => {
  it('asks for an address with examples but never fills one in', async () => {
    renderPanel(settings({ has_ai_key: true, ai_api_key_masked: 'sk-a......z', ai_model: 'deepseek-chat' }))

    const baseUrlInput = screen.getByPlaceholderText('https://api.example.com/v1')
    await waitFor(() => expect(baseUrlInput).toHaveValue(''))
    const hint = screen.getByText(/請填寫 API 位址/)
    expect(hint).toHaveTextContent('https://api.deepseek.com/v1')
    expect(hint).toHaveTextContent('https://api.openai.com/v1')
    expect(screen.getByRole('button', { name: '儲存設定' })).toBeDisabled()

    fireEvent.change(baseUrlInput, { target: { value: 'https://api.deepseek.com/v1' } })
    expect(screen.queryByText(/請填寫 API 位址/)).not.toBeInTheDocument()
    expect(api.saveAiSettings).not.toHaveBeenCalled()
  })
})

describe('SettingsAIPanel connection test', () => {
  const configured = () => settings({
    ai_base_url: 'https://api.example.com/v1',
    ai_model: 'saved-model',
    ai_api_key_masked: 'sk-1......k1',
    has_ai_key: true,
    ai_configured: true,
  })

  it('tests an unsaved key without writing it to the saved settings', async () => {
    renderPanel(configured())
    await waitFor(() => expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('saved-model'))

    fireEvent.change(screen.getByPlaceholderText(/留空不修改/), { target: { value: 'sk-new-key-2' } })
    const keyField = screen.getByPlaceholderText(/留空不修改/).closest('div.flex.gap-2')!
    fireEvent.click(within(keyField as HTMLElement).getByRole('button', { name: '測試' }))

    await waitFor(() => expect(screen.getByText(/invalid key/)).toBeInTheDocument())
    expect(api.saveAiSettings).not.toHaveBeenCalled()
    expect(api.testAiSettings).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.testAiSettings).mock.calls[0][0]).toMatchObject({
      base_url: 'https://api.example.com/v1',
      model: 'saved-model',
      api_key: 'sk-new-key-2',
    })
    expect(screen.getByText('saved-model · sk-1......k1')).toBeInTheDocument()
  })

  it('tests the saved configuration from the status card without saving the form', async () => {
    renderPanel(configured())
    await waitFor(() => expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('saved-model'))

    fireEvent.change(screen.getByPlaceholderText('your-model-id'), { target: { value: 'draft-model' } })
    const statusCard = screen.getByText('連線狀態').closest('section')!
    fireEvent.click(within(statusCard).getByRole('button', { name: '測試' }))

    await waitFor(() => expect(api.strategyAiTest).toHaveBeenCalledTimes(1))
    expect(api.saveAiSettings).not.toHaveBeenCalled()
    expect(api.testAiSettings).not.toHaveBeenCalled()
  })
})

describe('SettingsAIPanel changed API address', () => {
  it('asks for a new key instead of saving the saved key against a new address', async () => {
    renderPanel(settings({
      ai_base_url: 'https://api.example.com/v1',
      ai_model: 'saved-model',
      ai_api_key_masked: 'sk-1......k1',
      has_ai_key: true,
      ai_configured: true,
    }))
    await waitFor(() => expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('saved-model'))

    fireEvent.change(screen.getByPlaceholderText('https://api.example.com/v1'), { target: { value: 'https://other.example/v1' } })

    expect(screen.getByText(/變更 API 位址後請填入該服務的 API Key/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '儲存設定' })).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText(/留空不修改/), { target: { value: 'sk-other' } })
    expect(screen.getByRole('button', { name: '儲存設定' })).toBeEnabled()
  })
})

describe('SettingsAIPanel API address comparison', () => {
  const renderSaved = (savedUrl: string) => renderPanel(settings({
    ai_base_url: savedUrl,
    ai_model: 'saved-model',
    ai_api_key_masked: 'sk-1......k1',
    has_ai_key: true,
    ai_configured: true,
  }))
  const typeUrl = (value: string) =>
    fireEvent.change(screen.getByPlaceholderText('https://api.example.com/v1'), { target: { value } })

  it.each([
    ['https://api.example.com', 'https://api.example.com/v1/'],
    ['https://api.example.com/v1', 'https://api.example.com/v1/chat/completions'],
    ['https://open.example.com/api/paas/v4', ' https://open.example.com/api/paas/v4/ '],
  ])('treats %s and %s as the same address, like the backend', async (saved, typed) => {
    renderSaved(saved)
    await waitFor(() => expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('saved-model'))

    typeUrl(typed)

    expect(screen.queryByText(/變更 API 位址後請填入該服務的 API Key/)).toBeNull()
    expect(screen.getByRole('button', { name: '儲存設定' })).toBeEnabled()
  })

  it.each([
    ['https://api.example.com/v1', 'https://api.example.com/V1'],
    ['https://api.example.com/v1', 'https://API.example.com/v1'],
  ])('treats %s and %s as different addresses, like the backend', async (saved, typed) => {
    renderSaved(saved)
    await waitFor(() => expect(screen.getByPlaceholderText('your-model-id')).toHaveValue('saved-model'))

    typeUrl(typed)

    expect(screen.getByText(/變更 API 位址後請填入該服務的 API Key/)).toBeInTheDocument()
  })
})
