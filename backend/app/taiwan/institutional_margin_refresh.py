"""Incremental refresh services for Taiwan Institutional and Margin datasets.

Design:
  - Granularity-aware: Since official TWSE & TPEx feeds provide full-market
    snapshots for each trading day in 1 HTTP call per exchange, the refresh
    service operates at the trading-day level.
  - Resume-capable: Inspects persisted store dates and skips finalized ones; institutional
    days written on the trading day itself are refetched (margin days are still skipped).
    A failed day after the newest stored one stops the run, so the next run resumes from it.
  - Calendar-aware: Skips weekends and confirmed market holidays.
  - Rate-limit friendly: Bounded timeout and retry policy.
"""
from __future__ import annotations

import http.client
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any, Callable

import polars as pl

from app.taiwan.enrichment.institutional import (
    TaiwanInstitutionalProvider,
    TwseInstitutionalAdapter,
    TpexInstitutionalAdapter,
)
from app.taiwan.enrichment.margin import (
    TaiwanMarginProvider,
    TwseMarginAdapter,
    TpexMarginAdapter,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_BACKOFF_SEC = 1.0

# TWSE answers a rate-limited client with these rather than 429. The 307 has no
# Location header, so urllib surfaces it as "redirect loop" and the body is the
# 因為安全性考量 block page.
THROTTLE_STATUS_CODES = frozenset({307, 308, 428, 429})

# Pause between trading days when backfilling. Without it a 120-day backfill
# fires ~480 requests back to back and trips the exchange's throttle.
INTER_REQUEST_DELAY_SEC = 0.4


class ExchangeThrottledError(RuntimeError):
    """The exchange refused the request because we asked too often."""


def _record_stop(stats: dict[str, Any], calendar: TaiwanTradingCalendar, stopped: date, end_date: date) -> None:
    """Note in *stats* the day a run stopped at and how many trading days after it up to *end_date* it skipped."""
    stats["stopped_at"] = str(stopped)
    stats["dates_not_attempted"] = sum(
        1
        for offset in range(1, (end_date - stopped).days + 1)
        if calendar.is_trading_day(stopped + timedelta(days=offset)) is not False
    )


def _fetch_json_with_retry(
    url: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF_SEC,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch JSON with minimal finite retry for transient HTTP/network errors.

    Retryable:
      - Timeouts (TimeoutError, urllib.error.URLError wrapping timeout)
      - Connection reset / network dropped
      - Truncated or malformed responses (http.client.HTTPException, e.g. IncompleteRead)
      - HTTP 429 (Too Many Requests)
      - HTTP 428 / 307 / 308 — TWSE answers a rate-limited client with these
        instead of 429. The 307 carries no Location, so urllib reports it as a
        redirect loop; the body is the "因為安全性考量" block page.
      - HTTP 5xx (500, 502, 503, 504)

    Non-retryable:
      - HTTP 4xx (400, 401, 403, 404, etc. except the throttling codes above)
      - JSON decode / malformed data errors
    """
    attempt = 0
    backoff = initial_backoff

    while attempt < max_attempts:
        attempt += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            is_retryable = exc.code in THROTTLE_STATUS_CODES or 500 <= exc.code <= 599
            if not is_retryable or attempt >= max_attempts:
                if exc.code in THROTTLE_STATUS_CODES:
                    raise ExchangeThrottledError(
                        f"交易所暫時拒絕存取 (HTTP {exc.code})，通常是短時間請求過多；"
                        f"請稍候再回補，或縮小回補範圍。"
                    ) from exc
                raise
            logger.warning(
                "HTTP %d on %s (attempt %d/%d), retrying in %.1fs...",
                exc.code, url, attempt, max_attempts, backoff,
            )
            time.sleep(backoff)
            backoff *= 2.0
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError, http.client.HTTPException) as exc:
            if attempt >= max_attempts:
                raise
            logger.warning(
                "Network/timeout error on %s: %s (attempt %d/%d), retrying in %.1fs...",
                url, exc, attempt, max_attempts, backoff,
            )
            time.sleep(backoff)
            backoff *= 2.0
        except json.JSONDecodeError:
            # Data parsing error is permanent; do not retry
            raise

    raise RuntimeError(f"Failed to fetch {url} after {max_attempts} attempts")


class TaiwanInstitutionalRefreshService:
    """Refreshes institutional flows from official TWSE/TPEx endpoints into TaiwanInstitutionalStore."""

    def __init__(
        self,
        store: TaiwanInstitutionalStore | None = None,
        provider: TaiwanInstitutionalProvider | None = None,
        calendar: TaiwanTradingCalendar | None = None,
    ) -> None:
        self._store = store or TaiwanInstitutionalStore()
        self._provider = provider or TaiwanInstitutionalProvider()
        self._calendar = calendar or TaiwanTradingCalendar()

    def refresh_dates(
        self,
        start_date: date,
        end_date: date,
        force: bool = False,
        on_progress: Callable[[date, int], None] | None = None,
    ) -> dict[str, Any]:
        """Fetch and persist market-wide institutional flows for all trading days in [start_date, end_date].

        on_progress, when given, is called with (current_date, day_index) for every
        trading day visited (day_index starts at 1), before that day is fetched or skipped.

        Stored days still provisional are refetched. Without force, a day up to the
        latest stored one is never written from a single exchange: a stored day where
        either exchange returns no rows, or a gap day where only one does, is listed
        in repair_failed_dates and left as it was, so a stored day stays provisional.
        A gap day with no rows from either exchange is only skipped. A failure on any
        such day is listed there too, since moving on opens no new gap. Failures on
        later days, and every failure under force, go to failed_dates. Under force a
        stored provisional day with no rows from either exchange is also listed in
        repair_failed_dates, and a single-exchange result is written as before. Without
        force, a day after the latest stored one with rows from one exchange only is a
        failure. A failure on a day after the latest stored one ends the run there,
        noted in stopped_at and dates_not_attempted.
        """
        stored = self._store.available_dates()
        latest_stored = max(stored, default=None)
        stored_set = set(stored)
        repairs = set(self._store.provisional_dates(sorted(d for d in stored if start_date <= d <= end_date)))
        available = set(stored) - repairs if not force else set()
        stats = {
            "dates_requested": 0,
            "dates_fetched": 0,
            "dates_skipped": 0,
            "total_rows_written": 0,
            "failed_dates": [],
            "repair_failed_dates": [],
            "stopped_at": None,
            "dates_not_attempted": 0,
        }

        cur = start_date
        day_index = 0
        fetched_any = False
        while cur <= end_date:
            # Skip confirmed non-trading days
            if self._calendar.is_trading_day(cur) is False:
                cur += timedelta(days=1)
                continue

            stats["dates_requested"] += 1
            day_index += 1
            if on_progress is not None:
                on_progress(cur, day_index)

            if cur in available:
                stats["dates_skipped"] += 1
                cur += timedelta(days=1)
                continue

            # Space out the calls we are actually about to make; skipped days
            # cost nothing and must not slow a mostly-cached backfill down.
            if fetched_any:
                time.sleep(INTER_REQUEST_DELAY_SEC)
            fetched_any = True

            try:
                # 1 HTTP call for TWSE, 1 HTTP call for TPEx
                flows_twse = self._provider.twse.parse_payload(
                    self._fetch_json(self._provider.twse.build_url(cur)),
                    cur,
                    self._provider.twse.build_url(cur),
                )
                flows_tpex = self._provider.tpex.parse_payload(
                    self._fetch_json(self._provider.tpex.build_url(cur)),
                    cur,
                    self._provider.tpex.build_url(cur),
                )
                all_flows = flows_twse + flows_tpex
                within_stored = not force and latest_stored is not None and cur <= latest_stored
                one_sided = within_stored and not (flows_twse and flows_tpex) and (cur in stored_set or bool(all_flows))
                if not force and not within_stored and all_flows and not (flows_twse and flows_tpex):
                    raise ValueError(f"no {'TWSE' if not flows_twse else 'TPEx'} institutional rows")
                if one_sided:
                    # Writing half a day would move the mtime and leave the other exchange missing or frozen.
                    missing = "TWSE and TPEx" if not all_flows else ("TWSE" if not flows_twse else "TPEx")
                    stats["dates_skipped"] += 1
                    stats["repair_failed_dates"].append({"date": str(cur), "error": f"no {missing} rows"})
                elif all_flows:
                    rows = [
                        {
                            "symbol": f.symbol,
                            "date": cur,
                            "trade_date": cur,
                            "foreign_buy": f.foreign_buy,
                            "foreign_sell": f.foreign_sell,
                            "foreign_net": f.foreign_net,
                            "investment_trust_buy": f.investment_trust_buy,
                            "investment_trust_sell": f.investment_trust_sell,
                            "investment_trust_net": f.investment_trust_net,
                            "dealer_buy": f.dealer_buy,
                            "dealer_sell": f.dealer_sell,
                            "dealer_net": f.dealer_net,
                            "dealer_proprietary_buy": f.dealer_proprietary_buy,
                            "dealer_proprietary_sell": f.dealer_proprietary_sell,
                            "dealer_proprietary_net": f.dealer_proprietary_net,
                            "dealer_hedge_buy": f.dealer_hedge_buy,
                            "dealer_hedge_sell": f.dealer_hedge_sell,
                            "dealer_hedge_net": f.dealer_hedge_net,
                            "official_net": f.official_net,
                            "computed_net": f.computed_net,
                            "has_discrepancy": f.has_discrepancy,
                            "status": f.meta.status if f.meta else "official",
                            "source": f.meta.source if f.meta else "official",
                        }
                        for f in all_flows
                    ]
                    df = pl.DataFrame(rows)
                    written = self._store.write_batch(df, partition_date=cur)
                    stats["total_rows_written"] += written
                    stats["dates_fetched"] += 1
                else:
                    stats["dates_skipped"] += 1
                    if cur in repairs:
                        stats["repair_failed_dates"].append({"date": str(cur), "error": "no rows"})
            except Exception as exc:
                logger.warning("Institutional refresh failed for %s: %s", cur, exc)
                failure = {"date": str(cur), "error": str(exc)}
                if latest_stored is None or cur > latest_stored:
                    stats["failed_dates"].append(failure)
                    # Storing later days would move the latest stored date past this gap.
                    _record_stop(stats, self._calendar, cur, end_date)
                    break
                stats["failed_dates" if force else "repair_failed_dates"].append(failure)

            cur += timedelta(days=1)

        return stats

    def _fetch_json(self, url: str) -> dict[str, Any]:
        return _fetch_json_with_retry(url, timeout=15.0)


class TaiwanMarginRefreshService:
    """Refreshes margin trading balances from official TWSE/TPEx endpoints into TaiwanMarginStore."""

    def __init__(
        self,
        store: TaiwanMarginStore | None = None,
        provider: TaiwanMarginProvider | None = None,
        calendar: TaiwanTradingCalendar | None = None,
    ) -> None:
        self._store = store or TaiwanMarginStore()
        self._provider = provider or TaiwanMarginProvider()
        self._calendar = calendar or TaiwanTradingCalendar()

    def refresh_dates(
        self,
        start_date: date,
        end_date: date,
        force: bool = False,
        on_progress: Callable[[date, int], None] | None = None,
    ) -> dict[str, Any]:
        """Fetch and persist market-wide margin records for all trading days in [start_date, end_date].

        on_progress, when given, is called with (current_date, day_index) for every
        trading day visited (day_index starts at 1), before that day is fetched or skipped.

        *end_date* stops at the latest day published by now (resolve_margin_target): a stored margin
        day is never refetched. A day is written only when both exchanges return rows; a day where
        only one does, or a failure, is listed in failed_dates, and on a day after the latest
        stored one it ends the run there, noted in stopped_at and dates_not_attempted.
        """
        from app.taiwan.daily_update import resolve_margin_target

        end_date = resolve_margin_target(end_date, taipei_now(), self._calendar)
        stored = self._store.available_dates()
        latest_stored = max(stored, default=None)
        available = set(stored) if not force else set()
        stats = {
            "dates_requested": 0,
            "dates_fetched": 0,
            "dates_skipped": 0,
            "total_rows_written": 0,
            "failed_dates": [],
            "stopped_at": None,
            "dates_not_attempted": 0,
        }

        cur = start_date
        day_index = 0
        fetched_any = False
        while cur <= end_date:
            # Skip confirmed non-trading days
            if self._calendar.is_trading_day(cur) is False:
                cur += timedelta(days=1)
                continue

            stats["dates_requested"] += 1
            day_index += 1
            if on_progress is not None:
                on_progress(cur, day_index)

            if cur in available:
                stats["dates_skipped"] += 1
                cur += timedelta(days=1)
                continue

            # Space out the calls we are actually about to make; skipped days
            # cost nothing and must not slow a mostly-cached backfill down.
            if fetched_any:
                time.sleep(INTER_REQUEST_DELAY_SEC)
            fetched_any = True

            try:
                # 1 HTTP call for TWSE, 1 HTTP call for TPEx
                margins_twse = self._provider.twse.parse_payload(
                    self._fetch_json(self._provider.twse.build_url(cur)),
                    cur,
                    self._provider.twse.build_url(cur),
                )
                margins_tpex = self._provider.tpex.parse_payload(
                    self._fetch_json(self._provider.tpex.build_url(cur)),
                    cur,
                    self._provider.tpex.build_url(cur),
                )
                all_margins = margins_twse + margins_tpex
                if all_margins and not (margins_twse and margins_tpex):
                    raise ValueError(f"no {'TWSE' if not margins_twse else 'TPEx'} margin rows")
                if all_margins:
                    rows = [
                        {
                            "symbol": m.symbol,
                            "date": cur,
                            "trade_date": cur,
                            "margin_previous_balance": m.margin_previous_balance,
                            "margin_buy": m.margin_buy,
                            "margin_sell": m.margin_sell,
                            "margin_cash_redemption": m.margin_cash_redemption,
                            "margin_balance": m.margin_balance,
                            "margin_change": m.margin_change,
                            "short_previous_balance": m.short_previous_balance,
                            "short_sell": m.short_sell,
                            "short_cover": m.short_cover,
                            "short_stock_redemption": m.short_stock_redemption,
                            "short_balance": m.short_balance,
                            "short_change": m.short_change,
                            "short_margin_ratio": m.short_margin_ratio,
                            "unit": m.unit,
                            "status": m.meta.status if m.meta else "official",
                            "source": m.meta.source if m.meta else "official",
                        }
                        for m in all_margins
                    ]
                    df = pl.DataFrame(rows)
                    written = self._store.write_batch(df, partition_date=cur)
                    stats["total_rows_written"] += written
                    stats["dates_fetched"] += 1
                else:
                    stats["dates_skipped"] += 1
            except Exception as exc:
                logger.warning("Margin refresh failed for %s: %s", cur, exc)
                stats["failed_dates"].append({"date": str(cur), "error": str(exc)})
                if latest_stored is None or cur > latest_stored:
                    _record_stop(stats, self._calendar, cur, end_date)
                    break

            cur += timedelta(days=1)

        return stats

    def _fetch_json(self, url: str) -> dict[str, Any]:
        return _fetch_json_with_retry(url, timeout=15.0)
