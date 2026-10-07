"""The daily update, the settings update button and the bootstrap import keep the security master current.

A daily run whose target is today, at or after the 16:00 Taipei cutoff, refreshes the master once
before the snapshot fetch, so a listing added today is stored with today's bars and its recent
partitions are refetched. A failed or busy refresh only adds a note. The bootstrap import never
replaces a newer local master. The CLI can force a refresh or preview it without writing.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.indicators import pipeline
from app.taiwan import bootstrap as bootstrap_module
from app.taiwan import daily_update
from app.taiwan.bootstrap import TaiwanBootstrapService
from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import REPAIR_WINDOW_PARTITIONS, TaiwanDailyStore
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.providers.snapshot_provider import OfficialDailySnapshotAdapter
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe import service as service_module
from app.taiwan.universe.models import TaiwanInstrument
from app.taiwan.universe.service import (
    SecurityMasterRefreshResult,
    SecurityMasterUnavailableError,
)

pytestmark = pytest.mark.usefixtures("deny_network")

TODAY = date(2026, 10, 7)
AFTER_CUTOFF = datetime(2026, 10, 7, 16, 30, tzinfo=TAIPEI_TZ)
BEFORE_CUTOFF = datetime(2026, 10, 7, 15, 59, tzinfo=TAIPEI_TZ)
OLD_STAMP = "2026-09-17T17:44:22+08:00"
CAL = TaiwanTradingCalendar()


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(service_module, "_REPLACE_RETRY_SECONDS", 0.0)


def _stock(
    symbol: str, listing_date: str | None = "2001-07-23", stamp: str = OLD_STAMP
) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=f"name-{code}",
        instrument_type="stock",
        listing_status="active",
        listing_date=listing_date,
        isin=None,
        industry="半導體業",
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source=f"{exchange}_ISIN",
        updated_at=stamp,
        underlying_scope="domestic",
    )


BASELINE = ["2330.TWSE", "2317.TWSE", "6488.TPEX", "8069.TPEX"]


class _Directory:
    def __init__(self, instruments: list[TaiwanInstrument]):
        self.instruments = instruments
        self.calls = 0

    def get_instruments(self, html_content=None, official_product_types=None):
        self.calls += 1
        return list(self.instruments)


def _master(tmp_path: Path, added: dict[str, str | None] | None = None) -> TaiwanSecurityMaster:
    """A master saved with BASELINE whose official directories also list *added* (symbol -> listing date)."""
    path = tmp_path / "taiwan" / "security_master.parquet"
    seed = TaiwanSecurityMaster(
        cache_path=path, twse_adapter=_Directory([]), tpex_adapter=_Directory([])
    )
    seed._replace_instruments({s: _stock(s) for s in BASELINE})
    seed.save_cache()
    fresh = [_stock(s) for s in BASELINE] + [_stock(s, d) for s, d in (added or {}).items()]
    master = TaiwanSecurityMaster(
        cache_path=path,
        twse_adapter=_Directory([i for i in fresh if i.exchange == "TWSE"]),
        tpex_adapter=_Directory([i for i in fresh if i.exchange == "TPEX"]),
    )
    assert master.load_cache() is True
    return master


class _Snapshots:
    """MI_INDEX and dailyQuotes for any requested day, listing every code in *twse* / *tpex*.

    TPEx answers 404 for the days in *tpex_down*.
    """

    def __init__(self, twse: list[str], tpex: list[str], tpex_down: set[date] | None = None):
        self.twse = twse
        self.tpex = tpex
        self.tpex_down = tpex_down or set()
        self.requested: list[date] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        raw = request.url.params["date"]
        if "dailyQuotes" in request.url.path:
            roc_year, month, day = (int(part) for part in raw.split("/"))
            if date(roc_year + 1911, month, day) in self.tpex_down:
                return httpx.Response(404)
        if "MI_INDEX" in request.url.path:
            day = datetime.strptime(raw, "%Y%m%d").date()
            self.requested.append(day)
            rows = [
                [c, "n", "1,000", "1", "10,000", "10.00", "10.50", "9.50", "10.00"]
                for c in self.twse
            ]
            return httpx.Response(
                200,
                json={
                    "stat": "OK",
                    "date": raw,
                    "tables": [
                        {
                            "title": "每日收盤行情(全部)",
                            "fields": [
                                "證券代號",
                                "證券名稱",
                                "成交股數",
                                "成交筆數",
                                "成交金額",
                                "開盤價",
                                "最高價",
                                "最低價",
                                "收盤價",
                            ],
                            "data": rows,
                        }
                    ],
                },
            )
        rows = [
            [c, "n", "10.00", "+0.10", "10.00", "10.50", "9.50", "10.00", "2,000", "20,000"]
            for c in self.tpex
        ]
        return httpx.Response(
            200,
            json={
                "stat": "ok",
                "tables": [
                    {
                        "title": "上櫃股票行情",
                        "fields": [
                            "代號",
                            "名稱",
                            "收盤",
                            "漲跌",
                            "開盤",
                            "最高",
                            "最低",
                            "均價",
                            "成交股數",
                            "成交金額(元)",
                        ],
                        "data": rows,
                    }
                ],
            },
        )


def _dated_store(dates: list[date]) -> MagicMock:
    store = MagicMock()
    store.available_dates.return_value = list(dates)
    store.provisional_dates.return_value = []
    return store


def _trading_days_before(end: date, count: int) -> list[date]:
    days: list[date] = []
    cur = end - timedelta(days=1)
    while len(days) < count:
        if CAL.is_trading_day(cur) is not False:
            days.append(cur)
        cur -= timedelta(days=1)
    return sorted(days)


def _seed_partitions(store: TaiwanDailyStore, days: list[date]) -> None:
    for d in days:
        store.write_batch(
            pl.DataFrame(
                {
                    "symbol": ["2330.TWSE", "6488.TPEX"],
                    "date": [d, d],
                    "open": [1.0, 1.0],
                    "high": [1.0, 1.0],
                    "low": [1.0, 1.0],
                    "close": [1.0, 1.0],
                    "volume": [1.0, 1.0],
                    "amount": [1.0, 1.0],
                    "quote_ts": pl.Series([None, None], dtype=pl.Int64),
                }
            )
        )
        final = (
            datetime(d.year, d.month, d.day, tzinfo=TAIPEI_TZ) + timedelta(days=1, hours=12)
        ).timestamp()
        part = store._data_dir / f"date={d.isoformat()}" / "part.parquet"
        os.utime(part, (final, final))


def _update_service(store, daily_service, master) -> TaiwanDailyUpdateService:
    return TaiwanDailyUpdateService(
        daily_store=store,
        inst_store=_dated_store([]),
        margin_store=_dated_store([]),
        daily_service=daily_service,
        inst_service=MagicMock(),
        margin_service=MagicMock(),
        calendar=CAL,
        market_inst_service=MagicMock(),
        corporate_action_service=MagicMock(),
        security_master=master,
    )


def _run(service: TaiwanDailyUpdateService, monkeypatch, now: datetime, **kwargs):
    monkeypatch.setattr(daily_update, "taipei_now", lambda: now)
    monkeypatch.setattr(daily_update, "publish_enriched", lambda *a, **k: True)
    return service.run_update(refresh_institutional=False, refresh_margin=False, **kwargs)


def _fetch_stats() -> dict:
    return {
        "dates_requested": 1,
        "dates_fetched": 1,
        "dates_skipped": 0,
        "total_rows_written": 2,
        "failed_dates": [],
        "repair_failed_dates": [],
    }


def _recording_master(
    order: list[str], result: SecurityMasterRefreshResult | None = None
) -> MagicMock:
    master = MagicMock(spec=TaiwanSecurityMaster)

    def refresh(**kwargs):
        order.append("master")
        return result or SecurityMasterRefreshResult(
            ok=True, dry_run=False, attempted_at=AFTER_CUTOFF.isoformat()
        )

    master.refresh.side_effect = refresh
    master.cache_path = Path("missing") / "security_master.parquet"
    master.refresh_metadata_path = Path("missing") / "security_master.refresh.json"
    master.to_provider_dataframe.return_value = pl.DataFrame()
    return master


def _recording_daily(order: list[str]) -> MagicMock:
    daily = MagicMock()

    def refresh_dates(**kwargs):
        order.append("daily")
        return _fetch_stats()

    daily.refresh_dates.side_effect = refresh_dates
    return daily


# Daily update gating -------------------------------------------------------------------------


def test_refreshes_once_before_the_daily_fetch_after_the_cutoff_on_target_today(
    tmp_path, monkeypatch
):
    order: list[str] = []
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    master = _recording_master(order)

    result = _run(
        _update_service(store, _recording_daily(order), master), monkeypatch, AFTER_CUTOFF
    )

    assert result.target_latest_trading_date == str(TODAY)
    assert order == ["master", "daily"]
    assert master.refresh.call_args.kwargs["now"] == AFTER_CUTOFF
    assert master.refresh.call_args.kwargs.get("force", False) is False
    assert result.daily.status == "success"


@pytest.mark.parametrize(
    ("now", "target"),
    [
        (BEFORE_CUTOFF, None),
        (BEFORE_CUTOFF, TODAY),
        (datetime(2026, 10, 10, 17, 0, tzinfo=TAIPEI_TZ), date(2026, 10, 10)),
        (datetime(2026, 10, 10, 17, 0, tzinfo=TAIPEI_TZ), None),
        (AFTER_CUTOFF, date(2026, 10, 6)),
    ],
    ids=[
        "before-cutoff",
        "before-cutoff-explicit-today",
        "saturday-explicit",
        "saturday",
        "target-not-today",
    ],
)
def test_no_refresh_before_the_cutoff_on_closed_days_or_for_another_target(
    tmp_path, monkeypatch, now, target
):
    order: list[str] = []
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    master = _recording_master(order)

    _run(
        _update_service(store, _recording_daily(order), master),
        monkeypatch,
        now,
        target_date=target,
    )

    master.refresh.assert_not_called()


def test_no_automatic_refresh_without_the_daily_snapshot_step(tmp_path, monkeypatch):
    order: list[str] = []
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    master = _recording_master(order)
    service = _update_service(store, _recording_daily(order), master)

    _run(service, monkeypatch, AFTER_CUTOFF, refresh_daily=False)
    _run(service, monkeypatch, AFTER_CUTOFF, daily_symbols=["2330.TWSE"])

    master.refresh.assert_not_called()


@pytest.mark.parametrize(
    ("result", "fragment"),
    [
        (
            SecurityMasterRefreshResult(
                ok=False, dry_run=False, attempted_at="t", error="TWSE company directory is empty"
            ),
            "TWSE company directory is empty",
        ),
        (
            SecurityMasterRefreshResult(
                ok=False, dry_run=False, attempted_at="t", error="busy", busy=True
            ),
            "another refresh is in progress",
        ),
    ],
    ids=["failed", "busy"],
)
def test_failed_or_busy_refresh_keeps_the_daily_step_successful_with_a_note(
    tmp_path, monkeypatch, result, fragment
):
    order: list[str] = []
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    master = _recording_master(order, result)

    res = _run(_update_service(store, _recording_daily(order), master), monkeypatch, AFTER_CUTOFF)

    assert order == ["master", "daily"]
    assert res.daily.status == "success"
    assert res.overall_status == "success"
    assert "Security master" in res.daily.note
    assert fragment in res.daily.note


def test_refresh_that_raises_keeps_the_daily_step_successful_with_a_note(tmp_path, monkeypatch):
    order: list[str] = []
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    master = _recording_master(order)
    master.refresh.side_effect = RuntimeError("lock exploded")

    res = _run(_update_service(store, _recording_daily(order), master), monkeypatch, AFTER_CUTOFF)

    assert res.daily.status == "success"
    assert "lock exploded" in res.daily.note


def test_explicit_refresh_runs_at_any_time_and_passes_force(tmp_path, monkeypatch):
    order: list[str] = []
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    master = _recording_master(order)

    _run(
        _update_service(store, _recording_daily(order), master),
        monkeypatch,
        BEFORE_CUTOFF,
        refresh_master=True,
        master_force=True,
    )

    master.refresh.assert_called_once()
    assert master.refresh.call_args.kwargs["force"] is True


# Same-run allowlist and new listing backfill -----------------------------------------------------


def _snapshot_daily_service(
    store: TaiwanDailyStore, master: TaiwanSecurityMaster, snapshots: _Snapshots
):
    adapter = OfficialDailySnapshotAdapter(
        security_master=master, client=httpx.Client(transport=httpx.MockTransport(snapshots))
    )
    return TaiwanDailyRefreshService(
        store=store, provider=MagicMock(), calendar=CAL, snapshot_adapter=adapter
    )


def test_listing_added_by_the_refresh_is_stored_in_the_same_run(tmp_path, monkeypatch):
    """The snapshot adapter is built before the refresh, yet its allowlist must include the new listings."""
    master = _master(tmp_path, {"7812.TWSE": "2026-10-07", "7856.TPEX": "2026-10-07"})
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    _seed_partitions(store, [date(2026, 10, 6)])
    snapshots = _Snapshots(["2330", "2317", "7812"], ["6488", "8069", "7856"])
    service = _update_service(store, _snapshot_daily_service(store, master, snapshots), master)

    res = _run(service, monkeypatch, AFTER_CUTOFF)

    stored = set(store.read_range(None, TODAY, TODAY)["symbol"].to_list())
    assert {"7812.TWSE", "7856.TPEX"} <= stored
    assert res.daily.status == "success"
    assert snapshots.requested == [TODAY]


def test_new_listing_refetches_its_partitions_within_the_repair_window(tmp_path, monkeypatch):
    days = _trading_days_before(TODAY, REPAIR_WINDOW_PARTITIONS + 2)
    listed = days[-3]
    master = _master(tmp_path, {"7856.TPEX": listed.isoformat()})
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    _seed_partitions(store, days)
    snapshots = _Snapshots(["2330"], ["6488", "7856"])
    service = _update_service(store, _snapshot_daily_service(store, master, snapshots), master)

    res = _run(service, monkeypatch, AFTER_CUTOFF)

    assert sorted(snapshots.requested) == [*days[-3:], TODAY]
    for d in [*days[-3:], TODAY]:
        assert "7856.TPEX" in store.read_range(None, d, d)["symbol"].to_list()
    assert "7856.TPEX" not in store.read_range(None, days[-4], days[-4])["symbol"].to_list()
    assert res.daily.status == "success"


def _static_master(tmp_path: Path, instruments: list[TaiwanInstrument]) -> TaiwanSecurityMaster:
    path = tmp_path / "taiwan" / "security_master.parquet"
    master = TaiwanSecurityMaster(
        cache_path=path, twse_adapter=_Directory([]), tpex_adapter=_Directory([])
    )
    master._replace_instruments({i.symbol: i for i in instruments})
    master.save_cache()
    return master


def test_symbols_listed_before_the_window_or_delisted_never_trigger_a_refetch(
    tmp_path, monkeypatch
):
    """A halted older stock or a delisted listing has no rows in the window, yet costs no requests."""
    days = _trading_days_before(TODAY, REPAIR_WINDOW_PARTITIONS + 2)
    master = _static_master(
        tmp_path,
        [
            *(_stock(s) for s in BASELINE),
            _stock("1235.TWSE", "2019-03-01"),
            _stock("7856.TPEX", None),
            replace(_stock("6604.TPEX", days[-3].isoformat()), listing_status="delisted"),
        ],
    )
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    _seed_partitions(store, days)
    snapshots = _Snapshots(["2330"], ["6488"])
    service = _update_service(store, _snapshot_daily_service(store, master, snapshots), master)

    _run(service, monkeypatch, AFTER_CUTOFF, refresh_master=False)

    assert snapshots.requested == [TODAY]


def _store_rows(store: TaiwanDailyStore, symbol: str, days: list[date]) -> None:
    for d in days:
        store.write_batch(
            pl.DataFrame(
                {
                    "symbol": [symbol],
                    "date": [d],
                    "open": [1.0],
                    "high": [1.0],
                    "low": [1.0],
                    "close": [1.0],
                    "volume": [1.0],
                    "amount": [1.0],
                    "quote_ts": pl.Series([None], dtype=pl.Int64),
                }
            )
        )


def test_listing_is_pending_only_for_days_before_its_first_stored_row(tmp_path):
    """A halt after a listing's first stored day is not a missing day; missing leading days are."""
    days = _trading_days_before(TODAY, 8)
    master = _static_master(
        tmp_path,
        [
            *(_stock(s) for s in BASELINE),
            _stock("7812.TWSE", days[-6].isoformat()),
            _stock("7856.TPEX", days[-6].isoformat()),
        ],
    )
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    _seed_partitions(store, days)
    _store_rows(store, "7812.TWSE", [days[-6], days[-5], days[-3], days[-2], days[-1]])
    _store_rows(store, "7856.TPEX", [days[-4], days[-2], days[-1]])

    pending = daily_update.pending_listing_dates(store, master, days[-1])

    assert pending == [days[-6], days[-5]]


