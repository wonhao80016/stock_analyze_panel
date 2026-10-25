// 後端 API 客戶端 — 全項目統一入口
//
// Dev: Vite 按啟動腳本解析出的 BACKEND_HOST/BACKEND_PORT 代理 /api
// Prod:同源(FastAPI 託管前端 dist)

import { toast } from '@/components/Toast'

const BASE = ''

type RequestOptions = RequestInit & {
  /** 為 true 時不彈錯誤 toast（由調用方自行彙總提示，如多圖串行隊列） */
  quiet?: boolean
  /** false for a read the user triggered: its every failure gets a toast even if the same read just failed. */
  dedupe?: boolean
}

/** A non-2xx response; `code` is the backend's machine-readable error code when it sent one. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | undefined,
    readonly path: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** True when the panel needs a login: a session 401 or an uninitialised password (403 NOT_INITIALIZED). */
export function isLoginRequiredError(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false
  if (err.status === 403) return err.code === 'NOT_INITIALIZED'
  // The auth endpoints answer 401 for a wrong password, which is not an expired session.
  return err.status === 401 && !err.path.startsWith('/api/auth/')
}

// Retries and polling repeat the same read; its toast is shown again only after it succeeded
// or stayed quiet for this long, while every failed write still raises its own toast.
const REPEATED_READ_FAILURE_QUIET_MS = 60_000
const lastReadFailures = new Map<string, { msg: string; at: number }>()

function isRepeatedReadFailure(path: string, msg: string): boolean {
  const now = Date.now()
  const previous = lastReadFailures.get(path)
  lastReadFailures.set(path, { msg, at: now })
  return previous != null && previous.msg === msg && now - previous.at < REPEATED_READ_FAILURE_QUIET_MS
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { quiet, dedupe = true, ...fetchInit } = init ?? {}
  const isRead = (fetchInit.method ?? 'GET').toUpperCase() === 'GET'
  const isFormData = fetchInit.body instanceof FormData
  const headers: Record<string, string> = {}
  if (!isFormData) headers['Content-Type'] = 'application/json'
  // 合併調用方傳入的 headers (此前會被整體覆蓋丟棄)
  Object.assign(headers, fetchInit.headers as Record<string, string> | undefined)
  const res = await fetch(`${BASE}${path}`, { ...fetchInit, headers })
  if (!res.ok) {
    let detail = ''
    let code: string | undefined
    try {
      const j = JSON.parse(await res.text())
      const raw = j.detail ?? j.message ?? ''
      if (Array.isArray(raw)) {
        // FastAPI 422 校驗錯誤: [{type, loc, msg, input}, ...] → 取 msg 拼接
        detail = raw.map((e: any) => e?.msg || String(e)).join('; ')
      } else if (typeof raw === 'string') {
        detail = raw
      } else if (raw && typeof raw === 'object') {
        detail = JSON.stringify(raw)
      }
      if (typeof j.code === 'string') code = j.code
      else if (raw && typeof raw === 'object' && typeof raw.code === 'string') code = raw.code
    } catch { /* ignore */ }
    const msg = detail || `${res.status} ${res.statusText}`
    // A silenced or undeduped failure must not mute the next deduped read of the same path.
    const repeated = isRead && !quiet && dedupe && isRepeatedReadFailure(path, msg)
    // 401 (未登錄/會話過期) 不彈 toast — 由全局認證攔截器統一跳登錄頁, 避免刷屏
    if (res.status !== 401 && !quiet && !repeated) toast(msg, 'error')
    throw new ApiError(msg, res.status, code, path)
  }
  if (isRead) lastReadFailures.delete(path)
  return res.json() as Promise<T>
}

// ===== Capabilities =====
export interface CapabilityLimits {
  rpm: number | null
  batch: number | null
  subscribe: number | null
}

export interface CapabilitiesResponse {
  label: string
  capabilities: Record<string, CapabilityLimits>
}

// ===== 個股分析 =====
export type LevelType = 'sr' | 'pivot' | 'extreme' | 'boll' | 'keltner_s' | 'keltner_m' | 'keltner_l' | 'atr_stop' | 'gap' | 'fib' | 'round'

export interface PriceLevel {
  value: number
  label: string
  type: LevelType
  side: 'resistance' | 'support' | 'neutral'
  strength?: 'strong' | 'medium' | 'weak'
  /** 檔位(僅 pivot 有):0=P, 1=R1/S1, 2=R2/S2, 3=R3/S3。前端按"顯示到第幾檔"過濾。 */
  rank?: number
}

/** 帶狀曲線指標(布林帶/Keltner/ATR)的每日時間序列,與 dates 對齊。 */
export interface LevelSeries {
  boll?: { upper: (number | null)[]; lower: (number | null)[]; mid?: (number | null)[] }
  keltner_s?: { upper: (number | null)[]; lower: (number | null)[] }
  keltner_m?: { upper: (number | null)[]; lower: (number | null)[] }
  keltner_l?: { upper: (number | null)[]; lower: (number | null)[] }
  atr?: { stop_loss: (number | null)[]; take_profit: (number | null)[] }
}

export interface StockLevels {
  levels: Record<LevelType, PriceLevel[]>
  close: number | null
  summary: string
  symbol: string
  /** dates 與 series 對齊;前端按自身 rows 的日期映射,缺失填 null */
  dates?: string[]
  series?: LevelSeries
}

// ===== Kline =====
export interface MinuteKlineRow {
  datetime: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  /** Taiwan: volume traded across a recording gap, not part of this bar's volume. */
  unattributed_volume?: number | null
}

export interface MinuteKlineSession {
  date: string
  prev_close: number | null
  rows: MinuteKlineRow[]
}

export interface PriceLimitInfo {
  /** Decimal (0.1 = 10%); null when the instrument has no limit or its rule is unknown. */
  rate: number | null
  limit_up: number | null
  limit_down: number | null
  source: 'rule' | 'model' | 'official' | 'instrument' | 'no_limit' | 'unknown'
}

export interface KlineRow {
  symbol?: string
  date: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
  change_pct?: number
  ma5?: number | null
  ma20?: number | null
  ma60?: number | null
  macd_dif?: number | null
  macd_dea?: number | null
  macd_hist?: number | null
  rsi_14?: number | null
  vol_ratio_5d?: number | null
  /** Taiwan live candle: multiplier scaling its partial-session volume to a full session; above 1 the volume ratio is an estimate. */
  volume_time_factor?: number | null
  /** Taiwan: volume and turnover count intraday board-lot trades only and amount is null, until the official daily row is stored. */
  board_lot_volume?: boolean
  turnover_rate?: number | null
  /** Taiwan watchlist: not_applicable for ETF/TDR; board_lot_estimate while turnover uses MIS board-lot volume. */
  turnover_rate_status?: 'not_applicable' | 'board_lot_estimate' | null
  [key: string]: any
}

export const BOARD_LOT_VOLUME_NOTE = '盤中為整股量（不含零股、鉅額、盤後定價）'

// ===== Watchlist =====
export interface WatchlistEntry {
  symbol: string
  added_at: string
  note?: string
  name?: string | null
  /** 所屬分組 id 列表 (同一標的可屬於多個分組; 空數組=未分組) */
  group_ids?: string[]
}

export type WatchlistGroupColor =
  | 'sky'
  | 'blue'
  | 'indigo'
  | 'violet'
  | 'fuchsia'
  | 'rose'
  | 'orange'
  | 'amber'
  | 'lime'
  | 'emerald'
  | 'teal'
  | 'cyan'

export interface WatchlistGroup {
  id: string
  name: string
  color: WatchlistGroupColor
}

export interface WatchlistImportCandidate {
  code: string
  symbol: string | null
  name: string | null
  matched: boolean
  already_in_watchlist: boolean
}

export interface WatchlistImportResult {
  provider: string
  codes: string[]
  candidates: WatchlistImportCandidate[]
  matched_count: number
  unmatched_count: number
}

// ===== Screener =====
export interface StrategyLoadError {
  file: string
  error: string
}

/** QuoteService.status(); /api/intraday/refresh returns the same payload after one fetch. */
export interface QuoteStatus {
  enabled: boolean
  running: boolean
  paused?: boolean
  mode?: 'none' | 'watchlist' | 'full_market'
  realtime_allowed?: boolean
  interval_s: number
  symbol_count: number
  watchlist_symbol_count?: number
  index_symbol_count?: number
  etf_symbol_count?: number
  quote_age_ms: number | null
  is_trading_hours: boolean
  is_polling_window?: boolean
  market_phase?: string
  final_sync_done?: boolean
  final_sync_failed?: string | null
  last_fetch_ms: number | null
}

export interface ScreenerResultSummary {
  total: number
  as_of: string
}

export type PickGrade = 'buy' | 'watch'
export type PickGradeReason = 'outside_top_n' | 'score_unavailable' | 'market_below_ma20'
export type MarketFilter = 'applied' | 'passed' | 'unavailable'

/** TAIEX close against its 20-day average on the post-close basis date. */
export interface MarketState {
  as_of: string
  index_symbol: string
  close: number | null
  ma: number | null
  window: number
  below_ma: boolean | null
  status: 'ok' | 'unavailable'
  reason: 'index_missing' | 'insufficient_history' | 'index_gap' | null
}

export interface PickGrading {
  top_n: number
  market_filter: MarketFilter
}

export interface ScreenerCachedRow {
  symbol: string
  name?: string
  /** null when the strategy could not score the pick; such picks rank last. */
  score?: number | null
  grade?: PickGrade
  grade_reason?: PickGradeReason | null
  /** The price sits at the limit-up price; shown beside the grade, which it never changes. */
  limit_up_locked?: boolean
  /** Scoring field ids without a value, set only when score is null. */
  missing_scoring_fields?: string[]
  /** On a composite pick no child could score: each child and the scoring fields it lacked. */
  missing_scoring_children?: { strategy_id: string; name: string; fields: string[] }[]
  [field: string]: any
}

export interface ScreenerCached {
  as_of: string | null
  results: Record<string, { total: number; as_of: string; rows: ScreenerCachedRow[] }>
  today_ever_matched: Record<string, string[]> | null
  today_ever_rows: Record<string, Record<string, any>> | null
  updated_at: number | null
  market_state?: MarketState | null
  grading?: PickGrading
  /** Strategies whose latest cached run raised; absent in caches written before failures were recorded. */
  failures?: Record<string, { error: string }>
}

export interface ScreenerCachedSummary {
  as_of: string | null
  results: Record<string, ScreenerResultSummary>
  today_ever_counts: Record<string, number>
  updated_at: number | null
  /** When the post-close cache file was written; unlike updated_at, realtime overlays never move it. */
  cache_updated_at?: number | null
}

export type PickHorizon = '1' | '5' | '10' | '20'

/** Returns in these payloads are decimals: 0.0123 means 1.23%. */
export interface PickPerformanceMethod {
  entry: string
  horizons: number[]
  price_adjustment: string
  costs_included: boolean
  slippage_included: boolean
  exit_fill_simulated: boolean
  return_unit: 'decimal'
}

