import type { MinuteKlineRow } from '@/lib/api'

// Minute bars are stored and served in UTC, so +8 yields Taipei wall-clock time.
// If they were ever served as Taipei-local ISO strings this would shift every bar
// to 17:xx, outside FULL_DAY_TIMES, and the chart would silently render empty.
export function formatMinuteTime(datetime: string): string {
  const match = datetime.match(/(\d{2}):(\d{2})/)
  if (!match) return datetime.slice(11, 16)
  const hour = (parseInt(match[1]) + 8) % 24
  return `${String(hour).padStart(2, '0')}:${match[2]}`
}

// Taiwan minute bars carry volume in shares and amount in TWD, so the running
// average is a plain amount/volume. (A-share feeds reported lots and needed a
// x100 here; multiplying Taiwan shares would divide the average by 100.)
// A bar without an amount contributes close x volume instead.
export function computeIntradayAverage(data: MinuteKlineRow[]): number[] {
  const result: number[] = []
  let amount = 0
  let volume = 0
  for (const row of data) {
    const barVolume = Number.isFinite(row.volume) && row.volume > 0 ? row.volume : 0
    const barAmount = Number.isFinite(row.amount) && row.amount > 0 ? row.amount : row.close * barVolume
    amount += barAmount
    volume += barVolume
    result.push(volume > 0 ? amount / volume : row.close)
  }
  return result
}

// Below this share of the day's volume, an average over the recorded bars misrepresents the session.
export const MIN_AVERAGE_COVERAGE = 0.8

/**
 * Share of the observed volume the bars place in a minute; null without any volume.
 * Unattributed volume is in the same board-lot units as the bars, unlike the official day volume.
 */
export function computeVolumeCoverage(data: MinuteKlineRow[]): number | null {
  let attributed = 0
  let unattributed = 0
  for (const row of data) {
    if (Number.isFinite(row.volume) && row.volume > 0) attributed += row.volume
    const gap = row.unattributed_volume
    if (gap != null && Number.isFinite(gap) && gap > 0) unattributed += gap
  }
  const observed = attributed + unattributed
  return observed > 0 ? attributed / observed : null
}

export interface IsolatedPoint {
  value: number
  symbol: 'circle'
  symbolSize: number
}

/** Marks each point whose neighbours are both empty, which a line with connectNulls off never draws. */
export function markIsolatedPoints(values: (number | null)[], symbolSize = 3): (number | null | IsolatedPoint)[] {
  return values.map((value, index) => {
    if (value == null) return null
    const before = index > 0 ? values[index - 1] : null
    const after = index < values.length - 1 ? values[index + 1] : null
    return before == null && after == null ? { value, symbol: 'circle', symbolSize } : value
  })
}

export function formatCoverage(coverage: number): string {
  return `均價覆蓋 ${Math.round(coverage * 100)}%`
}

/** Minutes since the 09:00 open, or null outside the session grid. */
export function sessionMinute(datetime: string): number | null {
  const [hour, minute] = formatMinuteTime(datetime).split(':').map(Number)
  const offset = hour * 60 + minute - SESSION_OPEN_MINUTE
  return offset >= 0 && offset <= SESSION_MINUTES ? offset : null
}

// Taiwan regular session, continuous with no lunch break. Minute bars outside this
// grid are dropped by the charts, so the range must match the real session exactly.
const SESSION_OPEN_MINUTE = 9 * 60
const SESSION_CLOSE_MINUTE = 13 * 60 + 30
export const SESSION_MINUTES = SESSION_CLOSE_MINUTE - SESSION_OPEN_MINUTE

function generateFullDayTimes(): string[] {
  const times: string[] = []
  for (let m = SESSION_OPEN_MINUTE; m <= SESSION_CLOSE_MINUTE; m++) {
    const hour = Math.floor(m / 60)
    const minute = m % 60
    times.push(`${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`)
  }
  return times
}

export const FULL_DAY_TIMES = generateFullDayTimes()