def test_failed_listing_refetch_is_partial_and_retried_by_the_next_run(tmp_path, monkeypatch):
    days = _trading_days_before(TODAY, 5)
    master = _master(tmp_path, {"7856.TPEX": days[-3].isoformat()})
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    _seed_partitions(store, days)
    snapshots = _Snapshots(["2330"], ["6488", "7856"], tpex_down={days[-3]})
    service = _update_service(store, _snapshot_daily_service(store, master, snapshots), master)

    first = _run(service, monkeypatch, AFTER_CUTOFF)

    assert first.daily.status == "partial"
    assert str(days[-3]) in first.daily.note
    assert "7856.TPEX" not in store.read_range(None, days[-3], days[-3])["symbol"].to_list()
    assert first.security_master["added"] == ["7856.TPEX"]

    snapshots.tpex_down.clear()
    snapshots.requested.clear()
    directory_calls = master.twse_adapter.calls
    evening = datetime(2026, 10, 7, 21, 40, tzinfo=TAIPEI_TZ)
    second = _run(service, monkeypatch, evening)

    assert master.twse_adapter.calls == directory_calls, "refreshed earlier today"
    assert second.security_master is None
    assert days[-3] in snapshots.requested
    assert "7856.TPEX" in store.read_range(None, days[-3], days[-3])["symbol"].to_list()
    assert second.daily.status == "success"