export interface PickAdjustmentState {
  status: string
  reason: string | null
  covered_through: string | null
}

export interface PickHorizonStats {
  sample_count: number
  sample_days: number
  mean_return: number | null
  median_return: number | null
  positive_ratio: number | null
  adjustment_not_covered: number
  data_incomplete: number
  prices_incomplete: number
  stale_exit_count: number
  pending_count: number
}

export interface PickStrategyStats {
  strategy_id: string
  signal_count: number
  signal_days: number
  reconstructed_count?: number
  scoring_unavailable_count?: number
  input_unavailable_days?: number
  filled_count: number
  not_filled_limit_up: number
  not_filled_no_trade: number
  limit_check_unknown: number
  pending_entry_count: number
  tracking_count: number
  completed_count: number
  entry_prices_incomplete_count: number
  entry_data_incomplete_count: number
  absent_count: number
  history_degraded_count: number
  excluded: { manifest_missing: number; recorded_late: number; universe_incomplete: number }
  horizons: Record<PickHorizon, PickHorizonStats>
}

export interface PickPerformanceSummary {
  method: PickPerformanceMethod
  adjustment: PickAdjustmentState
  first_signal_date: string | null
  last_signal_date: string | null
  prices_through: string | null
  strategies: PickStrategyStats[]
  calculation_issues?: PickCalculationIssue[]
}

export interface PickCalculationIssue {
  strategy_id: string
  date: string
  status: 'input_unavailable' | 'scoring_unavailable'
  missing_fields: string[]
}

export interface PickHorizonReturn {
  value: number | null
  reason: string | null
  stale_exit: boolean
  stale_days: number
}

export interface PickRecord {
  signal_date: string
  provenance?: 'recorded' | 'reconstructed' | null
  calculation_status?: 'ok' | 'scoring_unavailable' | 'input_unavailable' | null
  missing_fields?: string[]
  symbol: string
  name: string | null
  rank: number | null
  entry_date: string | null
  entry_price_raw: number | null
  status: string
  limit_check: string
  absent_since: string | null
  excluded_reason: string | null
  recorded_late: boolean | null
  manifest_missing: boolean | null
  universe_complete: boolean | null
  history_degraded: boolean | null
  returns: Record<PickHorizon, PickHorizonReturn>
}

export interface PickPerformanceDetail {
  strategy_id: string
  method: PickPerformanceMethod
  adjustment: PickAdjustmentState
  prices_through: string | null
  total: number
  signal_days: number
  rows: PickRecord[]
  calculation_issues?: PickCalculationIssue[]
}

export interface PickBackfillJob {
  job_id: string
  status: 'running' | 'completed' | 'failed'
  requested_days: number
  dates: string[]
  completed_days: number
  written_runs: number
  existing_runs: number
  zero_signal_runs: number
  scoring_unavailable_runs?: number
  failures: { date: string; strategy_id: string; reason: string; missing_fields?: string[] }[]
  error: string | null
}

export interface MarketSnapshotRow {
  symbol: string
  name?: string | null
  close?: number | null
  change_pct?: number | null
  amount?: number | null
  volume?: number | null
  turnover_rate?: number | null
  vol_ratio_5d?: number | null
  total_shares?: number | null
  float_shares?: number | null
  market_cap?: number | null
  float_market_cap?: number | null
  consecutive_limit_ups?: number | null
  [key: string]: any
}

// ===== Strategy Engine =====
export interface StrategyParamDef {
  id: string
  label: string
  type: 'float' | 'int' | 'select' | 'bool'
  default: number | string | boolean
  min?: number
  max?: number
  step?: number
  options?: string[]
}

export interface CompositeChildInfo {
  id: string
  name: string
  source: string
  weight: number
}

export interface StrategyDetail {
  id: string
  name: string
  description: string
  tags: string[]
  source: 'builtin' | 'custom' | 'ai' | 'composite'
  execution_backend: 'polars_expr' | 'matrix_native' | 'python_history_legacy' | 'composite'
  asset_types: string[]
  timeframes: string[]
  version: string
  basic_filter: Record<string, any>
  params: StrategyParamDef[]
  params_defaults: Record<string, any>
  scoring: Record<string, number>
  scoring_directions: Record<string, ScoringDirection>
  entry_signals: string[]
  exit_signals: string[]
  minute_exit_trigger_supported_signals: string[]
  stop_loss: number | null
  take_profit: number | null
  trailing_stop: number | null
  trailing_take_profit_activate: number | null
  trailing_take_profit_drawdown: number | null
  max_hold_days: number | null
  display_limit?: number
  order_by: string
  descending: boolean
  limit: number
  // 疊加策略(composite)專屬: 子策略列表與合併模式。非 composite 時為 null。
  composite_children?: CompositeChildInfo[] | null
}

export type ScoringDirection = 'high' | 'low'

// ===== Custom Signals (自定義訊號) =====
export interface CustomSignalCondition {
  left: string     // 字段名
  op: string       // > >= < <= == !=
  right: string    // "field:xxx" 或數字字符串
  leftDays?: number   // 左字段取幾日前 (0=當日, 默認)
  rightDays?: number  // 右字段取幾日前 (僅 right 為字段時有意義)
}

export interface CustomSignal {
  id: string
  name: string
  kind: 'entry' | 'exit' | 'both'
  conditions: CustomSignalCondition[]
  enabled: boolean
}

export type CustomSignalFieldUnit = 'TWD' | 'shares' | 'percent' | 'decimal' | 'ratio' | 'index' | 'count'

export interface CustomSignalField {
  key: string
  label: string
  unit?: CustomSignalFieldUnit
}

export interface CustomSignalFieldGroup {
  key: string
  label: string
  fields: CustomSignalField[]
}

export interface CustomSignalOptions {
  fields: CustomSignalField[]
  groups?: CustomSignalFieldGroup[]
  maxDays?: number
  operators: string[]
  kinds: { key: string; label: string }[]
  units?: Partial<Record<CustomSignalFieldUnit, string>>
}

export interface CustomSignalReferences {
  monitor_rules: { id: string; name: string }[]
  strategies: { id: string; name: string }[]
}

export interface CustomSignalAIGenerateResult {
  name: string
  conditions: CustomSignalCondition[]
}

// ===== Monitor (監控規則 + 觸發記錄) =====
export interface MonitorCondition {
  field: string
  op: string              // truth | > >= < <= == !=
  value?: number | null   // op 非 truth 時必填
  /** 遲滯重新啟用門檻: 命中後需等 field 反向穿越此值才重新允許觸發; 留空 = 純邊緣觸發 */
  rearm_value?: number | null
}

export type StrategyNotifyEvent = 'buy_signal' | 'sell_signal' | 'pool_entry' | 'pool_exit'

export type SectorKind = 'index' | 'concept' | 'industry'

export interface SectorMonitorTarget {
  key: string
  kind: SectorKind
  name: string
  symbol?: string
  source_id?: string
  field?: string
  source_field?: string
  value?: string
  level?: number | null
  available: boolean
  member_count: number
}

export interface MonitorRule {
  id: string
  name: string
  enabled: boolean
  type: 'strategy' | 'signal' | 'price' | 'market' | 'sector'
  asset_type?: 'stock' | 'etf' | 'index'
  scope: 'symbols' | 'all' | 'sector' | 'watchlist_group'
  symbols: string[]
  /** scope=watchlist_group 時綁定的自選分組 id (成員動態解析, 增刪自選自動生效) */
  group_id?: string | null
  sector?: string | null
  sector_kind?: SectorKind | null
  sector_targets?: SectorMonitorTarget[]
  sector_trigger?: 'change_pct' | 'momentum'
  threshold_pct?: number
  window_minutes?: 1 | 3 | 5 | 10 | 15
  strategy_id?: string | null
  direction: 'entry' | 'exit' | 'both' | 'up' | 'down'
  notify_events?: StrategyNotifyEvent[]
  score_min?: number | null
  score_max?: number | null
  conditions: MonitorCondition[]
  logic: 'and' | 'or'
  /** edge=未命中轉命中才觸發(建議) | level=命中期間每 cooldown 重播 */
  trigger_mode?: 'edge' | 'level'
  cooldown_seconds: number
  severity: 'info' | 'warn' | 'critical'
  message: string
  webhook_url?: string
  webhook_enabled?: boolean  // 兼容老規則, 已由 webhook_channels 取代
  webhook_channels?: string[]  // External push channels on hit; backend whitelist is 'line' | 'telegram'
  created_at?: string
  runtime_warning?: string
}

export interface MonitorRuleOptions {
  threshold_fields: { key: string; label: string }[]
  builtin_signals: { key: string; label: string }[]
  custom_signals: { key: string; label: string }[]
  operators: string[]
  types: { key: string; label: string }[]
  scopes: { key: string; label: string }[]
  logics: { key: string; label: string }[]
  severities: { key: string; label: string }[]
  directions: { key: string; label: string }[]
  intraday_signal_support: {
    available: boolean
    source: string | null
    max_symbols: number
    reason: string
  }
  sector_targets: Record<SectorKind, SectorMonitorTarget[]>
}

export interface AlertEvent {
  /** Unique per record; several alerts from one evaluation round share the same ts. */
  id?: string
  ts: number
  rule_id?: string
  rule_name?: string
  source: string
  type: string
  symbol?: string
  name?: string | null
  message: string
  price?: number | null
  change_pct?: number | null
  signals?: string[]
  severity?: string
  strategy_id?: string
  conditions?: MonitorCondition[]
  logic?: 'and' | 'or'
  sector_kind?: SectorKind
  sector_key?: string
  sector_name?: string
  sector_source_field?: string
  sector_value?: string
  sector_level?: number | null
  window_change_pct?: number | null
  coverage_ratio?: number
  valid_count?: number
  total_count?: number
  up_count?: number
  down_count?: number
  leader?: { symbol?: string; name?: string; change_pct?: number } | null
  /** ext 富化字段 (行業/概念等), 鍵為 "{configId}__{fieldName}" */
  [key: string]: unknown
}

// ===== Taiwan Realtime & Monitor Types (Phase 5C) =====
export type TaiwanMarketStatus =
  | 'open'
  | 'pre_open'
  | 'closing_auction'
  | 'post_close'
  | 'closed'
  | 'non_trading_day'
  | 'scheduled_open_unverified'

export interface TaiwanSourceMeta {
  source: string
  source_url: string
  fetched_at: string
  trade_date: string
  status: string
  is_realtime: boolean
  is_stale: boolean
  source_type: string
  freshness_class: string
  is_best_effort: boolean
  fallback_reason?: string | null
}

