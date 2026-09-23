interface TurnoverRateInput {
  volume?: number | null
  turnover_rate?: number | null
}

/**
 * The float-shares formula assumes A-share volume in lots, so it applies only when
 * floatShares is known; otherwise the backend's market-aware turnover_rate is used.
 */
export function resolveTurnoverRate(latest: TurnoverRateInput, floatShares?: number | null): number | null {
  if (floatShares) {
    return latest.volume ? Number(latest.volume) * 100 / floatShares * 100 : null
  }
  const fallback = latest.turnover_rate
  return fallback != null && Number.isFinite(Number(fallback)) ? Number(fallback) : null
}