def test_automatic_refresh_is_skipped_after_a_success_earlier_today(tmp_path, monkeypatch):
    order: list[str] = []
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    master = _recording_master(order)
    record = tmp_path / "security_master.refresh.json"
    master.refresh_metadata_path = record
    service = _update_service(store, _recording_daily(order), master)

    record.write_text(
        json.dumps({"last_success": "2026-10-06T16:31:00+08:00", "styles_complete": True}),
        encoding="utf-8",
    )
    _run(service, monkeypatch, AFTER_CUTOFF)
    assert master.refresh.call_count == 1

    record.write_text(
        json.dumps({"last_success": "2026-10-07T16:31:00+08:00", "styles_complete": True}),
        encoding="utf-8",
    )
    _run(service, monkeypatch, datetime(2026, 10, 7, 21, 40, tzinfo=TAIPEI_TZ))
    assert master.refresh.call_count == 1

    _run(service, monkeypatch, datetime(2026, 10, 7, 21, 40, tzinfo=TAIPEI_TZ), refresh_master=True)
    assert master.refresh.call_count == 2


def test_automatic_refresh_skips_an_unreadable_cache_instead_of_starting_from_nothing(
    tmp_path, monkeypatch
):
    master = _master(tmp_path, {"7812.TWSE": "2026-10-07"})
    master.cache_path.write_bytes(b"not parquet")
    unloaded = TaiwanSecurityMaster(
        cache_path=master.cache_path,
        twse_adapter=master.twse_adapter,
        tpex_adapter=master.tpex_adapter,
    )
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    service = _update_service(store, _recording_daily([]), unloaded)

    res = _run(service, monkeypatch, AFTER_CUTOFF)

    assert master.twse_adapter.calls == 0
    assert master.cache_path.read_bytes() == b"not parquet"
    assert "unreadable" in res.daily.note