export interface TaiwanRealtimeQuote {
  symbol: string
  name: string
  exchange: 'TWSE' | 'TPEx' | string
  last_price: number | null
  prev_close: number | null
  open: number | null
  high: number | null
  low: number | null
  change: number | null
  change_pct: number | null
  volume: number | null               // In SHARES (股)
  amount: number | null               // In TWD
  quote_time: string | null           // ISO string
  trade_date: string                  // YYYY-MM-DD
  market_status: TaiwanMarketStatus | string
  source_meta: TaiwanSourceMeta
  bid_price?: number | null
  ask_price?: number | null
  bid_volume?: number | null          // In SHARES (股)
  ask_volume?: number | null          // In SHARES (股)
  bids?: [number, number][]           // 5-level order book: [price, shares]
  asks?: [number, number][]           // 5-level order book: [price, shares]
  limit_up?: number | null
  limit_down?: number | null
  price_limit_pct?: number | null
  is_no_limit?: boolean
}

export interface TaiwanSearchResult {
  symbol: string
  code: string
  name: string
  exchange: string
  instrument_type: string
  is_supported: boolean
  price_limit_pct?: number | null
  is_no_limit?: boolean
}

// ===== Phase 6A: Taiwan Stock Detail Workspace =====
export interface TaiwanSectionMeta {
  source: string
  trade_date?: string | null
  fetched_at?: string | null
  status: 'available' | 'unavailable' | 'stale' | 'fallback' | string
  is_stale: boolean
  fallback_reason?: string | null
}

export interface TaiwanStockIdentity {
  symbol: string
  code: string
  name: string
  exchange: string
  instrument_type: string
  is_supported: boolean
  listing_status?: string | null
  listing_date?: string | null
  industry?: string | null
  cfi_code?: string | null
  etf_category?: string | null
  security_class?: 'tdr' | null
}

export interface TaiwanStockPriceLimit {
  limit_up?: number | null
  limit_down?: number | null
  price_limit_pct?: number | null
  is_no_limit: boolean
  rule_type: string
}

