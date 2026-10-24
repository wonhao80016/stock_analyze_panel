import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Settings } from './Settings'

vi.mock('./settings/DataSources', () => ({ SettingsDataSourcesPanel: () => <div>data-sources-panel</div> }))
vi.mock('./settings/AI', () => ({ SettingsAIPanel: () => <div>ai-panel</div> }))
vi.mock('./settings/Monitoring', () => ({ SettingsMonitoringPanel: () => <div>monitoring-panel</div> }))
vi.mock('./settings/MenuSettings', () => ({ SettingsMenuSettingsPanel: () => <div>menus-panel</div> }))
vi.mock('./settings/System', () => ({ SettingsSystemPanel: () => <div>system-panel</div> }))
vi.mock('./settings/CustomSignals', () => ({ SettingsCustomSignalsPanel: () => <div>signals-panel</div> }))
vi.mock('./settings/Account', () => ({ SettingsAccountPanel: () => <div>account-panel</div> }))

describe('settings tabs', () => {
  it('has no timeout tab and sends ?tab=timeout to the first tab', () => {
    render(
      <MemoryRouter initialEntries={['/settings?tab=timeout']}>
        <Settings />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('button', { name: '逾時設定' })).toBeNull()
    expect(screen.getByText('data-sources-panel')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '資料來源' })).toHaveClass('text-accent')
  })
})
