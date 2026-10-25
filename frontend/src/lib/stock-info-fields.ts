/**
 * 個股日K信息條（StockInfoBar Row 2）的指標自定義配置。
 *
 * 與自選列表列配置同源：複用 list-columns 的通用列模型與合併/序列化底座，
 * 僅做純 localStorage 同步持久化（無後端雙寫）。個股預覽彈窗與回測成交K線
 * Modal 共用同一份配置。
 */

import { storage } from '@/lib/storage'
import {
  buildExtColumnsParam as buildExtColumnsParamBase,
  mergeColumns as mergeColumnsBase,
  serializeColumns as serializeColumnsBase,
  type ColumnConfig,
  type ColumnGroup,
} from '@/lib/list-columns'

export type { ColumnConfig, ColumnGroup }

// ===== 內置指標註冊表 =====

export const BUILTIN_INFO_FIELDS: ColumnConfig[] = [
  // 規模
  { id: 'builtin:market_cap', source: { type: 'builtin', key: 'market_cap' }, label: '市值', visible: true, align: 'left' },
  // 成交
  { id: 'builtin:turnover', source: { type: 'builtin', key: 'turnover' }, label: '換手', visible: true, align: 'left' },
  { id: 'builtin:volume', source: { type: 'builtin', key: 'volume' }, label: '成交量', visible: false, align: 'left' },
  { id: 'builtin:amplitude', source: { type: 'builtin', key: 'amplitude' }, label: '振幅', visible: false, align: 'left' },
  // 行情
  { id: 'builtin:open', source: { type: 'builtin', key: 'open' }, label: '開盤', visible: false, align: 'left' },
  { id: 'builtin:high', source: { type: 'builtin', key: 'high' }, label: '最高', visible: false, align: 'left' },
  { id: 'builtin:low', source: { type: 'builtin', key: 'low' }, label: '最低', visible: false, align: 'left' },
]

export const INFO_GROUPS: ColumnGroup[] = [
  { id: 'scale', label: '規模', icon: '🏦', keys: ['market_cap'] },
  { id: 'volume', label: '成交', icon: '📊', keys: ['turnover', 'volume', 'amplitude'] },
  { id: 'quote', label: '行情', icon: '📈', keys: ['open', 'high', 'low'] },
]

// ===== localStorage 持久化 =====

/** 加載信息條指標配置：localStorage → 默認值，自動補齊新增默認項。 */
export function loadInfoFields(): ColumnConfig[] {
  const saved = storage.stockInfoBarFields.get([]) as ColumnConfig[]
  if (saved.length === 0) return [...BUILTIN_INFO_FIELDS]
  return mergeFields(saved, BUILTIN_INFO_FIELDS)
}

/** 保存信息條指標配置到 localStorage。 */
export function saveInfoFields(columns: ColumnConfig[]): void {
  storage.stockInfoBarFields.set(serializeFields(columns))
}

/** 序列化（此處無 pinned/action 列，直接用底座默認實現）。 */
function serializeFields(columns: ColumnConfig[]): ColumnConfig[] {
  return serializeColumnsBase(columns)
}

/** 從信息條字段配置中提取 ext 列參數（逗號分隔 config_id.field_name），用於 klineDaily 接口。 */
export function buildInfoExtColumnsParam(columns: ColumnConfig[]): string {
  return buildExtColumnsParamBase(columns)
}

/** 合併用戶保存的配置與默認配置。 */
function mergeFields(saved: ColumnConfig[], defaults: ColumnConfig[]): ColumnConfig[] {
  // 無固定列，傳入空的 pinnedFirstIds 跳過「代碼置頂」邏輯
  return mergeColumnsBase(saved, defaults, { pinnedFirstIds: [] })
}
