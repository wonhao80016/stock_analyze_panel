import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { WatchlistGroupBar, WatchlistGroupPicker } from './WatchlistGroups'
import { api, type WatchlistGroup } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: {
    preferences: vi.fn().mockResolvedValue({}),
    monitorRulesList: vi.fn(),
    updateWatchlistGroupsInNav: vi.fn(),
  },
}))

const GROUPS: WatchlistGroup[] = [
  { id: 'g1', name: '核心', color: 'sky' },
  { id: 'g2', name: '觀察', color: 'rose' },
]

function rule(id: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    id, name, enabled: true, type: 'price', scope: 'watchlist_group', symbols: [], group_id: 'g1',
    direction: 'up', conditions: [], logic: 'and', ...overrides,
  }
}

function wrap(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
  return queryClient
}

function renderBar(overrides: Partial<React.ComponentProps<typeof WatchlistGroupBar>> = {}) {
  return wrap(
    <WatchlistGroupBar
      groups={GROUPS}
      counts={{ ungrouped: 0, g1: 2, g2: 1 }}
      selected="all"
      total={3}
      onSelect={vi.fn()}
      onCreate={vi.fn().mockResolvedValue(undefined)}
      onRename={vi.fn().mockResolvedValue(undefined)}
      onDelete={vi.fn().mockResolvedValue(undefined)}
      {...overrides}
    />,
  )
}

function dragTab(from: HTMLElement, to: HTMLElement) {
  fireEvent.dragStart(from, { dataTransfer: { setData: vi.fn(), effectAllowed: '' } })
  fireEvent.dragOver(to, { dataTransfer: { dropEffect: '' } })
  fireEvent.drop(to, { dataTransfer: {} })
}

beforeEach(() => {
  vi.mocked(api.monitorRulesList).mockResolvedValue({ rules: [] } as any)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('B268 deleting a group names the monitor rules bound to it', () => {
  it('lists the rules that will stop firing', async () => {
    vi.mocked(api.monitorRulesList).mockResolvedValue({
      rules: [
        rule('r1', '核心突破'),
        rule('r2', '觀察跌破', { group_id: 'g2' }),
        rule('r3', '全市場', { scope: 'all', group_id: null }),
      ],
    } as any)
    renderBar()
    fireEvent.click(screen.getByRole('button', { name: '管理自選分組' }))
    fireEvent.click(screen.getAllByTitle('刪除分組')[0])
    const notice = await screen.findByText(/核心突破/)
    expect(notice).toHaveTextContent('監控規則')
    expect(notice).toHaveTextContent('停止觸發')
    expect(screen.queryByText(/觀察跌破/)).not.toBeInTheDocument()
    expect(screen.queryByText(/全市場/)).not.toBeInTheDocument()
  })

  it('refreshes the monitor rules after the delete', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined)
    const qc = renderBar({ onDelete })
    const invalidate = vi.spyOn(qc, 'invalidateQueries')
    fireEvent.click(screen.getByRole('button', { name: '管理自選分組' }))
    fireEvent.click(screen.getAllByTitle('刪除分組')[0])
    const confirm = screen.getByRole('button', { name: '確認' })
    await waitFor(() => expect(confirm).not.toBeDisabled())
    fireEvent.click(confirm)
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('g1'))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: QK.monitorRules }))
  })
})

describe('B269 group edits cannot overlap', () => {
  it('ignores a second membership click while the first is in flight', async () => {
    const onToggleMember = vi.fn().mockReturnValue(new Promise<void>(() => {}))
    wrap(<WatchlistGroupPicker groups={GROUPS} groupIds={[]} symbol="2330.TWSE" onToggleMember={onToggleMember} />)
    fireEvent.click(screen.getByRole('button', { name: '2330.TWSE 的分組' }))
    const item = screen.getByRole('menuitemcheckbox', { name: /核心/ })
    fireEvent.click(item)
    fireEvent.click(item)
    expect(onToggleMember).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('menuitemcheckbox', { name: /觀察/ })).toBeDisabled()
  })

  it('accepts the next membership click once the first has settled', async () => {
    const onToggleMember = vi.fn().mockResolvedValue(undefined)
    wrap(<WatchlistGroupPicker groups={GROUPS} groupIds={[]} symbol="2330.TWSE" onToggleMember={onToggleMember} />)
    fireEvent.click(screen.getByRole('button', { name: '2330.TWSE 的分組' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /核心/ }))
    await waitFor(() => expect(screen.getByRole('menuitemcheckbox', { name: /觀察/ })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /觀察/ }))
    expect(onToggleMember).toHaveBeenCalledTimes(2)
  })

  it('locks tab dragging until the previous reorder is saved', async () => {
    const onReorder = vi.fn().mockReturnValue(new Promise<void>(() => {}))
    renderBar({ onReorder })
    const core = screen.getByRole('tab', { name: /核心/ })
    const watch = screen.getByRole('tab', { name: /觀察/ })
    // jsdom drag events carry no clientX, so dropping on a tab inserts before it.
    dragTab(watch, core)
    expect(onReorder).toHaveBeenCalledWith(['g2', 'g1'])
    await waitFor(() => expect(screen.getByRole('tab', { name: /核心/ })).toHaveAttribute('draggable', 'false'))
    dragTab(screen.getByRole('tab', { name: /觀察/ }), screen.getByRole('tab', { name: /核心/ }))
    expect(onReorder).toHaveBeenCalledTimes(1)
  })
})
