// 策略庫 — 看板的推薦卡只列出「今日有選到股票」的策略, 所以「跑了但零命中」
// 與「根本沒跑」在畫面上長得一樣(都是不存在)。這一頁的存在理由就是把三種
// 狀態分開, 因此測試鎖的是狀態的區分, 不是版面。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useSearchParams } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Strategies } from './Strategies'
import { api } from '@/lib/api'
import { INVESTMENT_DISCLAIMER } from '@/lib/disclaimer'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      screenerStrategies: vi.fn(),
      screenerCached: vi.fn(),
      screenerPickPerformance: vi.fn(),
      screenerPickPerformanceDetail: vi.fn(),
      screenerPickBackfill: vi.fn(),
      screenerPickBackfillStatus: vi.fn(),
    },
  }
})

vi.mock('@/components/StockPreviewDialog', () => ({
  StockPreviewDialog: ({ symbol }: { symbol: string }) => <div>preview:{symbol}</div>,
}))

function SearchProbe() {
  const [params] = useSearchParams()
  return <output data-testid="search">{params.toString()}</output>
}

function renderPage(path = '/strategies') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Strategies />
        <SearchProbe />
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

  it('展開策略後可將策略 ID 帶入回測工作區', async () => {
    mockAll()
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '布林突破 的細節' }))

    expect(screen.getByRole('link', { name: '回測此策略' })).toHaveAttribute(
      'href',
      '/backtest?strategy_id=boll_breakout',
    )
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

