import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import * as columnsModule from './watchlist-columns'
import { BUILTIN_COLUMNS, COLUMN_GROUPS, loadColumnConfig, saveColumnConfig } from './watchlist-columns'
import { BUILTIN_INFO_FIELDS, loadInfoFields } from './stock-info-fields'
import { storage } from './storage'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: {
    watchlistColumns: vi.fn(),
    updateWatchlistColumns: vi.fn(),
  },
}))

const FINANCE_KEYS = ['eps', 'bps', 'roe', 'pe_ttm', 'pb', 'gross_margin', 'net_margin', 'revenue_yoy', 'net_income_yoy', 'debt_ratio']

function withVisibility(id: string, visible: boolean) {
  return BUILTIN_COLUMNS.map(c => (c.id === id ? { ...c, visible } : c))
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('B289 the watchlist offers no financial columns', () => {
  it('drops the A-share financial columns from the registry and the groups', () => {
    const keys = BUILTIN_COLUMNS.map(c => (c.source.type === 'builtin' ? c.source.key : ''))
    for (const key of FINANCE_KEYS) expect(keys, key).not.toContain(key)
    expect(COLUMN_GROUPS.some(g => g.id === 'finance')).toBe(false)
  })

  it('drops a financial column a user saved earlier', async () => {
    vi.mocked(api.watchlistColumns).mockResolvedValue({
      columns: [...BUILTIN_COLUMNS, { id: 'builtin:eps', source: { type: 'builtin', key: 'eps' }, label: 'EPS', visible: true, align: 'center' }],
    } as any)
    const loaded = await loadColumnConfig()
    expect(loaded.some(c => c.id === 'builtin:eps')).toBe(false)
  })
})

describe('D407 the 流通值 columns are gone', () => {
  const stale = (key: string) => ({ id: `builtin:${key}`, source: { type: 'builtin', key }, label: '流通值', visible: true, align: 'center' })

  it('drops a watchlist 流通值 column a user saved earlier', async () => {
    expect(BUILTIN_COLUMNS.some(c => c.id === 'builtin:float_val')).toBe(false)
    vi.mocked(api.watchlistColumns).mockResolvedValue({ columns: [...BUILTIN_COLUMNS, stale('float_val')] } as any)
    const loaded = await loadColumnConfig()
    expect(loaded.map(c => c.id)).toEqual(BUILTIN_COLUMNS.map(c => c.id))
  })

  it('drops a stock info bar 流通值 field a user saved earlier', () => {
    expect(BUILTIN_INFO_FIELDS.some(f => f.id === 'builtin:float_market_cap')).toBe(false)
    storage.stockInfoBarFields.set([stale('float_market_cap'), ...BUILTIN_INFO_FIELDS])
    expect(loadInfoFields().map(f => f.id)).toEqual(BUILTIN_INFO_FIELDS.map(f => f.id))
  })
})

describe('B290 / F4 a column change the backend rejected is reconciled against the last sync', () => {
  async function failedSaveAfterSync(backendAtSync: ReturnType<typeof withVisibility>) {
    vi.mocked(api.watchlistColumns).mockResolvedValueOnce({ columns: backendAtSync } as any)
    await loadColumnConfig()
    vi.mocked(api.updateWatchlistColumns).mockRejectedValueOnce(new Error('offline'))
    await saveColumnConfig(withVisibility('builtin:amount', true))
    expect(Number(localStorage.getItem('watchlist_columns_unsynced'))).toBeGreaterThan(0)
  }

  it('pushes the local change when the backend is unchanged since the last sync', async () => {
    await failedSaveAfterSync(withVisibility('builtin:amount', false))
    vi.mocked(api.watchlistColumns).mockResolvedValue({ columns: withVisibility('builtin:amount', false) } as any)
    vi.mocked(api.updateWatchlistColumns).mockResolvedValue({ columns: [] } as any)
    const onUnsyncedDiscarded = vi.fn()

    const loaded = await loadColumnConfig({ onUnsyncedDiscarded })

    expect(loaded.find(c => c.id === 'builtin:amount')?.visible).toBe(true)
    const pushed = vi.mocked(api.updateWatchlistColumns).mock.calls.at(-1)![0] as any[]
    expect(pushed.find(c => c.id === 'builtin:amount')?.visible).toBe(true)
    expect(onUnsyncedDiscarded).not.toHaveBeenCalled()
    expect(localStorage.getItem('watchlist_columns_unsynced')).toBeNull()
  })

  it('takes the backend copy and reports the conflict when it changed elsewhere', async () => {
    await failedSaveAfterSync(withVisibility('builtin:amount', false))
    vi.mocked(api.watchlistColumns).mockResolvedValue({ columns: withVisibility('builtin:annual_vol', true) } as any)
    const pushesBefore = vi.mocked(api.updateWatchlistColumns).mock.calls.length
    const onUnsyncedDiscarded = vi.fn()

    const loaded = await loadColumnConfig({ onUnsyncedDiscarded })

    expect(loaded.find(c => c.id === 'builtin:amount')?.visible).toBe(false)
    expect(loaded.find(c => c.id === 'builtin:annual_vol')?.visible).toBe(true)
    expect(onUnsyncedDiscarded).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.updateWatchlistColumns).mock.calls.length).toBe(pushesBefore)
    expect(localStorage.getItem('watchlist_columns_unsynced')).toBeNull()
  })

  it('keeps the local change while the backend is unreachable', async () => {
    await failedSaveAfterSync(withVisibility('builtin:amount', false))
    vi.mocked(api.watchlistColumns).mockRejectedValue(new Error('offline'))
    const onUnsyncedDiscarded = vi.fn()
    const loaded = await loadColumnConfig({ onUnsyncedDiscarded })
    expect(loaded.find(c => c.id === 'builtin:amount')?.visible).toBe(true)
    expect(onUnsyncedDiscarded).not.toHaveBeenCalled()
    expect(localStorage.getItem('watchlist_columns_unsynced')).not.toBeNull()
  })

  it('loads the backend copy once a save went through', async () => {
    vi.mocked(api.updateWatchlistColumns).mockResolvedValue({ columns: [] } as any)
    await saveColumnConfig(withVisibility('builtin:amount', true))

    vi.mocked(api.watchlistColumns).mockResolvedValue({ columns: withVisibility('builtin:amount', false) } as any)
    const loaded = await loadColumnConfig()
    expect(loaded.find(c => c.id === 'builtin:amount')?.visible).toBe(false)
  })
})

