/**
 * 集中管理所有 localStorage 持久化。
 *
 * - key 在此註冊，各頁面只通過 storage.xxx.get/set 調用。
 * - 類型安全，不再散落 try/catch。
 */

function kv<T>(key: string) {
  return {
    get(fallback: T): T {
      try {
        const raw = localStorage.getItem(key)
        if (raw !== null) return JSON.parse(raw) as T
      } catch { /* ignore */ }
      return fallback
    },
    set(val: T) {
      try { localStorage.setItem(key, JSON.stringify(val)) } catch { /* ignore */ }
    },
  }
}

export const storage = {
  /** 自選列表列配置 */
  watchlistColumns:     kv<unknown[]>('watchlist_columns'),

  /** 個股日K信息條指標配置 */
  stockInfoBarFields:   kv<unknown[]>('stock_info_bar_fields'),

  /** 個股日K成交量對比設置 */
  stockVolumeCompare:   kv<{ enabled: boolean; days: number }>('stock_volume_compare'),

  /** Chart volume display unit: shares (股) or lots (張). */
  volumeUnit:           kv<string>('volume_unit'),

  /** 個股詳情多日分時週期 */
  stockPreviewIntradayDays: kv<number>('stock_preview_intraday_days'),

  /** 自選列表視圖模式 table | card (分組卡片為臨時模式, 不持久化) */
  watchlistView:        kv<string>('watchlist_view'),

  /** 自選列表日K蠟燭圖顯示狀態 */
  watchlistCandle:      kv<boolean>('watchlist_showCandle'),

  /** 自選列表分時圖顯示狀態 */
  watchlistIntraday:    kv<boolean>('watchlist_showIntraday'),

  /** 自選列表板塊篩選 */
  watchlistBoardFilter: kv<string[]>('watchlist_boardFilter'),

  /** 自選分組統計條配置 (metric: 統計指標, sort: 排序方式, card*: 分組卡片顯示項) */
  watchlistGroupStats: kv<{ metric: string; sort: string; cardTopN?: number; cardColorBar?: boolean; cardRank?: boolean }>('watchlist_groupStats'),

} as const
