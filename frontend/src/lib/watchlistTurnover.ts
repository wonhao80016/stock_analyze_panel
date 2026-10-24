import { BOARD_LOT_VOLUME_NOTE, type KlineRow } from './api'

export interface TurnoverDisplay {
  text: string
  suffix: string | null
  title: string
}

type TurnoverFields = Pick<KlineRow, 'turnover_rate' | 'turnover_rate_status' | 'board_lot_volume'>

export function turnoverDisplay(r: TurnoverFields): TurnoverDisplay {
  if (r.turnover_rate_status === 'not_applicable') {
    return { text: '不適用', suffix: null, title: '換手率不適用：ETF、TDR 沒有換手率分母' }
  }
  const rate = r.turnover_rate
  const text = rate != null ? `${rate.toFixed(2)}%` : '—'
  if (r.turnover_rate_status === 'board_lot_estimate') {
    return { text, suffix: '整股暫估', title: `換手率暫估：${BOARD_LOT_VOLUME_NOTE}，官方日K發佈後改用官方量` }
  }
  return { text, suffix: null, title: '換手率' }
}
