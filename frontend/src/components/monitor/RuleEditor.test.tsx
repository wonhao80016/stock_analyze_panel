// 監控規則合併回歸測試 — 涵蓋監控情境晶片(價格與漲跌/量能/技術訊號)產生的
// condition 是否與後端通用引擎(monitor.py + custom_signals.ALLOWED_FIELDS)的
// 既有約定一致: change_pct / distance_to_upper_limit / distance_to_lower_limit
// 一律小數制(0.01 = 1%), 技術訊號多選需全部命中(AND), 對齊舊版台股引擎語意。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RuleEditor } from './RuleEditor'
import type { MonitorRule } from '@/lib/api'

const monitorRuleSave = vi.fn().mockResolvedValue({ ok: true, rule: {} })

vi.mock('@/lib/api', () => ({
  api: {
    monitorRuleOptions: vi.fn().mockResolvedValue({
      threshold_fields: [
        { key: 'close', label: '收盤價' },
        { key: 'change_pct', label: '漲跌幅' },
        { key: 'volume', label: '成交量' },
        { key: 'vol_ratio_5d', label: '5日量比' },
        { key: 'distance_to_upper_limit', label: '距漲停距離' },
        { key: 'distance_to_lower_limit', label: '距跌停距離' },
      ],
      // 非空陣列只是用來讓「訊號條件」區塊出現(RuleEditor 以此欄位長度判斷是否顯示
      // SignalPicker); 實際可選訊號清單來自 signals.ts 的 SIGNAL_OPTIONS(未 mock)。
      builtin_signals: [{ key: 'signal_intraday_avg_cross_up', label: '分時價格上穿均價' }],
      custom_signals: [],
      operators: ['>', '>=', '<', '<=', '==', '!='],
      types: [
        { key: 'signal', label: '訊號' },
        { key: 'price', label: '價格/漲跌' },
        { key: 'market', label: '市場異動' },
        { key: 'strategy', label: '策略監控' },
        { key: 'sector', label: '板塊監控' },
      ],
      scopes: [
        { key: 'symbols', label: '指定標的' },
        { key: 'watchlist_group', label: '自選分組' },
        { key: 'all', label: '全市場' },
        { key: 'sector', label: '板塊' },
      ],
      logics: [
        { key: 'and', label: '全部滿足 (AND)' },
        { key: 'or', label: '任一滿足 (OR)' },
      ],
      severities: [
        { key: 'info', label: '普通' },
        { key: 'warn', label: '警告' },
        { key: 'critical', label: '重要' },
      ],
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

function renderEditor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <RuleEditor
        rule={null}
        preset={{ scope: 'symbols', symbols: ['2330.TWSE'] }}
        onClose={() => {}}
      />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('RuleEditor — 監控情境晶片(取代台股即時引擎的 9 種 rule_type)', () => {
  it('接近漲停晶片輸入 1% 產生小數 condition (distance_to_upper_limit)', async () => {
    renderEditor()
    await screen.findByText('監控情境')

    fireEvent.click(screen.getByRole('button', { name: '價格與漲跌' }))
    fireEvent.click(screen.getByRole('button', { name: '接近漲停' }))
    fireEvent.change(screen.getByLabelText('接近漲停數值'), { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    const saved = monitorRuleSave.mock.calls[0][0] as MonitorRule
    const cond = saved.conditions.find(c => c.field === 'distance_to_upper_limit')
    expect(cond).toBeTruthy()
    expect(cond?.op).toBe('<=')
    expect(cond?.value).toBeCloseTo(0.01)
  })

  it('漲幅高於晶片輸入 3% 產生 {change_pct, >=, 0.03}', async () => {
    renderEditor()
    await screen.findByText('監控情境')

    fireEvent.click(screen.getByRole('button', { name: '價格與漲跌' }))
    fireEvent.click(screen.getByRole('button', { name: '漲幅高於' }))
    fireEvent.change(screen.getByLabelText('漲幅高於數值'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    const saved = monitorRuleSave.mock.calls[0][0] as MonitorRule
    const cond = saved.conditions.find(c => c.field === 'change_pct')
    expect(cond).toEqual(expect.objectContaining({ field: 'change_pct', op: '>=' }))
    expect(cond?.value).toBeCloseTo(0.03)
  })

  it('技術訊號情境下多選訊號產生 logic=and (對齊舊版台股引擎: 全部命中才觸發)', async () => {
    renderEditor()
    await screen.findByText('監控情境')

    fireEvent.click(screen.getByRole('button', { name: '技術訊號' }))
    fireEvent.click(screen.getByRole('button', { name: '放量' }))
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    const saved = monitorRuleSave.mock.calls[0][0] as MonitorRule
    expect(saved.logic).toBe('and')
    expect(saved.conditions).toContainEqual(
      expect.objectContaining({ field: 'signal_volume_surge', op: 'truth' }),
    )
  })

  it('成交量高於晶片以「張」輸入時換算為股儲存 (1張=1000股)', async () => {
    renderEditor()
    await screen.findByText('監控情境')

    fireEvent.click(screen.getByRole('button', { name: '量能' }))
    fireEvent.click(screen.getByRole('button', { name: '成交量高於' }))
    fireEvent.change(screen.getByLabelText('成交量高於數值'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    const saved = monitorRuleSave.mock.calls[0][0] as MonitorRule
    const cond = saved.conditions.find(c => c.field === 'volume')
    expect(cond?.op).toBe('>=')
    expect(cond?.value).toBe(5000)
  })
})
