/**
 * 自選列表自定義列配置。
 *
 * 自選頁只保留業務內置列、分組和偏好持久化；通用列模型/合併/擴展列參數
 * 來自 list-columns，策略頁等其它股票列表可複用同一底座。
 */

import { storage } from '@/lib/storage'
import type { VolumeUnit } from '@/lib/volumeUnit'
import {
  buildExtColumnsParam as buildExtColumnsParamBase,
  createExtColumn as createExtColumnBase,
  mergeColumns as mergeColumnsBase,
  serializeColumns as serializeColumnsBase,
  type ColumnConfig,
  type ColumnGroup,
  type ColumnSource,
  type ExtColumnDisplayConfig,
  type CandleColumnConfig,
} from '@/lib/list-columns'

export type { ColumnConfig, ColumnGroup, ColumnSource, ExtColumnDisplayConfig, CandleColumnConfig }

// ===== 內置列註冊表（與當前硬編碼一一對應） =====

export const BUILTIN_COLUMNS: ColumnConfig[] = [
  // 固定列
  { id: 'builtin:symbol', source: { type: 'builtin', key: 'symbol' }, label: '代碼/名稱', visible: true, pinned: true, align: 'left' },
  // 價格
  { id: 'builtin:price', source: { type: 'builtin', key: 'price' }, label: '現價', visible: true, align: 'center' },
  { id: 'builtin:pct', source: { type: 'builtin', key: 'pct' }, label: '漲跌幅', visible: true, align: 'center' },
  { id: 'builtin:change_amount', source: { type: 'builtin', key: 'change_amount' }, label: '漲跌額', visible: false, align: 'center' },
  { id: 'builtin:amplitude', source: { type: 'builtin', key: 'amplitude' }, label: '振幅', visible: false, align: 'center' },
  // 成交
  { id: 'builtin:turnover', source: { type: 'builtin', key: 'turnover' }, label: '換手率', visible: true, align: 'center' },
  { id: 'builtin:amount', source: { type: 'builtin', key: 'amount' }, label: '成交額', visible: false, align: 'center' },
  { id: 'builtin:float_val', source: { type: 'builtin', key: 'float_val' }, label: '流通值', visible: false, align: 'center' },
  { id: 'builtin:vol_ratio', source: { type: 'builtin', key: 'vol_ratio' }, label: '量比', visible: true, align: 'center' },
  { id: 'builtin:annual_vol', source: { type: 'builtin', key: 'annual_vol' }, label: '年化波動', visible: false, align: 'center' },
  // 均線
  { id: 'builtin:ma5', source: { type: 'builtin', key: 'ma5' }, label: 'MA5', visible: false, align: 'center' },
  { id: 'builtin:ma10', source: { type: 'builtin', key: 'ma10' }, label: 'MA10', visible: false, align: 'center' },
  { id: 'builtin:ma20', source: { type: 'builtin', key: 'ma20' }, label: 'MA20', visible: false, align: 'center' },
  { id: 'builtin:ma60', source: { type: 'builtin', key: 'ma60' }, label: 'MA60', visible: false, align: 'center' },
  // 區間
  { id: 'builtin:high_60d', source: { type: 'builtin', key: 'high_60d' }, label: '60日高', visible: false, align: 'center' },
  { id: 'builtin:low_60d', source: { type: 'builtin', key: 'low_60d' }, label: '60日低', visible: false, align: 'center' },
  // 技術指標
  { id: 'builtin:rsi6', source: { type: 'builtin', key: 'rsi6' }, label: 'RSI6', visible: false, align: 'center' },
  { id: 'builtin:rsi14', source: { type: 'builtin', key: 'rsi14' }, label: 'RSI14', visible: true, align: 'center' },
  { id: 'builtin:rsi24', source: { type: 'builtin', key: 'rsi24' }, label: 'RSI24', visible: false, align: 'center' },
  { id: 'builtin:macd_dif', source: { type: 'builtin', key: 'macd_dif' }, label: 'MACD-DIF', visible: false, align: 'center' },
  { id: 'builtin:macd_dea', source: { type: 'builtin', key: 'macd_dea' }, label: 'MACD-DEA', visible: false, align: 'center' },
  { id: 'builtin:macd_hist', source: { type: 'builtin', key: 'macd_hist' }, label: 'MACD柱', visible: false, align: 'center' },
  { id: 'builtin:kdj_k', source: { type: 'builtin', key: 'kdj_k' }, label: 'KDJ-K', visible: false, align: 'center' },
  { id: 'builtin:kdj_d', source: { type: 'builtin', key: 'kdj_d' }, label: 'KDJ-D', visible: false, align: 'center' },
  { id: 'builtin:kdj_j', source: { type: 'builtin', key: 'kdj_j' }, label: 'KDJ-J', visible: false, align: 'center' },
  { id: 'builtin:boll_upper', source: { type: 'builtin', key: 'boll_upper' }, label: '布林上軌', visible: false, align: 'center' },
  { id: 'builtin:boll_lower', source: { type: 'builtin', key: 'boll_lower' }, label: '布林下軌', visible: false, align: 'center' },
  { id: 'builtin:atr14', source: { type: 'builtin', key: 'atr14' }, label: 'ATR14', visible: false, align: 'center' },
  { id: 'builtin:vol_ma5', source: { type: 'builtin', key: 'vol_ma5' }, label: '量MA5', visible: false, align: 'center' },
  { id: 'builtin:vol_ma10', source: { type: 'builtin', key: 'vol_ma10' }, label: '量MA10', visible: false, align: 'center' },
  // 動量
  { id: 'builtin:momentum_5d', source: { type: 'builtin', key: 'momentum_5d' }, label: '5D 動量', visible: false, align: 'center' },
  { id: 'builtin:momentum_10d', source: { type: 'builtin', key: 'momentum_10d' }, label: '10D 動量', visible: false, align: 'center' },
  { id: 'builtin:momentum_20d', source: { type: 'builtin', key: 'momentum_20d' }, label: '20D 動量', visible: false, align: 'center' },
  { id: 'builtin:momentum_30d', source: { type: 'builtin', key: 'momentum_30d' }, label: '30D 動量', visible: false, align: 'center' },
  { id: 'builtin:momentum_60d', source: { type: 'builtin', key: 'momentum_60d' }, label: '60D 動量', visible: false, align: 'center' },
  // Consecutive limit-up/down day counts (consecutive_limit_ups/downs); default-off columns.
  { id: 'builtin:limit_ups', source: { type: 'builtin', key: 'limit_ups' }, label: '連續漲停', visible: false, align: 'center' },
  { id: 'builtin:limit_downs', source: { type: 'builtin', key: 'limit_downs' }, label: '連續跌停', visible: false, align: 'center' },
  // 訊號 & 圖表
  { id: 'builtin:signals', source: { type: 'builtin', key: 'signals' }, label: '訊號', visible: true, align: 'center' },
  { id: 'builtin:candle', source: { type: 'builtin', key: 'candle' }, label: '日 K', visible: false, align: 'center' },
  { id: 'builtin:intraday', source: { type: 'builtin', key: 'intraday' }, label: '分時', visible: false, align: 'center' },
  // No financial columns: /api/watchlist/enriched carries no financial fields for either market.
]

