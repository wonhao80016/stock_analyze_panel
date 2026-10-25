/**
 * 集中管理所有 React Query key。
 *
 * - 新增查詢只需在此加一行，所有消費方自動引用。
 * - SSE invalidation 基於 SSE_INVALIDATE_PREFIXES 列表，新增 key 無需改 useQuoteStream。
 */

// ===== Query Key 工廠 =====

/** Mirrors the api.alertsList parameters; every one changes the response. */
export interface AlertsKeyParams {
  days?: number
  limit?: number
  source?: string
  type?: string
  extColumns?: string
}

export const QK = {
  // 全局 / 共享 (Layout 預取)
  authStatus:     ['auth-status'] as const,
  settings:       ['settings'] as const,
  version:        ['version'] as const,
  preferences:    ['preferences'] as const,
  dataSources:    ['data-sources'] as const,
  taiwanDataStatus: ['taiwan-data-status'] as const,
  taiwanHistoryStatus: ['taiwan-history-status'] as const,
  taiwanEnrichmentStatus: ['taiwan-enrichment-status'] as const,
  taiwanMarketIntelligence: ['taiwanMarketIntelligence'] as const,
  taiwanIndustryIntelligence: (sortBy: string, order: string) => ['taiwanIndustryIntelligence', sortBy, order] as const,
  taiwanAbnormalDiagnostics: (signalType: string) => ['taiwanAbnormalDiagnostics', signalType] as const,
  taiwanScreenerAll: ['taiwanScreener'] as const,
  taiwanScreener: (payload: unknown) => ['taiwanScreener', payload] as const,
  quoteStatus:    ['quote-status'] as const,
  quoteInterval:  ['quote-interval'] as const,

  // Watchlist
  watchlist:            ['watchlist'] as const,
  watchlistGroups:      ['watchlist-groups'] as const,
  watchlistEnriched:    (ext?: string) => ['watchlist-enriched', ext] as const,
  // 不用 watchlist- 前綴: 日K歷史盤中幾乎不變, 若被 SSE quotes_updated 高頻失效
  // (expert 1s) 會導致全自選日K每秒重拉, staleTime 形同虛設。
  // 刷新點: staleTime 過期 + Watchlist 增刪自選/改蠟燭天數時的手動失效;
  // 當日最後一根蠟燭由 Watchlist 用 enriched 實時 OHLC 前端修補 (零額外請求)。
  watchlistKlineBatch:  (symbols: string) => ['kline-batch', symbols] as const,
  // Not prefixed with watchlist-: SSE quotes_updated would refetch it on every tick.
  // The intraday column refreshes with its own refetchInterval instead.
  minuteBatch:          (symbols: string) => ['minute-batch', symbols] as const,
  instrumentSearch:     (q: string, assetTypes?: string) =>
    ['instrument-search', q, assetTypes ?? 'stock'] as const,

  // Screener
  screenerStrategies:   (assetType: string = 'stock') => ['screener-strategies', assetType] as const,
  screenerCachedSummary: ['screener-cached', 'summary'] as const,
  screenerCached:       (asOf?: string, ext?: string) => ['screener-cached', 'all', asOf ?? '', ext ?? ''] as const,
  screenerPickPerformance: ['screener-pick-performance', 'summary'] as const,
  screenerPickPerformanceAll: ['screener-pick-performance'] as const,
  screenerPickBackfill: (jobId: string) => ['screener-pick-backfill', jobId] as const,
  screenerPickPerformanceDetail: (strategyId: string, limit: number) =>
    ['screener-pick-performance', 'detail', strategyId, limit] as const,
  marketSnapshot:       ['market-snapshot'] as const,

  dimensionMembers:     (id: string, field: string, value: string, date?: string) => ['dimension-members', id, field, value, date] as const,

  // Kline
  kline:                (symbol: string, start: string, end: string, extColumns?: string) =>
                           ['kline', symbol, start, end, extColumns ?? ''] as const,
  stockLevels:          (symbol: string, days?: number) => ['stock-levels', symbol, days ?? 120] as const,
  klineMinute:          (symbol: string, date: string) =>
                             ['kline-minute', symbol, date] as const,
  klineMinuteRange:     (symbol: string, days: number) =>
                             ['kline-minute-range', symbol, days] as const,

  // Schema
  extDataSchemaAll:     ['ext-data-schema-all'] as const,

  // Custom Signals
  customSignals:        ['custom-signals'] as const,
  customSignalsOptions: ['custom-signals-options'] as const,

  // Monitor (監控規則 + 觸發記錄)
  monitorRules:         ['monitor-rules'] as const,
  monitorRuleOptions:   ['monitor-rule-options'] as const,
  alertsAll:            ['alerts'] as const,
  alerts:               (params: AlertsKeyParams) => ['alerts', {
                           days: params.days ?? null,
                           limit: params.limit ?? null,
                           source: params.source ?? null,
                           type: params.type ?? null,
                           extColumns: params.extColumns ?? null,
                         }] as const,
  taiwanQuotes:         (symbols: string) => ['taiwan-quotes', symbols] as const,
  taiwanSearch:         (q: string) => ['taiwan-search', q] as const,
  taiwanStockDetail:    (symbol: string, days?: number) => ['taiwan-stock-detail', symbol, days ?? 120] as const,
  taiwanCurrentData:    (symbol: string) => ['taiwan-current-data', symbol] as const,
  taiwanInstitutionalHistory: (symbol: string, days: number) => ['taiwan-institutional-history', symbol, days] as const,
  taiwanMarginHistory:  (symbol: string, days: number) => ['taiwan-margin-history', symbol, days] as const,
} as const

// ===== SSE 應該 invalidate 的 key 前綴列表 =====
// 新增需要 SSE 推送的查詢，只需在此加一行
//
// 注意: 策略頁 (screener-cached) 不在此列表 —— 行情刷新時策略結果不變
// (非監控策略讀盤後靜態緩存, 監控策略由獨立的 strategy_results_updated 事件在
// 重算完成後刷新)。若加入 'screener', 會導致每個行情 tick 雙重刷新策略頁,
// 且在 monitor "重算" 窗口內讀到空結果, 造成策略列表閃爍 (變 0 → 空失效 → 又出現)。

export const SSE_INVALIDATE_PREFIXES = [
  // 精確前綴: 只命中自選頁的實時數據 (quotes/enriched)。不能用寬泛的 'watchlist' ——
  // 會誤傷 ['watchlist'] (自選列表) 和 ['watchlist-groups'] (分組配置, 只隨手動操作變化)。
  // 舊設置裡的 'watchlist' 單開關由 useQuoteStream 兼容讀取。
  'watchlist-enriched',
  'quote-status',
  'taiwan-quotes',
  'taiwan-stock-detail',
] as const