describe('B066 filter units follow the displayed units', () => {
  const unitOf = (key: string) => {
    const col = BUILTIN_COLUMNS.find(c => c.source.type === 'builtin' && c.source.key === key)!
    return (columnsModule as any).filterUnit(col)
  }
  const toRaw = (key: string, input: string) => {
    const col = BUILTIN_COLUMNS.find(c => c.source.type === 'builtin' && c.source.key === key)!
    return (columnsModule as any).filterBoundToRaw(col, input)
  }

  it('reads decimal-ratio columns as percent', () => {
    for (const key of ['pct', 'amplitude', 'annual_vol', 'momentum_5d', 'momentum_60d']) {
      expect(unitOf(key)?.label, key).toBe('%')
      expect(toRaw(key, '3'), key).toBeCloseTo(0.03)
    }
  })

  it('keeps turnover as a percent value and amount in 億', () => {
    expect(toRaw('turnover', '3')).toBe(3)
    expect(toRaw('amount', '1.5')).toBe(150_000_000)
    expect(toRaw('vol_ma5', '2')).toBe(2000)
  })

  it('leaves unitless indicators untouched', () => {
    expect(unitOf('rsi14')).toBeNull()
    expect(toRaw('rsi14', '70')).toBe(70)
  })
})

describe('F8 volume filters follow the display unit', () => {
  const col = BUILTIN_COLUMNS.find(c => c.source.type === 'builtin' && c.source.key === 'vol_ma5')!
  const mod = columnsModule as any

  it('reads shares when the volume unit is shares', () => {
    expect(mod.filterUnit(col, 'shares')?.label).toBe('股')
    expect(mod.filterBoundToRaw(col, '2', 'shares')).toBe(2)
  })

  it('reads lots when the volume unit is lots', () => {
    expect(mod.filterUnit(col, 'lots')?.label).toBe('張')
    expect(mod.filterBoundToRaw(col, '2', 'lots')).toBe(2000)
  })
})
