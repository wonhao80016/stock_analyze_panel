import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type CustomSourceConfig } from '@/lib/api'
import { DataSourceEditor } from './DataSourceEditor'

vi.mock('@/lib/api', () => ({
  api: {
    dataSource: vi.fn(),
    saveDataSource: vi.fn(),
    updateDataSource: vi.fn(),
    testDataSource: vi.fn(),
  },
}))

async function renderEditor(config: CustomSourceConfig, onActivate: (name: string) => void = () => {}) {
  vi.mocked(api.dataSource).mockResolvedValue(config)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <DataSourceEditor
        existingName={config.name}
        onCancel={() => {}}
        onSaved={() => {}}
        activeName=""
        onActivate={onActivate}
      />
    </QueryClientProvider>,
  )
  await waitFor(() => expect(screen.getByPlaceholderText('my_tushare')).toHaveValue(config.name))
  // The daily tab is open by default; its detail fades in after the empty placeholder exits.
  if (config.datasets.daily) await screen.findByDisplayValue(config.datasets.daily.url)
  return view
}

describe('DataSourceEditor volume field', () => {
  it('asks for volume in shares, which the custom source stores without conversion', async () => {
    await renderEditor({
      name: 'demo',
      display_name: 'demo',
      auth: { type: 'none' },
      datasets: {
        daily: { url: 'http://example.test/daily', method: 'GET', response_path: '', field_map: { vol: 'volume' } },
      },
    })

    const option = screen.getAllByRole('option').find(item => item.getAttribute('value') === 'volume')!
    expect(option.textContent).toContain('成交量 (股')
    expect(option.textContent).not.toContain('張')
  })
})

describe('DataSourceEditor ratio units', () => {
  const realtime = (units?: Record<string, 'decimal' | 'percent'>): CustomSourceConfig => ({
    name: 'demo',
    display_name: 'demo',
    auth: { type: 'none' },
    datasets: {
      realtime: {
        url: 'http://example.test/rt', method: 'GET', response_path: '',
        field_map: { turnover: 'turnover_rate', price: 'last_price' },
        ...(units ? { units } : {}),
      },
    },
  })

  const openRealtime = () => fireEvent.click(screen.getByText('即時行情').closest('button')!)

  it('asks for the unit of each mapped ratio field and starts undeclared', async () => {
    await renderEditor(realtime())
    openRealtime()

    const select = screen.getByLabelText('turnover_rate 單位') as HTMLSelectElement
    expect(select.value).toBe('')
    expect(screen.queryByLabelText('last_price 單位')).toBeNull()
    expect(screen.getByText(/尚未宣告單位/)).toBeInTheDocument()
  })

  it('keeps a declared unit', async () => {
    await renderEditor(realtime({ turnover_rate: 'percent' }))
    openRealtime()

    expect((screen.getByLabelText('turnover_rate 單位') as HTMLSelectElement).value).toBe('percent')
  })

  it('shows the converted preview values of a dataset test', async () => {
    const { api } = await import('@/lib/api')
    vi.mocked(api.testDataSource).mockResolvedValue({
      provider: 'demo', dataset: 'realtime', rows: 1,
      columns: ['symbol', 'turnover_rate'],
      preview: [{ symbol: '2330.TWSE', turnover_rate: 0.0015 }],
    })
    await renderEditor(realtime({ turnover_rate: 'percent' }))
    openRealtime()

    fireEvent.click(screen.getByText('測試'))

    expect(await screen.findByText(/turnover_rate = 0.0015/)).toBeInTheDocument()
  })
})

