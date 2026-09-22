// 策略庫 — 看板的推薦卡只列出「今日有選到股票」的策略, 所以「跑了但零命中」
// 與「根本沒跑」在畫面上長得一樣(都是不存在)。這一頁的存在理由就是把三種
// 狀態分開, 因此測試鎖的是狀態的區分, 不是版面。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Strategies } from './Strategies'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: { ...actual.api, screenerStrategies: vi.fn(), screenerCached: vi.fn() },
  }
})

vi.mock('@/components/StockPreviewDialog', () => ({
  StockPreviewDialog: ({ symbol }: { symbol: string }) => <div>preview:{symbol}</div>,
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Strategies />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const PRESETS = [
  { id: 'boll_breakout', name: '布林突破', description: '突破布林上軌加放量', tags: ['布林'], params: [], entry_signals: [], exit_signals: [] },
  { id: 'oversold_bounce', name: '超跌反彈', description: 'RSI14 低於 30', tags: [], params: [], entry_signals: [], exit_signals: [] },
  { id: 'high_turnover_surge', name: '高換手拉升', description: '換手率大於 5%', tags: [], params: [], entry_signals: [], exit_signals: [] },
]

function mockAll() {
  vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: PRESETS, load_errors: [] } as any)
  vi.mocked(api.screenerCached).mockResolvedValue({
    as_of: '2026-09-22',
    results: {
      boll_breakout: { total: 2, as_of: '2026-09-22', rows: [
        { symbol: '2330.TWSE', name: '台積電', close: 1000, change_pct: 0.02 },
        { symbol: '2317.TWSE', name: '鴻海', close: 200, change_pct: -0.01 },
      ] },
      oversold_bounce: { total: 0, as_of: '2026-09-22', rows: [] },
      // high_turnover_surge 不在 results 裡 = 本輪沒有執行
    },
    updated_at: null,
  } as any)
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('策略庫', () => {
  it('把「有命中」「零命中」「未執行」分成三種狀態, 而不是只顯示有命中的', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(screen.getByText('2 檔')).toBeInTheDocument()
    expect(screen.getByText('超跌反彈')).toBeInTheDocument()
    expect(screen.getByText('0 檔')).toBeInTheDocument()
    expect(screen.getByText('高換手拉升')).toBeInTheDocument()
    expect(screen.getByText('未執行')).toBeInTheDocument()
  })

  it('未執行的策略要說明原因, 不能只是沒有結果', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText(/這支策略本輪沒有執行/)).toBeInTheDocument()
    expect(screen.getByText(/流通股數/)).toBeInTheDocument()
  })

  it('預設收合, 展開後才列出選股與計算方式', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(screen.queryByText('台積電')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '布林突破 的細節' }))

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(screen.getByText('鴻海')).toBeInTheDocument()
    expect(screen.getByText('突破布林上軌加放量')).toBeInTheDocument()
  })

  it('篩選器可以只看未執行的策略', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /未執行 1/ }))

    expect(screen.getByText('高換手拉升')).toBeInTheDocument()
    expect(screen.queryByText('布林突破')).not.toBeInTheDocument()
  })

  it('選股清單為空時不會誤報成未執行', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('超跌反彈')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '超跌反彈 的細節' }))
    expect(screen.getByText(/沒有任何標的符合這支策略的條件/)).toBeInTheDocument()
  })
})