def test_refresh_dates_raises_an_unavailable_master_before_any_request(tmp_path):
    master = MagicMock(spec=TaiwanSecurityMaster)
    master.ensure_loaded.side_effect = SecurityMasterUnavailableError("cache unreadable")
    master.to_provider_dataframe.side_effect = SecurityMasterUnavailableError("cache unreadable")
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    snapshots = _Snapshots(["2330"], ["6488"])

    with pytest.raises(SecurityMasterUnavailableError):
        _snapshot_daily_service(store, master, snapshots).refresh_dates(TODAY, TODAY)

    assert snapshots.requested == []


def test_refresh_without_new_listings_refetches_nothing(tmp_path, monkeypatch):
    days = _trading_days_before(TODAY, 3)
    master = _master(tmp_path)
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    _seed_partitions(store, days)
    snapshots = _Snapshots(["2330"], ["6488"])
    service = _update_service(store, _snapshot_daily_service(store, master, snapshots), master)

    _run(service, monkeypatch, AFTER_CUTOFF)

    assert snapshots.requested == [TODAY]


# Settings update button ---------------------------------------------------------------------------


def _button(tmp_path: Path, master, days: list[date], refresh_result: dict | None = None):
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    _seed_partitions(store, days)
    refresh = MagicMock()
    refresh.refresh_dates.return_value = refresh_result or {"dates_fetched": 1}
    published: list[bool] = []
    svc = TaiwanBootstrapService(
        store=store,
        refresh_service=refresh,
        publish=lambda only_if_behind: published.append(only_if_behind) or True,
        security_master=master,
    )
    return svc, refresh, published