describe('DataSourceEditor new source name', () => {
  const renderNew = (takenNames: string[]) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={queryClient}>
        <DataSourceEditor
          takenNames={takenNames}
          onCancel={() => {}}
          onSaved={() => {}}
          activeName=""
          onActivate={() => {}}
        />
      </QueryClientProvider>,
    )
  }

  it.each(['mysrc', 'MySrc ', 'taiwan', 'tickflow', '__new__'])('refuses to create a source named %j', async (name) => {
    const { api } = await import('@/lib/api')
    vi.mocked(api.saveDataSource).mockClear()
    renderNew(['mysrc'])

    fireEvent.change(screen.getByPlaceholderText('my_tushare'), { target: { value: name } })
    fireEvent.click(screen.getByRole('button', { name: /儲存/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('已被使用')
    expect(api.saveDataSource).not.toHaveBeenCalled()
  })
})

describe('DataSourceEditor dataset list', () => {
  it('keeps the enable toggles outside the row buttons and labels them', async () => {
    const { container } = await renderEditor({ name: 'demo', display_name: 'demo', auth: { type: 'none' }, datasets: {} })

    expect(container.querySelectorAll('button button')).toHaveLength(0)
    expect(screen.getByRole('button', { name: '日K 啟用' })).toBeInTheDocument()
  })

  it('describes Taiwan symbols and the built-in fallback', async () => {
    await renderEditor({
      name: 'demo',
      display_name: 'demo',
      auth: { type: 'none' },
      datasets: { daily: { url: 'http://example.test/daily', method: 'GET', response_path: '', field_map: { code: 'symbol' } } },
    })

    const option = screen.getAllByRole('option').find(item => item.getAttribute('value') === 'symbol')!
    expect(option.textContent).toContain('2330.TWSE')
    expect(option.textContent).not.toMatch(/\.SZ|\.SH/)
    expect(screen.queryByText('回退 TF')).toBeNull()
    expect(screen.getAllByText('回退內建').length).toBeGreaterThan(0)
    expect(screen.getByPlaceholderText('測試標的,逗號分隔')).toHaveValue('2330.TWSE,6488.TPEX')
  })
})

describe('DataSourceEditor field map rows', () => {
  it('gives an added row its own identity when there are more mappings than target fields', async () => {
    const fieldMap = Object.fromEntries(
      Array.from({ length: 9 }, (_, i) => [`src${i + 1}`, i === 8 ? 'amount' : 'close']),
    )
    await renderEditor({
      name: 'demo',
      display_name: 'demo',
      auth: { type: 'none' },
      datasets: { daily: { url: 'http://example.test/daily', method: 'GET', response_path: '', field_map: fieldMap } },
    })

    fireEvent.click(screen.getByRole('button', { name: /新增對應/ }))
    const inputs = screen.getAllByPlaceholderText('外部欄位名')
    expect(inputs).toHaveLength(10)
    fireEvent.change(inputs[9], { target: { value: 'fresh' } })

    const after = screen.getAllByPlaceholderText('外部欄位名')
    expect(after[8]).toHaveValue('src9')
    expect(after[9]).toHaveValue('fresh')
  })
})

describe('DataSourceEditor existing source', () => {
  const renderExisting = async (config: CustomSourceConfig, onActivate = vi.fn()) => {
    await renderEditor(config, onActivate)
    return onActivate
  }
  const daily = { url: 'http://example.test/daily', method: 'GET', response_path: '', field_map: { c: 'close' } }

  it('saves an edit through the update endpoint', async () => {
    const { api } = await import('@/lib/api')
    vi.mocked(api.saveDataSource).mockClear()
    vi.mocked(api.updateDataSource).mockResolvedValue({} as never)
    await renderExisting({ name: 'mysrc', display_name: 'My Source', auth: { type: 'none' }, datasets: { daily } })

    fireEvent.click(screen.getByRole('button', { name: /儲存/ }))

    await waitFor(() => expect(api.updateDataSource).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.updateDataSource).mock.calls[0][0]).toBe('mysrc')
    expect(api.saveDataSource).not.toHaveBeenCalled()
  })

  it('cannot be switched to when it only offers daily K or adjustment factors', async () => {
    const onActivate = await renderExisting({
      name: 'dailyonly', display_name: 'Daily Only', auth: { type: 'none' },
      datasets: { daily, adj_factor: { ...daily, field_map: { f: 'ex_factor' } } },
    })

    const button = screen.getByRole('button', { name: /切換為目前資料來源/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', expect.stringContaining('台股日K與除權因子固定由台灣官方資料源提供'))
    fireEvent.click(button)
    expect(onActivate).not.toHaveBeenCalled()
  })

  it('can be switched to when it offers realtime quotes', async () => {
    await renderExisting({
      name: 'rt', display_name: 'RT', auth: { type: 'none' },
      datasets: { realtime: { ...daily, field_map: { p: 'last_price' } } },
    })

    expect(screen.getByRole('button', { name: /切換為目前資料來源/ })).toBeEnabled()
  })
})

describe('DataSourceEditor switch with unsaved edits', () => {
  it('asks to save before switching while the form has unsaved edits', async () => {
    const onActivate = vi.fn()
    await renderEditor({
      name: 'rt', display_name: 'RT', auth: { type: 'none' },
      datasets: { realtime: { url: 'http://example.test/rt', method: 'GET', response_path: '', field_map: { p: 'last_price' } } },
    }, onActivate)

    fireEvent.change(screen.getByPlaceholderText('我的 Tushare'), { target: { value: 'RT edited' } })

    const button = screen.getByRole('button', { name: /切換為目前資料來源/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', expect.stringContaining('請先儲存'))
    fireEvent.click(button)
    expect(onActivate).not.toHaveBeenCalled()
  })
})
