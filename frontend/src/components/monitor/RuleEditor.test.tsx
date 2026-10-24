// 監控規則合併回歸測試 — 涵蓋監控情境晶片(價格與漲跌/量能/技術訊號)產生的
// condition 是否與後端通用引擎(monitor.py + custom_signals.ALLOWED_FIELDS)的
// 既有約定一致: change_pct / distance_to_upper_limit / distance_to_lower_limit
// 一律小數制(0.01 = 1%), 技術訊號多選需全部命中(AND), 對齊舊版台股引擎語意。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RuleEditor } from './RuleEditor'
import { api, type MonitorRule } from '@/lib/api'

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

function renderEditor(rule: MonitorRule | null = null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <RuleEditor
        rule={rule}
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

function savedRule(overrides: Partial<MonitorRule>): MonitorRule {
  return {
    id: 'mr_saved',
    name: '已存規則',
    enabled: true,
    type: 'signal',
    asset_type: 'stock',
    scope: 'symbols',
    symbols: ['2330.TWSE'],
    direction: 'entry',
    conditions: [],
    logic: 'and',
    cooldown_seconds: 3600,
    severity: 'info',
    message: '',
    ...overrides,
  }
}

describe('RuleEditor — 分時訊號能力、指數切換與成交量單位', () => {
  it('分時資料不可用時停用分時訊號並直接顯示原因', async () => {
    const reason = '台股沒有分鐘資料源, 分時訊號需要設定分鐘資料插件'
    const base = await api.monitorRuleOptions()
    vi.mocked(api.monitorRuleOptions).mockResolvedValueOnce({
      ...base,
      intraday_signal_support: { available: false, source: null, max_symbols: 0, reason },
    })
    renderEditor()
    await screen.findByText('監控情境')
    fireEvent.click(screen.getByRole('button', { name: '技術訊號' }))

    expect(await screen.findByText(reason)).toBeTruthy()
    expect((screen.getByRole('button', { name: '分時價格上穿均價' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('切到指數時移除訊號條件, 只留價格條件', async () => {
    vi.mocked(api.instrumentSearch).mockResolvedValue({
      results: [{ symbol: 'IX0001.TWSE', name: '加權指數' }],
    } as Awaited<ReturnType<typeof api.instrumentSearch>>)
    renderEditor(savedRule({
      conditions: [
        { field: 'signal_limit_up', op: 'truth' },
        { field: 'signal_intraday_avg_cross_up', op: 'truth' },
        { field: 'signal_volume_surge', op: 'truth' },
        { field: 'change_pct', op: '>=', value: 0.01 },
      ],
    }))
    await screen.findByText('監控情境')

    fireEvent.click(screen.getByRole('button', { name: '指數' }))
    fireEvent.change(screen.getByPlaceholderText('搜尋代號或名稱新增標的…'), { target: { value: 'IX' } })
    fireEvent.click(await screen.findByRole('button', { name: /IX0001\.TWSE/ }))
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    const saved = monitorRuleSave.mock.calls[0][0] as MonitorRule
    expect(saved.asset_type).toBe('index')
    expect(saved.type).toBe('price')
    expect(saved.conditions.map(c => c.field)).toEqual(['change_pct'])
  })

  it('指數模式隱藏訊號與量能選項, 門檻欄位只列指數有的價格欄位', async () => {
    vi.mocked(api.instrumentSearch).mockResolvedValue({
      results: [{ symbol: 't00.TWSE', name: '加權指數' }],
    } as Awaited<ReturnType<typeof api.instrumentSearch>>)
    renderEditor()
    await screen.findByText('監控情境')
    fireEvent.click(screen.getByRole('button', { name: '技術訊號' }))

    fireEvent.click(screen.getByRole('button', { name: '指數' }))

    expect(screen.queryByRole('button', { name: '技術訊號' })).toBeNull()
    expect(screen.queryByRole('button', { name: '量能' })).toBeNull()
    expect(screen.queryByRole('button', { name: '訊號條件' })).toBeNull()
    expect(screen.getByRole('button', { name: '價格與漲跌' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: '閾值條件' }))
    const fieldOptions = Array.from(document.querySelectorAll('select option')).map(o => (o as HTMLOptionElement).value)
    expect(fieldOptions).toContain('close')
    expect(fieldOptions).not.toContain('volume')
    expect(fieldOptions).not.toContain('vol_ratio_5d')

    fireEvent.change(screen.getByPlaceholderText('搜尋代號或名稱新增標的…'), { target: { value: 't00' } })
    fireEvent.click(await screen.findByRole('button', { name: /t00\.TWSE/ }))
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    const saved = monitorRuleSave.mock.calls[0][0] as MonitorRule
    expect(saved.type).toBe('price')
    expect(saved.conditions).toEqual([expect.objectContaining({ field: 'close' })])
  })

  it('切到指數時一併移除接近漲跌停的門檻條件並隱藏其晶片', async () => {
    vi.mocked(api.instrumentSearch).mockResolvedValue({
      results: [{ symbol: 't00.TWSE', name: '加權指數' }],
    } as Awaited<ReturnType<typeof api.instrumentSearch>>)
    renderEditor(savedRule({
      type: 'price',
      conditions: [
        { field: 'distance_to_upper_limit', op: '<=', value: 0.01 },
        { field: 'distance_to_lower_limit', op: '<=', value: 0.01 },
        { field: 'change_pct', op: '>=', value: 0.03 },
      ],
    }))
    await screen.findByText('監控情境')

    fireEvent.click(screen.getByRole('button', { name: '指數' }))
    expect(screen.queryByRole('button', { name: '接近漲停' })).toBeNull()
    fireEvent.change(screen.getByPlaceholderText('搜尋代號或名稱新增標的…'), { target: { value: 't00' } })
    fireEvent.click(await screen.findByRole('button', { name: /t00\.TWSE/ }))
    fireEvent.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(monitorRuleSave).toHaveBeenCalledTimes(1))
    const saved = monitorRuleSave.mock.calls[0][0] as MonitorRule
    expect(saved.conditions.map(c => c.field)).toEqual(['change_pct'])
  })

  it('成交量以張顯示時保留小數, 不把已存門檻四捨五入', async () => {
    renderEditor(savedRule({ type: 'price', conditions: [{ field: 'volume', op: '>=', value: 1500 }] }))
    await screen.findByText('監控情境')

    expect((screen.getByLabelText('成交量高於數值') as HTMLInputElement).value).toBe('1.5')
  })
})