export const COLUMN_GROUPS: ColumnGroup[] = [
  { id: 'price', label: '價格', icon: '💰', keys: ['price', 'pct', 'change_amount', 'amplitude'] },
  { id: 'volume', label: '成交', icon: '📊', keys: ['turnover', 'amount', 'float_val', 'vol_ratio', 'annual_vol'] },
  { id: 'ma', label: '均線', icon: '📈', keys: ['ma5', 'ma10', 'ma20', 'ma60'] },
  { id: 'range', label: '區間', icon: '📏', keys: ['high_60d', 'low_60d'] },
  { id: 'tech', label: '技術指標', icon: '🔬', keys: ['rsi6', 'rsi14', 'rsi24', 'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j', 'boll_upper', 'boll_lower', 'atr14', 'vol_ma5', 'vol_ma10'] },
  { id: 'momentum', label: '動量', icon: '🚀', keys: ['momentum_5d', 'momentum_10d', 'momentum_20d', 'momentum_30d', 'momentum_60d'] },
  { id: 'limit', label: '連續漲跌停', icon: '🔥', keys: ['limit_ups', 'limit_downs'] },
  { id: 'signal', label: '訊號', icon: '📡', keys: ['signals', 'candle', 'intraday'] },
]

// ===== 篩選單位 =====

