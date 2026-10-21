import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
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
