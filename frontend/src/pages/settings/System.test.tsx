import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SettingsSystemPanel } from './System'

vi.mock('@/lib/useSharedQueries', () => ({
  useVersion: vi.fn(() => ({ data: undefined })),
}))

vi.mock('@/components/AlertToast', () => ({ refreshAlertToastConfig: vi.fn() }))

const VOICES = [
  { voiceURI: 'tw-default', name: 'Taiwan Default', lang: 'zh-TW' },
  { voiceURI: 'hk-voice', name: 'Hong Kong Voice', lang: 'zh-HK' },
]

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('voice_broadcast_enabled', '1')
  localStorage.setItem('voice_broadcast_voice', 'hk-voice')
  vi.stubGlobal('speechSynthesis', {
    getVoices: () => VOICES,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    speak: vi.fn(),
    cancel: vi.fn(),
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('voice selection', () => {
  it('returns to the default preference after a voice was picked by hand', () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SettingsSystemPanel />
      </QueryClientProvider>,
    )

    const select = screen.getByRole('option', { name: '預設偏好' }).closest('select') as HTMLSelectElement
    expect(select.value).toBe('hk-voice')

    fireEvent.change(select, { target: { value: screen.getByRole<HTMLOptionElement>('option', { name: '預設偏好' }).value } })

    expect(localStorage.getItem('voice_broadcast_voice')).toBeNull()
    expect(select.selectedOptions[0].textContent).toBe('預設偏好')
  })
})
