/**
 * 買賣觸發器信號定義 — 選股頁彈窗 / 回測頁共用。
 *
 * 信號 ID 必須與後端 backtest/strategy.py:_build_signal_mask 對齊
 * (signal_* 前綴為內置原子信號, csg_ 前綴為用戶自定義信號)。
 */

export type SignalKind = 'entry' | 'exit' | 'both'

export interface BuiltinSignalDefinition {
  id: string
  name: string
  kind: SignalKind
  category: string
  description: string
}

/** 內置原子信號清單 (權威展示來源, 兩頁統一)
 *
 * TAIWAN_LOCALIZATION_POLISH: name/category/description 全面改為正體中文，
 * 並把「連板」相關描述改為依實際語意翻譯的「連續漲停」，不再是 A 股「連板」
 * 慣例術語直接透傳；id 全數不變 (與 backend backtest/strategy.py 及既有
 * saved rule/策略 identifier 對齊)。
 */
export const BUILTIN_SIGNAL_DEFINITIONS: BuiltinSignalDefinition[] = [
  {
    id: 'signal_ma_golden_5_20',
    name: 'MA5上穿MA20',
    kind: 'entry',
    category: '均線',
    description: '短期均線 MA5 上穿中期均線 MA20，常用於趨勢轉強確認。',
  },
  {
    id: 'signal_ma_dead_5_20',
    name: 'MA5下穿MA20',
    kind: 'exit',
    category: '均線',
    description: '短期均線 MA5 下穿中期均線 MA20，常用於趨勢轉弱或止盈止損。',
  },
  {
    id: 'signal_ma_golden_20_60',
    name: 'MA20上穿MA60',
    kind: 'entry',
    category: '均線',
    description: '中期均線 MA20 上穿長期均線 MA60，偏中線趨勢訊號。',
  },
  {
    id: 'signal_macd_golden',
    name: 'MACD金叉',
    kind: 'entry',
    category: 'MACD',
    description: 'MACD DIF 上穿 DEA，表示動能可能由弱轉強。',
  },
  {
    id: 'signal_macd_dead',
    name: 'MACD死叉',
    kind: 'exit',
    category: 'MACD',
    description: 'MACD DIF 下穿 DEA，表示動能可能由強轉弱。',
  },
  {
    id: 'signal_ma20_breakout',
    name: '突破MA20',
    kind: 'entry',
    category: '趨勢',
    description: '收盤價向上突破 MA20，常用於趨勢突破買點。',
  },
  {
    id: 'signal_ma20_breakdown',
    name: '跌破MA20',
    kind: 'exit',
    category: '趨勢',
    description: '收盤價向下跌破 MA20，常用於趨勢破位賣點。',
  },
  {
    id: 'signal_ma5_breakout',
    name: '突破MA5',
    kind: 'entry',
    category: '趨勢',
    description: '收盤價向上突破 MA5，偏短線轉強或回踩企穩買點。',
  },
  {
    id: 'signal_ma5_breakdown',
    name: '跌破MA5',
    kind: 'exit',
    category: '趨勢',
    description: '收盤價向下跌破 MA5，偏短線轉弱或止盈止損賣點。',
  },
  {
    id: 'signal_ma10_breakout',
    name: '突破MA10',
    kind: 'entry',
    category: '趨勢',
    description: '收盤價向上突破 MA10，偏短中線轉強或突破買點。',
  },
  {
    id: 'signal_ma10_breakdown',
    name: '跌破MA10',
    kind: 'exit',
    category: '趨勢',
    description: '收盤價向下跌破 MA10，偏短中線轉弱或破位賣點。',
  },
  {
    id: 'signal_n_day_high',
    name: '60日新高',
    kind: 'entry',
    category: '趨勢',
    description: '收盤價創近 60 日新高，表示階段強勢或突破。',
  },
  {
    id: 'signal_n_day_low',
    name: '60日新低',
    kind: 'exit',
    category: '趨勢',
    description: '收盤價創近 60 日新低，表示階段弱勢或風險釋放。',
  },
  {
    id: 'signal_boll_breakout_upper',
    name: '突破布林上軌',
    kind: 'entry',
    category: 'BOLL',
    description: '價格突破布林上軌，偏強勢突破或加速訊號。',
  },
  {
    id: 'signal_boll_breakdown_lower',
    name: '跌破布林下軌',
    kind: 'exit',
    category: 'BOLL',
    description: '價格跌破布林下軌，偏弱勢破位或超跌風險訊號。',
  },
  {
    id: 'signal_volume_surge',
    name: '放量',
    kind: 'both',
    category: '量價',
    description: '成交量顯著放大，可作為入場確認、出場確認或告警條件。',
  },
  // The four limit-board signals were dropped: their upstream computation needs the
  // A-share instrument table for limit prices and is skipped for Taiwan symbols.
]

export const MONITOR_INTRADAY_SIGNAL_LABELS: Record<string, string> = {
  signal_intraday_avg_cross_up: '分時價格上穿均價',
  signal_intraday_avg_cross_down: '分時價格下穿均價',
  signal_intraday_zero_cross_up: '分時價格上穿0軸',
  signal_intraday_zero_cross_down: '分時價格下穿0軸',
}

export const MONITOR_INTRADAY_SIGNAL_OPTIONS = Object.keys(MONITOR_INTRADAY_SIGNAL_LABELS)

