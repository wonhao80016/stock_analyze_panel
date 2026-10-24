import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { CustomSourceConfig } from '@/lib/api'
import { DataSourceEditor } from './DataSourceEditor'

vi.mock('@/lib/api', () => ({
  api: {
    dataSource: vi.fn(),
    saveDataSource: vi.fn(),
    testDataSource: vi.fn(),
  },
}))

function renderEditor(config: CustomSourceConfig) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <DataSourceEditor
        initial={config}
        onCancel={() => {}}
        onSaved={() => {}}
        activeName=""
        onActivate={() => {}}
      />
    </QueryClientProvider>,
  )
}

describe('DataSourceEditor volume field', () => {
  it('asks for volume in shares, which the custom source stores without conversion', () => {
    renderEditor({
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

  it('asks for the unit of each mapped ratio field and starts undeclared', () => {
    renderEditor(realtime())
    openRealtime()

    const select = screen.getByLabelText('turnover_rate 單位') as HTMLSelectElement
    expect(select.value).toBe('')
    expect(screen.queryByLabelText('last_price 單位')).toBeNull()
    expect(screen.getByText(/尚未宣告單位/)).toBeInTheDocument()
  })

  it('keeps a declared unit', () => {
    renderEditor(realtime({ turnover_rate: 'percent' }))
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
    renderEditor(realtime({ turnover_rate: 'percent' }))
    openRealtime()

    fireEvent.click(screen.getByText('測試'))

    expect(await screen.findByText(/turnover_rate = 0.0015/)).toBeInTheDocument()
  })
})
