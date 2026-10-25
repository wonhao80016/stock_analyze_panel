import type { LevelType, MonitorRule } from '@/lib/api'

export const LEVEL_GROUPS: { key: LevelType; label: string }[] = [
  { key: 'sr', label: '壓力支撐' },
  { key: 'pivot', label: '樞軸點' },
  { key: 'extreme', label: '前高前低' },
  { key: 'boll', label: '布林帶' },
  { key: 'keltner_s', label: 'Keltner短期' },
  { key: 'keltner_m', label: 'Keltner中期' },
  { key: 'keltner_l', label: 'Keltner長期' },
  { key: 'atr_stop', label: 'ATR波動通道' },
  { key: 'gap', label: '缺口位' },
  { key: 'fib', label: '斐波那契' },
  { key: 'round', label: '整數關口' },
]

export type PriceAlertDirection = 'up' | 'down'

export interface PointPriceAlert {
  rule: MonitorRule
  direction: PriceAlertDirection
  target: number
}

export interface MonitorPriceLine {
  value: number
  label: string
  color: string
}

export function parsePointPriceAlert(rule: MonitorRule, symbol?: string): PointPriceAlert | null {
  if (
    rule.type !== 'price'
    || rule.scope !== 'symbols'
    || rule.symbols.length !== 1
    || (symbol != null && rule.symbols[0] !== symbol)
    || rule.conditions.length !== 1
  ) return null

  const condition = rule.conditions[0]
  if (condition.field !== 'close' || !['>=', '<='].includes(condition.op)) return null
  if (typeof condition.value !== 'number' || !Number.isFinite(condition.value) || condition.value <= 0) return null

  return {
    rule,
    direction: condition.op === '>=' ? 'up' : 'down',
    target: condition.value,
  }
}

export function inferPriceAlertDirection(
  target: number,
  currentPrice: number | null | undefined,
): PriceAlertDirection {
  return currentPrice != null && Number.isFinite(currentPrice) && target < currentPrice ? 'down' : 'up'
}

export function buildPriceAlertMessage(
  name: string,
  symbol: string,
  direction: PriceAlertDirection,
  target: number,
): string {
  return `${name || symbol}股價${direction === 'up' ? '上穿' : '下穿'} ${target.toFixed(2)}`
}

export function buildMonitorPriceLines(rules: MonitorRule[], symbol: string): MonitorPriceLine[] {
  return rules.flatMap(rule => {
    if (!rule.enabled) return []
    const alert = parsePointPriceAlert(rule, symbol)
    if (!alert) return []
    const isUp = alert.direction === 'up'
    return [{
      value: alert.target,
      label: `${isUp ? '上穿' : '下穿'} ${alert.target.toFixed(2)}`,
      color: isUp ? '#C74040' : '#2D9B65',
    }]
  })
}