describe('策略庫 — 事後統計分頁', () => {
  it('區分缺必要篩選資料與缺評分資料, 保留有效報酬但不宣稱可靠排名', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5], {
      scoring_unavailable_count: 5, input_unavailable_days: 2,
    })])
    mockDetail([record({ calculation_status: 'scoring_unavailable', missing_fields: ['turnover_rate'] })])
    renderPage('/strategies?tab=performance')
    expect(await screen.findByText(/5 筆缺評分資料；保留完整候選與報酬/)).toBeInTheDocument()
    expect(screen.getByText('2 日缺必要篩選資料，未能計算歷史訊號')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '布林突破 的選股紀錄' }))
    expect(await screen.findByText('缺評分資料（換手率）；列序不代表有效排名')).toBeInTheDocument()
  })

  it('補算所選交易日並在完成後刷新事後統計與已展開明細', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5], { reconstructed_count: 5 })])
    mockDetail()
    const completed = { job_id: 'history-test', status: 'completed', requested_days: 10,
      dates: ['2026-09-23', '2026-09-24'], completed_days: 2, written_runs: 4, existing_runs: 1,
      zero_signal_runs: 1, failures: [], error: null } as const
    const running = { ...completed, status: 'running' as const, completed_days: 0, dates: [...completed.dates], failures: [] }
    vi.mocked(api.screenerPickBackfill).mockResolvedValue(running)
    vi.mocked(api.screenerPickBackfillStatus).mockResolvedValueOnce(running)
      .mockResolvedValue({ ...completed, dates: [...completed.dates], failures: [] })
    renderPage('/strategies?tab=performance')
    fireEvent.click(await screen.findByRole('button', { name: '布林突破 的選股紀錄' }))
    await screen.findByText('台積電')
    expect(screen.getByText('依目前策略重建歷史 5 筆')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('歷史回算交易日數'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: '補算歷史訊號' }))
    expect(await screen.findByRole('button', { name: '正在補算…' })).toBeDisabled()
    expect(screen.getByLabelText('歷史回算交易日數')).toBeDisabled()
    await screen.findByText(/計算進度：0／2 日/)
    await screen.findByText(/補算完成：2／2 日/, {}, { timeout: 5000 })
    expect(api.screenerPickBackfill).toHaveBeenCalledWith(10)
    await waitFor(() => expect(api.screenerPickPerformance).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(api.screenerPickPerformanceDetail).toHaveBeenCalledTimes(2))
    expect(screen.getByText(/本機僅有 2 個可回算交易日/)).toBeInTheDocument()
  })

  it('歷史回算狀態讀取失敗後可重試且保留原統計', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    const running = { job_id: 'history-error', status: 'running', requested_days: 5,
      dates: ['2026-09-23'], completed_days: 0, written_runs: 0, existing_runs: 0,
      zero_signal_runs: 0, failures: [], error: null } as const
    vi.mocked(api.screenerPickBackfill).mockResolvedValue({ ...running, dates: [...running.dates], failures: [] })
    vi.mocked(api.screenerPickBackfillStatus)
      .mockRejectedValueOnce(new Error('job unavailable'))
      .mockResolvedValue({ ...running, status: 'completed', completed_days: 1, dates: [...running.dates], failures: [] })
    renderPage('/strategies?tab=performance')
    await screen.findByText('布林突破')
    fireEvent.click(screen.getByRole('button', { name: '補算歷史訊號' }))
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: '補算歷史訊號' })).toBeEnabled()
    expect(screen.getByText('布林突破')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '補算歷史訊號' }))
    await waitFor(() => expect(api.screenerPickBackfill).toHaveBeenCalledTimes(2))
    await screen.findByText(/補算完成：1／1 日/)
    expect(api.screenerPickBackfillStatus).toHaveBeenCalledTimes(2)
  })


  function horizon(sample: number, extra: Record<string, unknown> = {}) {
    return {
      sample_count: sample,
      sample_days: sample > 0 ? 1 : 0,
      mean_return: sample > 0 ? 0.0123 : null,
      median_return: sample > 0 ? -0.005 : null,
      positive_ratio: sample > 0 ? 0.6 : null,
      adjustment_not_covered: 0,
      data_incomplete: 0,
      prices_incomplete: 0,
      stale_exit_count: 0,
      pending_count: 0,
      ...extra,
    }
  }

  function stats(id: string, samples: [number, number, number, number], extra: Record<string, unknown> = {}) {
    const [h1, h5, h10, h20] = samples
    return {
      strategy_id: id,
      signal_count: 40,
      signal_days: 8,
      filled_count: 36,
      not_filled_limit_up: 3,
      not_filled_no_trade: 1,
      limit_check_unknown: 0,
      pending_entry_count: 0,
      tracking_count: 30,
      completed_count: 6,
      entry_prices_incomplete_count: 0,
      entry_data_incomplete_count: 0,
      absent_count: 0,
      history_degraded_count: 0,
      excluded: { manifest_missing: 0, recorded_late: 0, universe_incomplete: 0 },
      horizons: { '1': horizon(h1), '5': horizon(h5), '10': horizon(h10), '20': horizon(h20) },
      ...extra,
    }
  }

  function summary(strategies: unknown[], extra: Record<string, unknown> = {}) {
    return {
      method: {
        entry: 'next_session_open',
        horizons: [1, 5, 10, 20],
        price_adjustment: 'forward_adjusted_incl_dividends',
        costs_included: false,
        slippage_included: false,
        exit_fill_simulated: false,
        return_unit: 'decimal',
      },
      adjustment: { status: 'ok', reason: null, covered_through: '2026-09-30' },
      first_signal_date: '2026-09-01',
      last_signal_date: '2026-10-01',
      prices_through: '2026-10-02',
      strategies,
      ...extra,
    }
  }

  function mockSummary(strategies: unknown[], extra: Record<string, unknown> = {}) {
    vi.mocked(api.screenerPickPerformance).mockResolvedValue(summary(strategies, extra) as any)
  }

  const ret = (value: number | null, reason: string | null, staleDays = 0) =>
    ({ value, reason, stale_exit: staleDays > 0, stale_days: staleDays })

  function record(extra: Record<string, unknown> = {}) {
    return {
      signal_date: '2026-09-29',
      symbol: '2330.TWSE',
      name: '台積電',
      rank: 1,
      entry_date: '2026-09-30',
      entry_price_raw: 1000,
      status: 'tracking',
      limit_check: 'checked',
      absent_since: null,
      excluded_reason: null,
      recorded_late: false,
      manifest_missing: false,
      universe_complete: true,
      history_degraded: false,
      returns: { '1': ret(0.0123, null), '5': ret(null, 'pending'), '10': ret(null, 'pending'), '20': ret(null, 'pending') },
      ...extra,
    }
  }

  function mockDetail(rows: unknown[] = [record()]) {
    vi.mocked(api.screenerPickPerformanceDetail).mockResolvedValue({
      strategy_id: 'boll_breakout',
      method: summary([]).method,
      adjustment: summary([]).adjustment,
      prices_through: '2026-10-02',
      total: rows.length,
      signal_days: rows.length > 0 ? 1 : 0,
      rows,
    } as any)
  }

  function tab(name: string) {
    return screen.getByRole('tab', { name })
  }

  function horizonButton(label: string) {
    return screen.getByRole('button', { name: label })
  }

  it('預設分頁仍是原本的策略清單, 不請求事後統計', async () => {
    mockAll()
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(screen.getByText('2 檔')).toBeInTheDocument()
    expect(screen.getByText('選股基準日 2026-09-22')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /未執行 1/ })).toBeInTheDocument()
    expect(tab('策略清單')).toHaveAttribute('aria-selected', 'true')
    expect(tab('事後統計')).toHaveAttribute('aria-selected', 'false')
    expect(api.screenerPickPerformance).not.toHaveBeenCalled()
  })

  it('切到「事後統計」才發出請求, 網址改為 tab=performance', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    renderPage()

    expect(await screen.findByText('布林突破')).toBeInTheDocument()
    expect(api.screenerPickPerformance).not.toHaveBeenCalled()

    fireEvent.click(tab('事後統計'))

    expect(await screen.findByText(/以 T\+1 開盤價為計算起點，已含除權息調整，未扣交易成本/)).toBeInTheDocument()
    expect(api.screenerPickPerformance).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('search')).toHaveTextContent('tab=performance')
    expect(tab('事後統計')).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByText('2 檔')).not.toBeInTheDocument()
  })

  it('說明列附上資料區間與未計入的成本項目', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    renderPage('/strategies?tab=performance')

    expect(await screen.findByText(/訊號日 2026-09-01 ～ 2026-10-01/)).toBeInTheDocument()
    expect(screen.getByText(/價格資料至 2026-10-02/)).toBeInTheDocument()
    expect(screen.getByText(/未扣交易成本、未計滑點、未模擬出場可成交性/)).toBeInTheDocument()
  })

  it('載入中顯示讀取狀態', async () => {
    mockAll()
    vi.mocked(api.screenerPickPerformance).mockReturnValue(new Promise(() => {}))
    renderPage('/strategies?tab=performance')

    expect(await screen.findByText(/正在讀取事後統計/)).toBeInTheDocument()
  })

  it('沒有任何選股紀錄時顯示空狀態', async () => {
    mockAll()
    mockSummary([], { first_signal_date: null, last_signal_date: null })
    renderPage('/strategies?tab=performance')

    expect(await screen.findByText(/尚無選股紀錄/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('讀取失敗時依錯誤代碼說明原因', async () => {
    mockAll()
    vi.mocked(api.screenerPickPerformance).mockRejectedValue(
      new Error(JSON.stringify({ code: 'pick_history_unreadable', message: 'Strategy pick snapshots could not be read.' })),
    )
    renderPage('/strategies?tab=performance')

    expect(await screen.findByText(/選股紀錄目前無法讀取/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('價格資料讀取失敗時有對應的說明', async () => {
    mockAll()
    vi.mocked(api.screenerPickPerformance).mockRejectedValue(
      new Error(JSON.stringify({ code: 'pick_prices_unreadable', message: 'Daily prices could not be read.' })),
    )
    renderPage('/strategies?tab=performance')

    expect(await screen.findByText(/日 K 價格資料目前無法讀取/)).toBeInTheDocument()
  })

  it('除權息資料不可用時顯示橫幅與原因', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [0, 0, 0, 0])], {
      adjustment: { status: 'unavailable', reason: 'events_missing', covered_through: null },
    })
    renderPage('/strategies?tab=performance')

    const banner = await screen.findByRole('alert')
    expect(banner).toHaveTextContent('除權息資料目前無法使用')
    expect(banner).toHaveTextContent('除權息資料檔不存在')
    expect(banner).toHaveTextContent('報酬暫不計算')
  })

  it('除權息資料正常時不顯示橫幅', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    renderPage('/strategies?tab=performance')

    expect(await screen.findByText(/除權息資料涵蓋至 2026-09-30/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('有 T+20 樣本時預設選 T+20, 也能切換', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    renderPage('/strategies?tab=performance')

    await screen.findByText('布林突破')
    expect(horizonButton('T+20')).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(horizonButton('T+5'))
    expect(horizonButton('T+5')).toHaveAttribute('aria-pressed', 'true')
    expect(horizonButton('T+20')).toHaveAttribute('aria-pressed', 'false')
  })

  it('T+20 沒有樣本時改選有樣本的最長持有期, 樣本為 0 的按鈕停用', async () => {
    mockAll()
    mockSummary([
      stats('boll_breakout', [3, 2, 0, 0]),
      stats('oversold_bounce', [4, 0, 1, 0]),
    ])
    renderPage('/strategies?tab=performance')

    await screen.findByText('布林突破')
    expect(horizonButton('T+20')).toBeDisabled()
    expect(horizonButton('T+10')).toBeEnabled()
    expect(horizonButton('T+10')).toHaveAttribute('aria-pressed', 'true')
    expect(horizonButton('T+1')).toHaveAttribute('aria-pressed', 'false')
  })

  it('所有持有期都沒有樣本時選 T+1, 且全部停用', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [0, 0, 0, 0])])
    renderPage('/strategies?tab=performance')

    await screen.findByText('布林突破')
    for (const label of ['T+1', 'T+5', 'T+10', 'T+20']) {
      expect(horizonButton(label)).toBeDisabled()
    }
    expect(horizonButton('T+1')).toHaveAttribute('aria-pressed', 'true')
  })

  it('小數報酬乘以 100 顯示為百分比', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [40, 40, 40, 40])])
    renderPage('/strategies?tab=performance')

    const row = (await screen.findByText('布林突破')).closest('tr')!
    expect(within(row).getByText('+1.23%')).toBeInTheDocument()
    expect(within(row).getByText('-0.50%')).toBeInTheDocument()
    expect(within(row).getByText('60.0%')).toBeInTheDocument()
    expect(within(row).queryByText(/0\.0123/)).not.toBeInTheDocument()
  })

  it('用策略名稱並依名稱排序, 訊號日數少於 20 的列標樣本少並為灰字', async () => {
    mockAll()
    mockSummary([
      stats('oversold_bounce', [5, 5, 5, 5], { signal_count: 20, signal_days: 20 }),
      stats('boll_breakout', [200, 200, 200, 200], { signal_count: 200, signal_days: 19 }),
    ])
    renderPage('/strategies?tab=performance')

    const bollRow = (await screen.findByText('布林突破')).closest('tr')!
    const bounceRow = screen.getByText('超跌反彈').closest('tr')!
    expect(bollRow.compareDocumentPosition(bounceRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(bollRow).toHaveClass('text-muted')
    expect(within(bollRow).getByText('樣本少')).toBeInTheDocument()
    expect(bounceRow).toHaveClass('text-foreground')
    expect(within(bounceRow).queryByText('樣本少')).not.toBeInTheDocument()
    expect(screen.getByText(/訊號日數少於 20 日的策略標為「樣本少」/)).toBeInTheDocument()
  })

  it('彙總表揭露缺席 (停牌或下市) 的筆數', async () => {
    mockAll()
    mockSummary([
      stats('boll_breakout', [5, 5, 5, 5], { absent_count: 2 }),
      stats('oversold_bounce', [5, 5, 5, 5]),
    ])
    renderPage('/strategies?tab=performance')

    const bollRow = (await screen.findByText('布林突破')).closest('tr')!
    expect(within(bollRow).getByText('缺席 2')).toBeInTheDocument()
    const bounceRow = screen.getByText('超跌反彈').closest('tr')!
    expect(within(bounceRow).queryByText(/缺席/)).not.toBeInTheDocument()
  })

  it('明細讀取失敗時在展開區說明原因', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    vi.mocked(api.screenerPickPerformanceDetail).mockRejectedValue(
      new Error(JSON.stringify({ code: 'pick_prices_unreadable', message: 'Daily prices could not be read.' })),
    )
    renderPage('/strategies?tab=performance')

    fireEvent.click(await screen.findByRole('button', { name: '布林突破 的選股紀錄' }))

    expect(await screen.findByText(/日 K 價格資料目前無法讀取/)).toBeInTheDocument()
    expect(screen.getByText('布林突破')).toBeInTheDocument()
  })

  it('明細沒有紀錄時顯示空狀態', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    mockDetail([])
    renderPage('/strategies?tab=performance')

    fireEvent.click(await screen.findByRole('button', { name: '布林突破 的選股紀錄' }))

    expect(await screen.findByText('這支策略沒有選股紀錄。')).toBeInTheDocument()
  })

  it('顯示未計入筆數、持有期層級的排除與歷史資料不完整提示', async () => {
    mockAll()
    const base = stats('boll_breakout', [5, 5, 5, 5], {
      excluded: { manifest_missing: 1, recorded_late: 2, universe_incomplete: 0 },
      history_degraded_count: 4,
    }) as any
    base.horizons['20'] = horizon(5, { adjustment_not_covered: 2, prices_incomplete: 1 })
    mockSummary([base])
    renderPage('/strategies?tab=performance')

    const row = (await screen.findByText('布林突破')).closest('tr')!
    expect(within(row).getByText('延遲紀錄／市場不完整／清單缺漏，未計入 3 筆')).toBeInTheDocument()
    expect(within(row).getByText('另 3 筆未計入')).toBeInTheDocument()
    expect(within(row).getByText(/4 筆選股產生時歷史資料讀取不完整/)).toBeInTheDocument()
  })

  it('展開一列後才請求明細, 點標的開啟預覽', async () => {
    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    mockDetail()
    renderPage('/strategies?tab=performance')

    await screen.findByText('布林突破')
    expect(api.screenerPickPerformanceDetail).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '布林突破 的選股紀錄' }))

    expect(await screen.findByText('台積電')).toBeInTheDocument()
    expect(api.screenerPickPerformanceDetail).toHaveBeenCalledWith('boll_breakout', 200)
    expect(screen.getAllByText('未滿期').length).toBe(3)

    fireEvent.click(screen.getByRole('button', { name: '查看 台積電 走勢' }))
    expect(screen.getByText('preview:2330.TWSE')).toBeInTheDocument()
  })

  it('含所有條件式提示的畫面附上投資風險提示', async () => {
    mockAll()
    const full = stats('boll_breakout', [5, 5, 5, 5], {
      signal_days: 3,
      limit_check_unknown: 2,
      pending_entry_count: 1,
      entry_prices_incomplete_count: 1,
      entry_data_incomplete_count: 1,
      absent_count: 2,
      history_degraded_count: 3,
      excluded: { manifest_missing: 1, recorded_late: 1, universe_incomplete: 1 },
    }) as any
    for (const key of ['1', '5', '10', '20']) {
      full.horizons[key] = horizon(5, {
        adjustment_not_covered: 1, data_incomplete: 1, prices_incomplete: 1, stale_exit_count: 1, pending_count: 1,
      })
    }
    mockSummary([full], {
      adjustment: { status: 'unavailable', reason: 'coverage_missing', covered_through: null },
    })
    const reasons = ['pending', 'not_filled_limit_up', 'not_filled_no_trade', 'adjustment_not_covered',
      'data_incomplete', 'prices_incomplete', 'events_missing', 'events_unreadable', 'coverage_missing']
    const statuses = ['pending_entry', 'tracking', 'completed', 'not_filled_limit_up', 'not_filled_no_trade',
      'prices_incomplete', 'data_incomplete']
    mockDetail([
      ...reasons.map((reason, i) => record({
        symbol: `R${i}.TWSE`,
        status: statuses[i % statuses.length],
        returns: { '1': ret(0.01, null, 2), '5': ret(null, reason), '10': ret(-0.02, null), '20': ret(null, reason) },
      })),
      record({ symbol: 'X1.TWSE', excluded_reason: 'recorded_late' }),
      record({ symbol: 'X2.TWSE', excluded_reason: 'universe_incomplete' }),
      record({ symbol: 'X3.TWSE', excluded_reason: 'manifest_missing' }),
      record({ symbol: 'X4.TWSE', absent_since: '2026-09-30', limit_check: 'unknown', history_degraded: true }),
    ])
    const { container } = renderPage('/strategies?tab=performance')

    fireEvent.click(await screen.findByRole('button', { name: '布林突破 的選股紀錄' }))
    await screen.findByText('X4.TWSE')

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('缺席 2')).toBeInTheDocument()
    expect(screen.getByText('漲停未判定 2')).toBeInTheDocument()
    expect(screen.getByText('沿用前收 1')).toBeInTheDocument()
    expect(screen.getByText('2026-09-30 起無資料')).toBeInTheDocument()
    expect(container.textContent).toContain(INVESTMENT_DISCLAIMER)
  })

  it('摘要讀取失敗時顯示原因; 只有明細失敗時仍附投資風險提示', async () => {
    const errors = [
      new Error(JSON.stringify({ code: 'pick_history_unreadable', message: 'x' })),
      new Error(JSON.stringify({ code: 'pick_prices_unreadable', message: 'x' })),
      new Error('500 Internal Server Error'),
    ]
    for (const error of errors) {
      mockAll()
      vi.mocked(api.screenerPickPerformance).mockRejectedValue(error)
      const { unmount } = renderPage('/strategies?tab=performance')
      await screen.findByText(/無法讀取/)
      unmount()
    }

    mockAll()
    mockSummary([stats('boll_breakout', [5, 5, 5, 5])])
    vi.mocked(api.screenerPickPerformanceDetail).mockRejectedValue(new Error('500 Internal Server Error'))
    const { container } = renderPage('/strategies?tab=performance')
    fireEvent.click(await screen.findByRole('button', { name: '布林突破 的選股紀錄' }))
    expect(await screen.findByText('目前無法讀取這支策略的選股紀錄。')).toBeInTheDocument()
    expect(container.textContent).toContain(INVESTMENT_DISCLAIMER)
  })
})