def _button_clock(monkeypatch, now: datetime) -> None:
    monkeypatch.setattr(bootstrap_module, "taipei_now", lambda: now)
    monkeypatch.setattr(
        bootstrap_module,
        "resolve_target_latest_trading_date",
        lambda: daily_update.resolve_target_latest_trading_date(CAL, as_of_dt=now),
    )


def test_button_refreshes_the_master_and_refetches_new_listings(tmp_path, monkeypatch):
    days = _trading_days_before(TODAY, 5)
    master = _master(tmp_path, {"7856.TPEX": days[-2].isoformat()})
    master.refresh = MagicMock(wraps=master.refresh)
    svc, refresh, _published = _button(tmp_path, master, days)
    _button_clock(monkeypatch, AFTER_CUTOFF)

    res = svc.update_to_latest()

    master.refresh.assert_called_once()
    assert master.refresh.call_args.kwargs["now"] == AFTER_CUTOFF
    refresh.refresh_dates.assert_called_once_with(
        start_date=days[-2], end_date=TODAY, refetch=[days[-2], days[-1]]
    )
    assert res["security_master"]["added"] == ["7856.TPEX"]


@pytest.mark.parametrize(
    "now",
    [BEFORE_CUTOFF, datetime(2026, 10, 10, 17, 0, tzinfo=TAIPEI_TZ)],
    ids=["before-cutoff", "saturday"],
)
def test_button_does_not_refresh_outside_the_window(tmp_path, monkeypatch, now):
    master = _recording_master([])
    svc, _refresh, _published = _button(tmp_path, master, _trading_days_before(TODAY, 5))
    _button_clock(monkeypatch, now)

    svc.update_to_latest()

    master.refresh.assert_not_called()


def test_button_refetches_missing_listing_days_without_a_refresh(tmp_path, monkeypatch):
    """A later click retries a failed backfill even before the refresh window opens."""
    days = _trading_days_before(TODAY, 5)
    master = _static_master(
        tmp_path, [*(_stock(s) for s in BASELINE), _stock("7856.TPEX", days[-2].isoformat())]
    )
    master.refresh = MagicMock(wraps=master.refresh)
    svc, refresh, _published = _button(tmp_path, master, days)
    _button_clock(monkeypatch, BEFORE_CUTOFF)

    svc.update_to_latest()

    master.refresh.assert_not_called()
    refresh.refresh_dates.assert_called_once_with(
        start_date=days[-2], end_date=days[-1], refetch=[days[-2], days[-1]]
    )


def test_button_keeps_updating_when_the_refresh_fails(tmp_path, monkeypatch):
    failed = SecurityMasterRefreshResult(ok=False, dry_run=False, attempted_at="t", error="boom")
    master = _recording_master([], failed)
    svc, refresh, _published = _button(tmp_path, master, _trading_days_before(TODAY, 5))
    _button_clock(monkeypatch, AFTER_CUTOFF)

    res = svc.update_to_latest()

    refresh.refresh_dates.assert_called_once()
    assert res["ok"] is True
    assert res["security_master"]["ok"] is False
    assert "boom" in res["security_master"]["note"]