// 多策略共振 — 每支策略的清單各自持有同一檔股票一次, 沒有任何地方說「其他策略
// 也選了它」。這一塊的存在理由就是那個跨策略的交集, 所以測試鎖聚合而非版面。
describe('策略庫 — 多策略共振', () => {
  function mockOverlap() {
    vi.mocked(api.taiwanMarketIntelligence)?.mockResolvedValue?.(null as any)
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [
        { id: 'a', name: '布林突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] },
        { id: 'b', name: 'MA 金叉', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] },
        { id: 'c', name: '趨勢突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] },
      ],
      load_errors: [],
    } as any)
    const tsmc = { symbol: '2330.TWSE', name: '台積電', close: 1000, change_pct: 0.02 }
    const hon = { symbol: '2317.TWSE', name: '鴻海', close: 200, change_pct: 0.01 }
    const solo = { symbol: '1101.TWSE', name: '台泥', close: 40, change_pct: -0.01 }
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-22',
      results: {
        a: { total: 2, as_of: '2026-09-22', rows: [tsmc, hon] },
        b: { total: 3, as_of: '2026-09-22', rows: [tsmc, hon, solo] },
        c: { total: 1, as_of: '2026-09-22', rows: [tsmc] },
      },
      updated_at: null,
    } as any)
  }

  it('列出被多支策略同時選中的個股, 並標出是哪幾支', async () => {
    mockOverlap()
    renderPage()

    expect(await screen.findByText('多策略共振')).toBeInTheDocument()
    const panel = screen.getByText('多策略共振').closest('section')!
    // 台積電 3 支、鴻海 2 支都要在; 只被一支選中的台泥不該出現在這一塊
    expect(within(panel).getByText('台積電')).toBeInTheDocument()
    expect(within(panel).getByText('鴻海')).toBeInTheDocument()
    expect(within(panel).queryByText('台泥')).not.toBeInTheDocument()
    // 命中的策略名稱要列出來, 不能只給一個數字
    expect(within(panel).getAllByText('布林突破').length).toBeGreaterThan(0)
    expect(within(panel).getAllByText('趨勢突破').length).toBeGreaterThan(0)
  })

  it('門檻可以收緊到只看命中更多支的個股', async () => {
    mockOverlap()
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    expect(within(panel).getByText('鴻海')).toBeInTheDocument()

    fireEvent.click(within(panel).getByRole('button', { name: /≥3 支/ }))

    expect(within(panel).getByText('台積電')).toBeInTheDocument()
    expect(within(panel).queryByText('鴻海')).not.toBeInTheDocument()
  })

  it('沒有任何個股被重複命中時整塊不出現', async () => {
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [{ id: 'a', name: '布林突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [] }],
      load_errors: [],
    } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-22',
      results: { a: { total: 1, as_of: '2026-09-22', rows: [{ symbol: '2330.TWSE', name: '台積電', close: 1, change_pct: 0 }] } },
      updated_at: null,
    } as any)
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(screen.queryByText('多策略共振')).not.toBeInTheDocument()
  })
})

// 選股上限 — 達到 limit 的策略公布的是「分數前 N 名」而非「所有符合條件者」,
// 所以共振的命中支數會系統性少算。數字可以不精確, 但不能看起來很精確。
describe('策略庫 — 選股上限揭露', () => {
  function mockCapped() {
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [
        { id: 'capped', name: '均線多頭', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 2 },
        { id: 'roomy', name: '布林突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 100 },
      ],
      load_errors: [],
    } as any)
    const a = { symbol: '2330.TWSE', name: '台積電', close: 1000, change_pct: 0.02 }
    const b = { symbol: '2317.TWSE', name: '鴻海', close: 200, change_pct: 0.01 }
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-22',
      results: {
        capped: { total: 2, as_of: '2026-09-22', rows: [a, b] },
        roomy: { total: 2, as_of: '2026-09-22', rows: [a, b] },
      },
      updated_at: null,
    } as any)
  }

  it('達到上限的策略在列上標出來, 未達上限的不標', async () => {
    mockCapped()
    renderPage()

    // 策略名稱在共振區的標籤上也會出現, 用唯一的策略 id 定位那一列。
    const capped = (await screen.findByText('capped')).closest('section')!
    expect(within(capped).getByText('已達上限')).toBeInTheDocument()

    const roomy = screen.getByText('roomy').closest('section')!
    expect(within(roomy).queryByText('已達上限')).not.toBeInTheDocument()
  })

  it('共振區說明命中支數的語意, 並點名被截斷的策略', async () => {
    mockCapped()
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    expect(within(panel).getByText(/出現在幾支策略的選股清單中/)).toBeInTheDocument()
    expect(within(panel).getByText(/均線多頭 已達各自的選股上限/)).toBeInTheDocument()
  })

  it('沒有任何策略達到上限時不顯示截斷警示', async () => {
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [
        { id: 'a', name: '布林突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 100 },
        { id: 'b', name: 'MA 金叉', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 100 },
      ],
      load_errors: [],
    } as any)
    const a = { symbol: '2330.TWSE', name: '台積電', close: 1000, change_pct: 0.02 }
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-22',
      results: {
        a: { total: 1, as_of: '2026-09-22', rows: [a] },
        b: { total: 1, as_of: '2026-09-22', rows: [a] },
      },
      updated_at: null,
    } as any)
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    expect(within(panel).getByText(/出現在幾支策略的選股清單中/)).toBeInTheDocument()
    expect(within(panel).queryByText(/已達各自的選股上限/)).not.toBeInTheDocument()
  })
})

