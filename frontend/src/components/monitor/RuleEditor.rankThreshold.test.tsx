import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RuleEditor } from './RuleEditor'
import { type MonitorRule } from '@/lib/api'

const monitorRuleSave = vi.fn().mockResolvedValue({ ok: true, rule: {} })

vi.mock('@/lib/api', () => ({
  api: {
    monitorRuleOptions: vi.fn().mockResolvedValue({
      threshold_fields: [{ key: 'close', label: '收盤價' }],
      builtin_signals: [],
      custom_signals: [],
      operators: ['>', '>=', '<', '<=', '==', '!='],
      types: [{ key: 'strategy', label: '策略監控' }],
      scopes: [
        { key: 'symbols', label: '指定標的' },
        { key: 'watchlist_group', label: '自選分組' },
        { key: 'all', label: '全市場' },
      ],
      logics: [],
      severities: [{ key: 'info', label: '普通' }],
      directions: [],
      intraday_signal_support: { available: true, source: null, max_symbols: 50, reason: '' },
      sector_targets: { index: [], concept: [], industry: [] },
    }),
    preferences: vi.fn().mockResolvedValue({}),
    screenerStrategies: vi.fn().mockResolvedValue({ presets: [] }),
    watchlistList: vi.fn().mockResolvedValue({ symbols: [] }),
    watchlistGroups: vi.fn().mockResolvedValue({ groups: [] }),
    instrumentSearch: vi.fn().mockResolvedValue({ results: [] }),
    instrumentNames: vi.fn().mockResolvedValue({ names: {} }),
    customSignalsList: vi.fn().mockResolvedValue({ signals: [] }),
    monitorRuleSave: (rule: MonitorRule) => monitorRuleSave(rule),
  },
  genRuleId: () => 'mr_test',
}))

const STRATEGY_PRESET: Partial<MonitorRule> = { type: 'strategy', scope: 'all', strategy_id: 'demo' }

function legacyRule(): MonitorRule {
  return {
    id: 'mr_legacy',
    name: '舊策略監控',
    enabled: true,
    type: 'strategy',
    asset_type: 'stock',
    scope: 'all',
    symbols: [],
    strategy_id: 'demo',
    direction: 'entry',
    notify_events: ['pool_entry'],
    score_min: 70,
    score_max: null,
    max_rank: null,
    conditions: [],
    logic: 'and',
    cooldown_seconds: 3600,
    severity: 'info',
    message: '',
  }
}

function renderEditor(rule: MonitorRule | null, preset?: Partial<MonitorRule>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <RuleEditor rule={rule} preset={preset} onClose={() => {}} />
    </QueryClientProvider>,
  )
}

function savedRule(): MonitorRule {
  return monitorRuleSave.mock.calls[0][0] as MonitorRule
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('RuleEditor — 策略排名門檻', () => {
  it('新規則提供「排名前 N 名」並依當天策略排名說明, 不再提供分數輸入', async () => {
    renderEditor(null, STRATEGY_PRESET)
    const rank = await screen.findByLabelText('排名前 N 名')

    expect(screen.getByText(/依當天策略排名/)).toBeTruthy()
    expect(screen.queryByText(/最低分/)).toBeNull()
    expect(screen.queryByText(/相對分數，已不建議使用/)).toBeNull()

    fireEvent.change(rank, { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    expect(savedRule().max_rank).toBe(10)
    expect(savedRule().score_min).toBeNull()
    expect(savedRule().score_max).toBeNull()
  })

  it('留空排名門檻存成 null (不限)', async () => {
    renderEditor(null, STRATEGY_PRESET)
    await screen.findByLabelText('排名前 N 名')

    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    expect(savedRule().max_rank).toBeNull()
  })

  it.each(['0', '1.5', '-2'])('排名門檻 %s 不是 ≥1 的整數時不送出', async value => {
    renderEditor(null, STRATEGY_PRESET)
    fireEvent.change(await screen.findByLabelText('排名前 N 名'), { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    expect(await screen.findByText(/排名門檻必須是大於等於 1 的整數/)).toBeTruthy()
    expect(monitorRuleSave).not.toHaveBeenCalled()
  })

  it('舊規則的評分範圍標示為不建議使用, 原樣保存時照舊生效', async () => {
    renderEditor(legacyRule())

    expect(await screen.findByText(/評分範圍 70 至 不限（相對分數，已不建議使用）/)).toBeTruthy()
    expect((screen.getByLabelText('排名前 N 名') as HTMLInputElement).disabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    expect(savedRule().score_min).toBe(70)
    expect(savedRule().max_rank).toBeNull()
  })

  it('移除舊評分範圍後才能設定排名門檻', async () => {
    renderEditor(legacyRule())
    fireEvent.click(await screen.findByRole('button', { name: '移除評分範圍' }))

    expect(screen.queryByText(/相對分數，已不建議使用/)).toBeNull()
    const rank = screen.getByLabelText('排名前 N 名') as HTMLInputElement
    expect(rank.disabled).toBe(false)
    fireEvent.change(rank, { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    expect(savedRule().score_min).toBeNull()
    expect(savedRule().score_max).toBeNull()
    expect(savedRule().max_rank).toBe(5)
  })
})