def test_button_republishes_when_only_the_classification_changed(tmp_path, monkeypatch):
    """A current store would otherwise publish only when behind, so a new limit rule would wait a day."""
    days = _trading_days_before(TODAY, 5)
    changed = SecurityMasterRefreshResult(
        ok=True,
        dry_run=False,
        attempted_at="t",
        changed={"0050.TWSE": {"price_limit_pct": ["unknown", 0.1]}},
    )
    master = _recording_master([], changed)
    svc, refresh, published = _button(tmp_path, master, days)
    now = datetime(2026, 10, 6, 16, 30, tzinfo=TAIPEI_TZ)
    _button_clock(monkeypatch, now)

    res = svc.update_to_latest()

    master.refresh.assert_called_once()
    refresh.refresh_dates.assert_not_called()
    assert res["already_current"] is True
    assert published == [False]


# Bootstrap import --------------------------------------------------------------------------------


def _master_file(path: Path, stamps: list[str], symbols: list[str] | None = None) -> None:
    symbols = symbols or [f"{1101 + i}.TWSE" for i in range(len(stamps))]
    seed = TaiwanSecurityMaster(
        cache_path=path, twse_adapter=_Directory([]), tpex_adapter=_Directory([])
    )
    seed._replace_instruments({s: _stock(s, stamp=t) for s, t in zip(symbols, stamps, strict=True)})
    seed.save_cache()


def test_bootstrap_import_keeps_a_newer_local_master(tmp_path):
    bundled, local = tmp_path / "bundled.parquet", tmp_path / "taiwan" / "security_master.parquet"
    _master_file(bundled, ["2026-09-10T18:00:00+08:00"])
    _master_file(local, ["2026-10-07T16:30:00+08:00", "2026-09-17T17:44:22.089419"])
    before = local.read_bytes()

    assert bootstrap_module.import_security_master(bundled, local) is False
    assert local.read_bytes() == before


def test_bootstrap_import_compares_naive_stamps_as_taipei_time(tmp_path):
    """Older caches mix naive and +08:00 stamps; a naive local stamp is Taipei time, not UTC."""
    bundled, local = tmp_path / "bundled.parquet", tmp_path / "taiwan" / "security_master.parquet"
    _master_file(bundled, ["2026-09-17T18:00:00+08:00"])
    _master_file(local, ["2026-09-17T19:00:00"])

    assert bootstrap_module.import_security_master(bundled, local) is False


def test_bootstrap_import_replaces_an_older_master_and_reloads_the_singleton(tmp_path, monkeypatch):
    bundled, local = tmp_path / "bundled.parquet", tmp_path / "taiwan" / "security_master.parquet"
    _master_file(
        bundled,
        ["2026-09-10T18:00:00+08:00", "2026-09-10T18:00:00+08:00"],
        ["2330.TWSE", "7812.TWSE"],
    )
    _master_file(local, ["2026-09-01T18:00:00+08:00"], ["2330.TWSE"])
    singleton = TaiwanSecurityMaster(
        cache_path=local, twse_adapter=_Directory([]), tpex_adapter=_Directory([])
    )
    assert singleton.load_cache() is True
    monkeypatch.setattr(bootstrap_module, "get_security_master", lambda: singleton)
    pipeline._taiwan_limit_profile_cache["2330.TWSE"] = (0.1, False, None)

    assert bootstrap_module.import_security_master(bundled, local) is True

    assert sorted(pl.read_parquet(local)["symbol"].to_list()) == ["2330.TWSE", "7812.TWSE"]
    assert "2330.TWSE" not in pipeline._taiwan_limit_profile_cache
    assert "7812.TWSE" in singleton._instruments


def test_bootstrap_import_carries_local_delistings_and_management_styles(tmp_path, monkeypatch):
    bundled, local = tmp_path / "bundled.parquet", tmp_path / "taiwan" / "security_master.parquet"
    bundle_stamp, local_stamp = "2026-09-10T18:00:00+08:00", "2026-09-01T18:00:00+08:00"
    etf = replace(
        _stock("00720B.TPEX", stamp=bundle_stamp), instrument_type="etf", etf_category="bond"
    )
    seed = TaiwanSecurityMaster(
        cache_path=bundled, twse_adapter=_Directory([]), tpex_adapter=_Directory([])
    )
    seed._replace_instruments(
        {"2330.TWSE": _stock("2330.TWSE", stamp=bundle_stamp), etf.symbol: etf}
    )
    seed.save_cache()
    seed = TaiwanSecurityMaster(
        cache_path=local, twse_adapter=_Directory([]), tpex_adapter=_Directory([])
    )
    seed._replace_instruments(
        {
            "2330.TWSE": _stock("2330.TWSE", stamp=local_stamp),
            etf.symbol: replace(etf, updated_at=local_stamp, etf_management_style="passive"),
            "8183.TPEX": replace(_stock("8183.TPEX", stamp=local_stamp), listing_status="delisted"),
            "2317.TWSE": _stock("2317.TWSE", stamp=local_stamp),
        },
        delisted_at={"8183.TPEX": "2026-08-31"},
    )
    seed.save_cache()
    monkeypatch.setattr(bootstrap_module, "get_security_master", lambda: MagicMock())

    assert bootstrap_module.import_security_master(bundled, local) is True

    installed = TaiwanSecurityMaster(
        cache_path=local, twse_adapter=_Directory([]), tpex_adapter=_Directory([])
    )
    assert installed.load_cache() is True
    assert installed.get_instrument("00720B.TPEX").etf_management_style == "passive"
    assert installed.get_instrument("8183.TPEX").listing_status == "delisted"
    assert installed.to_dataframe().filter(pl.col("symbol") == "8183.TPEX")[
        "delisted_at"
    ].to_list() == ["2026-08-31"]
    assert installed.get_instrument("2330.TWSE").updated_at == bundle_stamp
    assert installed.get_instrument("2317.TWSE").listing_status == "delisted"
    assert installed.to_dataframe().filter(pl.col("symbol") == "2317.TWSE")[
        "delisted_at"
    ].to_list() == ["2026-09-10"]


