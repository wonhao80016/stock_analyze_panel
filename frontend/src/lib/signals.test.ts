// TAIWAN_LOCALIZATION_POLISH — Signal Library 正體化回歸測試
//
// BUILTIN_SIGNAL_DEFINITIONS 是「訊號庫」設定頁 / SignalPicker (Screener) /
// RuleEditor (Monitor) 共用的唯一權威來源 (frontend/src/lib/signals.ts 頭部
// 註解: "權威展示來源, 兩頁統一")。本次只改 name/category/description 的顯示
// 文字, id 完全不變 (與 backend backtest/strategy.py 及已保存的 rule/策略
// identifier 對齊, 也是 Monitor rule 的 saved condition.field 值)。
import { describe, expect, it } from 'vitest'
import {
  BUILTIN_SIGNAL_DEFINITIONS,
  MONITOR_INTRADAY_SIGNAL_LABELS,
  SIGNAL_LABELS,
  SIGNAL_OPTIONS,
  cnSignal,
  ABNORMAL_SIGNAL_DEFINITIONS,
} from './signals'

// 這份 id 清單鎖定內建訊號集合不被意外增減/改名。漲跌停四訊號由
// TaiwanEnrichedRefreshService 依台股法定漲跌幅與跳動單位自行計算。
const EXPECTED_BUILTIN_IDS = [
  'signal_ma_golden_5_20',
  'signal_ma_dead_5_20',
  'signal_ma_golden_20_60',
  'signal_macd_golden',
  'signal_macd_dead',
  'signal_ma20_breakout',
  'signal_ma20_breakdown',
  'signal_ma5_breakout',
  'signal_ma5_breakdown',
  'signal_ma10_breakout',
  'signal_ma10_breakdown',
  'signal_n_day_high',
  'signal_n_day_low',
  'signal_boll_breakout_upper',
  'signal_boll_breakdown_lower',
  'signal_volume_surge',
  'signal_limit_up',
  'signal_limit_down',
  'signal_limit_down_recovery',
  'signal_broken_limit_up',
]

// 已知在 audit 中發現、且本批次應已處理掉的簡體字/A股慣用語彙（用「連續漲停」
// 取代裸「连板」是本批次刻意的語意修正，不是單純字元轉換）。
const KNOWN_SIMPLIFIED_OR_A_SHARE_TERMS = [
  '趋势', '强势', '强转弱', '信号', '市场', '资金', '连板', '动能', '确认',
  '开盘价', '收盘价', '涨跌幅', '涨跌额', '换手率', '成交额', '显著', '风险',
  '阶段', '价格', '布林上轨', '布林下轨', '短线', '中线', '长期', '止盈止损',
]

describe('BUILTIN_SIGNAL_DEFINITIONS — ids unchanged', () => {
  it('has exactly the 20 built-in signal ids the publisher can produce', () => {
    expect(BUILTIN_SIGNAL_DEFINITIONS.map(s => s.id).sort()).toEqual([...EXPECTED_BUILTIN_IDS].sort())
  })

  it('SIGNAL_OPTIONS (consumed by SignalPicker/RuleEditor) still exposes exactly these ids', () => {
    expect(SIGNAL_OPTIONS.sort()).toEqual([...EXPECTED_BUILTIN_IDS].sort())
  })

  it('SIGNAL_LABELS keys still match the original ids (RuleEditor/SignalPicker lookups by id keep working)', () => {
    for (const id of EXPECTED_BUILTIN_IDS) {
      expect(SIGNAL_LABELS[id]).toBeDefined()
      expect(typeof SIGNAL_LABELS[id]).toBe('string')
    }
  })
})

