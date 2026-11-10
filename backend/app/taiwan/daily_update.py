"""Taiwan Daily Incremental Update Service (Phase 6B/Orchestration).

Production-ready orchestrator for Taiwan market daily data maintenance:
  1. Daily OHLCV (TaiwanDailyStore)
  2. Institutional Flows (TaiwanInstitutionalStore)
  3. Margin Balances (TaiwanMarginStore)

Core Design Principles:
  - Resolution of target trading date(s) using TaiwanTradingCalendar and local data provenance.
  - Catch-up behavior across multiple missing trading dates.
  - Granularity and Cost Awareness:
      * Institutional & Margin: Market-wide official endpoints (2 HTTP calls per date each). Safe for auto-refresh.
      * Daily OHLCV: Per-symbol requests (~2,335 HTTP calls for full market). Kept in manual/safe mode by default
        to prevent provider rate limits or system saturation until full-market batch endpoint is available.
  - Partial failure semantics: Reports per-dataset status (success/failed/skipped) and overall_status
    ('success' | 'partial' | 'failed').
  - Bounded concurrency and idempotency; recent partitions that are half a snapshot or were
    written on their own trading day are refetched, all other existing partitions are left untouched.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.corporate_actions import TaiwanCorporateActionRefreshService
from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import REPAIR_WINDOW_PARTITIONS, TaiwanDailyStore
from app.taiwan.institutional_margin_refresh import (
    TaiwanInstitutionalRefreshService,
    TaiwanMarginRefreshService,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_institutional_refresh import TaiwanMarketInstitutionalRefreshService
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar, taipei_now
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.service import SecurityMasterUnavailableError

logger = logging.getLogger(__name__)

# Post-close cutoff when official post-market data is expected to be published
DEFAULT_POST_CLOSE_HOUR = 16
DEFAULT_POST_CLOSE_MINUTE = 0

# Margin balances are published later than institutional flows; the scheduled margin run starts here.
MARGIN_PUBLISH_HOUR = 21
MARGIN_PUBLISH_MINUTE = 40

UpdateStatus = Literal["success", "partial", "failed", "skipped"]

# Held by whatever rewrites daily partitions and publishes enriched in this process: the
# scheduled run, update-to-latest and the history bundle import. Other processes are not excluded.
_update_lock = threading.RLock()


class TaiwanUpdateBusyError(RuntimeError):
    """Another Taiwan data update in this process holds the update lock."""


@contextlib.contextmanager
def exclusive_update(*, wait: bool) -> Iterator[None]:
    """Hold the update lock; without *wait*, raise TaiwanUpdateBusyError when it is taken."""
    if not _update_lock.acquire(blocking=wait):
        raise TaiwanUpdateBusyError("另一個台股資料更新正在執行, 請稍後再試")
    try:
        yield
    finally:
        _update_lock.release()


class DatasetRefreshStats(BaseModel):
    status: UpdateStatus = "skipped"
    dates_requested: int = 0
    dates_fetched: int = 0
    dates_skipped: int = 0
    failed_dates: list[dict[str, Any]] = Field(default_factory=list)
    rows_written: int = 0
    note: str | None = None


DatasetFreshnessStatus = Literal["current", "stale", "unavailable"]


class FreshnessStatus(BaseModel):
    daily_as_of: str | None = None
    institutional_as_of: str | None = None
    margin_as_of: str | None = None
    target_latest_trading_date: str
    is_fully_current: bool = False
    daily_status: DatasetFreshnessStatus = "unavailable"
    institutional_status: DatasetFreshnessStatus = "unavailable"
    margin_status: DatasetFreshnessStatus = "unavailable"
    daily_days_behind: int = 0
    institutional_days_behind: int = 0
    margin_days_behind: int = 0
    scheduler_enabled: bool = True
    scheduled_update_time: str = "16:30"
    scheduled_timezone: str = "Asia/Taipei"


class TaiwanDailyUpdateResult(BaseModel):
    run_started_at: str
    run_finished_at: str
    target_start_date: str
    target_end_date: str
    target_latest_trading_date: str
    daily: DatasetRefreshStats
    institutional: DatasetRefreshStats
    margin: DatasetRefreshStats
    freshness: FreshnessStatus
    overall_status: UpdateStatus
    security_master: dict[str, Any] | None = None


class ChipDataRefreshResult(BaseModel):
    institutional: DatasetRefreshStats
    market_institutional: DatasetRefreshStats
    margin: DatasetRefreshStats


def resolve_target_latest_trading_date(
    calendar: TaiwanTradingCalendar | None = None,
    as_of_dt: datetime | None = None,
    post_close_hour: int = DEFAULT_POST_CLOSE_HOUR,
    post_close_minute: int = DEFAULT_POST_CLOSE_MINUTE,
) -> date:
    """Resolve the latest trading date that should be available.

    Rules:
      - If today is a weekend or confirmed holiday, step back to the most recent potential trading day.
      - If today is a potential trading day:
          * If current time is AFTER post-market publication cutoff (e.g. >= 16:00 Asia/Taipei),
            target is today.
          * If current time is BEFORE cutoff, today's full-market daily/inst/margin data is not yet
            finalized/published by exchanges, so target is the PREVIOUS trading day.
      - Never fabricates unconfirmed future dates.
    """
    cal = calendar or TaiwanTradingCalendar()
    dt = as_of_dt or taipei_now()
    cur = dt.date()

    # If current day is Saturday, Sunday, or confirmed holiday, step back
    while cal.is_trading_day(cur) is False:
        cur -= timedelta(days=1)

    # If cur is today and we haven't reached market publication cutoff time, step back
    if cur == dt.date():
        cutoff = dt.replace(hour=post_close_hour, minute=post_close_minute, second=0, microsecond=0)
        if dt < cutoff:
            cur -= timedelta(days=1)
            while cal.is_trading_day(cur) is False:
                cur -= timedelta(days=1)

    return cur


def resolve_margin_target(target: date, now: datetime, calendar: TaiwanTradingCalendar) -> date:
    """Latest day up to *target* whose margin balances a run at *now* may store.

    A stored margin day is never refetched, so before MARGIN_PUBLISH on a session's own day
    (Asia/Taipei) the margin step stops at the previous trading day.
    """
    published = resolve_target_latest_trading_date(
        calendar,
        as_of_dt=now.astimezone(TAIPEI_TZ),
        post_close_hour=MARGIN_PUBLISH_HOUR,
        post_close_minute=MARGIN_PUBLISH_MINUTE,
    )
    return min(target, published)


def resolve_missing_date_range(
    earliest_available: date | None,
    target_latest: date,
    calendar: TaiwanTradingCalendar | None = None,
) -> tuple[date, date] | None:
    """Determine the start and end dates needed to catch up.

    If earliest_available is >= target_latest, no catch-up needed.
    Otherwise, returns (earliest_available + 1 day, target_latest).
    """
    if earliest_available is None:
        return target_latest, target_latest
    if earliest_available >= target_latest:
        return None

    cal = calendar or TaiwanTradingCalendar()
    # Find next candidate date
    cur = earliest_available + timedelta(days=1)
    while cur <= target_latest and cal.is_trading_day(cur) is False:
        cur += timedelta(days=1)

    if cur > target_latest:
        return None
    return cur, target_latest


def master_refresh_due(target: date, now: datetime, calendar: TaiwanTradingCalendar) -> bool:
    """Whether a daily run for *target* at *now* refreshes the security master on its own.

    Only a run for today's session (Asia/Taipei), on a potential trading day, at or after the
    post-close cutoff does.
    """
    local = now.astimezone(TAIPEI_TZ)
    cutoff = local.replace(
        hour=DEFAULT_POST_CLOSE_HOUR, minute=DEFAULT_POST_CLOSE_MINUTE, second=0, microsecond=0
    )
    today = local.date()
    return target == today and local >= cutoff and calendar.is_trading_day(today) is not False


@dataclass(frozen=True)
class MasterRefreshOutcome:
    """A daily run's security master refresh; *changed* means instruments or their rules changed."""

    summary: dict[str, Any]
    note: str | None = None
    changed: bool = False


