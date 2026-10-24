import { afterEach, describe, expect, it } from 'vitest'
import './theme'

function switchThemeInAnotherTab(theme: 'dark' | 'light') {
  localStorage.setItem('tf-theme', theme)
  window.dispatchEvent(new StorageEvent('storage', { key: 'tf-theme', newValue: theme }))
}

afterEach(() => {
  document.documentElement.classList.remove('dark')
})

describe('theme sync across tabs', () => {
  it('applies html.dark when another tab switches the theme', () => {
    document.documentElement.classList.add('dark')

    switchThemeInAnotherTab('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)

    switchThemeInAnotherTab('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('ignores storage changes of other keys', () => {
    document.documentElement.classList.add('dark')
    localStorage.setItem('tf-theme', 'light')
    window.dispatchEvent(new StorageEvent('storage', { key: 'unrelated', newValue: 'x' }))
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