def test_bootstrap_import_keeps_the_local_master_while_a_refresh_holds_the_lock(tmp_path):
    bundled, local = tmp_path / "bundled.parquet", tmp_path / "taiwan" / "security_master.parquet"
    _master_file(bundled, ["2026-09-10T18:00:00+08:00"])
    _master_file(local, ["2026-09-01T18:00:00+08:00"])
    before = local.read_bytes()
    with TaiwanSecurityMaster(cache_path=local).refresh_lock() as held:
        assert held
        assert bootstrap_module.import_security_master(bundled, local) is False

    assert local.read_bytes() == before


def test_bootstrap_import_replace_blocked_once_by_a_reader_is_retried(tmp_path, monkeypatch):
    bundled, local = tmp_path / "bundled.parquet", tmp_path / "taiwan" / "security_master.parquet"
    _master_file(bundled, ["2026-09-10T18:00:00+08:00"])
    _master_file(local, ["2026-09-01T18:00:00+08:00"])
    real_replace = os.replace
    attempts: list[int] = []

    def flaky_replace(src, dst):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError("held by a reader")
        return real_replace(src, dst)

    monkeypatch.setattr(service_module.os, "replace", flaky_replace)
    monkeypatch.setattr(bootstrap_module, "get_security_master", lambda: MagicMock())

    assert bootstrap_module.import_security_master(bundled, local) is True
    assert len(attempts) == 2
    assert pl.read_parquet(local)["updated_at"].to_list() == ["2026-09-10T18:00:00+08:00"]