def pending_listing_dates(
    store: TaiwanDailyStore, master: TaiwanSecurityMaster, target: date
) -> list[date]:
    """Newest REPAIR_WINDOW_PARTITIONS stored days up to *target* before a recent listing's first row.

    Only supported active stocks and ETFs whose listing date falls inside that window count, so a
    halt after the first stored row, or a halted or delisted older symbol, never causes a refetch;
    a listing leaves the check once its listing date leaves the window.
    """
    window = [d for d in store.available_dates()[-REPAIR_WINDOW_PARTITIONS:] if d <= target]
    if not window:
        return []
    listed: dict[str, date] = {}
    for asset_type in ("stock", "etf"):
        frame = master.to_provider_dataframe(asset_type=asset_type)
        if frame.is_empty() or "list_date" not in frame.columns:
            continue
        for symbol, raw in frame.select("symbol", "list_date").iter_rows():
            try:
                listed_on = date.fromisoformat(raw or "")
            except (TypeError, ValueError):
                continue
            if window[0] <= listed_on <= window[-1]:
                listed[symbol] = listed_on
    if not listed:
        return []
    stored = store.scan_range(sorted(listed), min(listed.values()), window[-1])
    first_stored: dict[str, date] = {}
    for symbol, d in zip(stored["symbol"].to_list(), stored["date"].to_list(), strict=True):
        if d < first_stored.get(symbol, date.max):
            first_stored[symbol] = d
    return sorted(
        {
            d
            for d in window
            for symbol, listed_on in listed.items()
            if listed_on <= d < first_stored.get(symbol, date.max)
        }
    )