export interface TaiwanStockRealtimeDetail {
  last_price?: number | null
  prev_close?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  change?: number | null
  change_pct?: number | null
  volume?: number | null
  amount?: number | null
  quote_time?: string | null
  market_status: string
  bid_price?: number | null
  ask_price?: number | null
  bids: [number, number][]
  asks: [number, number][]
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanDailyItem {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount?: number | null
  prev_close?: number | null
  raw_close?: number | null
  /** Published turnover in percent (1.5 means 1.5%); null for ETFs and sessions without one. */
  turnover_rate?: number | null
}

export interface TaiwanHistoricalDailyDetail {
  status: string
  rows: TaiwanDailyItem[]
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanInstitutionalDetail {
  status: string
  foreign_net?: number | null
  investment_trust_net?: number | null
  dealer_net?: number | null
  total_net?: number | null
  foreign_net_5d?: number | null
  investment_trust_net_5d?: number | null
  dealer_net_5d?: number | null
  trend?: Record<string, any>[]
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanMarginDetail {
  status: string
  margin_balance?: number | null
  margin_change?: number | null
  short_balance?: number | null
  short_change?: number | null
  short_margin_ratio?: number | null
  trend?: Record<string, any>[]
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanInstitutionalHistoryRow {
  date: string
  foreign_net: number | null
  investment_trust_net: number | null
  dealer_net: number | null
  total_net: number | null
}

export interface TaiwanInstitutionalHistoryResponse {
  symbol: string
  rows: TaiwanInstitutionalHistoryRow[]
}

export interface TaiwanMarginHistoryRow {
  date: string
  margin_balance: number | null
  margin_change: number | null
  short_balance: number | null
  short_change: number | null
  short_margin_ratio: number | null
}

export interface TaiwanMarginHistoryResponse {
  symbol: string
  rows: TaiwanMarginHistoryRow[]
}

export interface TaiwanFactorsDetail {
  status: string
  foreign_net_5d?: number | null
  investment_trust_net_5d?: number | null
  dealer_net_5d?: number | null
  margin_balance_change?: number | null
  short_margin_ratio?: number | null
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanMarketContextDetail {
  benchmark_symbol: string
  benchmark_name: string
  close?: number | null
  change?: number | null
  change_pct?: number | null
  meta?: TaiwanSectionMeta | null
}

export interface TaiwanMonitorSummaryDetail {
  rule_count: number
  active_count: number
  rules: Record<string, any>[]
}

export interface TaiwanRecentAlertDetail {
  alert_id: string
  rule_id: string
  rule_name: string
  rule_type: string
  severity: string
  trigger_price: number | null
  trigger_value: number
  message: string
  triggered_at: string
}

export interface TaiwanStockDetailResponse {
  symbol: string
  identity: TaiwanStockIdentity
  price_limit: TaiwanStockPriceLimit
  realtime: TaiwanStockRealtimeDetail
  daily_history: TaiwanHistoricalDailyDetail
  institutional: TaiwanInstitutionalDetail
  margin: TaiwanMarginDetail
  factors: TaiwanFactorsDetail
  market_context: TaiwanMarketContextDetail
  monitor_summary: TaiwanMonitorSummaryDetail
  recent_alerts: TaiwanRecentAlertDetail[]
  overall_data_quality: 'good' | 'partial' | 'stale' | 'degraded' | string
}

export type TaiwanUsageScope =
  | 'current_reference'
  | 'historical_reference'
  | 'pit_historical'
  | 'unsupported'

export interface TaiwanDataSection {
  dataset: string
  status: string
  usage_scope: TaiwanUsageScope
  historically_eligible: boolean
  available_at?: string | null
  source?: string | null
  provider?: string | null
  reason?: string | null
  data?: Record<string, unknown> | null
}

export interface TaiwanCurrentDataResponse {
  symbol: string
  exchange: string
  security_type: 'stock' | 'etf'
  identity: Record<string, unknown>
  sections: Record<string, TaiwanDataSection>
}

// ===== Taiwan Screener Types (Phase 6B) =====
export interface TaiwanScreenerRequest {
  exchange?: 'TWSE' | 'TPEX' | 'ALL'
  instrument?: 'stock' | 'etf' | 'ALL'
  industry?: string | null
  price_min?: number | null
  price_max?: number | null
  change_pct_min?: number | null
  change_pct_max?: number | null
  volume_min?: number | null
  volume_max?: number | null
  amount_min?: number | null
  amount_max?: number | null
  rsi_14_min?: number | null
  rsi_14_max?: number | null
  momentum_5d_min?: number | null
  momentum_5d_max?: number | null
  vol_ratio_5d_min?: number | null
  vol_ratio_5d_max?: number | null
  signals?: string[] | null
  above_ma5?: boolean | null
  above_ma20?: boolean | null
  foreign_net_min?: number | null
  foreign_net_max?: number | null
  investment_trust_net_min?: number | null
  investment_trust_net_max?: number | null
  dealer_net_min?: number | null
  dealer_net_max?: number | null
  margin_balance_change_min?: number | null
  margin_balance_change_max?: number | null
  short_balance_min?: number | null
  short_balance_max?: number | null
  short_margin_ratio_min?: number | null
  short_margin_ratio_max?: number | null
  near_upper_limit?: boolean | null
  near_lower_limit?: boolean | null
  distance_to_upper_limit_max?: number | null
  distance_to_lower_limit_max?: number | null
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

// Run-only fields; keep them off TaiwanScreenerRequest, which the NL translator returns.
export interface TaiwanScreenerRunRequest extends TaiwanScreenerRequest {
  volume_patterns?: string[] | null
}

export interface ScreenerResultItem {
  symbol: string
  name: string
  exchange: string
  instrument_type: string
  industry?: string | null
  signals?: string[]
  volume_patterns?: string[]
  close?: number | null
  change_pct?: number | null
  volume?: number | null
  amount?: number | null
  quote_date?: string | null
  price_limit_pct?: number | null
  is_no_limit: boolean
  limit_up?: number | null
  limit_down?: number | null
  distance_to_upper_limit?: number | null
  distance_to_lower_limit?: number | null
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  rsi_14?: number | null
  momentum_5d?: number | null
  vol_ratio_5d?: number | null
  foreign_net?: number | null
  foreign_net_5d?: number | null
  investment_trust_net?: number | null
  investment_trust_net_5d?: number | null
  dealer_net?: number | null
  institutional_date?: string | null
  institutional_status?: string
  margin_balance?: number | null
  margin_balance_change?: number | null
  short_balance?: number | null
  short_margin_ratio?: number | null
  margin_date?: string | null
  margin_status?: string
}

export interface TaiwanScreenerResponse {
  items: ScreenerResultItem[]
  total: number
  page: number
  page_size: number
  sort_by: string
  sort_order: string
  data_dates: {
    daily_as_of?: string | null
    daily_unlisted_count?: number
    institutional_as_of?: string | null
    margin_as_of?: string | null
  }
  degraded_sections: string[]
}

export interface TaiwanDataStatus {
  daily_as_of: string | null
  institutional_as_of: string | null
  margin_as_of: string | null
  target_latest_trading_date: string
  is_fully_current: boolean
  daily_status: 'current' | 'stale' | 'unavailable'
  institutional_status: 'current' | 'stale' | 'unavailable'
  margin_status: 'current' | 'stale' | 'unavailable'
  daily_days_behind: number
  institutional_days_behind: number
  margin_days_behind: number
  scheduler_enabled: boolean
  scheduled_update_time: string
  scheduled_timezone: string
}

export interface TaiwanScreenerTranslation {
  request: TaiwanScreenerRequest | null
  recognized_conditions: string[]
  unsupported_conditions: string[]
  clarification_needed: boolean
  clarification_message: string | null
}

export interface MarketBreadthStats {
  supported_count: number
  snapshot_row_count: number
  traded_count: number
  advance_count: number
  decline_count: number
  flat_count: number
  uncompared_count: number
  /** Rows without a close or with zero volume; advance/decline/flat count traded rows only. */
  untraded_count?: number
  upper_limit_count: number
  lower_limit_count: number
  turnover: number
}

// Net buy amounts in TWD from the exchanges' official summaries; null when the day has none.
export interface InstitutionalNetAmounts {
  foreign_net_amount: number | null
  investment_trust_net_amount: number | null
  dealer_net_amount: number | null
  total_net_amount: number | null
  has_discrepancy: boolean
}

export interface InstitutionalMarketAggregate extends InstitutionalNetAmounts {
  trade_date: string | null
  twse: InstitutionalNetAmounts | null
  tpex: InstitutionalNetAmounts | null
  status: 'current' | 'stale' | 'unavailable'
}

export interface MarginMarketAggregate {
  trade_date: string | null
  row_count: number
  margin_balance: number | null
  margin_balance_change: number | null
  short_balance: number | null
  short_balance_change: number | null
  aggregate_short_margin_ratio: number | null
  status: 'current' | 'stale' | 'unavailable'
}

export interface IndexSnapshot {
  symbol: string
  name: string
  trade_date: string | null
  close: number | null
  change: number | null
  change_pct: number | null
  status: string
}

export interface TaiwanMarketIntelligenceSnapshot {
  trade_date: string
  generated_at: string
  market_totals: MarketBreadthStats
  by_exchange: {
    twse: MarketBreadthStats
    tpex: MarketBreadthStats
  }
  by_instrument: {
    stock: MarketBreadthStats
    etf: MarketBreadthStats
  }
  institutional: InstitutionalMarketAggregate
  margin: MarginMarketAggregate
  indexes: {
    taiex: IndexSnapshot | null
    tpex_index: IndexSnapshot | null
  }
  data_quality: {
    target_trade_date: string
    previous_trade_date: string | null
    overall_status: 'complete' | 'provisional' | 'partial' | 'unavailable'
    universe_supported_symbols: number
    daily_snapshot_symbols: number
    missing_symbols_count: number
    twse_supported_symbols: number
    twse_snapshot_symbols: number
    tpex_supported_symbols: number
    tpex_snapshot_symbols: number
  }
}

export interface IndustryConstituentMover {
  symbol: string
  name: string
  change_pct: number | null
  close: number | null
  turnover: number
}

/** Industry value of stocks without a domestic industry (TDRs included); an API value, not a label. */
export const UNCLASSIFIED_INDUSTRY = 'UNCLASSIFIED'

export function industryLabel(industry: string): string {
  return industry === UNCLASSIFIED_INDUSTRY ? '未分類' : industry
}

export interface IndustryMetrics {
  industry: string
  supported_symbol_count: number
  snapshot_symbol_count: number
  traded_symbol_count: number
  comparable_symbol_count: number
  turnover: number
  turnover_share: number
  advance_count: number
  decline_count: number
  flat_count: number
  uncompared_count: number
  untraded_count?: number
  advance_ratio: number | null
  decline_ratio: number | null
  average_change_pct: number | null
  median_change_pct: number | null
  foreign_net: number | null
  investment_trust_net: number | null
  dealer_net: number | null
  margin_balance_change: number | null
  short_balance_change: number | null
  /** The industry's own equal-weight 5-session return; relative_strength_5d is this minus the market's. */
  average_return_5d: number | null
  relative_strength_5d: number | null
  relative_strength_20d: number | null
  relative_strength_5d_comparable_count: number
  relative_strength_20d_comparable_count: number
  top_gainers: IndustryConstituentMover[]
  top_losers: IndustryConstituentMover[]
  top_turnover: IndustryConstituentMover[]
}

export interface TaiwanIndustryIntelligenceSnapshot {
  trade_date: string
  generated_at: string
  market_reference: {
    trade_date: string
    total_stock_turnover: number
    market_equal_weight_return_5d: number | null
    market_equal_weight_return_20d: number | null
    comparable_stocks_5d_count: number
    comparable_stocks_20d_count: number
  }
  industries: IndustryMetrics[]
  data_quality: {
    target_trade_date: string
    previous_trade_date: string | null
    base_date_5d: string | null
    base_date_20d: string | null
    supported_stock_count: number
    classified_stock_count: number
    unclassified_stock_count: number
    etfs_excluded_count: number
    industry_count: number
    classification_coverage_pct: number
    daily_status: 'current' | 'stale' | 'unavailable'
    institutional_status: 'current' | 'stale' | 'unavailable'
    margin_status: 'current' | 'stale' | 'unavailable'
    overall_status: 'complete' | 'provisional' | 'partial' | 'unavailable'
  }
}

export interface EvidenceMeta {
  classification: 'KNOWN' | 'MISSING' | 'DERIVED'
  source: string
  formula?: string | null
  as_of?: string | null
}

export interface TaiwanStockResearchContext {
  symbol: string
  generated_at: string
  as_of_date: string
  identity: {
    canonical_symbol: string
    code: string
    name: string
    exchange: string
    instrument_type: string
    industry: string | null
    security_class?: 'tdr' | null
    currency: string
    listing_status: string
    listing_date: string | null
    meta: EvidenceMeta
  }
  market_context: {
    trade_date: string
    market_turnover: number
    advance_count: number
    decline_count: number
    flat_count: number
    upper_limit_count: number
    lower_limit_count: number
    exchange_turnover: number | null
    institutional_market_net_amount: number | null
    margin_market_change: number | null
    status: string
    meta: EvidenceMeta
  }
  industry_context: {
    industry: string | null
    turnover: number | null
    turnover_share: number | null
    advance_ratio: number | null
    average_change_pct: number | null
    median_change_pct: number | null
    foreign_net: number | null
    investment_trust_net: number | null
    relative_strength_5d: number | null
    relative_strength_20d: number | null
    status: string
    meta: EvidenceMeta
  }
  price_context: {
    trade_date: string
    open: number | null
    high: number | null
    low: number | null
    close: number | null
    volume: number | null
    amount: number | null
    previous_close: number | null
    change: number | null
    change_pct: number | null
    return_5d: number | null
    return_20d: number | null
    high_20d: number | null
    low_20d: number | null
    distance_from_20d_high: number | null
    distance_from_20d_low: number | null
    meta: EvidenceMeta
  }
  technical_context: {
    ma5: number | null
    ma20: number | null
    ma60: number | null
    rsi14: number | null
    above_ma5: boolean | null
    above_ma20: boolean | null
    distance_to_ma20: number | null
    vol_ratio_5d: number | null
    meta: EvidenceMeta
  }
  institutional_context: {
    as_of: string | null
    foreign_net_1d: number | null
    foreign_net_5d: number | null
    foreign_net_20d: number | null
    investment_trust_net_1d: number | null
    investment_trust_net_5d: number | null
    investment_trust_net_20d: number | null
    dealer_net_1d: number | null
    dealer_net_5d: number | null
    dealer_net_20d: number | null
    coverage_days_5d: number
    coverage_days_20d: number
    status: string
    meta: EvidenceMeta
  }
  margin_context: {
    as_of: string | null
    margin_balance: number | null
    margin_balance_change_1d: number | null
    margin_balance_change_5d: number | null
    short_balance: number | null
    short_balance_change_1d: number | null
    short_balance_change_5d: number | null
    short_margin_ratio: number | null
    status: string
    meta: EvidenceMeta
  }
  fundamentals_context: {
    status: string
    as_of_period: string | null
    available_at: string | null
    pe: number | null
    pb: number | null
    dividend_yield: number | null
    monthly_revenue_yoy: number | null
    latest_eps: number | null
    meta: EvidenceMeta
  }
  etf_context: {
    status: string
    etf_type: string | null
    underlying_scope: string | null
    leverage_multiplier: number | null
    inverse: boolean | null
    benchmark: string | null
    meta: EvidenceMeta
  }
  market_rules_context: {
    price_limit_pct: number | null
    is_no_limit: boolean
    limit_up: number | null
    limit_down: number | null
    tick_size: number | null
    meta: EvidenceMeta
  }
  realtime_context: {
    status: string
    last_price: number | null
    quote_ts: string | null
    meta: EvidenceMeta
  }
  monitor_context: {
    active_rule_count: number
    recent_alert_count: number
    meta: EvidenceMeta
  }
  data_quality: {
    overall_status: 'complete' | 'provisional' | 'partial' | 'unavailable'
    sections: { section: string; status: string; as_of: string | null }[]
    target_trade_date: string
  }
  evidence_summary: {
    known_fields_count: number
    missing_fields_count: number
    derived_fields_count: number
    missing_sections: string[]
  }
}

export interface DiagnosticSignalEvidence {
  type: string
  subtype?: string | null
  severity: 'low' | 'moderate' | 'high' | 'extreme'
  observed: number
  baseline?: number | null
  ratio?: number | null
  delta?: number | null
  threshold: number
  formula: string
  lookback_sessions: number
  valid_sessions: number
  source: string
  status: string
}

export interface TaiwanAbnormalDiagnosticItem {
  symbol: string
  code: string
  name: string
  exchange: string
  industry: string | null
  close: number | null
  previous_close: number | null
  change: number | null
  change_pct: number | null
  volume: number | null
  amount: number | null
  volume_ratio_5d: number | null
  amount_ratio_5d: number | null
  foreign_net: number | null
  investment_trust_net: number | null
  margin_balance_change: number | null
  short_balance_change: number | null
  short_margin_ratio: number | null
  signal_count: number
  signals: DiagnosticSignalEvidence[]
  market_context: {
    trade_date: string
    advance_ratio: number | null
    market_turnover: number | null
    overall_status: string
  }
  industry_context: {
    industry: string | null
    turnover_share: number | null
    advance_ratio: number | null
    relative_strength_5d: number | null
    relative_strength_20d: number | null
  }
}

export interface TaiwanAbnormalDiagnosticsSnapshot {
  trade_date: string
  generated_at: string
  universe_count: number
  diagnostic_count: number
  items: TaiwanAbnormalDiagnosticItem[]
  data_quality: {
    target_trade_date: string
    universe_supported_count: number
    evaluated_symbol_count: number
    diagnostic_symbol_count: number
    daily_status: 'current' | 'stale' | 'unavailable'
    institutional_status: 'current' | 'stale' | 'unavailable'
    margin_status: 'current' | 'stale' | 'unavailable'
    overall_status: 'complete' | 'provisional' | 'partial' | 'unavailable'
  }
  provenance: string[]
}

export interface ObservationItem {
  text: string
  evidence_refs: string[]
}

export interface TaiwanAIStockResearchReport {
  symbol: string
  code: string
  name: string
  industry: string | null
  instrument_type: string
  evidence_as_of: string
  generated_at: string
  prompt_version: string
  overview: string
  market_interpretation?: string | null
  industry_interpretation?: string | null
  price_technical_interpretation?: string | null
  institutional_interpretation?: string | null
  margin_interpretation?: string | null
  fundamentals_interpretation?: string | null
  abnormal_diagnostics_interpretation?: string | null
  key_observations: ObservationItem[]
  risk_factors: ObservationItem[]
  missing_information: string[]
  disclaimer: string
}

export interface TaiwanAIResearchResponse {
  status: 'success' | 'unavailable' | 'error'
  error_code?: string | null
  error_message?: string | null
  report?: TaiwanAIStockResearchReport | null
  provider?: string | null
  model?: string | null
  prompt_version: string
  evidence_as_of?: string | null
  generated_at: string
  evidence_registry_keys: string[]
}

// ── Phase 7G: Multi-Stock Objective Research Comparison ──────

export interface ComparisonInstrumentResult {
  symbol: string
  context: TaiwanStockResearchContext
  diagnostic_item?: TaiwanAbnormalDiagnosticItem | null
}

export interface TaiwanStockComparisonResponse {
  symbols_requested: string[]
  comparison_date: string
  generated_at: string
  instruments: ComparisonInstrumentResult[]
  unsupported_symbols: string[]
}

export interface ComparisonObservationItem {
  text: string
  evidence_refs: string[]
}

export interface TaiwanComparisonAIStockResearchReport {
  symbols: string[]
  comparison_date: string
  generated_at: string
  prompt_version: string
  provider?: string | null
  model?: string | null
  comparison_overview: string
  price_technical_comparison?: string | null
  institutional_comparison?: string | null
  margin_comparison?: string | null
  fundamentals_comparison?: string | null
  abnormal_diagnostics_comparison?: string | null
  key_observations: ComparisonObservationItem[]
  risk_factors: ComparisonObservationItem[]
  missing_information: string[]
  disclaimer: string
}

export interface TaiwanComparisonAIResearchResponse {
  status: 'success' | 'unavailable' | 'error'
  error_code?: string | null
  error_message?: string | null
  report?: TaiwanComparisonAIStockResearchReport | null
  provider?: string | null
  model?: string | null
  prompt_version: string
  comparison_date?: string | null
  generated_at: string
  evidence_registry_keys: string[]
}

/** 生成監控規則 id (時間戳 + 隨機後綴), 用戶無需手動填寫。 */
export function genRuleId(): string {
  const ts = Date.now().toString(36)
  const rand = Math.random().toString(36).slice(2, 6)
  return `mr_${ts}_${rand}`
}

// ===== Strategy Backtest =====
export interface StrategyBacktestTrade {
  symbol: string
  name?: string
  entry_date: string
  exit_date: string
  entry_price: number
  exit_price: number
  pnl_pct: number
  duration: number
  exit_reason: string
  shares?: number
  lots?: number
  position_pct?: number
  entry_value?: number
  exit_value?: number
  pnl_amount?: number
  entry_score?: number | null
  entry_signal_date?: string | null
  exit_signal_date?: string | null
  blocked_exit_days?: number
  entry_signal_id?: string | null
  exit_signal_id?: string | null
  // entry_price / exit_price / shares are as traded; *_adj are forward-adjusted.
  entry_price_adj?: number | null
  exit_price_adj?: number | null
  exit_shares?: number | null
}

export interface StrategyBacktestRunPayload {
  strategy_id: string
  symbols?: string[] | null
  start?: string | null
  end?: string | null
  params?: Record<string, any> | null
  overrides?: Record<string, any> | null
  matching?: 'close_t' | 'open_t+1'
  entry_fill?: 'close_t' | 'open_t+1' | null
  exit_fill?: 'close_t' | 'open_t+1' | 'signal_next_minute' | null
  fees_pct?: number
  commission_pct?: number
  commission_discount?: number
  minimum_commission?: number
  stamp_tax_pct?: number
  slippage_bps?: number
  max_positions?: number
  initial_capital?: number
  position_sizing?: 'equal' | 'score_weight'
  minimum_holding_sessions?: number
  day_trade_eligible_symbols?: string[]
  asset_type?: 'stock' | 'etf' | 'index'
  lot_mode?: 'board_lot' | 'odd_lot'
  minute_fill?: boolean
  // Annual decimal rate, -0.05 to 0.2; the API answers 400 outside that range.
  risk_free_rate?: number
}

export interface StrategyBacktestStatsMethodology {
  version: string
  annualization: 'calendar_cagr'
  sessions: number
  return_periods: number
  calendar_days: number
  periods_per_year: number | null
  min_annualized_sessions: number
  annualization_unavailable_reason: string | null
  risk_free_rate: number
  risk_free_rate_per_period: number | null
  profit_factor_basis: 'pnl_amount' | 'pnl_pct'
  monte_carlo_basis: 'pnl_pct_x_position_pct' | 'pnl_pct'
}

export interface StrategyBacktestBenchmark {
  symbol: string
  name: string
  price_basis: 'price_index'
  includes_dividends: boolean
  base_date: string | null
  // Bounds of the index rows found inside the queried period.
  coverage_start: string | null
  coverage_end: string | null
  missing_sessions: number
}

/** A field an entry needs whose coverage on one exchange stays below threshold on some formal sessions. */
export interface StrategyBacktestExchangeCoverageWarning {
  field: string
  exchange: 'TWSE' | 'TPEX'
  threshold: number
  // The first few such sessions; session_count counts them all.
  sessions: string[]
  session_count: number
}

export interface StrategyBacktestResult {
  run_id: string
  config: Record<string, any>
  // annual_return, sharpe, sortino and calmar are null below methodology.min_annualized_sessions.
  stats: Record<string, any> & {
    risk_free_rate?: number
    methodology?: StrategyBacktestStatsMethodology
    benchmark?: StrategyBacktestBenchmark
    // Null unless the index has a close on the first and the last session.
    benchmark_return?: number | null
    // Null when benchmark_return is null or in full mode, where total_return is not an account return.
    excess?: number | null
    excluded_symbols?: { symbol: string; reason: string }[]
    // Absent when the run is refused before matching.
    exchange_coverage_warnings?: StrategyBacktestExchangeCoverageWarning[]
  }
  equity_curve: { date: string; value: number; cash?: number; positions?: number; exposure?: number }[]
  drawdown_curve: { date: string; value: number }[]
  benchmark_curve?: { date: string; value: number; close?: number; name?: string; symbol?: string }[]
  trades: StrategyBacktestTrade[]
  per_symbol_stats: {
    symbol: string
    n_trades: number
    total_return: number
    win_rate: number
    best: number
    worst: number
  }[]
  strategy_info: {
    id: string
    name: string
    description: string
    entry_signals: string[]
    exit_signals: string[]
    stop_loss: number | null
    take_profit: number | null
    trailing_stop: number | null
    trailing_take_profit_activate: number | null
    trailing_take_profit_drawdown: number | null
    score_min: number | null
    score_max: number | null
    max_hold_days: number | null
    source: string
    execution_backend?: string
    // 疊加策略回測: 子策略構成與權重歸因
    composite_children?: { id: string; weight: number }[]
  }
  elapsed_ms: number
  error: string | null
}

// ===== Settings =====

export interface SettingsState {
  // 首次使用引導
  onboarding_completed: boolean
  // AI 配置
  ai_provider: string
  ai_base_url: string
  ai_api_key_masked: string
  has_ai_key: boolean
  ai_configured?: boolean
  ai_model: string
  ai_openai_model?: string
  ai_reasoning_effort?: string
  ai_codex_model?: string
  ai_codex_command?: string
  ai_codex_reasoning_effort?: string
  ai_user_agent: string
  ai_max_output_tokens?: number
  ai_context_window?: number
}

export interface DataSourceItem {
  name: string
  display_name: string
  datasets: string[]
  path?: string | null
}

/** 內置可選插件數據源 (plugins/ 目錄, 需手動裝依賴) */
export interface PluginDataSourceItem {
  name: string
  display_name: string
  datasets: string[]
  runtime: string          // node | python | none
  available: boolean       // 依賴是否已安裝
  status: string           // 可用性原因 (供 UI 顯示)
  description: string
  install_hint: string     // 未裝依賴時顯示的安裝命令
  api_key_env?: string     // 聲明後設置頁提供 Key 輸入框 (先探後存)
}

export interface DataSourceLoadError {
  name?: string
  path: string
  errors: string[]
}

export interface DataSourcesResponse {
  builtin: DataSourceItem[]
  plugins: PluginDataSourceItem[]
  custom: DataSourceItem[]
  errors: DataSourceLoadError[]
  config_dir: string
}

export interface DataSourceTestResult {
  provider: string
  dataset: string
  rows: number
  columns: string[]
  preview: Record<string, unknown>[]
}

/** 插件 Key 保存結果 (先探後存: 無效 Key 返回 ok=false 且不落盤) */
export interface PluginKeyResult {
  ok: boolean
  reason?: string
  error?: string
  api_key_masked?: string
  plugin_available?: boolean
  plugin?: PluginDataSourceItem | null
}

export interface DatasetConfig {
  url: string
  method: string
  batch?: number | null
  rpm?: number | null
  response_path: string
  field_map: Record<string, string>
  transforms?: Record<string, string>
  symbols_param?: string
  start_param?: string
  end_param?: string
  asset_type_param?: string | null
  freq_param?: string | null
  timeout?: number | null
  /** Unit of each mapped ratio field (change_pct, amplitude, turnover_rate); a mapped one must be declared. */
  units?: Record<string, 'decimal' | 'percent'>
}

export interface AuthConfig {
  type: string
  token_env?: string | null
  header?: string
  param?: string
}

export interface CustomSourceConfig {
  name: string
  display_name: string
  auth: AuthConfig
  datasets: Record<string, DatasetConfig>
  /** Set only on a source read back from disk because it failed to load. */
  load_error?: string[]
}

export interface Preferences {
  realtime_quotes_enabled: boolean
  taiwan_market_closed_today?: boolean
  watchlist_groups_in_nav: boolean
  minute_sync_enabled: boolean
  minute_sync_days: number
  minute_sync_segment_days: number
  daily_data_provider?: string
  adj_factor_provider?: string
  minute_data_provider?: string
  realtime_data_provider?: string
  financial_data_provider?: string
  realtime_watchlist_symbols?: string[]
  realtime_pull_stock?: boolean
  realtime_pull_etf?: boolean
  realtime_pull_index?: boolean
  realtime_index_mode?: 'core' | 'all'
  realtime_index_symbols?: string[]
  pipeline_pull_a_share: boolean
  pipeline_pull_etf: boolean
  pipeline_pull_index: boolean
  pipeline_regime_enabled: boolean
  regime_batch_days: number
  regime_warmup_days: number
  pipeline_index_symbols: string
  pipeline_schedule: { hour: number; minute: number }
  instruments_schedule: { hour: number; minute: number }
  enriched_batch_size: number
  index_daily_batch_size: number
  sse_refresh_pages: Record<string, boolean>
  strategy_monitor_enabled: boolean
  strategy_monitor_ids: string[]
  system_notify_enabled: boolean
  webhook_enabled_default?: boolean
  webhook_default_channels?: string[]
  line_target_id?: string
  line_channel_access_token_masked?: string
  line_configured?: boolean
  telegram_chat_id?: string
  telegram_bot_token_masked?: string
  telegram_configured?: boolean
  sidebar_index_symbols: string[]
  nav_order: string[]
  nav_hidden: string[]
  screener_auto_run: boolean
  minute_intraday_refresh: boolean
  minute_intraday_refresh_interval: number
  monitor_ext_fields: { concept: MonitorExtFieldItem | null; industry: MonitorExtFieldItem | null }
}

/** 監控中心 ext 字段單項配置 (行業/概念標籤的來源 + 顯示裁剪) */
export interface MonitorExtFieldItem {
  /** "configId.fieldName" */
  field: string
  /** 顯示前N個標籤, 0=不限制 */
  maxTags?: number
  /** 隱藏的位置 (0-based), 如 [0] 表示隱藏第一個 */
  hiddenIndices?: number[]
}
export interface StrategyAlertEvent {
  source: string
  type: string
  strategy_id?: string
  symbol?: string
  name?: string | null
  message: string
  price?: number | null
  change_pct?: number | null
  signals?: string[]
  /** ext 富化字段 (行業/概念等), 鍵為 "{configId}__{fieldName}" */
  [key: string]: unknown
}

// ===== API surface =====
export const api = {

  // ===== Auth (訪問認證) =====
  authStatus: () =>
    request<{ configured: boolean; authenticated: boolean }>('/api/auth/status'),
  authSetup: (password: string) =>
    request<{ ok: boolean }>('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  authLogin: (password: string) =>
    request<{ ok: boolean }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  authLogout: () =>
    request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  authChangePassword: (oldPassword: string, newPassword: string) =>
    request<{ ok: boolean }>('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }),

  settings: () => request<SettingsState>('/api/settings'),

  /** 標記首次使用嚮導完成（持久化到後端 preferences） */
  completeOnboarding: (options?: { quiet?: boolean }) =>
    request<{ ok: boolean; onboarding_completed: boolean }>(
      '/api/settings/onboarding/complete', { method: 'POST', ...options },
    ),

  /** 保存 AI 配置 */
  saveAiSettings: (ai: { provider?: string; base_url?: string; api_key?: string; model?: string; reasoning_effort?: string; codex_command?: string; codex_reasoning_effort?: string; user_agent?: string; max_output_tokens?: number; context_window?: number }) =>
    request<{ ok: boolean; ai_provider?: string; ai_model?: string; ai_openai_model?: string; ai_reasoning_effort?: string; ai_codex_model?: string; ai_codex_command?: string; ai_codex_reasoning_effort?: string; ai_configured?: boolean; ai_max_output_tokens?: number; ai_context_window?: number }>('/api/settings/ai', {
      method: 'POST',
      body: JSON.stringify(ai),
    }),

  /** Probe a candidate AI configuration without saving it; blank fields fall back to the saved values. */
  testAiSettings: (ai: { provider?: string; base_url?: string; api_key?: string; model?: string; reasoning_effort?: string; user_agent?: string; max_output_tokens?: number; context_window?: number }) =>
    request<{ ok: boolean; error?: string; model?: string; response?: string }>('/api/settings/ai/test', {
      method: 'POST',
      body: JSON.stringify(ai),
    }),

  /** 一鍵清空 AI 配置(保留自定義 UA) */
  clearAiSettings: () =>
    request<{ ok: boolean }>('/api/settings/ai', { method: 'DELETE' }),

  preferences: () => request<Preferences>('/api/settings/preferences'),
  dataSources: () => request<DataSourcesResponse>('/api/settings/data-sources'),
  dataSource: (name: string, options?: { dedupe?: boolean }) =>
    request<CustomSourceConfig>(`/api/settings/data-sources/${encodeURIComponent(name)}`, options),
  /** Create a custom source; the backend answers 409 when the name is taken. */
  saveDataSource: (config: CustomSourceConfig) =>
    request<DataSourcesResponse>('/api/settings/data-sources', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  updateDataSource: (name: string, config: CustomSourceConfig) =>
    request<DataSourcesResponse>(`/api/settings/data-sources/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    }),
  deleteDataSource: (name: string) =>
    request<DataSourcesResponse>(`/api/settings/data-sources/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  reloadDataSources: () => request<DataSourcesResponse>('/api/settings/data-sources/reload', { method: 'POST' }),
  installPlugin: (name: string) => {
    // npm install 可能耗時較長, 用 6 分鐘超時
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 360_000)
    return request<DataSourcesResponse & { install_ok: boolean; install_message: string }>(
      `/api/settings/plugins/${encodeURIComponent(name)}/install`,
      { method: 'POST', signal: controller.signal },
    ).finally(() => clearTimeout(timer))
  },
  uninstallPlugin: (name: string) =>
    request<DataSourcesResponse & { uninstall_ok: boolean; uninstall_message: string }>(
      `/api/settings/plugins/${encodeURIComponent(name)}/install`,
      { method: 'DELETE' },
    ),
  savePluginKey: (plugin: string, apiKey: string) => {
    // 先探後存: 後端會用候選 Key 實探一次, 探測超時 10s + 餘量
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 30_000)
    return request<PluginKeyResult>('/api/settings/plugin-key', {
      method: 'POST',
      body: JSON.stringify({ plugin, api_key: apiKey }),
      signal: controller.signal,
    }).finally(() => clearTimeout(timer))
  },
  clearPluginKey: (plugin: string) =>
    request<PluginKeyResult>(`/api/settings/plugin-key/${encodeURIComponent(plugin)}`, { method: 'DELETE' }),
  testDataSource: (
    provider: string,
    dataset: string,
    symbols?: string[],
    config?: CustomSourceConfig,
  ) =>
    request<DataSourceTestResult>('/api/settings/data-sources/test', {
      method: 'POST',
      body: JSON.stringify({ provider, dataset, symbols, config }),
    }),
  updateDataProviders: (cfg: Partial<Pick<Preferences, 'daily_data_provider' | 'adj_factor_provider' | 'minute_data_provider' | 'realtime_data_provider' | 'financial_data_provider'>>) =>
    request<Pick<Preferences, 'daily_data_provider' | 'adj_factor_provider' | 'minute_data_provider' | 'realtime_data_provider'>>(
      '/api/settings/preferences/data-providers',
      { method: 'PUT', body: JSON.stringify(cfg) },
    ),
  updateRealtimeQuotes: (enabled: boolean) =>
    request<{ realtime_quotes_enabled: boolean; realtime_allowed?: boolean; mode?: string; error?: string }>('/api/settings/preferences/realtime-quotes', {
      method: 'PUT',
      body: JSON.stringify({ realtime_quotes_enabled: enabled }),
    }),
  updateMarketClosedToday: (closed: boolean) =>
    request<{ taiwan_market_closed_today: boolean }>('/api/settings/preferences/market-closed-today', {
      method: 'PUT',
      body: JSON.stringify({ closed }),
    }),
  updateWatchlistGroupsInNav: (enabled: boolean) =>
    request<{ watchlist_groups_in_nav: boolean }>('/api/settings/preferences/watchlist-groups-in-nav', {
      method: 'PUT',
      body: JSON.stringify({ watchlist_groups_in_nav: enabled }),
    }),
  quoteStatus: () =>
    request<QuoteStatus>('/api/intraday/status'),
  quoteInterval: () =>
    request<{ interval: number; min_interval: number; max_interval: number }>(
      '/api/settings/preferences/quote-interval',
    ),
  updateQuoteInterval: (interval: number) =>
    request<{ interval: number; min_interval: number; max_interval: number }>(
      '/api/settings/preferences/quote-interval',
      { method: 'PUT', body: JSON.stringify({ interval }) },
    ),
  intradayRefresh: () =>
    request<QuoteStatus | { error: string }>('/api/intraday/refresh', { method: 'POST' }),
  updateRealtimeMonitorConfig: (cfg: {
    sse_refresh_pages?: Record<string, boolean>
    strategy_monitor_enabled?: boolean
    strategy_monitor_ids?: string[]
    sidebar_index_symbols?: string[]
    screener_auto_run?: boolean
    minute_intraday_refresh?: boolean
    minute_intraday_refresh_interval?: number
    monitor_ext_fields?: { concept: MonitorExtFieldItem | null; industry: MonitorExtFieldItem | null }
  }) =>
    request<{
      sse_refresh_pages: Record<string, boolean>
      strategy_monitor_enabled: boolean
      strategy_monitor_ids: string[]
      sidebar_index_symbols: string[]
      screener_auto_run: boolean
      minute_intraday_refresh: boolean
      minute_intraday_refresh_interval: number
      monitor_ext_fields: { concept: MonitorExtFieldItem | null; industry: MonitorExtFieldItem | null }
    }>('/api/settings/preferences/realtime-monitor', {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),
  updateSystemNotify: (enabled: boolean) =>
    request<{ system_notify_enabled: boolean }>('/api/settings/preferences/system-notify', {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  updateWebhookDefaultChannels: (channels: string[]) =>
    request<{ webhook_default_channels: string[] }>('/api/settings/preferences/webhook-default-channels', {
      method: 'PUT',
      body: JSON.stringify({ channels }),
    }),
  updateLineMessaging: (recipient: string, token?: string, clearToken: boolean = false) =>
    request<{ line_target_id: string; line_channel_access_token_masked: string; line_configured: boolean }>('/api/settings/preferences/line-messaging', {
      method: 'PUT',
      body: JSON.stringify({ recipient, token, clear_token: clearToken }),
    }),
  updateTelegramBot: (recipient: string, token?: string, clearToken: boolean = false) =>
    request<{ telegram_chat_id: string; telegram_bot_token_masked: string; telegram_configured: boolean }>('/api/settings/preferences/telegram-bot', {
      method: 'PUT',
      body: JSON.stringify({ recipient, token, clear_token: clearToken }),
    }),
  testLineMessaging: () =>
    request<{ ok: boolean }>('/api/settings/preferences/line-messaging/test', { method: 'POST' }),
  testTelegramBot: () =>
    request<{ ok: boolean }>('/api/settings/preferences/telegram-bot/test', { method: 'POST' }),
  saveNavOrder: (nav_order: string[]) =>
    request<{ nav_order: string[] }>('/api/settings/preferences/nav-order', {
      method: 'PUT',
      body: JSON.stringify({ nav_order }),
    }),
  saveNavHidden: (nav_hidden: string[]) =>
    request<{ nav_hidden: string[] }>('/api/settings/preferences/nav-hidden', {
      method: 'PUT',
      body: JSON.stringify({ nav_hidden }),
    }),
  // 自選列表列配置
  watchlistColumns: () =>
    request<{ columns: any[] | null }>('/api/settings/preferences/watchlist-columns'),
  updateWatchlistColumns: (columns: any[]) =>
    request<{ columns: any[] }>('/api/settings/preferences/watchlist-columns', {
      method: 'PUT',
      body: JSON.stringify({ columns }),
    }),

  capabilities: () => request<CapabilitiesResponse>('/api/capabilities'),
  version: () => request<{ version: string }>('/api/data/version'),

  klineDaily: (symbol: string, days = 120, dateRange?: { start: string; end: string }, extColumns?: string) =>
    request<{
      symbol: string
      name?: string
      stock_info?: { name?: string; total_shares?: number; float_shares?: number; ext?: Record<string, unknown> }
      rows: KlineRow[]
      source?: string
    }>(
      (dateRange
        ? `/api/kline/daily?symbol=${encodeURIComponent(symbol)}&start_date=${dateRange.start}&end_date=${dateRange.end}`
        : `/api/kline/daily?symbol=${encodeURIComponent(symbol)}&days=${days}`)
      + (extColumns ? `&ext_columns=${encodeURIComponent(extColumns)}` : ''),
    ),
  klineDailyBatch: (symbols: string[], days = 12) =>
    request<{ data: Record<string, KlineRow[]> }>('/api/kline/daily-batch', {
      method: 'POST',
      body: JSON.stringify({ symbols, days }),
    }),
  klineMinuteBatch: (symbols: string[], date?: string) =>
    request<{ data: Record<string, MinuteKlineRow[]> }>('/api/kline/minute-batch', {
      method: 'POST',
      body: JSON.stringify({ symbols, date }),
    }),
  /** Fuzzy search of TaiwanSecurityMaster by code or Traditional Chinese name. */
  instrumentSearch: (q: string, limit = 20, assetTypes?: string) =>
    request<{ results: { symbol: string; name: string; code: string; asset_type?: string; market: 'taiwan' }[] }>(
      `/api/kline/instruments/search?q=${encodeURIComponent(q)}&limit=${limit}${assetTypes ? `&asset_types=${encodeURIComponent(assetTypes)}` : ''}`,
    ),

  /** 批量查股票名稱 (傳入 symbol 列表, 返回 {symbol: name}) */
  instrumentNames: (symbols: string[]) =>
    request<{ names: Record<string, string> }>('/api/kline/instruments/names', {
      method: 'POST',
      body: JSON.stringify(symbols),
    }),
  klineMinute: (symbol: string, date?: string) =>
    request<{
      symbol: string
      name?: string
      stock_info?: { name?: string; total_shares?: number; float_shares?: number }
      date: string | null
      rows: MinuteKlineRow[]
      source?: 'local' | 'local_partial' | 'live' | 'none'
      asset_type?: 'stock' | 'etf' | 'index'
      price_limit?: PriceLimitInfo | null
      prev_close?: number | null
    }>(
      `/api/kline/minute?symbol=${encodeURIComponent(symbol)}${date ? `&date=${date}` : ''}`,
    ),
  klineMinuteRange: (symbol: string, days = 10) =>
    request<{
      symbol: string
      name?: string
      asset_type: 'stock' | 'etf' | 'index'
      requested_days: number
      sessions: MinuteKlineSession[]
      source: 'local' | 'none'
    }>(
      `/api/kline/minute-range?symbol=${encodeURIComponent(symbol)}&days=${days}`,
    ),
  syncMinuteSingle: (symbol: string, days?: number) =>
    request<{ status: string; symbol: string; rows: number }>('/api/kline/sync_minute_single', {
      method: 'POST',
      body: JSON.stringify({ symbol, ...(days != null ? { days } : {}) }),
    }),

  watchlistList: () => request<{ symbols: WatchlistEntry[] }>('/api/watchlist'),
  watchlistAdd: (symbol: string, note = '', groupId?: string | null) =>
    request<{ symbols: WatchlistEntry[] }>('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify({ symbol, note, group_id: groupId ?? null }),
    }),
  watchlistBatchAdd: (symbols: string[], note = '', groupId?: string | null) =>
    request<{ symbols: WatchlistEntry[]; added: number }>('/api/watchlist/batch', {
      method: 'POST',
      body: JSON.stringify({ symbols, note, group_id: groupId ?? null }),
    }),
  watchlistGroups: () =>
    request<{ groups: WatchlistGroup[] }>('/api/watchlist/groups'),
  watchlistGroupCreate: (name: string, color: WatchlistGroupColor) =>
    request<{ groups: WatchlistGroup[]; group: WatchlistGroup }>('/api/watchlist/groups', {
      method: 'POST',
      body: JSON.stringify({ name, color }),
    }),
  watchlistGroupRename: (groupId: string, name: string, color: WatchlistGroupColor) =>
    request<{ groups: WatchlistGroup[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}`,
      { method: 'PUT', body: JSON.stringify({ name, color }) },
    ),
  watchlistGroupReorder: (orderedIds: string[]) =>
    request<{ groups: WatchlistGroup[] }>('/api/watchlist/groups/reorder', {
      method: 'PUT',
      body: JSON.stringify({ ordered_ids: orderedIds }),
    }),
  watchlistGroupDelete: (groupId: string) =>
    request<{ groups: WatchlistGroup[]; symbols: WatchlistEntry[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}`,
      { method: 'DELETE' },
    ),
  watchlistGroupClear: (groupId: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}/clear`,
      { method: 'POST' },
    ),
  watchlistGroupAddMember: (groupId: string, symbol: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(symbol)}`,
      { method: 'POST' },
    ),
  watchlistGroupRemoveMember: (groupId: string, symbol: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(symbol)}`,
      { method: 'DELETE' },
    ),
  watchlistOcrStatus: () =>
    request<{ provider: string; available: boolean }>('/api/watchlist/ocr-status'),
  watchlistImportImage: (file: File, signal?: AbortSignal, quiet = false) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<WatchlistImportResult>('/api/watchlist/import-image', {
      method: 'POST',
      body: fd,
      signal,
      quiet,
    })
  },
  watchlistRemove: (symbol: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/${encodeURIComponent(symbol)}`,
      { method: 'DELETE' },
    ),
  watchlistMoveToTop: (symbol: string) =>
    request<{ symbols: WatchlistEntry[] }>(
      `/api/watchlist/${encodeURIComponent(symbol)}/top`,
      { method: 'POST' },
    ),
  watchlistClear: () =>
    request<{ removed: number }>('/api/watchlist', { method: 'DELETE' }),
  watchlistEnriched: (extColumns?: string) =>
    request<{ rows: any[]; as_of: string | null; elapsed_ms: number }>(
      extColumns
        ? `/api/watchlist/enriched?ext_columns=${encodeURIComponent(extColumns)}`
        : '/api/watchlist/enriched',
    ),

  screenerStrategies: async (assetType?: 'stock' | 'etf' | 'index') => {
    const data = await request<{ strategies: StrategyDetail[]; load_errors?: StrategyLoadError[] }>(
      `/api/strategies?${assetType ? `asset_type=${assetType}&` : ''}timeframe=1d`,
    )
    return { presets: data.strategies, load_errors: data.load_errors }
  },
  screenerCachedSummary: () =>
    request<ScreenerCachedSummary>('/api/screener/cached-summary'),
  screenerCached: (extColumns?: string) =>
    request<ScreenerCached>(
      extColumns
        ? `/api/screener/cached?ext_columns=${encodeURIComponent(extColumns)}`
        : '/api/screener/cached',
    ),
  // quiet: a 503 detail is a {code, message} object the page renders itself.
  screenerPickPerformance: () =>
    request<PickPerformanceSummary>('/api/screener/pick-performance', { quiet: true }),
  screenerPickBackfill: (days = 5) =>
    request<PickBackfillJob>('/api/screener/pick-performance/backfill', {
      method: 'POST', body: JSON.stringify({ days }),
    }),
  screenerPickBackfillStatus: (jobId: string) =>
    request<PickBackfillJob>(`/api/screener/pick-performance/backfill/${encodeURIComponent(jobId)}`, { quiet: true }),
  screenerPickPerformanceDetail: (strategyId: string, limit = 200) =>
    request<PickPerformanceDetail>(
      `/api/screener/pick-performance/${encodeURIComponent(strategyId)}?limit=${limit}`,
      { quiet: true },
    ),
  marketSnapshot: () =>
    request<{ as_of: string | null; rows: MarketSnapshotRow[] }>('/api/screener/market-snapshot'),

  strategyBacktestRun: (payload: StrategyBacktestRunPayload) =>
    request<StrategyBacktestResult>('/api/backtest/strategy/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // ===== 擴展數據 =====

  dimensionMembers: (id: string, opts: { field: string; value: string; date?: string; limit?: number }) => {
    const qs = new URLSearchParams({ field: opts.field, value: opts.value })
    if (opts.date) qs.set('date', opts.date)
    if (opts.limit) qs.set('limit', String(opts.limit))
    return request<DimensionMembersResult>(`/api/ext-data/${encodeURIComponent(id)}/dimension-members?${qs.toString()}`)
  },

  extDataSchemaAll: () =>
    request<{ items: { id: string; label: string; mode: string; columns: { name: string; type: string; label: string }[] }[] }>('/api/ext-data/schema-all'),

  // ===== 個股分析 =====
  stockAnalysisLevels: (symbol: string, days = 120) =>
    request<StockLevels>(`/api/stock-analysis/levels?symbol=${encodeURIComponent(symbol)}&days=${days}`),

  // ===== Custom Signals (自定義訊號) =====
  customSignalsList: () =>
    request<{ signals: CustomSignal[] }>('/api/custom-signals'),

  customSignalsOptions: () =>
    request<CustomSignalOptions>('/api/custom-signals/options'),

  customSignalCreate: (signal: CustomSignal) =>
    request<{ ok: boolean; signal: CustomSignal }>('/api/custom-signals', {
      method: 'POST',
      body: JSON.stringify(signal),
    }),

  /** Without force, disabling a signal that monitor rules use fails with 409. */
  customSignalUpdate: (signal: CustomSignal, force = false) =>
    request<{ ok: boolean; signal: CustomSignal }>(
      `/api/custom-signals/${encodeURIComponent(signal.id)}${force ? '?force=true' : ''}`,
      { method: 'PUT', body: JSON.stringify(signal) },
    ),

  customSignalReferences: (id: string) =>
    request<CustomSignalReferences>(`/api/custom-signals/${encodeURIComponent(id)}/references`),

  /** Without force, deleting a signal that monitor rules use fails with 409. */
  customSignalDelete: (id: string, force = false) =>
    request<{ ok: boolean }>(
      `/api/custom-signals/${encodeURIComponent(id)}${force ? '?force=true' : ''}`,
      { method: 'DELETE' },
    ),

  customSignalsAiGenerate: (description: string) =>
    request<CustomSignalAIGenerateResult>('/api/custom-signals/ai/generate', {
      method: 'POST',
      body: JSON.stringify({ description }),
    }),

  // ===== Monitor Rules (監控規則) =====
  monitorRulesList: () =>
    request<{ rules: MonitorRule[] }>('/api/monitor-rules'),

  monitorRuleOptions: () =>
    request<MonitorRuleOptions>('/api/monitor-rules/options'),

  monitorRuleSave: (rule: MonitorRule) =>
    request<{ ok: boolean; rule: MonitorRule }>('/api/monitor-rules', {
      method: 'POST',
      body: JSON.stringify(rule),
    }),

  /** Disabling skips binding validation; enabling runs the full save validation. */
  monitorRuleSetEnabled: (id: string, enabled: boolean) =>
    request<{ ok: boolean; rule: MonitorRule }>(`/api/monitor-rules/${encodeURIComponent(id)}/enabled`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),

  monitorRuleDelete: (id: string) =>
    request<{ ok: boolean }>(`/api/monitor-rules/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // ===== Taiwan Realtime & Monitor APIs (Phase 5C) =====
  taiwanQuotes: (symbols: string[], force = false) =>
    request<{ quotes: TaiwanRealtimeQuote[]; count: number }>(
      `/api/intraday/quotes?symbols=${encodeURIComponent(symbols.join(','))}&force=${force}`,
    ),

  taiwanSearch: (query: string, limit = 20) =>
    request<{ results: TaiwanSearchResult[]; count: number }>(
      `/api/intraday/taiwan/search?q=${encodeURIComponent(query)}&limit=${limit}`,
    ),

  taiwanStockDetail: (symbol: string, days = 120, quiet = false) =>
    request<TaiwanStockDetailResponse>(
      `/api/taiwan/stocks/${encodeURIComponent(symbol)}?days=${days}`,
      { quiet },
    ),

  taiwanCurrentData: (symbol: string) =>
    request<TaiwanCurrentDataResponse>(`/api/taiwan/data/${encodeURIComponent(symbol)}`),

  taiwanInstitutionalHistory: (symbol: string, days = 60) =>
    request<TaiwanInstitutionalHistoryResponse>(
      `/api/taiwan/stocks/${encodeURIComponent(symbol)}/institutional-history?days=${days}`,
    ),

  taiwanMarginHistory: (symbol: string, days = 60) =>
    request<TaiwanMarginHistoryResponse>(
      `/api/taiwan/stocks/${encodeURIComponent(symbol)}/margin-history?days=${days}`,
    ),

  taiwanScreenerRun: (payload: TaiwanScreenerRunRequest, signal?: AbortSignal) =>
    request<TaiwanScreenerResponse>('/api/taiwan/screener/run', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal,
    }),

  taiwanDataStatus: () =>
    request<TaiwanDataStatus>('/api/taiwan/data-status'),

  taiwanScreenerTranslate: (query: string) =>
    request<TaiwanScreenerTranslation>('/api/taiwan/screener/translate', {
      method: 'POST',
      body: JSON.stringify({ query }),
    }),

  taiwanMarketIntelligence: (date?: string) =>
    request<TaiwanMarketIntelligenceSnapshot>(
      date ? `/api/taiwan/market-intelligence?date=${encodeURIComponent(date)}` : '/api/taiwan/market-intelligence',
    ),

  taiwanIndustryIntelligence: (params?: { date?: string; sort_by?: string; order?: string }) => {
    const q = new URLSearchParams()
    if (params?.date) q.set('date', params.date)
    if (params?.sort_by) q.set('sort_by', params.sort_by)
    if (params?.order) q.set('order', params.order)
    const qs = q.toString()
    return request<TaiwanIndustryIntelligenceSnapshot>(
      qs ? `/api/taiwan/industry-intelligence?${qs}` : '/api/taiwan/industry-intelligence',
    )
  },

  taiwanStockResearchContext: (symbol: string, date?: string) =>
    request<TaiwanStockResearchContext>(
      date
        ? `/api/taiwan/stocks/${encodeURIComponent(symbol)}/research-context?date=${encodeURIComponent(date)}`
        : `/api/taiwan/stocks/${encodeURIComponent(symbol)}/research-context`,
    ),

  taiwanStockAIResearch: (symbol: string, date?: string) =>
    request<TaiwanAIResearchResponse>(
      `/api/taiwan/stocks/${encodeURIComponent(symbol)}/ai-research`,
      {
        method: 'POST',
        body: JSON.stringify({ date: date || null }),
      },
    ),

  taiwanStockCompare: (symbols: string[], date?: string) =>
    request<TaiwanStockComparisonResponse>('/api/taiwan/stocks/compare', {
      method: 'POST',
      body: JSON.stringify({ symbols, date: date || null }),
    }),

  taiwanStockCompareAIResearch: (symbols: string[], date?: string) =>
    request<TaiwanComparisonAIResearchResponse>('/api/taiwan/stocks/compare/ai-research', {
      method: 'POST',
      body: JSON.stringify({ symbols, date: date || null }),
    }),

  taiwanAbnormalDiagnostics: (params?: {
    date?: string
    include_all?: boolean
    signal_type?: string
    industry?: string
    exchange?: string
  }) => {
    const q = new URLSearchParams()
    if (params?.date) q.set('date', params.date)
    if (params?.include_all !== undefined) q.set('include_all', String(params.include_all))
    if (params?.signal_type) q.set('signal_type', params.signal_type)
    if (params?.industry) q.set('industry', params.industry)
    if (params?.exchange) q.set('exchange', params.exchange)
    const qs = q.toString()
    return request<TaiwanAbnormalDiagnosticsSnapshot>(
      qs ? `/api/taiwan/abnormal-diagnostics?${qs}` : '/api/taiwan/abnormal-diagnostics',
    )
  },

  // ===== Alerts (觸發記錄) =====
  alertsList: (params?: { days?: number; limit?: number; source?: string; type?: string; extColumns?: string }) => {
    const qs = new URLSearchParams()
    if (params?.days) qs.set('days', String(params.days))
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.source) qs.set('source', params.source)
    if (params?.type) qs.set('type', params.type)
    if (params?.extColumns) qs.set('ext_columns', params.extColumns)
    const s = qs.toString()
    return request<{ alerts: AlertEvent[]; total: number }>(`/api/alerts${s ? `?${s}` : ''}`)
  },

  alertsClear: () =>
    request<{ ok: boolean; cleared: number }>('/api/alerts', { method: 'DELETE' }),

  alertDelete: (id: string) =>
    request<{ ok: boolean }>(`/api/alerts/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  /** 檢查 AI 配置狀態 */
  strategyAiStatus: () =>
    request<{ configured: boolean; has_key: boolean; has_model: boolean; provider?: string }>('/api/strategies/ai/status'),

  /** 測試 AI 連通性 */
  strategyAiTest: () =>
    request<{ ok: boolean; error?: string; model?: string; response?: string; usage?: { prompt: number; completion: number } }>(
      '/api/strategies/ai/test',
      { method: 'POST' },
    ),

  // ===== Taiwan Historical Data & Bootstrap =====
  /** 獲取台股歷史日 K 本地存儲狀態與 Bootstrap 需求 */
  taiwanHistoryStatus: () =>
    request<TaiwanHistoryStatus>('/api/taiwan/history-status'),

  /** 觸發 GitHub Release 官方歷史日 K 資料包下載與自動匯入 */
  taiwanBootstrapRun: () =>
    request<TaiwanBootstrapRunResult>('/api/taiwan/bootstrap/run', { method: 'POST' }),

  /** 查詢台股歷史資料 Bootstrap 作業進度 */
  taiwanBootstrapJob: (jobId: string, opts?: { quiet?: boolean }) =>
    request<TaiwanBootstrapJobState>(`/api/taiwan/bootstrap/jobs/${jobId}`, { quiet: opts?.quiet }),

  /** 增量補齊已存在的歷史資料至最新交易日（不重新下載整包） */
  taiwanUpdateLatest: () =>
    request<TaiwanUpdateLatestResult>('/api/taiwan/bootstrap/update-latest', { method: 'POST' }),

  // ===== Taiwan Institutional / Margin Enrichment Backfill =====
  /** 獲取三大法人與融資融券本地資料覆蓋狀況 */
  taiwanEnrichmentStatus: () =>
    request<TaiwanEnrichmentStatus>('/api/taiwan/enrichment-status'),

  /** 觸發三大法人與融資融券歷史回補（依交易日曆往回推 N 個交易日） */
  taiwanEnrichmentBackfillRun: (days: number) =>
    request<TaiwanBootstrapRunResult>('/api/taiwan/enrichment/backfill', {
      method: 'POST',
      body: JSON.stringify({ days }),
    }),

  /** 查詢三大法人與融資融券回補任務進度 */
  taiwanEnrichmentBackfillJob: (jobId: string, opts?: { quiet?: boolean }) =>
    request<TaiwanEnrichmentBackfillJobState>(`/api/taiwan/enrichment/backfill/${jobId}`, { quiet: opts?.quiet }),
}

// ===== Taiwan Historical Bootstrap Interfaces =====
export interface TaiwanHistoryStatus {
  has_data: boolean
  earliest_date: string | null
  latest_date: string | null
  trading_days: number
  needs_bootstrap: boolean
  asset_info?: {
    repo?: string
    tag?: string
    asset?: string
    sha256?: string
    approx_size_mb?: number
    date_range?: string
    download_url?: string
  }
}

export interface TaiwanBootstrapJobState {
  job_id: string
  status: 'pending' | 'downloading' | 'verifying' | 'extracting' | 'importing' | 'syncing' | 'success' | 'failed'
  stage: string
  progress: number
  message: string
  error?: string | null
  bytes_downloaded: number
  total_bytes: number
  updated_at: string
}

export interface TaiwanBootstrapRunResult {
  job_id: string
  status: string
}

export interface TaiwanUpdateLatestResult {
  ok: boolean
  already_current: boolean
  message: string
  dates_fetched: number
  /** Set when the backend also rebuilt the caches the panels read. */
  caches_refreshed?: boolean
  /** Latest stored TAIEX date after the refresh, null when it could not be refreshed. */
  benchmark_index_latest_date?: string | null
  stats?: Record<string, any>
  /** Institutional, market-wide institutional amount and margin refresh run after the daily bars. */
  chip_data?: TaiwanChipDataRefresh
}

export interface TaiwanChipDatasetRefresh {
  status: 'success' | 'partial' | 'failed' | 'skipped'
  dates_requested: number
  dates_fetched: number
  dates_skipped: number
  failed_dates: Record<string, any>[]
  rows_written: number
  note: string | null
}

export interface TaiwanChipDataRefresh {
  institutional: TaiwanChipDatasetRefresh
  market_institutional: TaiwanChipDatasetRefresh
  margin: TaiwanChipDatasetRefresh
  as_of?: Record<'institutional' | 'market_institutional' | 'margin', string | null>
  margin_target_date?: string
}

// ===== Taiwan Institutional / Margin Enrichment Backfill =====
export interface TaiwanEnrichmentDatasetStatus {
  has_data: boolean
  day_count: number
  earliest: string | null
  latest: string | null
}

export interface TaiwanEnrichmentStatus {
  institutional: TaiwanEnrichmentDatasetStatus
  margin: TaiwanEnrichmentDatasetStatus
}

export interface TaiwanEnrichmentRefreshStats {
  dates_requested: number
  dates_fetched: number
  dates_skipped: number
  total_rows_written: number
  failed_dates: { date: string; error: string }[]
  repair_failed_dates?: { date: string; error: string }[]
}

export interface TaiwanEnrichmentBackfillJobState {
  job_id: string
  status: 'pending' | 'running' | 'success' | 'failed'
  stage: string
  progress: number
  current_dataset: 'institutional' | 'margin' | null
  current_date: string | null
  day_index: number
  total_days: number
  message: string
  error?: string | null
  result?: {
    institutional: TaiwanEnrichmentRefreshStats
    margin: TaiwanEnrichmentRefreshStats
  } | null
}

// ===== 擴展數據 =====
export interface DimensionMembersResult {
  id: string
  label: string
  date: string | null
  field: string
  value: string
  total: number
  limit: number
  rows: Record<string, any>[]
}
