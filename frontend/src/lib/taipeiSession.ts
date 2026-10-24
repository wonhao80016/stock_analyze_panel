import { useEffect, useState } from 'react'

// Mon-Fri 08:30 trial matching until just after the 13:30 closing auction, Asia/Taipei.
const SESSION_OPEN_MINUTE = 8 * 60 + 30
const SESSION_CLOSE_MINUTE = 13 * 60 + 35

const TAIPEI_CLOCK = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Taipei',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

function taipeiClock(ms: number) {
  const p = Object.fromEntries(TAIPEI_CLOCK.formatToParts(new Date(ms)).map(part => [part.type, part.value]))
  return {
    date: `${p.year}-${p.month}-${p.day}`,
    weekend: p.weekday === 'Sat' || p.weekday === 'Sun',
    minute: Number(p.hour) * 60 + Number(p.minute),
  }
}

/**
 * Whether the Taiwan session may be trading at `now`.
 * A same-day non_trading_day from the exchange calendar (holiday, typhoon closure) overrides the clock.
 */
export function taipeiSessionOpen(now: number, lastStatus?: string, lastFetchedAt?: number): boolean {
  const clock = taipeiClock(now)
  if (clock.weekend || clock.minute < SESSION_OPEN_MINUTE || clock.minute > SESSION_CLOSE_MINUTE) return false
  return !(lastStatus === 'non_trading_day' && lastFetchedAt && taipeiClock(lastFetchedAt).date === clock.date)
}

/** Re-renders each minute, so a refetchInterval gated on taipeiSessionOpen resumes when the session opens. */
export function useMinuteClock(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(id)
  }, [])
  return now
}