export interface FilterUnit {
  label: string
  toRaw: (input: number) => number
}

const PERCENT_OF_DECIMAL: FilterUnit = { label: '%', toRaw: v => v / 100 }
const PERCENT_VALUE: FilterUnit = { label: '%', toRaw: v => v }
const HUNDRED_MILLION: FilterUnit = { label: '億', toRaw: v => v * 100_000_000 }
const LOTS: FilterUnit = { label: '張', toRaw: v => v * 1000 }
const SHARES: FilterUnit = { label: '股', toRaw: v => v }
const PRICE: FilterUnit = { label: '元', toRaw: v => v }

/** Filter input unit per builtin key, matching how stock-table/primitives.tsx displays the column. */
const FILTER_UNITS: Record<string, FilterUnit> = {
  price: PRICE, change_amount: PRICE,
  pct: PERCENT_OF_DECIMAL, amplitude: PERCENT_OF_DECIMAL, annual_vol: PERCENT_OF_DECIMAL,
  momentum_5d: PERCENT_OF_DECIMAL, momentum_10d: PERCENT_OF_DECIMAL, momentum_20d: PERCENT_OF_DECIMAL,
  momentum_30d: PERCENT_OF_DECIMAL, momentum_60d: PERCENT_OF_DECIMAL,
  // turnover_rate is already a percent value (5 = 5%), CONTRIBUTING.md 3.1.
  turnover: PERCENT_VALUE,
  amount: HUNDRED_MILLION, float_val: HUNDRED_MILLION,
  ma5: PRICE, ma10: PRICE, ma20: PRICE, ma60: PRICE, high_60d: PRICE, low_60d: PRICE,
  boll_upper: PRICE, boll_lower: PRICE, atr14: PRICE,
}

// Volume columns render in the user's volume unit (VolumeValue), so their filters follow it.
const VOLUME_KEYS = new Set(['vol_ma5', 'vol_ma10'])

/** Unit of a column's filter input; null when the input is compared as the raw value. */
export function filterUnit(col: ColumnConfig, volumeUnit: VolumeUnit = 'lots'): FilterUnit | null {
  if (col.source.type !== 'builtin') return null
  if (VOLUME_KEYS.has(col.source.key)) return volumeUnit === 'lots' ? LOTS : SHARES
  return FILTER_UNITS[col.source.key] ?? null
}

/** A typed filter bound converted to the row value's scale. */
export function filterBoundToRaw(col: ColumnConfig, input: string, volumeUnit: VolumeUnit = 'lots'): number {
  const value = Number(input)
  const unit = filterUnit(col, volumeUnit)
  return unit ? unit.toRaw(value) : value
}

// 操作列（始終顯示，不參與自定義）
export const ACTION_COLUMN_ID = 'builtin:action'

// ===== localStorage 持久化 =====

/** 序列化列配置（只保存用戶可自定義的列，排除 pinned 和 action） */
export function serializeColumns(columns: ColumnConfig[]): ColumnConfig[] {
  return serializeColumnsBase(columns, ACTION_COLUMN_ID)
}

// Holds the time of a local column change the backend has not accepted yet.
const UNSYNCED_COLUMNS_KEY = 'watchlist_columns_unsynced'
// The backend copy as of the last successful read or write. The backend keeps no updated_at,
// so a backend copy equal to this base means nobody changed it since and the local change may win.
const SYNCED_BASE_KEY = 'watchlist_columns_synced_base'

function columnsKey(columns: ColumnConfig[]): string {
  return JSON.stringify(serializeColumns(columns))
}