def _bundle(master_path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data/taiwan/security_master.parquet", master_path.read_bytes())
        part = io.BytesIO()
        pl.DataFrame(
            {
                "symbol": ["2330.TWSE"],
                "date": [date(2024, 1, 2)],
                "open": [1.0],
                "high": [1.0],
                "low": [1.0],
                "close": [1.0],
                "volume": [1.0],
                "amount": [1.0],
                "quote_ts": [1],
            }
        ).write_parquet(part)
        z.writestr("data/taiwan/daily/date=2024-01-02/part.parquet", part.getvalue())
    return buf.getvalue()


def test_bootstrap_job_keeps_a_newer_local_master(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.parquet"
    _master_file(bundled, ["2026-09-10T18:00:00+08:00"])
    taiwan_dir = tmp_path / "data" / "taiwan"
    local = taiwan_dir / "security_master.parquet"
    _master_file(local, ["2026-10-07T16:30:00+08:00"])
    before = local.read_bytes()
    payload = _bundle(bundled)

    class _Response:
        status_code = 200

        def __init__(self):
            self.headers = {"content-length": str(len(payload))}

        def iter_bytes(self, chunk_size=1024):
            yield payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def stream(self, method, url):
            return _Response()

    monkeypatch.setattr(bootstrap_module.httpx, "Client", _Client)
    monkeypatch.setattr(bootstrap_module, "taiwan_data_root", lambda: taiwan_dir)
    monkeypatch.setattr(
        bootstrap_module, "resolve_target_latest_trading_date", lambda: date(2024, 1, 2)
    )
    svc = TaiwanBootstrapService(
        download_url="https://mock/data.zip",
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        store=TaiwanDailyStore(data_dir=taiwan_dir / "daily"),
        refresh_service=MagicMock(),
        publish=lambda _: False,
        security_master=MagicMock(),
    )

    job_id = svc.start_bootstrap()
    for _ in range(200):
        job = svc.get_job(job_id)
        if job and job.status in ("success", "failed"):
            break
        time.sleep(0.02)

    assert job.status == "success", job.error
    assert local.read_bytes() == before
    assert (taiwan_dir / "daily" / "date=2024-01-02" / "part.parquet").exists()


# CLI -------------------------------------------------------------------------------------------


def test_cli_master_dry_run_prints_the_diff_and_writes_nothing(tmp_path, monkeypatch, capsys):
    master = _master(tmp_path, {"7812.TWSE": "2026-10-07"})
    files_before = {p: p.read_bytes() for p in master.cache_path.parent.rglob("*") if p.is_file()}

    def no_update_service(*args, **kwargs):
        raise AssertionError("a dry run must not build the update service")

    monkeypatch.setattr(daily_update, "get_security_master", lambda: master)
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", no_update_service)
    monkeypatch.setattr(sys, "argv", ["daily_update", "--master-dry-run"])

    daily_update.main()

    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True
    assert printed["dry_run"] is True
    assert printed["added"] == ["7812.TWSE"]
    files_after = {p: p.read_bytes() for p in master.cache_path.parent.rglob("*") if p.is_file()}
    assert files_after == files_before
    assert "7812.TWSE" not in master._instruments


@pytest.mark.parametrize(
    ("argv", "force"), [([], False), (["--force"], True)], ids=["default", "force"]
)
def test_cli_refresh_master_runs_the_update_with_a_forced_refresh(monkeypatch, capsys, argv, force):
    calls: list[dict] = []

    class _Service:
        def run_update(self, **kwargs):
            calls.append(kwargs)
            result = MagicMock()
            result.model_dump.return_value = {"overall_status": "success"}
            return result

    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", _Service)
    monkeypatch.setattr(sys, "argv", ["daily_update", "--refresh-master", *argv])

    daily_update.main()

    assert len(calls) == 1
    assert calls[0]["refresh_master"] is True
    assert calls[0]["master_force"] is force
    assert json.loads(capsys.readouterr().out) == {"overall_status": "success"}


def test_cli_without_refresh_master_leaves_the_refresh_automatic(monkeypatch, capsys):
    calls: list[dict] = []

    class _Service:
        def run_update(self, **kwargs):
            calls.append(kwargs)
            result = MagicMock()
            result.model_dump.return_value = {}
            return result

    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", _Service)
    monkeypatch.setattr(sys, "argv", ["daily_update"])

    daily_update.main()

    assert calls[0]["refresh_master"] is None


def test_refresh_reports_only_new_active_products_as_added(tmp_path):
    """Unsupported directory entries are refetched every refresh but are never new listings."""
    warrant = replace(
        _stock("03001P.TWSE"),
        instrument_type="unsupported",
        listing_status="unsupported",
        is_supported=False,
    )
    master = _static_master(tmp_path, [*(_stock(s) for s in BASELINE), warrant])
    fresh = [*(_stock(s) for s in BASELINE), warrant, _stock("7812.TWSE", "2026-10-07")]
    master.twse_adapter = _Directory([i for i in fresh if i.exchange == "TWSE"])
    master.tpex_adapter = _Directory([i for i in fresh if i.exchange == "TPEX"])

    result = master.refresh(now=AFTER_CUTOFF)

    assert result.ok, result.error
    assert result.added == ["7812.TWSE"]


class _StyleDirectory(_Directory):
    """A TPEx directory whose ETF/detail lookups answer only the codes in *answers*."""

    def __init__(self, instruments: list[TaiwanInstrument], answers: dict[str, str]):
        super().__init__(instruments)
        self.answers = answers

    def iter_etf_management_styles(self, detail_types):
        for code in detail_types:
            if code in self.answers:
                yield code, self.answers[code]


def _etf_master(tmp_path: Path, answers: dict[str, str]) -> TaiwanSecurityMaster:
    master = _master(tmp_path)
    etf = replace(_stock("00999B.TPEX"), instrument_type="etf", etf_category="bond")
    tpex = [_stock(s) for s in BASELINE if s.endswith(".TPEX")] + [etf]
    master.tpex_adapter = _StyleDirectory(tpex, answers)
    return master


@pytest.mark.parametrize(
    ("answers", "refreshes"),
    [({"00999B": "passive"}, 1), ({}, 2)],
    ids=["styles-complete", "lookups-stopped"],
)
def test_same_day_skip_needs_complete_management_style_lookups(
    tmp_path, monkeypatch, answers, refreshes
):
    master = _etf_master(tmp_path, answers)
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    service = _update_service(store, _recording_daily([]), master)

    _run(service, monkeypatch, AFTER_CUTOFF)
    record = json.loads(master.refresh_metadata_path.read_text(encoding="utf-8"))
    _run(service, monkeypatch, datetime(2026, 10, 7, 21, 40, tzinfo=TAIPEI_TZ))

    assert record["styles_complete"] is (refreshes == 1)
    assert master.twse_adapter.calls == refreshes


def test_first_load_without_style_lookups_does_not_count_as_todays_refresh(tmp_path):
    path = tmp_path / "taiwan" / "security_master.parquet"
    etf = replace(_stock("00999B.TPEX"), instrument_type="etf", etf_category="bond")
    master = TaiwanSecurityMaster(
        cache_path=path,
        twse_adapter=_Directory([_stock(s) for s in BASELINE if s.endswith(".TWSE")]),
        tpex_adapter=_StyleDirectory(
            [_stock(s) for s in BASELINE if s.endswith(".TPEX")] + [etf], {"00999B": "passive"}
        ),
    )

    master.ensure_loaded()

    record = json.loads(master.refresh_metadata_path.read_text(encoding="utf-8"))
    assert record["styles_complete"] is False
    day = datetime.fromisoformat(record["last_success"]).date()
    assert daily_update.master_refreshed_on(master, day) is False
