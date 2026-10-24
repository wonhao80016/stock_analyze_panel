import { describe, expect, it } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { ToastContainer, toast } from './Toast'

describe('ToastContainer', () => {
  it('stacks away from the bottom-right corner that monitor alert toasts occupy', () => {
    render(<ToastContainer />)
    act(() => toast('儲存失敗'))

    const container = screen.getByText('儲存失敗').parentElement!
    expect(container.className).not.toMatch(/\bbottom-4\b/)
    expect(container.className).toMatch(/\btop-4\b/)
    expect(container.className).toMatch(/\bright-4\b/)
  })
})
