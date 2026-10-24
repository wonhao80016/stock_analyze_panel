import { describe, expect, it } from 'vitest'
import { breadthBasisNote } from './marketBreadth'

describe('breadthBasisNote', () => {
  it('states that advance, decline and flat count traded rows only', () => {
    const note = breadthBasisNote({ untraded_count: 3, uncompared_count: 2 })
    expect(note).toContain('僅計有成交')
    expect(note).toContain('未成交 3')
    expect(note).toContain('無前收 2')
  })
})
