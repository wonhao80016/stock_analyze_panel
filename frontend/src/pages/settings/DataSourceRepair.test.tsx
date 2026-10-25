import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, type CustomSourceConfig } from '@/lib/api'
import { DataSourceEditor } from './DataSourceEditor'
import { LoadErrorList } from './DataSources'

vi.mock('@/lib/api', () => ({
  api: {
    dataSource: vi.fn(),
    saveDataSource: vi.fn(),
    testDataSource: vi.fn(),
  },
}))

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

const BROKEN: CustomSourceConfig = {
  name: 'broken',
  display_name: 'broken',
  auth: { type: 'none' },
  datasets: {
    realtime: { url: 'http://example.test/rt', method: 'GET', response_path: '', field_map: { t: 'turnover_rate' } },
  },
  load_error: ['realtime: turnover_rate 已映射但未宣告單位'],
}

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('repairing a data source that failed to load', () => {
  it('opens the stored config and shows why it did not load', async () => {
    vi.mocked(api.dataSource).mockResolvedValue(BROKEN)
    wrap(<DataSourceEditor existingName="broken" onCancel={() => {}} onSaved={() => {}} activeName="" onActivate={() => {}} />)

    expect(await screen.findByText(/turnover_rate 已映射但未宣告單位/)).toBeInTheDocument()
  })

  it('keeps the save error from the backend on screen', async () => {
    vi.mocked(api.saveDataSource).mockRejectedValue(new Error('資料來源設定無法載入, 未儲存: realtime: amplitude 未宣告'))
    wrap(<DataSourceEditor onCancel={() => {}} onSaved={() => {}} activeName="" onActivate={() => {}} />)

    fireEvent.change(screen.getByPlaceholderText('my_tushare'), { target: { value: 'broken' } })
    fireEvent.click(screen.getByText(/儲存/))

    expect(await screen.findByText(/amplitude 未宣告/)).toBeInTheDocument()
  })

  it('lets a failed source in the list be opened for editing', () => {
    const onEdit = vi.fn()
    render(<LoadErrorList errors={[{ path: '/d/broken.yaml', name: 'broken', errors: ['x'] }]} onEdit={onEdit} />)

    fireEvent.click(screen.getByRole('button', { name: /編輯/ }))

    expect(onEdit).toHaveBeenCalledWith('broken')
  })
})
