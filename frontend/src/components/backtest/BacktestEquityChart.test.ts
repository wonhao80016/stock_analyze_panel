import { describe, expect, it } from 'vitest'
import { buildBacktestEquityOption } from './BacktestEquityChart'

describe('BacktestEquityChart', () => {
  it('將淨值與回撤對齊到各自日期', () => {
    const option = buildBacktestEquityOption(
      [{ date: '2025-01-02', value: 1_000_000 }, { date: '2025-01-03', value: 1_050_000 }],
      [{ date: '2025-01-02', value: 0 }, { date: '2025-01-03', value: -0.02 }],
      {
        text: '#aaa',
        textStrong: '#fff',
        grid: '#333',
        border: '#444',
        crosshair: '#555',
        crosshairLabelBg: '#111',
        tooltipBg: '#111',
        tooltipBorder: '#444',
        tooltipText: '#fff',
        infoBarBg: '#111',
        zoomFill: '#222',
        fillSubtle: '#222',
      },
    )

    expect(option.xAxis).toMatchObject({ data: ['2025-01-02', '2025-01-03'] })
    expect(option.series).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: '策略淨值', data: [100, 105] }),
      expect.objectContaining({ name: '回撤', data: [0, -2] }),
    ]))
    expect(option.series).toHaveLength(2)
  })
})
