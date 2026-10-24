"""Refresh of the official market-wide institutional amount summaries (TWSE BFI82U + TPEx summary).

A day is written only when both exchanges return rows for it. When neither does, the day
counts as closed and is skipped only if it is neither stored here nor stored in the daily
bar store; otherwise it is a trading day and the empty answer is a failure. A failure, or
rows from one exchange only, on a day later than the latest day stored here when the call
started goes to failed_dates and ends the run there, since storing a later day would leave
this one behind the latest stored day; on any other day it goes to repair_failed_dates and the
stored partition is kept.

Backfill CLI: python -m app.taiwan.market_institutional_refresh --days 60
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from app.taiwan import institutional_margin_refresh
from app.taiwan.daily_store import REPAIR_WINDOW_PARTITIONS, TaiwanDailyStore
from app.taiwan.enrichment.market_institutional import (
    MarketInstitutionalAmount,
    TpexMarketInstitutionalAdapter,
    TwseMarketInstitutionalAdapter,
)
from app.taiwan.market_institutional_store import TaiwanMarketInstitutionalStore, amounts_frame
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

logger = logging.getLogger(__name__)


class TaiwanMarketInstitutionalRefreshService:
    """Fetches the official market-wide institutional amounts into TaiwanMarketInstitutionalStore."""

    def __init__(
        self,
        store: TaiwanMarketInstitutionalStore | None = None,
        twse: TwseMarketInstitutionalAdapter | None = None,
        tpex: TpexMarketInstitutionalAdapter | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        fetch_json: Callable[[str], dict[str, Any]] | None = None,
        daily_store: TaiwanDailyStore | None = None,
    ) -> None:
        self._store = store or TaiwanMarketInstitutionalStore()
        self._twse = twse or TwseMarketInstitutionalAdapter()
        self._tpex = tpex or TpexMarketInstitutionalAdapter()
        self._calendar = calendar or TaiwanTradingCalendar()
        self._fetch_json = fetch_json or (lambda url: institutional_margin_refresh._fetch_json_with_retry(url, timeout=15.0))
        self._daily_store = daily_store or TaiwanDailyStore()

    def dates_to_refresh(self, target: date) -> list[date]:
        """Trading days after the latest stored day up to *target* (only *target* when the store is
        empty), plus provisional days among the newest stored partitions up to *target*."""
        stored = self._store.available_dates()
        if not stored:
            wanted = {target}
        else:
            wanted = set()
            cur = stored[-1] + timedelta(days=1)
            while cur <= target:
                if self._calendar.is_trading_day(cur) is not False:
                    wanted.add(cur)
                cur += timedelta(days=1)
        provisional = self._store.provisional_dates(stored[-REPAIR_WINDOW_PARTITIONS:])
        wanted.update(d for d in provisional if d <= target)
        return sorted(wanted)

    def refresh_through(self, target: date) -> dict[str, Any]:
        return self.refresh_days(self.dates_to_refresh(target))

    def latest_stored_date(self) -> date | None:
        return self._store.latest_date()

    def backfill(self, days: int, end: date) -> dict[str, Any]:
        """Fetch the *days* trading days ending at *end*, skipping stored days that are not provisional."""
        wanted: list[date] = []
        cur = end
        while len(wanted) < days:
            if self._calendar.is_trading_day(cur) is not False:
                wanted.append(cur)
            cur -= timedelta(days=1)
        stored = set(self._store.available_dates())
        provisional = set(self._store.provisional_dates(sorted(stored & set(wanted))))
        todo = sorted(d for d in wanted if d not in stored or d in provisional)
        stats = self.refresh_days(todo)
        stats["dates_skipped"] += len(wanted) - len(todo)
        return stats

    def refresh_days(self, days: list[date]) -> dict[str, Any]:
        """Callers that also refresh daily bars must do so first: a day with daily bars is a trading day."""
        stored = self._store.available_dates()
        latest_stored = stored[-1] if stored else None
        known_trading_days = set(stored) | set(self._daily_store.available_dates())
        stats: dict[str, Any] = {
            "dates_requested": len(days),
            "dates_fetched": 0,
            "dates_skipped": 0,
            "total_rows_written": 0,
            "failed_dates": [],
            "repair_failed_dates": [],
            "stopped_at": None,
            "dates_not_attempted": 0,
        }

        def record_failure(d: date, error: str) -> bool:
            """Whether the run must stop at *d*."""
            past_gap = latest_stored is not None and d <= latest_stored
            stats["repair_failed_dates" if past_gap else "failed_dates"].append({"date": str(d), "error": error})
            return not past_gap

        ordered = sorted(days)
        for index, d in enumerate(ordered):
            stop = False
            try:
                twse_rows = self._fetch_side(self._twse, d)
                tpex_rows = self._fetch_side(self._tpex, d)
                if twse_rows and tpex_rows:
                    stats["total_rows_written"] += self._store.write_day(d, amounts_frame(d, twse_rows + tpex_rows))
                    stats["dates_fetched"] += 1
                elif not twse_rows and not tpex_rows:
                    stats["dates_skipped"] += 1
                    if d in known_trading_days:
                        stop = record_failure(d, "no TWSE or TPEx rows on a trading day")
                else:
                    stats["dates_skipped"] += 1
                    stop = record_failure(d, f"no {'TWSE' if not twse_rows else 'TPEx'} rows")
            except Exception as exc:
                logger.warning("Market institutional amounts refresh failed for %s: %s", d, exc)
                stop = record_failure(d, str(exc))
            if stop:
                stats["stopped_at"] = str(d)
                stats["dates_not_attempted"] = len(ordered) - index - 1
                break
        return stats

    def _fetch_side(
        self, adapter: TwseMarketInstitutionalAdapter | TpexMarketInstitutionalAdapter, d: date
    ) -> list[MarketInstitutionalAmount]:
        time.sleep(institutional_margin_refresh.INTER_REQUEST_DELAY_SEC)
        url = adapter.build_url(d)
        return adapter.parse_payload(self._fetch_json(url), d, url)


def main() -> None:
    import argparse
    import json

    from app.taiwan.daily_update import resolve_target_latest_trading_date

    parser = argparse.ArgumentParser(description="Backfill Taiwan market-wide institutional amounts")
    parser.add_argument("--days", type=int, default=60, help="Number of trading days to cover, ending at --end")
    parser.add_argument("--end", type=str, help="Last trading day YYYY-MM-DD (defaults to the latest published one)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    svc = TaiwanMarketInstitutionalRefreshService()
    end = date.fromisoformat(args.end) if args.end else resolve_target_latest_trading_date(svc._calendar)
    print(json.dumps(svc.backfill(args.days, end), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
