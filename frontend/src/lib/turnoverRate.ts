interface TurnoverRateInput {
  volume?: number | null
  turnover_rate?: number | null
}

/** The backend's turnover_rate, a percentage (1.23 means 1.23%), or null when missing or not finite. */
export function resolveTurnoverRate(latest: TurnoverRateInput): number | null {
  const value = latest.turnover_rate
  return value != null && Number.isFinite(Number(value)) ? Number(value) : null
}