describe('BUILTIN_SIGNAL_DEFINITIONS — Traditional Chinese wording', () => {
  it('no known simplified-Chinese / A-share-only term remains in any name/category/description', () => {
    for (const sig of BUILTIN_SIGNAL_DEFINITIONS) {
      for (const bad of KNOWN_SIMPLIFIED_OR_A_SHARE_TERMS) {
        expect(sig.name, `signal ${sig.id} name`).not.toContain(bad)
        expect(sig.category, `signal ${sig.id} category`).not.toContain(bad)
        expect(sig.description, `signal ${sig.id} description`).not.toContain(bad)
      }
    }
  })

  it('categories are Traditional Chinese (均線/趨勢/量價/漲跌停), not the old simplified set', () => {
    const categories = new Set(BUILTIN_SIGNAL_DEFINITIONS.map(s => s.category))
    expect(categories.has('均線')).toBe(true)
    expect(categories.has('趨勢')).toBe(true)
    expect(categories.has('漲跌停')).toBe(true)
    expect(categories.has('量價')).toBe(true)
    expect(categories.has('均线')).toBe(false)
    expect(categories.has('趋势')).toBe(false)
  })

  it('limit-board signals use Taiwan wording, not A-share board jargon', () => {
    const ids = ['signal_limit_up', 'signal_limit_down', 'signal_limit_down_recovery', 'signal_broken_limit_up']
    for (const id of ids) {
      const sig = BUILTIN_SIGNAL_DEFINITIONS.find(s => s.id === id)!
      expect(sig, id).toBeDefined()
      expect(SIGNAL_OPTIONS).toContain(id)
      expect(sig.name).not.toMatch(/連板|炸板|翹板/)
    }
  })

  it('every signal still has a non-empty description (semantics preserved, not just deleted)', () => {
    for (const sig of BUILTIN_SIGNAL_DEFINITIONS) {
      expect(sig.description.length).toBeGreaterThan(0)
    }
  })
})

describe('MONITOR_INTRADAY_SIGNAL_LABELS and cnSignal — Traditional Chinese', () => {
  it('intraday signal labels are Traditional Chinese (分時, not 分时)', () => {
    for (const label of Object.values(MONITOR_INTRADAY_SIGNAL_LABELS)) {
      expect(label).toContain('分時')
      expect(label).not.toContain('分时')
    }
  })

  it('cnSignal resolves a built-in signal id to its Traditional Chinese name', () => {
    expect(cnSignal('signal_volume_surge')).toBe('放量')
  })

  it('cnSignal resolves a technical field id to Traditional Chinese (e.g. consecutive_limit_ups)', () => {
    expect(cnSignal('consecutive_limit_ups')).toBe('連續漲停')
    expect(cnSignal('consecutive_limit_downs')).toBe('連續跌停')
  })

  it('cnSignal falls back to the raw id for unknown names (no fabricated translation)', () => {
    expect(cnSignal('csg_some_custom_signal')).toBe('csg_some_custom_signal')
  })

  it('cnSignal prefers an explicitly supplied custom-name map over the built-in tables', () => {
    expect(cnSignal('csg_1', { csg_1: '自訂訊號A' })).toBe('自訂訊號A')
  })
})