def master_refreshed_on(master: TaiwanSecurityMaster, day: date) -> bool:
    """Whether *master*'s refresh record holds a complete successful refresh on *day* (Asia/Taipei).

    A success whose TPEx management style lookups were skipped or cut short does not count.
    """
    try:
        record = json.loads(master.refresh_metadata_path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(record["last_success"])
        complete = record.get("styles_complete") is True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
    if not complete:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TAIPEI_TZ)
    return stamp.astimezone(TAIPEI_TZ).date() == day


def refresh_security_master(
    master: TaiwanSecurityMaster,
    *,
    now: datetime,
    force: bool = False,
    automatic: bool = False,
) -> MasterRefreshOutcome | None:
    """Refresh *master* for a daily run; never raises, a failed or busy refresh only yields a note.

    An *automatic* refresh returns None when the master already refreshed successfully today, and
    skips with a note when its cache file exists but cannot be loaded, since refreshing against an
    empty baseline would bypass the validation against the previous list.
    """
    if automatic:
        if master_refreshed_on(master, now.astimezone(TAIPEI_TZ).date()):
            logger.info("Security master already refreshed today; skipping the automatic refresh")
            return None
        if master.cache_path.exists():
            try:
                master.ensure_loaded()
            except SecurityMasterUnavailableError as exc:
                note = f"Security master refresh skipped, its cache file is unreadable: {exc}"
                logger.warning("%s", note)
                return MasterRefreshOutcome(
                    summary={"ok": False, "busy": False, "error": str(exc), "note": note},
                    note=note,
                )
    try:
        result = master.refresh(now=now, force=force)
    except Exception as exc:
        logger.exception("Security master refresh failed: %s", exc)
        note = f"Security master not refreshed, keeping the previous list: {exc}"
        return MasterRefreshOutcome(
            summary={"ok": False, "busy": False, "error": str(exc), "note": note}, note=note
        )
    summary: dict[str, Any] = {
        "ok": result.ok,
        "busy": result.busy,
        "error": result.error,
        "added": result.added,
        "removed": result.removed,
        "reclassified": len(result.changed),
        "note": None,
    }
    if not result.ok:
        note = (
            "Security master refresh skipped: another refresh is in progress"
            if result.busy
            else f"Security master not refreshed, keeping the previous list: {result.error}"
        )
        logger.warning("%s", note)
        return MasterRefreshOutcome(summary={**summary, "note": note}, note=note)
    return MasterRefreshOutcome(
        summary=summary, changed=bool(result.added or result.removed or result.changed)
    )


def _refresh_corporate_actions(
    window: tuple[date, date] | None, service: TaiwanCorporateActionRefreshService | None
) -> str | None:
    """Fetch the reference prices of the sessions about to be published; returns a failure note."""
    if window is None:
        return None
    try:
        res = (service or TaiwanCorporateActionRefreshService()).refresh_publish_window(*window)
    except Exception as e:
        logger.exception("Corporate action refresh failed: %s", e)
        return f"Corporate action reference prices not refreshed: {e}"
    failed = res.get("failed") or []
    if not failed:
        return None
    note = "Corporate action tables not refreshed: " + ", ".join(
        f"{f['source']} {f['start']}..{f['end']}" for f in failed
    )
    logger.warning("%s", note)
    return note


def publish_enriched(
    daily_store: TaiwanDailyStore | None = None,
    *,
    only_if_behind: bool = False,
    corporate_actions: TaiwanCorporateActionRefreshService | None = None,
    notes: list[str] | None = None,
    since: date | None = None,
) -> bool:
    """Refresh issued shares, save them as the newest stored session's turnover denominators and
    refresh corporate-action reference prices, then republish the enriched table.

    Returns whether a publish ran. With only_if_behind it runs only when a recent
    day's enriched partition is missing or older than its bars, the last full
    publication never committed, or the table was published under other indicator
    definitions. A *since* before the recent window extends the republish back
    to that day, for bars written into older days. A failed reference-price
    fetch never stops the publish, and a failed publish never raises; both are logged and,
    when *notes* is given, appended to it.
    """
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
    from app.taiwan.share_capital_store import TaiwanShareCapitalStore

    if only_if_behind:
        service = TaiwanEnrichedRefreshService(daily_store=daily_store)
        if (
            service.stale_since() is None
            and not service.publication_incomplete()
            and not service.indicator_definitions_changed()
        ):
            return False
    # Issued shares first: the enriched publish reads them as the turnover
    # denominator, so refreshing afterwards would leave today's partition
    # priced by yesterday's share counts until the next run.
    try:
        TaiwanShareCapitalStore().refresh()
    except Exception as e:
        logger.warning("Taiwan share capital refresh failed: %s", e)
    window = TaiwanEnrichedRefreshService(daily_store=daily_store).recent_window()
    # Only the newest stored session may take the current count: once a later day is stored,
    # this session's saved denominators must stay as they were on its own publish.
    if window is not None:
        try:
            TaiwanShareCapitalStore().save_session_denominators(window[1])
        except Exception as e:
            logger.warning("Taiwan share capital for %s not saved: %s", window[1].isoformat(), e)
    note = _refresh_corporate_actions(window, corporate_actions)
    if note and notes is not None:
        notes.append(note)
    try:
        if since is not None and window is not None and since < window[0]:
            TaiwanEnrichedRefreshService(daily_store=daily_store).refresh(
                start=since, end=window[1]
            )
        else:
            TaiwanEnrichedRefreshService(daily_store=daily_store).refresh_recent()
    except Exception as e:
        logger.warning("Taiwan enriched publish failed: %s", e)
        if notes is not None:
            notes.append(f"Enriched publish failed: {e}")
    return True


