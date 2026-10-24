import { describe, expectTypeOf, it } from 'vitest'
import type { api, TaiwanBootstrapJobState } from './api'

type Resolved<F extends (...args: any[]) => Promise<unknown>> = Awaited<ReturnType<F>>

describe('response types follow the backend payloads', () => {
  it('minute kline source includes the partial local copy (backend/app/api/kline.py get_minute)', () => {
    expectTypeOf<'local_partial'>().toMatchTypeOf<NonNullable<Resolved<typeof api.klineMinute>['source']>>()
  })

  it('LINE and Telegram settings return the channel state without ok (backend/app/api/settings.py)', () => {
    type Line = Resolved<typeof api.updateLineMessaging>
    type Telegram = Resolved<typeof api.updateTelegramBot>
    expectTypeOf<Line>().toEqualTypeOf<{
      line_target_id: string
      line_channel_access_token_masked: string
      line_configured: boolean
    }>()
    expectTypeOf<Telegram>().toEqualTypeOf<{
      telegram_chat_id: string
      telegram_bot_token_masked: string
      telegram_configured: boolean
    }>()
  })

  it('strategy runs return the engine StrategyResult with strategy_id (backend/app/strategy/engine.py)', () => {
    type Preset = Resolved<typeof api.screenerRunPreset>
    type Run = Resolved<typeof api.strategyRun>
    expectTypeOf<Preset>().toHaveProperty('strategy_id').toEqualTypeOf<string>()
    expectTypeOf<Preset>().toHaveProperty('scores')
    expectTypeOf<Run>().toEqualTypeOf<Preset>()
    expectTypeOf<Preset>().not.toHaveProperty('strategy')
  })

  it('intraday refresh returns the quote status or an unavailable error (backend/app/api/intraday.py)', () => {
    type Refresh = Resolved<typeof api.intradayRefresh>
    expectTypeOf<Refresh>().toEqualTypeOf<Resolved<typeof api.quoteStatus> | { error: string }>()
  })

  it('bootstrap job state matches BootstrapJobState (backend/app/taiwan/bootstrap.py)', () => {
    expectTypeOf<TaiwanBootstrapJobState>().toHaveProperty('bytes_downloaded').toEqualTypeOf<number>()
    expectTypeOf<TaiwanBootstrapJobState>().not.toHaveProperty('downloaded_bytes')
    expectTypeOf<TaiwanBootstrapJobState>().toHaveProperty('updated_at').toEqualTypeOf<string>()
    expectTypeOf<TaiwanBootstrapJobState['status']>().toEqualTypeOf<
      'pending' | 'downloading' | 'verifying' | 'extracting' | 'importing' | 'syncing' | 'success' | 'failed'
    >()
  })
})