// The abnormal diagnostics panel and the signal library settings page both render
// these strings as the rule the backend applies. A threshold edited on one side only
// leaves the UI describing a rule that is no longer enforced, which misleads worse
// than showing nothing, so the expectations below are written out in full rather than
// as substring probes. Source of truth: backend/app/taiwan/abnormal_diagnostics.py.
describe('ABNORMAL_SIGNAL_DEFINITIONS — the rules shown to the user', () => {
  const byId = (id: string) => {
    const sig = ABNORMAL_SIGNAL_DEFINITIONS.find(s => s.id === id)
    if (!sig) throw new Error(`no abnormal signal definition for ${id}`)
    return sig
  }

  it('covers exactly the ten signal types abnormal_diagnostics.py can emit', () => {
    expect(ABNORMAL_SIGNAL_DEFINITIONS.map(s => s.id)).toEqual([
      'VOLUME_SPIKE',
      'TURNOVER_SPIKE',
      'PRICE_MOVE',
      'FOREIGN_FLOW_SPIKE',
      'TRUST_FLOW_SPIKE',
      'MARGIN_SURGE',
      'SHORT_SURGE',
      'SHORT_MARGIN_RATIO_SPIKE',
      'PRICE_FLOW_DIVERGENCE',
      'RELATIVE_STRENGTH_OUTLIER',
    ])
  })

  it('states each trigger expression exactly as the backend evaluates it', () => {
    expect(byId('VOLUME_SPIKE').formula).toBe('成交量 ÷ 前 5 日平均成交量 ≥ 2.0')
    expect(byId('TURNOVER_SPIKE').formula).toBe('成交金額 ÷ 前 5 日平均成交金額 ≥ 2.0')
    expect(byId('PRICE_MOVE').formula).toBe('|漲跌幅| ÷ |槓桿倍數| ≥ 5%')
    expect(byId('FOREIGN_FLOW_SPIKE').formula).toBe(
      '|外資買賣超| ≥ 100,000 股　且　|外資買賣超| ÷ 前 20 日 |買賣超| 平均 ≥ 3.0',
    )
    expect(byId('TRUST_FLOW_SPIKE').formula).toBe(
      '|投信買賣超| ≥ 50,000 股　且　|投信買賣超| ÷ 前 20 日 |買賣超| 平均 ≥ 3.0',
    )
    expect(byId('MARGIN_SURGE').formula).toBe(
      '|融資增減| ≥ 500,000 股　且　|融資增減| ÷ 前 20 日 |增減| 中位數 ≥ 3.0',
    )
    expect(byId('SHORT_SURGE').formula).toBe(
      '|融券增減| ≥ 100,000 股　且　|融券增減| ÷ 前 20 日 |增減| 中位數 ≥ 3.0',
    )
    expect(byId('SHORT_MARGIN_RATIO_SPIKE').formula).toBe(
      '券資比 − 前 20 日券資比中位數 ≥ 5 個百分點',
    )
    expect(byId('PRICE_FLOW_DIVERGENCE').formula).toBe(
      '|漲跌幅| ÷ |槓桿倍數| ≥ 3%　且　反向外資買賣超 ≥ 200,000 股　且　該買賣超達前 20 日 |買賣超| 平均的 2 倍',
    )
    // The backend subtracts the industry's SAME-DAY average change from the stock's
    // 5-day return (abnormal_diagnostics.py, where average_change_pct is a one-session
    // mean). Stated as implemented, not as the surrounding comment claims.
    expect(byId('RELATIVE_STRENGTH_OUTLIER').formula).toBe(
      '|個股 5 日報酬率 − 所屬產業當日平均漲跌幅| ≥ 10 個百分點',
    )
  })

  it('keeps mean and median apart, matching which statistic each store is read with', () => {
    expect(byId('FOREIGN_FLOW_SPIKE').formula).toContain('平均')
    expect(byId('FOREIGN_FLOW_SPIKE').formula).not.toContain('中位數')
    expect(byId('TRUST_FLOW_SPIKE').formula).toContain('平均')
    expect(byId('TRUST_FLOW_SPIKE').formula).not.toContain('中位數')
    expect(byId('MARGIN_SURGE').formula).toContain('中位數')
    expect(byId('MARGIN_SURGE').formula).not.toContain('平均')
    expect(byId('SHORT_SURGE').formula).toContain('中位數')
    expect(byId('SHORT_SURGE').formula).not.toContain('平均')
    expect(byId('SHORT_MARGIN_RATIO_SPIKE').formula).toContain('中位數')
  })

  it('states the history each signal needs before it is evaluated', () => {
    expect(byId('VOLUME_SPIKE').dataRequirement).toBe('前 5 個交易日全部要有成交量資料')
    expect(byId('TURNOVER_SPIKE').dataRequirement).toBe('前 5 個交易日全部要有成交金額資料')
    expect(byId('FOREIGN_FLOW_SPIKE').dataRequirement).toBe(
      '前 20 日至少 15 日有法人資料，且該平均本身 ≥ 10,000 股',
    )
    expect(byId('TRUST_FLOW_SPIKE').dataRequirement).toBe(
      '前 20 日至少 15 日有法人資料，且該平均本身 ≥ 5,000 股',
    )
    // f_mult is only assigned inside the foreign-flow branch, so the divergence signal
    // inherits that branch's coverage and baseline gates.
    expect(byId('PRICE_FLOW_DIVERGENCE').dataRequirement).toBe(
      '前 20 日至少 15 日有法人資料，且該平均本身 ≥ 10,000 股',
    )
    for (const id of ['MARGIN_SURGE', 'SHORT_SURGE', 'SHORT_MARGIN_RATIO_SPIKE']) {
      expect(byId(id).dataRequirement).toBe('前 20 日至少 15 日有融資融券資料')
    }
  })

  it('leaves PRICE_MOVE without a history requirement, since it only needs the previous close', () => {
    expect(byId('PRICE_MOVE').dataRequirement).toBeUndefined()
  })

  it('records that RELATIVE_STRENGTH_OUTLIER is never evaluated for an ETF', () => {
    expect(byId('RELATIVE_STRENGTH_OUTLIER').dataRequirement).toBe(
      'ETF 結構性不適用（無所屬產業），不列入其觸發與未觸發統計',
    )
  })

  it('gives every signal a description distinct from its bare formula', () => {
    for (const sig of ABNORMAL_SIGNAL_DEFINITIONS) {
      expect(sig.description, sig.id).not.toBe(sig.formula)
      expect(sig.description.trim().length, sig.id).toBeGreaterThan(10)
    }
  })
})