describe('策略庫 — 分級與大盤濾鏡', () => {
  const marketState = {
    as_of: '2026-09-22', index_symbol: 't00.TWSE', close: 21000, ma: 21500, window: 20,
    below_ma: true, status: 'ok', reason: null,
  }

  function mockGraded(marketFilter: 'applied' | 'passed' | 'unavailable', rows: any[]) {
    vi.mocked(api.screenerStrategies).mockResolvedValue({ presets: PRESETS, load_errors: [] } as any)
    vi.mocked(api.screenerCached).mockResolvedValue({
      as_of: '2026-09-22',
      results: { boll_breakout: { total: rows.length, as_of: '2026-09-22', rows } },
      updated_at: null,
      market_state: marketFilter === 'unavailable'
        ? { ...marketState, close: null, ma: null, below_ma: null, status: 'unavailable', reason: 'insufficient_history' }
        : { ...marketState, below_ma: marketFilter === 'applied' },
      grading: { top_n: 5, market_filter: marketFilter },
    } as any)
  }

  const GRADED_ROWS = [
    { symbol: '2330.TWSE', name: '台積電', close: 1000, change_pct: 0.02, score: 80, grade: 'buy', grade_reason: null },
    { symbol: '2317.TWSE', name: '鴻海', close: 200, change_pct: -0.01, score: 70, grade: 'watch', grade_reason: 'outside_top_n' },
  ]

  async function expandBoll() {
    fireEvent.click(await screen.findByRole('button', { name: '布林突破 的細節' }))
    await screen.findByText('台積電')
  }

  it('選股列表每一列標出買進或觀察', async () => {
    mockGraded('passed', GRADED_ROWS)
    renderPage()
    await expandBoll()

    expect(screen.getByText('買進').closest('button')).toHaveTextContent('台積電')
    const watch = screen.getByText('觀察')
    expect(watch.closest('button')).toHaveTextContent('鴻海')
    expect(watch).toHaveAttribute('title', '排名在前 5 名之外')
  })

  it('無法評分的列標出評分資料不足, 分數顯示為 —', async () => {
    mockGraded('passed', [
      GRADED_ROWS[0],
      { symbol: '2454.TWSE', name: '聯發科', close: 1500, change_pct: 0, score: null, grade: 'watch', grade_reason: 'score_unavailable' },
    ])
    renderPage()
    await expandBoll()

    const badge = screen.getByText('觀察・資料不足')
    expect(badge.closest('button')).toHaveTextContent('聯發科')
    expect(badge).toHaveAttribute('title', '評分資料不足（分數 —）')
    expect(screen.getByText('買進').closest('button')).toHaveTextContent('台積電')
  })

  it('漲停鎖死的選股另加標籤, 分級不變', async () => {
    mockGraded('passed', [{ ...GRADED_ROWS[0], change_pct: 0.1, limit_up_locked: true }, GRADED_ROWS[1]])
    renderPage()
    await expandBoll()

    const tag = screen.getByText('漲停鎖死')
    expect(tag.closest('button')).toBe(screen.getByText('買進').closest('button'))
    expect(tag.closest('button')).toHaveTextContent('台積電')
  })

  it('選股基準日旁的大盤狀態列附上投資風險提示', async () => {
    mockGraded('passed', GRADED_ROWS)
    renderPage()

    expect(await screen.findByText('選股基準日 2026-09-22')).toBeInTheDocument()
    const line = screen.getByText(INVESTMENT_DISCLAIMER).closest('p') as HTMLElement
    expect(line).toHaveTextContent('大盤：未跌破 20 日均線')
  })

  it('跌破時列出大盤降級的徽章與說明', async () => {
    mockGraded('applied', [{ ...GRADED_ROWS[0], grade: 'watch', grade_reason: 'market_below_ma20' }])
    renderPage()
    await expandBoll()

    expect(screen.getByText('觀察・大盤')).toHaveAttribute(
      'title',
      '加權指數 2026-09-22 收盤 21000.00 低於 20 日均線 21500.00（價格指數，不含股利）',
    )
    expect(screen.getByText('大盤：跌破 20 日均線，買進已降為觀察')).toBeInTheDocument()
    expect(screen.queryByText('買進')).not.toBeInTheDocument()
  })

  it('無法判定時說明濾鏡未套用', async () => {
    mockGraded('unavailable', GRADED_ROWS)
    renderPage()

    expect(await screen.findByText('大盤狀態無法判定，濾鏡未套用')).toHaveAttribute('title', '交易日不足 20 天')
  })

  it('事後統計分頁不出現徽章與大盤狀態列', async () => {
    mockGraded('applied', GRADED_ROWS)
    vi.mocked(api.screenerPickPerformance).mockReturnValue(new Promise(() => {}))
    renderPage('/strategies?tab=performance')

    await waitFor(() => expect(api.screenerPickPerformance).toHaveBeenCalled())
    await waitFor(() => expect(api.screenerCached).toHaveBeenCalled())
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(screen.queryByText(/^大盤/)).not.toBeInTheDocument()
    expect(screen.queryByText('買進')).not.toBeInTheDocument()
    expect(screen.queryByText('觀察')).not.toBeInTheDocument()
  })
})
