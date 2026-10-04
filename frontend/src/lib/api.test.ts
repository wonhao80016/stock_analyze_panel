import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type StrategyBacktestRunPayload } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('strategyBacktestRun execution contract', () => {
  it('sends optional Taiwan execution fields unchanged', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: 'run' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const payload: StrategyBacktestRunPayload = {
      strategy_id: 'test',
      commission_discount: 0.5,
      minimum_commission: 20,
      minimum_holding_sessions: 0,
      lot_mode: 'odd_lot',
      day_trade_eligible_symbols: ['2330.TWSE'],
    }

    await api.strategyBacktestRun(payload)

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/backtest/strategy/run')
    expect(JSON.parse(init.body)).toMatchObject(payload)
  })
})
