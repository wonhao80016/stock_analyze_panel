import { ApiError } from '@/lib/api'

export const JOB_POLL_INTERVAL_MS = 800
export const JOB_POLL_RETRY_MS = 2000
export const JOB_POLL_MAX_FAILURES = 5

/** The Taiwan job endpoints answer 404 once the job is gone, e.g. after a backend restart. */
export function isMissingJobError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404
}
