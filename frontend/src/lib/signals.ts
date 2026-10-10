/**
 * 買賣觸發器訊號定義 — 選股頁彈窗 / 回測頁共用。
 *
 * 訊號 ID 必須與後端 backtest/strategy.py:_build_signal_mask 對齊
 * (signal_* 前綴為內置原子訊號, csg_ 前綴為用戶自定義訊號)。
 */

export type SignalKind = 'entry' | 'exit' | 'both'

export interface BuiltinSignalDefinition {
  id: string
  name: string
  kind: SignalKind
  category: string
  description: string
}

/** 內置原子訊號清單 (權威展示來源, 兩頁統一)
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
  {
    id: 'signal_limit_up',
    name: '漲停',
    kind: 'entry',
    category: '漲跌停',
    description: '收盤價達當日漲停價，依標的自身的漲跌幅限制與跳動單位判定。',
  },
  {
    id: 'signal_limit_down',
    name: '跌停',
    kind: 'exit',
    category: '漲跌停',
    description: '收盤價達當日跌停價，依標的自身的漲跌幅限制與跳動單位判定。',
  },
  {
    id: 'signal_broken_limit_up',
    name: '漲停打開',
    kind: 'exit',
    category: '漲跌停',
    description: '盤中觸及漲停價但收盤未守住。',
  },
  {
    id: 'signal_limit_down_recovery',
    name: '跌停翻紅',
    kind: 'entry',
    category: '漲跌停',
    description: '盤中觸及跌停價後收盤回升至跌停之上。',
  },
]

export const MONITOR_INTRADAY_SIGNAL_LABELS: Record<string, string> = {
  signal_intraday_avg_cross_up: '分時價格上穿均價',
  signal_intraday_avg_cross_down: '分時價格下穿均價',
  signal_intraday_zero_cross_up: '分時價格上穿0軸',
  signal_intraday_zero_cross_down: '分時價格下穿0軸',
}

export const MONITOR_INTRADAY_SIGNAL_OPTIONS = Object.keys(MONITOR_INTRADAY_SIGNAL_LABELS)

/** 內置原子訊號 → 中文標籤 */
export const SIGNAL_LABELS: Record<string, string> = BUILTIN_SIGNAL_DEFINITIONS.reduce<Record<string, string>>((acc, sig) => {
  acc[sig.id] = sig.name
  return acc
}, { ...MONITOR_INTRADAY_SIGNAL_LABELS })

/** 內置訊號 ID 列表 */
export const SIGNAL_OPTIONS = BUILTIN_SIGNAL_DEFINITIONS.map(sig => sig.id)

/** Ids a builtin strategy emits for its own condition. No enriched column carries them, so they
 * stay out of SIGNAL_OPTIONS. Keep in step with FIELD_LABELS in backend/app/strategy/field_labels.py. */
const STRATEGY_SIGNAL_LABELS: Record<string, string> = {
  signal_bullish_alignment_entry: '均線多頭排列',
  signal_pullback_ma20_bounce_entry: '回踩MA20反彈',
  signal_pullback_to_support_entry: '縮量回踩MA20',
  signal_boll_squeeze_breakout: '布林收斂突破',
  signal_boll_mid_break: '收盤低於布林中軌',
  signal_capitulation_reversal: '爆量止跌反轉',
  signal_close_20d_low: '收盤創20日新低',
  signal_consolidation_breakout: '整理區間突破',
  signal_ma20_break: '收盤低於MA20',
  signal_factor_rank_entry: '因子排名進場',
  signal_factor_rank_exit: '因子排名出場',
  signal_lorentzian_bullish: 'Lorentzian看多',
  signal_lorentzian_exit: 'Lorentzian出場',
  signal_pullback_resume: '回檔再起漲',
  signal_swing_structure_bullish: '波段結構轉多',
  signal_swing_structure_exit: '波段結構轉弱',
  signal_n_day_low_reclaim: '跌破60日低點後收回',
  signal_low_volatility_leader_entry: '低波動多頭',
}

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
  high_60d: '60日最高', low_60d: '60日最低', distance_from_low_60d: '距60日低點',
  momentum_5d: '5日動量', momentum_20d: '20日動量', momentum_60d: '60日動量',
  atr_14: 'ATR14', annual_vol_20d: '20日年化波動',
  consecutive_limit_ups: '連續漲停', consecutive_limit_downs: '連續跌停',
  distance_to_upper_limit: '距漲停距離', distance_to_lower_limit: '距跌停距離',
}

/** Display name for a signal or field id; customNames maps csg_ ids, unknown ids come back unchanged. */
export function cnSignal(name: string, customNames?: Record<string, string>): string {
  if (customNames && name in customNames) return customNames[name]
  return SIGNAL_LABELS[name] ?? STRATEGY_SIGNAL_LABELS[name] ?? FIELD_LABELS[name] ?? name
}

