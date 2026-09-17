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
export function computeIntradayAverage(data: MinuteKlineRow[]): number[] {
  const result: number[] = []
  let amount = 0
  let volume = 0
  for (const row of data) {
    amount += row.amount
    volume += row.volume
    result.push(volume > 0 ? amount / volume : row.close)
  }
  return result
}

// Taiwan regular session, continuous with no lunch break. Minute bars outside this
// grid are dropped by the charts, so the range must match the real session exactly.
const SESSION_OPEN_MINUTE = 9 * 60
const SESSION_CLOSE_MINUTE = 13 * 60 + 30

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
