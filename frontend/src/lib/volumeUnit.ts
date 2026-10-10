import { useSyncExternalStore } from 'react'
import { storage } from '@/lib/storage'

export type VolumeUnit = 'shares' | 'lots'

export const DEFAULT_VOLUME_UNIT: VolumeUnit = 'lots'
export const SHARES_PER_LOT = 1000

const STORAGE_KEY = 'volume_unit'
const listeners = new Set<() => void>()
// Holds the choice only while browser storage is unavailable, so storage stays authoritative.
let memoryUnit: VolumeUnit | null = null

function isVolumeUnit(value: unknown): value is VolumeUnit {
  return value === 'shares' || value === 'lots'
}

export function getVolumeUnit(): VolumeUnit {
  const stored = storage.volumeUnit.get('')
  if (isVolumeUnit(stored)) return stored
  return memoryUnit ?? DEFAULT_VOLUME_UNIT
}

export function setVolumeUnit(unit: VolumeUnit): void {
  storage.volumeUnit.set(unit)
  memoryUnit = storage.volumeUnit.get('') === unit ? null : unit
  listeners.forEach(listener => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === STORAGE_KEY) listener()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

export function useVolumeUnit(): [VolumeUnit, (unit: VolumeUnit) => void] {
  const unit = useSyncExternalStore(subscribe, getVolumeUnit, () => DEFAULT_VOLUME_UNIT)
  return [unit, setVolumeUnit]
}

export function volumeUnitLabel(unit: VolumeUnit): '股' | '張' {
  return unit === 'lots' ? '張' : '股'
}

function roundHalfUp(value: number, digits: number): number {
  const factor = 10 ** digits
  return Math.round((value + Number.EPSILON) * factor) / factor
}

// Integers from 10 up, one decimal below 10 unless the value is integral.
function compactNumber(value: number): string {
  const tiers: [number, string][] = [[1e8, '億'], [1e4, '萬'], [1, '']]
  let tier = tiers.findIndex(([scale]) => value >= scale)
  if (tier < 0) tier = tiers.length - 1
  for (;;) {
    const [scale, suffix] = tiers[tier]
    const scaled = value / scale
    const rounded = scaled >= 10 ? roundHalfUp(scaled, 0) : roundHalfUp(scaled, 1)
    if (rounded >= 1e4 && tier > 0) {
      tier -= 1
      continue
    }
    return `${rounded}${suffix}`
  }
}

/** Display text of a share count in the given unit; the data itself always stays in shares. */
export function formatVolumeNumber(shares: number, unit: VolumeUnit): string {
  const value = unit === 'lots' ? shares / SHARES_PER_LOT : shares
  const text = compactNumber(Math.abs(value))
  return value < 0 && text !== '0' ? `-${text}` : text
}

export function formatVolume(shares: number | null | undefined, unit: VolumeUnit): string {
  if (shares == null || !Number.isFinite(shares)) return '—'
  return `${formatVolumeNumber(shares, unit)}${volumeUnitLabel(unit)}`
}

export function volumePanelTitle(unit: VolumeUnit): string {
  return `成交量(${volumeUnitLabel(unit)})`
}