class TaiwanDailyUpdateService:
    """Orchestrator for daily incremental updates of Taiwan market datasets."""

    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        inst_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        daily_service: TaiwanDailyRefreshService | None = None,
        inst_service: TaiwanInstitutionalRefreshService | None = None,
        margin_service: TaiwanMarginRefreshService | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        market_inst_service: TaiwanMarketInstitutionalRefreshService | None = None,
        corporate_action_service: TaiwanCorporateActionRefreshService | None = None,
        security_master: TaiwanSecurityMaster | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        # The default master follows the default root; injected stores refresh only a passed master.
        self._security_master = security_master
        self._uses_default_master = security_master is None and daily_store is None
        self.daily_store = daily_store or TaiwanDailyStore()
        self.inst_store = inst_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()

        self.daily_service = daily_service or TaiwanDailyRefreshService(
            store=self.daily_store, calendar=self.calendar
        )
        self.inst_service = inst_service or TaiwanInstitutionalRefreshService(
            store=self.inst_store, calendar=self.calendar
        )
        self.margin_service = margin_service or TaiwanMarginRefreshService(
            store=self.margin_store, calendar=self.calendar
        )
        self.market_inst_service = market_inst_service or TaiwanMarketInstitutionalRefreshService(
            calendar=self.calendar, daily_store=self.daily_store
        )
        self.corporate_action_service = corporate_action_service or TaiwanCorporateActionRefreshService()

    def _calc_dataset_status(self, as_of: date | None, target: date) -> tuple[DatasetFreshnessStatus, int]:
        if as_of is None:
            return "unavailable", 0
        if as_of >= target:
            return "current", 0
        # Count missing trading days in (as_of, target]
        days_behind = 0
        cur = as_of + timedelta(days=1)
        while cur <= target:
            if self.calendar.is_trading_day(cur) is not False:
                days_behind += 1
            cur += timedelta(days=1)
        return "stale", days_behind

    def get_freshness(self, target_date: date | None = None) -> FreshnessStatus:
        """Inspect and return current storage freshness across all 3 datasets.

        Margin is measured against resolve_margin_target, since its day is published after MARGIN_PUBLISH.
        """
        target = target_date or resolve_target_latest_trading_date(self.calendar)
        d_dates = self.daily_store.available_dates()
        i_dates = self.inst_store.available_dates()
        m_dates = self.margin_store.available_dates()

        d_as_of = max(d_dates) if d_dates else None
        i_as_of = max(i_dates) if i_dates else None
        m_as_of = max(m_dates) if m_dates else None

        d_status, d_behind = self._calc_dataset_status(d_as_of, target)
        i_status, i_behind = self._calc_dataset_status(i_as_of, target)
        m_status, m_behind = self._calc_dataset_status(
            m_as_of, resolve_margin_target(target, taipei_now(), self.calendar)
        )

        is_current = (d_status == "current" and i_status == "current" and m_status == "current")

        return FreshnessStatus(
            daily_as_of=str(d_as_of) if d_as_of else None,
            institutional_as_of=str(i_as_of) if i_as_of else None,
            margin_as_of=str(m_as_of) if m_as_of else None,
            target_latest_trading_date=str(target),
            is_fully_current=is_current,
            daily_status=d_status,
            institutional_status=i_status,
            margin_status=m_status,
            daily_days_behind=d_behind,
            institutional_days_behind=i_behind,
            margin_days_behind=m_behind,
            scheduler_enabled=True,
            scheduled_update_time="16:30",
            scheduled_timezone="Asia/Taipei",
        )

    def _master(self) -> TaiwanSecurityMaster | None:
        if self._security_master is not None:
            return self._security_master
        return get_security_master() if self._uses_default_master else None

    def refresh_chip_data(
        self,
        target: date,
        *,
        refresh_institutional: bool = True,
        refresh_margin: bool = True,
        margin_target: date | None = None,
        force_from: date | None = None,
    ) -> ChipDataRefreshResult:
        """Catch up institutional flows, market-wide institutional amounts, then margin to *target*.

        Run it after the daily step: a stored daily partition is what marks an empty market amount
        answer as a failure. Market amounts follow refresh_institutional. *margin_target* caps the
        margin step below *target*; *force_from* refetches every day from it. Never raises.
        """
        if refresh_institutional:
            inst = self._refresh_institutional(target, force_from)
            market = self._refresh_market_institutional(target)
        else:
            inst = DatasetRefreshStats(
                note="Institutional refresh skipped by caller configuration."
            )
            market = DatasetRefreshStats(
                note="Market institutional refresh skipped by caller configuration."
            )
        if refresh_margin:
            margin = self._refresh_margin(
                target if margin_target is None else margin_target, force_from
            )
            if margin_target is not None and margin_target < target:
                deferred = (
                    f"Margin for {target} is published after "
                    f"{MARGIN_PUBLISH_HOUR}:{MARGIN_PUBLISH_MINUTE:02d} Asia/Taipei"
                )
                margin.note = f"{margin.note}; {deferred}" if margin.note else deferred
        else:
            margin = DatasetRefreshStats(note="Margin refresh skipped by caller configuration.")
        return ChipDataRefreshResult(institutional=inst, market_institutional=market, margin=margin)

    def chip_data_as_of(self) -> dict[str, str | None]:
        """Latest stored day of each chip dataset."""
        latest = {
            "institutional": self.inst_store.latest_date(),
            "market_institutional": self.market_inst_service.latest_stored_date(),
            "margin": self.margin_store.latest_date(),
        }
        return {name: d.isoformat() if d else None for name, d in latest.items()}

    def _refresh_institutional(self, target: date, force_from: date | None) -> DatasetRefreshStats:
        stats = DatasetRefreshStats()
        force = force_from is not None
        try:
            i_dates = self.inst_store.available_dates()
            i_max = max(i_dates) if i_dates else None
            i_range = (
                (force_from, target)
                if force
                else resolve_missing_date_range(i_max, target, self.calendar)
            )
            if not force:
                provisional = [
                    d
                    for d in self.inst_store.provisional_dates(i_dates[-REPAIR_WINDOW_PARTITIONS:])
                    if d <= target
                ]
                if provisional:
                    logger.info(
                        "Refetching provisional institutional partitions: %s",
                        ", ".join(map(str, provisional)),
                    )
                    i_range = (provisional[0], target)
            if not i_range:
                stats.status = "success"
                stats.dates_skipped = 1
                stats.note = "Institutional store already current"
                return stats
            res = self.inst_service.refresh_dates(i_range[0], i_range[1], force=force)
            stats.dates_requested = res.get("dates_requested", 0)
            stats.dates_fetched = res.get("dates_fetched", 0)
            stats.dates_skipped = res.get("dates_skipped", 0)
            stats.rows_written = res.get("total_rows_written", 0)
            stats.failed_dates = res.get("failed_dates", [])
            repair_failed = res.get("repair_failed_dates") or []
            if repair_failed:
                stats.note = "Earlier days not refreshed: " + ", ".join(
                    f["date"] for f in repair_failed
                )
            if stats.failed_dates:
                stats.status = "failed" if stats.dates_fetched == 0 else "partial"
            else:
                stats.status = "success"
        except Exception as e:
            logger.exception("Institutional refresh failed: %s", e)
            stats.status = "failed"
            stats.failed_dates.append({"error": str(e)})
        return stats

    def _refresh_market_institutional(self, target: date) -> DatasetRefreshStats:
        """The note is set only for failures."""
        stats = DatasetRefreshStats()
        try:
            res = self.market_inst_service.refresh_through(target)
        except Exception as e:
            logger.exception("Market institutional amounts refresh failed: %s", e)
            stats.status = "failed"
            stats.failed_dates.append({"error": str(e)})
            stats.note = f"Market institutional amounts not refreshed: {e}"
            return stats
        stats.dates_requested = res.get("dates_requested", 0)
        stats.dates_fetched = res.get("dates_fetched", 0)
        stats.dates_skipped = res.get("dates_skipped", 0)
        stats.rows_written = res.get("total_rows_written", 0)
        stats.failed_dates = res.get("failed_dates") or []
        if stats.failed_dates:
            stats.status = "failed" if stats.dates_fetched == 0 else "partial"
        else:
            stats.status = "success"
        parts = []
        failed = [f["date"] for f in stats.failed_dates]
        repair_failed = [f["date"] for f in res.get("repair_failed_dates") or []]
        if failed:
            parts.append("Market institutional amounts not stored: " + ", ".join(failed))
        if repair_failed:
            parts.append(
                "Earlier market institutional amount days not refreshed: "
                + ", ".join(repair_failed)
            )
        if parts:
            stats.note = "; ".join(parts)
            logger.warning("%s", stats.note)
        return stats

    def _refresh_margin(self, target: date, force_from: date | None) -> DatasetRefreshStats:
        stats = DatasetRefreshStats()
        force = force_from is not None
        try:
            m_dates = self.margin_store.available_dates()
            m_max = max(m_dates) if m_dates else None
            m_range = (
                (force_from, target)
                if force
                else resolve_missing_date_range(m_max, target, self.calendar)
            )
            if not m_range:
                stats.status = "success"
                stats.dates_skipped = 1
                stats.note = "Margin store already current"
                return stats
            res = self.margin_service.refresh_dates(m_range[0], m_range[1], force=force)
            stats.dates_requested = res.get("dates_requested", 0)
            stats.dates_fetched = res.get("dates_fetched", 0)
            stats.dates_skipped = res.get("dates_skipped", 0)
            stats.rows_written = res.get("total_rows_written", 0)
            stats.failed_dates = res.get("failed_dates", [])
            if stats.failed_dates:
                stats.status = "failed" if stats.dates_fetched == 0 else "partial"
            else:
                stats.status = "success"
        except Exception as e:
            logger.exception("Margin refresh failed: %s", e)
            stats.status = "failed"
            stats.failed_dates.append({"error": str(e)})
        return stats

    def run_update(
        self,
        target_date: date | None = None,
        refresh_daily: bool = True,
        daily_symbols: list[str] | None = None,
        force: bool = False,
        refresh_institutional: bool = True,
        refresh_margin: bool = True,
        refresh_master: bool | None = None,
        master_force: bool = False,
    ) -> TaiwanDailyUpdateResult:
        """Execute daily incremental refresh across datasets.

        Args:
            target_date: Override target trading date (defaults to resolved latest trading day).
            refresh_daily: Whether to trigger Daily OHLCV refresh. Defaults to True (using the fast
                           official market-wide snapshot path: TWSE 1 + TPEx 1 = 2 HTTP calls per date).
            daily_symbols: Subset of symbols to refresh for Daily OHLCV if specified (uses per-symbol path;
                           if None, uses the fast market-wide snapshot path).
            force: Refetch even if dates already exist in stores.
            refresh_institutional: Whether to refresh institutional flows. Defaults to True.
            refresh_margin: Whether to refresh margin balances. Defaults to True. The exchange
                            publishes margin later than institutional, so the scheduler runs
                            them on separate triggers.
            refresh_master: None refreshes the security master before the full-market daily step
                            when master_refresh_due(target) and it has not refreshed successfully
                            today; True refreshes it regardless; False never. A failed or busy
                            refresh only adds a note to the daily stats. Independently, every
                            full-market daily step refetches the stored days in
                            pending_listing_dates; a failed refetch makes the daily status partial.
            master_force: Let the master refresh accept count drops and removals beyond its
                          validation limits.

        Waits for any other update in this process (exclusive_update) before it starts.

        Returns:
            Structured TaiwanDailyUpdateResult.
        """
        with exclusive_update(wait=True):
            return self._run_update(
                target_date=target_date,
                refresh_daily=refresh_daily,
                daily_symbols=daily_symbols,
                force=force,
                refresh_institutional=refresh_institutional,
                refresh_margin=refresh_margin,
                refresh_master=refresh_master,
                master_force=master_force,
            )

    def _run_update(
        self,
        *,
        target_date: date | None,
        refresh_daily: bool,
        daily_symbols: list[str] | None,
        force: bool,
        refresh_institutional: bool,
        refresh_margin: bool,
        refresh_master: bool | None,
        master_force: bool,
    ) -> TaiwanDailyUpdateResult:
        run_start = taipei_now()
        target = target_date or resolve_target_latest_trading_date(self.calendar, as_of_dt=run_start)

        master = self._master()
        master_outcome: MasterRefreshOutcome | None = None
        if master is not None and (
            refresh_master is True
            or (
                refresh_master is None
                and refresh_daily
                and daily_symbols is None
                and master_refresh_due(target, run_start, self.calendar)
            )
        ):
            master_outcome = refresh_security_master(
                master, now=run_start, force=master_force, automatic=refresh_master is None
            )

        # 1. Determine date ranges per dataset
        d_dates = self.daily_store.available_dates()
        i_dates = self.inst_store.available_dates()
        m_dates = self.margin_store.available_dates()

        d_max = max(d_dates) if d_dates else None
        i_max = max(i_dates) if i_dates else None
        m_max = max(m_dates) if m_dates else None

        # Determine overall target range
        min_available = min(filter(None, [d_max, i_max, m_max]), default=target)
        overall_range = resolve_missing_date_range(min_available, target, self.calendar)
        start_bound = overall_range[0] if overall_range else target
        end_bound = target

        logger.info(
            "Starting TaiwanDailyUpdateService run: target=%s, start_bound=%s, daily_enabled=%s",
            target, start_bound, refresh_daily,
        )

        # 2. Daily OHLCV Refresh
        daily_stats = DatasetRefreshStats()
        written_since: date | None = None
        if not refresh_daily:
            daily_stats.status = "skipped"
            daily_stats.note = "Daily refresh skipped by caller configuration."
        else:
            try:
                backfill: list[date] = []
                if daily_symbols is None and master is not None:
                    backfill = pending_listing_dates(self.daily_store, master, target)
                    if backfill:
                        logger.info(
                            "Refetching daily partitions missing recent listings: %s",
                            ", ".join(map(str, backfill)),
                        )
                d_range = resolve_missing_date_range(d_max, target, self.calendar) if not force else (start_bound, end_bound)
                if not force and daily_symbols is None:
                    # The missing range starts after the latest stored day, so a recent day
                    # holding one exchange only, or written before the exchanges finalized it,
                    # would otherwise never be fetched again.
                    repair_dates = self.daily_store.repair_dates(target)
                    if repair_dates:
                        logger.info("Refetching daily partitions: %s", ", ".join(map(str, repair_dates)))
                    first_refetch = min([*repair_dates, *backfill], default=None)
                    if first_refetch is not None:
                        d_range = (first_refetch, target)
                if not d_range and not force:
                    daily_stats.status = "success"
                    daily_stats.dates_skipped = 1
                    daily_stats.note = "Daily store already current"
                else:
                    d_start, d_end = d_range if d_range else (start_bound, end_bound)
                    if daily_symbols is not None:
                        # Specific symbols requested -> use per-symbol path
                        res = self.daily_service.refresh_symbols(symbols=daily_symbols, start=d_start, end=d_end)
                        if "error" in res:
                            daily_stats.status = "failed"
                            daily_stats.failed_dates.append({"error": res["error"]})
                        else:
                            daily_stats.status = "success"
                            daily_stats.dates_fetched = 1 if res.get("symbols_fetched", 0) > 0 else 0
                            daily_stats.dates_skipped = 1 if res.get("symbols_fetched", 0) == 0 else 0
                            daily_stats.rows_written = res.get("rows_written", 0)
                            written_since = min(
                                (
                                    date.fromisoformat(r["start"])
                                    for entry in (res.get("per_symbol") or {}).values()
                                    for r in entry.get("ranges") or []
                                    if r.get("rows", 0) > 0
                                ),
                                default=None,
                            )
                            failed_symbols = res.get("failed") or []
                            if failed_symbols:
                                daily_stats.status = "partial" if daily_stats.rows_written > 0 else "failed"
                                reasons = []
                                for sym in failed_symbols:
                                    entry = (res.get("per_symbol") or {}).get(sym) or {}
                                    status = entry.get("status", "failed")
                                    reasons.append(f"{sym} ({entry.get('error') if status == 'error' else status})")
                                daily_stats.note = "Symbols not refreshed: " + ", ".join(reasons)
                    else:
                        # Full-market refresh -> use fast official snapshot path (TWSE 1 + TPEx 1)
                        refetch_kwargs: dict[str, Any] = {}
                        if backfill:
                            d_start = min(d_start, backfill[0])
                            refetch_kwargs["refetch"] = backfill
                        res = self.daily_service.refresh_dates(
                            start_date=d_start, end_date=d_end, force=force, **refetch_kwargs
                        )
                        daily_stats.dates_requested = res.get("dates_requested", 0)
                        daily_stats.dates_fetched = res.get("dates_fetched", 0)
                        daily_stats.dates_skipped = res.get("dates_skipped", 0)
                        daily_stats.rows_written = res.get("total_rows_written", 0)
                        daily_stats.failed_dates = res.get("failed_dates", [])
                        repair_failed = res.get("repair_failed_dates") or []
                        if repair_failed:
                            daily_stats.note = "Earlier days not refreshed: " + ", ".join(f["date"] for f in repair_failed)
                        if daily_stats.failed_dates:
                            daily_stats.status = "failed" if daily_stats.dates_fetched == 0 else "partial"
                        else:
                            daily_stats.status = "success"
                        failed = [*daily_stats.failed_dates, *repair_failed]
                        missed = sorted(
                            {f["date"] for f in failed if "date" in f}
                            & {d.isoformat() for d in backfill}
                        )
                        if missed:
                            if daily_stats.status == "success":
                                daily_stats.status = "partial"
                            missed_note = (
                                "Recent listings still missing on "
                                + ", ".join(missed)
                                + "; days before a listing's first stored row are retried"
                                " on the next run"
                            )
                            daily_stats.note = "; ".join(
                                n for n in (daily_stats.note, missed_note) if n
                            )
            except Exception as e:
                logger.exception("Daily refresh failed: %s", e)
                daily_stats.status = "failed"
                daily_stats.failed_dates.append({"error": str(e)})

        if master_outcome is not None and master_outcome.note:
            daily_stats.note = "; ".join(n for n in (master_outcome.note, daily_stats.note) if n)

        # 3-4. Institutional flows, market-wide amounts, then margin (TWSE + TPEx = 2 HTTP calls per
        # date each). Market-wide amounts never change the institutional status; their failures only
        # extend its note.
        chip = self.refresh_chip_data(
            target,
            refresh_institutional=refresh_institutional,
            refresh_margin=refresh_margin,
            margin_target=resolve_margin_target(target, run_start, self.calendar),
            force_from=start_bound if force else None,
        )
        inst_stats = chip.institutional
        if refresh_institutional and chip.market_institutional.note:
            market_note = chip.market_institutional.note
            inst_stats.note = (
                f"{inst_stats.note}; {market_note}" if inst_stats.note else market_note
            )
        margin_stats = chip.margin

        # 4b. Publish indicators and signals for the engines that read the enriched table.
        # A partial run never stores a day past a failed new day, so the stored days have no gap to publish across.
        if refresh_daily and daily_stats.status in {"success", "skipped", "partial"}:
            # Reference-price and publish failures only extend the daily note, never the daily status.
            notes: list[str] = []
            publish_enriched(
                self.daily_store,
                corporate_actions=self.corporate_action_service,
                notes=notes,
                since=written_since,
            )
            if notes:
                daily_stats.note = "; ".join([n for n in (daily_stats.note, *notes) if n])

        # 5. Evaluate Freshness and Overall Status
        run_finish = taipei_now()
        freshness = self.get_freshness(target_date=target)

        executed_statuses = []
        if refresh_daily:
            executed_statuses.append(daily_stats.status)
        executed_statuses.extend([inst_stats.status, margin_stats.status])

        if all(s == "success" for s in executed_statuses):
            overall_status = "success"
        elif any(s == "failed" for s in executed_statuses):
            if any(s == "success" for s in executed_statuses):
                overall_status = "partial"
            else:
                overall_status = "failed"
        elif any(s == "partial" for s in executed_statuses):
            overall_status = "partial"
        else:
            overall_status = "success"

        result = TaiwanDailyUpdateResult(
            run_started_at=run_start.isoformat(),
            run_finished_at=run_finish.isoformat(),
            target_start_date=str(start_bound),
            target_end_date=str(end_bound),
            target_latest_trading_date=str(target),
            daily=daily_stats,
            institutional=inst_stats,
            margin=margin_stats,
            freshness=freshness,
            overall_status=overall_status,
            security_master=master_outcome.summary if master_outcome is not None else None,
        )

        logger.info(
            "TaiwanDailyUpdateService finished: overall_status=%s, daily=%s, inst=%s, margin=%s, is_fully_current=%s",
            overall_status, daily_stats.status, inst_stats.status, margin_stats.status, freshness.is_fully_current,
        )
        return result