function recordSyncedBase(columns: ColumnConfig[]): void {
  try {
    localStorage.setItem(SYNCED_BASE_KEY, columnsKey(columns))
  } catch { /* storage unavailable */ }
}

function syncedBase(): string | null {
  try {
    return localStorage.getItem(SYNCED_BASE_KEY)
  } catch {
    return null
  }
}

function markColumnsUnsynced(unsynced: boolean): void {
  try {
    if (unsynced) localStorage.setItem(UNSYNCED_COLUMNS_KEY, String(Date.now()))
    else localStorage.removeItem(UNSYNCED_COLUMNS_KEY)
  } catch { /* storage unavailable */ }
}

function columnsUnsynced(): boolean {
  try {
    return localStorage.getItem(UNSYNCED_COLUMNS_KEY) !== null
  } catch {
    return false
  }
}

let latestSave = 0

/** 序列化並保存到後端 + localStorage */
export async function saveColumnConfig(columns: ColumnConfig[]): Promise<void> {
  const saveable = serializeColumns(columns)
  const save = ++latestSave
  // 同時寫 localStorage（即時）和後端（持久化）
  storage.watchlistColumns.set(saveable)
  markColumnsUnsynced(true)
  try {
    const { api } = await import('@/lib/api')
    await api.updateWatchlistColumns(saveable)
    // An older request finishing last must not clear the flag of a newer unsaved one.
    if (save === latestSave) {
      recordSyncedBase(saveable)
      markColumnsUnsynced(false)
    }
  } catch {
    // 後端不可用時 localStorage 仍有效
  }
}

export interface LoadColumnConfigOptions {
  /** Called when the backend changed elsewhere and its copy replaced a local change that never reached it. */
  onUnsyncedDiscarded?: () => void
}

/** 加載列配置：優先後端（本機有未同步修改且後端自上次同步未變時回寫本機版本），回退 localStorage，最終用默認值 */
export async function loadColumnConfig(options: LoadColumnConfigOptions = {}): Promise<ColumnConfig[]> {
  // 1. 嘗試從後端加載
  try {
    const { api } = await import('@/lib/api')
    const res = await api.watchlistColumns()
    if (res.columns && res.columns.length > 0) {
      const merged = mergeColumns(res.columns, BUILTIN_COLUMNS)
      const local = storage.watchlistColumns.get([]) as ColumnConfig[]
      if (columnsUnsynced() && local.length > 0) {
        const localMerged = mergeColumns(local, BUILTIN_COLUMNS)
        if (columnsKey(merged) === syncedBase()) {
          await saveColumnConfig(localMerged)
          return localMerged
        }
        if (columnsKey(localMerged) !== columnsKey(merged)) options.onUnsyncedDiscarded?.()
      }
      markColumnsUnsynced(false)
      recordSyncedBase(merged)
      // 同步到 localStorage
      storage.watchlistColumns.set(serializeColumns(merged))
      return merged
    }
  } catch {
    // 後端不可用，繼續嘗試 localStorage
  }

  // 2. 嘗試從 localStorage 加載
  const saved = storage.watchlistColumns.get([]) as ColumnConfig[]
  if (saved.length > 0) {
    return mergeColumns(saved, BUILTIN_COLUMNS)
  }

  // 3. 默認值
  return [...BUILTIN_COLUMNS]
}

/** 合併用戶保存的列與默認列 */
function mergeColumns(saved: ColumnConfig[], defaults: ColumnConfig[]): ColumnConfig[] {
  return mergeColumnsBase(saved, defaults, { actionColumnId: ACTION_COLUMN_ID })
}

/** 從列配置中提取 ext 列參數，用於後端 enriched 接口 */
export function buildExtColumnsParam(columns: ColumnConfig[]): string {
  return buildExtColumnsParamBase(columns)
}

/** 根據 ext schema 數據創建 ext 列配置 */
export function createExtColumn(
  configId: string,
  configLabel: string,
  fieldName: string,
  fieldLabel?: string,
): ColumnConfig {
  return createExtColumnBase(configId, configLabel, fieldName, fieldLabel)
}