// 策略複選 — 勾選 = 「必須命中」。交集語意: 勾兩支就只留同時被這兩支選中的個股,
// 所以測試鎖的是「勾選如何收窄結果」, 而不是版面。
describe('策略庫 — 指定必須命中的策略', () => {
  const tsmc = { symbol: '2330.TWSE', name: '台積電', close: 1000, change_pct: 0.02 }
  const hon = { symbol: '2317.TWSE', name: '鴻海', close: 200, change_pct: 0.01 }

  function mockThree() {
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [
        { id: 'a', name: '布林突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 500 },
        { id: 'b', name: 'MA 金叉', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 500 },
        { id: 'c', name: '趨勢突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 500 },
      ],
      load_errors: [],
    } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-22',
      results: {
        // 台積電 = a+b, 鴻海 = a+c
        a: { total: 2, as_of: '2026-09-22', rows: [tsmc, hon] },
        b: { total: 1, as_of: '2026-09-22', rows: [tsmc] },
        c: { total: 1, as_of: '2026-09-22', rows: [hon] },
      },
      updated_at: null,
    } as any)
  }

  it('未指定時列出所有重複命中的個股', async () => {
    mockThree()
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    expect(within(panel).getByText('台積電')).toBeInTheDocument()
    expect(within(panel).getByText('鴻海')).toBeInTheDocument()
    expect(within(panel).getByText(/未指定，列出所有重複命中/)).toBeInTheDocument()
  })

  it('指定一支後, 只留被那支選中的個股', async () => {
    mockThree()
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'MA 金叉' }))

    // b 只選了台積電, 所以鴻海被排除
    expect(within(panel).getByText('台積電')).toBeInTheDocument()
    expect(within(panel).queryByText('鴻海')).not.toBeInTheDocument()
  })

  it('指定兩支時取交集, 只中其中一支的不算', async () => {
    mockThree()
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'MA 金叉' }))
    fireEvent.click(within(panel).getByRole('checkbox', { name: '趨勢突破' }))

    // 沒有任何個股同時被 b 與 c 選中
    expect(within(panel).getByText(/沒有個股同時被指定的這幾支策略選中/)).toBeInTheDocument()
    expect(within(panel).queryByText('台積電')).not.toBeInTheDocument()
  })

  it('結果為空時選擇器仍在, 清除可以復原', async () => {
    mockThree()
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'MA 金叉' }))
    fireEvent.click(within(panel).getByRole('checkbox', { name: '趨勢突破' }))

    expect(within(panel).getByRole('checkbox', { name: '布林突破' })).toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: '清除' }))
    expect(within(panel).getByText('鴻海')).toBeInTheDocument()
  })

  it('選擇器只列出真的有選到股票的策略', async () => {
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [
        { id: 'a', name: '布林突破', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 500 },
        { id: 'b', name: 'MA 金叉', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 500 },
        { id: 'empty', name: '超跌反彈', description: '', tags: [], params: [], entry_signals: [], exit_signals: [], limit: 500 },
      ],
      load_errors: [],
    } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-22',
      results: {
        a: { total: 1, as_of: '2026-09-22', rows: [tsmc] },
        b: { total: 1, as_of: '2026-09-22', rows: [tsmc] },
        empty: { total: 0, as_of: '2026-09-22', rows: [] },
      },
      updated_at: null,
    } as any)
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    expect(within(panel).getByRole('checkbox', { name: '布林突破' })).toBeInTheDocument()
    expect(within(panel).queryByRole('checkbox', { name: '超跌反彈' })).not.toBeInTheDocument()
  })
})

// 選項的 tooltip — 要選哪幾支策略取交集, 前提是知道每支在算什麼。內容與展開面板
// 共用 strategyRuleParts, 所以這裡鎖的是「有掛上且內容正確」, 不重測格式化。
describe('策略庫 — 選項顯示計算方式', () => {
  it('游標停留在策略選項上時給出說明、訊號與風控', async () => {
    const preset = {
      id: 'a',
      name: '布林突破',
      description: '突破布林上軌加放量',
      tags: [],
      params: [{ id: 'min_ratio', label: '最低量比', default: 2 }],
      params_defaults: { min_ratio: 2 },
      entry_signals: ['signal_boll_breakout_upper'],
      exit_signals: [],
      stop_loss: 0.08,
      limit: 500,
    }
    vi.mocked(api.screenerStrategies).mockResolvedValue({
      presets: [preset, { ...preset, id: 'b', name: 'MA 金叉', description: 'MA5 上穿 MA20' }],
      load_errors: [],
    } as any)
    const tsmc = { symbol: '2330.TWSE', name: '台積電', close: 1000, change_pct: 0.02 }
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-22',
      results: {
        a: { total: 1, as_of: '2026-09-22', rows: [tsmc] },
        b: { total: 1, as_of: '2026-09-22', rows: [tsmc] },
      },
      updated_at: null,
    } as any)
    renderPage()

    const panel = (await screen.findByText('多策略共振')).closest('section')!
    const chip = within(panel).getByRole('checkbox', { name: '布林突破' })
    const tip = chip.getAttribute('title') ?? ''

    expect(tip).toContain('突破布林上軌加放量')
    // 訊號 id 要翻成中文, 不能把 signal_* 丟給使用者
    expect(tip).toContain('突破布林上軌')
    expect(tip).not.toContain('signal_boll_breakout_upper')
    expect(tip).toContain('停損 8.0%')
    expect(tip).toContain('最低量比: 2')
  })
})