export interface AbnormalSignalDefinition {
  id: string
  name: string
  category: string
  description: string
  /** Trigger expression, mirroring abnormal_diagnostics.py. Keep both in step. */
  formula: string
  /** History the signal needs before it is evaluated at all; absent when it needs none. */
  dataRequirement?: string
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
    formula: '成交量 ÷ 前 5 日平均成交量 ≥ 2.0',
    dataRequirement: '前 5 個交易日全部要有成交量資料',
  },
  {
    id: 'TURNOVER_SPIKE',
    name: '成交額放大',
    category: '量能',
    description: '成交金額達前 5 個交易日均額的 2 倍以上。',
    formula: '成交金額 ÷ 前 5 日平均成交金額 ≥ 2.0',
    dataRequirement: '前 5 個交易日全部要有成交金額資料',
  },
  {
    id: 'PRICE_MOVE',
    name: '價格異動',
    category: '價格',
    description: '單日漲跌幅絕對值達 5% 以上，槓桿型 ETF 會先除以槓桿倍數再判定。',
    formula: '|漲跌幅| ÷ |槓桿倍數| ≥ 5%',
  },
  {
    id: 'FOREIGN_FLOW_SPIKE',
    name: '外資異常買賣超',
    category: '籌碼',
    description: '外資買賣超達前 20 日平均的 3 倍，且絕對量至少 10 萬股。',
    formula: '|外資買賣超| ≥ 100,000 股　且　|外資買賣超| ÷ 前 20 日 |買賣超| 平均 ≥ 3.0',
    dataRequirement: '前 20 日至少 15 日有法人資料，且該平均本身 ≥ 10,000 股',
  },
  {
    id: 'TRUST_FLOW_SPIKE',
    name: '投信異常買賣超',
    category: '籌碼',
    description: '投信買賣超達前 20 日平均的 3 倍，且絕對量至少 5 萬股。',
    formula: '|投信買賣超| ≥ 50,000 股　且　|投信買賣超| ÷ 前 20 日 |買賣超| 平均 ≥ 3.0',
    dataRequirement: '前 20 日至少 15 日有法人資料，且該平均本身 ≥ 5,000 股',
  },
  {
    id: 'MARGIN_SURGE',
    name: '融資激增',
    category: '籌碼',
    description: '融資增減至少 50 萬股，且達前 20 日中位數的 3 倍。',
    formula: '|融資增減| ≥ 500,000 股　且　|融資增減| ÷ 前 20 日 |增減| 中位數 ≥ 3.0',
    dataRequirement: '前 20 日至少 15 日有融資融券資料',
  },
  {
    id: 'SHORT_SURGE',
    name: '融券激增',
    category: '籌碼',
    description: '融券增減至少 10 萬股，且達前 20 日中位數的 3 倍。',
    formula: '|融券增減| ≥ 100,000 股　且　|融券增減| ÷ 前 20 日 |增減| 中位數 ≥ 3.0',
    dataRequirement: '前 20 日至少 15 日有融資融券資料',
  },
  {
    id: 'SHORT_MARGIN_RATIO_SPIKE',
    name: '券資比驟升',
    category: '籌碼',
    description: '券資比較前 20 日中位數上升 5 個百分點以上。',
    formula: '券資比 − 前 20 日券資比中位數 ≥ 5 個百分點',
    dataRequirement: '前 20 日至少 15 日有融資融券資料',
  },
  {
    id: 'PRICE_FLOW_DIVERGENCE',
    name: '價量與法人背離',
    category: '背離',
    description:
      '漲跌幅達 3% 以上，但外資同時反向大額進出：上漲時外資大賣、下跌時外資大買。',
    formula:
      '|漲跌幅| ÷ |槓桿倍數| ≥ 3%　且　反向外資買賣超 ≥ 200,000 股　且　該買賣超達前 20 日 |買賣超| 平均的 2 倍',
    dataRequirement: '前 20 日至少 15 日有法人資料，且該平均本身 ≥ 10,000 股',
  },
  {
    id: 'RELATIVE_STRENGTH_OUTLIER',
    name: '產業相對強弱異常',
    category: '相對強弱',
    description: '個股與所屬產業的 5 日報酬率差距達 10 個百分點以上。',
    formula: '|個股 5 日報酬率 − 所屬產業等權重 5 日報酬率| ≥ 10 個百分點',
    dataRequirement: 'ETF 結構性不適用（無所屬產業），不列入其觸發與未觸發統計',
  },
]

export const ABNORMAL_SIGNAL_LABELS: Record<string, string> =
  ABNORMAL_SIGNAL_DEFINITIONS.reduce<Record<string, string>>((acc, sig) => {
    acc[sig.id] = sig.name
    return acc
  }, {})

export interface VolumePatternDefinition {
  id: string
  name: string
  /** Trigger expression, mirroring backend taiwan/volume_patterns.py. Keep both in step. */
  formula: string
}

/** Volume-price patterns; ids are the published vp_* columns. */
export const VOLUME_PATTERN_DEFINITIONS: VolumePatternDefinition[] = [
  { id: 'vp_double', name: '倍量', formula: '今日量 ≥ 前一交易日量 2 倍；兩日皆須有成交（量 > 0）' },
  { id: 'vp_step_up', name: '梯量', formula: '連 3 個交易日量遞增，且今日收盤 > 2 日前收盤；3 日皆須有成交（量 > 0）' },
  { id: 'vp_step_down', name: '縮量', formula: '連 3 個交易日量遞減，且今日收盤 < 2 日前收盤；3 日皆須有成交（量 > 0）' },
  { id: 'vp_low', name: '低量', formula: '今日量 < 前 20 個交易日中有成交日均量的 60%；今日須有成交，前 20 日至少 15 日有成交（量 > 0）' },
  { id: 'vp_flat', name: '平量', formula: '連 3 個交易日 (最大量−最小量)／最大量 < 15%；3 日皆須有成交（量 > 0）' },
  {
    id: 'vp_up_stall',
    name: '放量滯漲',
    formula: '今日量 ≥ 前 5 個交易日均量 1.5 倍，且符合任一：收盤 < 開盤；實體 > 0 且上影 > 2 倍實體；0 ≤ 漲幅 < 1%。今日與前 5 日皆須有成交（量 > 0）',
  },
]