def main():
    """Manual entry point for python -m app.taiwan.daily_update."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Taiwan Daily Incremental Update CLI")
    parser.add_argument("--status-only", action="store_true", help="Only display current data freshness")
    parser.add_argument("--skip-daily", action="store_true", help="Skip Daily OHLCV refresh")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to refresh for Daily OHLCV (uses per-symbol path)")
    parser.add_argument("--target-date", type=str, help="Target date YYYY-MM-DD override")
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Force refetch existing dates; with --symbols it widens the date range but only fills "
            "missing rows; with --refresh-master or --master-dry-run the security master also "
            "accepts count drops and delistings beyond its safety limits"
        ),
    )
    parser.add_argument(
        "--refresh-master",
        action="store_true",
        help=(
            "Refresh the security master from the official directories before the update, "
            "whatever the time (otherwise a full-market run for today's session after 16:00 "
            "Asia/Taipei refreshes it until one refresh succeeds that day). Unless the daily step "
            f"is skipped, it refetches the newest {REPAIR_WINDOW_PARTITIONS} stored days that "
            "lack a row of a stock or ETF listed within them"
        ),
    )
    parser.add_argument(
        "--master-dry-run",
        action="store_true",
        help=(
            "Fetch the official directories and print the security master changes a refresh "
            "would make as JSON, then exit without writing anything or running the update"
        ),
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.master_dry_run:
        preview = get_security_master().refresh(dry_run=True, force=args.force)
        print(json.dumps(asdict(preview), indent=2, ensure_ascii=False, default=str))
        return

    svc = TaiwanDailyUpdateService()
    t_date = date.fromisoformat(args.target_date) if args.target_date else None

    if args.status_only:
        freshness = svc.get_freshness(target_date=t_date)
        print(json.dumps(freshness.model_dump(), indent=2, ensure_ascii=False))
        return

    res = svc.run_update(
        target_date=t_date,
        refresh_daily=not args.skip_daily,
        daily_symbols=args.symbols,
        force=args.force,
        refresh_master=True if args.refresh_master else None,
        master_force=args.refresh_master and args.force,
    )
    print(json.dumps(res.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