/** 內置原子信號 → 中文標籤 */
export const SIGNAL_LABELS: Record<string, string> = BUILTIN_SIGNAL_DEFINITIONS.reduce<Record<string, string>>((acc, sig) => {
  acc[sig.id] = sig.name
  return acc
}, { ...MONITOR_INTRADAY_SIGNAL_LABELS })

/** 內置信號 ID 列表 */
export const SIGNAL_OPTIONS = BUILTIN_SIGNAL_DEFINITIONS.map(sig => sig.id)

/** 常用技術指標/字段 → 中文 (閾值條件展示用, 與後端 ENRICHED_COLUMNS 對齊) */
const FIELD_LABELS: Record<string, string> = {
  close: '收盤價', open: '開盤價', high: '最高價', low: '最低價',
  change_pct: '漲跌幅', change_amount: '漲跌額', amplitude: '振幅',
  turnover_rate: '換手率', volume: '成交量', amount: '成交額',
  ma5: 'MA5', ma10: 'MA10', ma20: 'MA20', ma30: 'MA30', ma60: 'MA60',
  ema5: 'EMA5', ema10: 'EMA10', ema20: 'EMA20',
  macd_dif: 'MACD-DIF', macd_dea: 'MACD-DEA', macd_hist: 'MACD柱',
  boll_upper: '布林上軌', boll_lower: '布林下軌',
  kdj_k: 'KDJ-K', kdj_d: 'KDJ-D', kdj_j: 'KDJ-J',
  rsi_6: 'RSI6', rsi_14: 'RSI14', rsi_24: 'RSI24',
  vol_ratio_5d: '5日量比', vol_ratio_20d: '20日量比',
  vol_ma5: '5日均量', vol_ma10: '10日均量',
  high_60d: '60日最高', low_60d: '60日最低',
  momentum_5d: '5日動量', momentum_20d: '20日動量', momentum_60d: '60日動量',
  atr_14: 'ATR14', annual_vol_20d: '20日年化波動',
  consecutive_limit_ups: '連續漲停', consecutive_limit_downs: '連續跌停',
}

/**
 * 信號/字段 ID → 中文顯示名。
 * 內置信號查 SIGNAL_LABELS; csg_ 前綴查傳入的自定義信號名稱映射;
 * 技術指標查 FIELD_LABELS; 都找不到則原樣返回。
 */
export function cnSignal(name: string, customNames?: Record<string, string>): string {
  if (customNames && name in customNames) return customNames[name]
  return SIGNAL_LABELS[name] ?? FIELD_LABELS[name] ?? name
}

export interface AbnormalSignalDefinition {
  id: string
  name: string
  category: string
  description: string
}

/** Abnormal move and capital flow diagnostics.
 *
 * Computed per trading day from the daily, institutional and margin stores, so
 * these are post-close statistics rather than the technical signals above. The
 * ids must match the backend's signal type strings.
 */
export const ABNORMAL_SIGNAL_DEFINITIONS: AbnormalSignalDefinition[] = [
  {
    id: 'VOLUME_SPIKE',
    name: '爆量',
    category: '量能',
    description: '成交量達前 5 個交易日均量的 2 倍以上。',
  },
  {
    id: 'TURNOVER_SPIKE',
    name: '成交額放大',
    category: '量能',
    description: '成交金額達前 5 個交易日均額的 2 倍以上。',
  },
  {
    id: 'PRICE_MOVE',
    name: '價格異動',
    category: '價格',
    description: '單日漲跌幅絕對值達 5% 以上，槓桿型 ETF 會先除以槓桿倍數再判定。',
  },
  {
    id: 'FOREIGN_FLOW_SPIKE',
    name: '外資異常買賣超',
    category: '籌碼',
    description: '外資買賣超達前 20 日平均的 3 倍，且絕對量至少 10 萬股。',
  },
  {
    id: 'TRUST_FLOW_SPIKE',
    name: '投信異常買賣超',
    category: '籌碼',
    description: '投信買賣超達前 20 日平均的 3 倍，且絕對量至少 5 萬股。',
  },
  {
    id: 'MARGIN_SURGE',
    name: '融資激增',
    category: '籌碼',
    description: '融資增減至少 50 萬股，且達前 20 日中位數的 3 倍。',
  },
  {
    id: 'SHORT_SURGE',
    name: '融券激增',
    category: '籌碼',
    description: '融券增減至少 10 萬股，且達前 20 日中位數的 3 倍。',
  },
  {
    id: 'SHORT_MARGIN_RATIO_SPIKE',
    name: '券資比驟升',
    category: '籌碼',
    description: '券資比較前 20 日中位數上升 5 個百分點以上。',
  },
  {
    id: 'PRICE_FLOW_DIVERGENCE',
    name: '價量與法人背離',
    category: '背離',
    description: '漲跌幅達 3% 以上，但法人資金流向與價格方向相反。',
  },
  {
    id: 'RELATIVE_STRENGTH_OUTLIER',
    name: '產業相對強弱異常',
    category: '相對強弱',
    description: '個股與所屬產業的 5 日報酬率差距達 10 個百分點以上。',
  },
]

export const ABNORMAL_SIGNAL_LABELS: Record<string, string> =
  ABNORMAL_SIGNAL_DEFINITIONS.reduce<Record<string, string>>((acc, sig) => {
    acc[sig.id] = sig.name
    return acc
  }, {})
