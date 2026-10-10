import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { formatVolume, formatVolumeNumber, getVolumeUnit, useVolumeUnit, volumePanelTitle } from './volumeUnit'

describe('formatVolume', () => {
  it('shows lots as shares / 1000, rounded to whole lots from 10 up', () => {
    expect(formatVolume(625_123, 'lots')).toBe('625張')
    expect(formatVolume(625_500, 'lots')).toBe('626張')
    expect(formatVolume(10_000, 'lots')).toBe('10張')
  })

  it('keeps one decimal below 10 lots unless the value is integral', () => {
    expect(formatVolume(1_500, 'lots')).toBe('1.5張')
    expect(formatVolume(1_000, 'lots')).toBe('1張')
    expect(formatVolume(300, 'lots')).toBe('0.3張')
    expect(formatVolume(9_960, 'lots')).toBe('10張')
  })

  it('abbreviates with 萬 and 億 in both units', () => {
    expect(formatVolume(12_345_678, 'lots')).toBe('1.2萬張')
    expect(formatVolume(630_000, 'shares')).toBe('63萬股')
    expect(formatVolume(625_123, 'shares')).toBe('63萬股')
    expect(formatVolume(123_456_789, 'shares')).toBe('1.2億股')
    expect(formatVolume(9_999, 'shares')).toBe('9999股')
    expect(formatVolume(99_999_600, 'shares')).toBe('1億股')
  })

  it('keeps small share counts exact and marks missing values', () => {
    expect(formatVolume(300, 'shares')).toBe('300股')
    expect(formatVolume(0, 'lots')).toBe('0張')
    expect(formatVolume(null, 'lots')).toBe('—')
    expect(formatVolume(Number.NaN, 'shares')).toBe('—')
  })

  it('formats axis numbers without the unit, which the panel title carries', () => {
    expect(formatVolumeNumber(3_000_000, 'lots')).toBe('3000')
    expect(formatVolumeNumber(3_000_000, 'shares')).toBe('300萬')
    expect(volumePanelTitle('lots')).toBe('成交量(張)')
    expect(volumePanelTitle('shares')).toBe('成交量(股)')
  })
})

describe('volume unit setting', () => {
  it('defaults to lots', () => {
    expect(getVolumeUnit()).toBe('lots')
    const { result } = renderHook(() => useVolumeUnit())
    expect(result.current[0]).toBe('lots')
  })

  it('persists the choice and notifies every subscriber', () => {
    const first = renderHook(() => useVolumeUnit())
    const second = renderHook(() => useVolumeUnit())

    act(() => first.result.current[1]('shares'))

    expect(first.result.current[0]).toBe('shares')
    expect(second.result.current[0]).toBe('shares')
    expect(localStorage.getItem('volume_unit')).toBe('"shares"')

    first.unmount()
    second.unmount()
    expect(renderHook(() => useVolumeUnit()).result.current[0]).toBe('shares')
  })

  it('ignores an unknown stored value', () => {
    localStorage.setItem('volume_unit', '"bogus"')
    expect(getVolumeUnit()).toBe('lots')
  })
})
