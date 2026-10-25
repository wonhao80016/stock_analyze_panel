"""Taiwan daily integrity report: calendar from stored TAIEX bars, report only."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_today

pytestmark = pytest.mark.usefixtures("deny_network")

TODAY = date(2026, 9, 24)
MON = date(2026, 9, 14)
TUE = date(2026, 9, 15)
HOLIDAY = date(2026, 9, 16)
THU = date(2026, 9, 17)
FRI = date(2026, 9, 18)
NEXT_MON = date(2026, 9, 21)
NEXT_TUE = date(2026, 9, 22)
NEXT_WED = date(2026, 9, 23)
CALENDAR = (MON, TUE, THU, FRI, NEXT_MON, NEXT_TUE, NEXT_WED)

BOTH_EXCHANGES = ("2330.TWSE", "6488.TPEX")


def _taipei_ts(d: date, t: time) -> float:
    return datetime.combine(d, t, tzinfo=TAIPEI_TZ).timestamp()


def _bars(symbols, d: date, quote_ts: int | None = None) -> pl.DataFrame:
    n = len(symbols)
    return pl.DataFrame(
        {
            "symbol": list(symbols),
            "date": [d] * n,
            "open": [10.0] * n,
            "high": [10.5] * n,
            "low": [9.5] * n,
            "close": [10.0] * n,
            "volume": [1000.0] * n,
            "amount": [10000.0] * n,
            "quote_ts": pl.Series([quote_ts] * n, dtype=pl.Int64),
        }
    )


def _write(path, frame: pl.DataFrame, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)
    os.utime(path, (mtime, mtime))


def _write_index(data_dir, days=CALENDAR, symbol="t00.TWSE") -> None:
    for d in days:
        _write(
            data_dir / "taiwan" / "index_daily" / f"date={d.isoformat()}" / "part.parquet",
            _bars((symbol,), d),
            _taipei_ts(d, time(14, 0)),
        )


def _write_daily(
    data_dir, d: date, symbols=BOTH_EXCHANGES, *, written: float | None = None
) -> float:
    mtime = written if written is not None else _taipei_ts(d + timedelta(days=1), time(18, 0))
    _write(
        data_dir / "taiwan" / "daily" / f"date={d.isoformat()}" / "part.parquet",
        _bars(symbols, d),
        mtime,
    )
    return mtime


def _write_enriched(data_dir, d: date, mtime: float) -> None:
    _write(
        data_dir / "kline_daily_enriched" / f"date={d.isoformat()}" / "part.parquet",
        _bars(BOTH_EXCHANGES, d),
        mtime,
    )


def _healthy_day(data_dir, d: date) -> None:
    written = _write_daily(data_dir, d)
    _write_enriched(data_dir, d, written + 60)


def _scan(data_dir, today: date = TODAY):
    from app.services.data_integrity import integrity_report

    return {(i.day, i.kind) for i in integrity_report(data_dir, today=today).issues}


def test_a_partition_with_one_exchange_is_reported(tmp_path):
    _write_index(tmp_path)
    for d in CALENDAR:
        _healthy_day(tmp_path, d)
    written = _write_daily(tmp_path, THU, ("2330.TWSE",))
    _write_enriched(tmp_path, THU, written + 60)

    assert _scan(tmp_path) == {(THU, "one_exchange")}


def test_a_taiex_date_without_a_daily_partition_is_reported(tmp_path):
    _write_index(tmp_path)
    for d in CALENDAR:
        if d != NEXT_MON:
            _healthy_day(tmp_path, d)

    assert _scan(tmp_path) == {(NEXT_MON, "missing")}


def test_enriched_older_than_its_daily_bars_is_reported(tmp_path):
    _write_index(tmp_path)
    for d in CALENDAR:
        _healthy_day(tmp_path, d)
    written = _write_daily(tmp_path, FRI)
    _write_enriched(tmp_path, FRI, written - 3600)

    assert _scan(tmp_path) == {(FRI, "enriched_stale")}


def test_a_missing_enriched_partition_is_reported(tmp_path):
    _write_index(tmp_path)
    for d in CALENDAR:
        _healthy_day(tmp_path, d)
    (tmp_path / "kline_daily_enriched" / f"date={TUE.isoformat()}" / "part.parquet").unlink()

    assert _scan(tmp_path) == {(TUE, "enriched_stale")}


def test_a_partition_written_on_its_own_day_is_reported_provisional(tmp_path):
    _write_index(tmp_path)
    for d in CALENDAR:
        _healthy_day(tmp_path, d)
    written = _write_daily(tmp_path, NEXT_TUE, written=_taipei_ts(NEXT_TUE, time(16, 40)))
    _write_enriched(tmp_path, NEXT_TUE, written + 60)

    assert _scan(tmp_path) == {(NEXT_TUE, "provisional")}


def test_a_weekday_absent_from_the_index_is_not_reported(tmp_path):
    _write_index(tmp_path)
    for d in CALENDAR:
        if d != NEXT_MON:
            _healthy_day(tmp_path, d)

    issues = _scan(tmp_path)
    assert all(day != HOLIDAY for day, _ in issues)
    assert issues == {(NEXT_MON, "missing")}


def test_leftover_kline_daily_partitions_are_not_reported(tmp_path):
    _write_index(tmp_path)
    for d in CALENDAR:
        _healthy_day(tmp_path, d)
    for d in (THU, FRI, NEXT_TUE):
        snapshot_ms = int(_taipei_ts(d, time(11, 0)) * 1000)
        _write(
            tmp_path / "kline_daily" / f"date={d.isoformat()}" / "part.parquet",
            _bars(("2330.TWSE",), d, snapshot_ms),
            _taipei_ts(d, time(11, 0)),
        )

    from app.services.data_integrity import integrity_report

    assert list(integrity_report(tmp_path, today=TODAY).issues) == []


def test_dates_before_the_stored_daily_history_and_today_are_not_reported(tmp_path):
    _write_index(tmp_path, (*CALENDAR, TODAY))
    for d in CALENDAR:
        if d >= THU:
            _healthy_day(tmp_path, d)

    assert _scan(tmp_path) == set()


def test_without_an_index_the_daily_partitions_are_still_checked(tmp_path):
    written = _write_daily(tmp_path, THU, ("2330.TWSE",))
    _write_enriched(tmp_path, THU, written + 60)
    _healthy_day(tmp_path, FRI)

    assert _scan(tmp_path) == {(THU, "one_exchange")}
    assert not (tmp_path / "taiwan" / "index_daily").exists()


def test_a_bad_partition_after_the_lagging_index_is_reported(tmp_path):
    _write_index(tmp_path, CALENDAR[:-1])
    for d in CALENDAR[:-1]:
        _healthy_day(tmp_path, d)
    written = _write_daily(tmp_path, NEXT_WED, ("6488.TPEX",))
    _write_enriched(tmp_path, NEXT_WED, written + 60)

    assert _scan(tmp_path) == {(NEXT_WED, "one_exchange")}


def test_the_newest_stored_day_is_not_reported_provisional(tmp_path):
    _write_index(tmp_path)
    for d in CALENDAR:
        _healthy_day(tmp_path, d)
    for d in (NEXT_TUE, NEXT_WED):
        written = _write_daily(tmp_path, d, written=_taipei_ts(d, time(16, 40)))
        _write_enriched(tmp_path, d, written + 60)

    assert _scan(tmp_path) == {(NEXT_TUE, "provisional")}


def test_all_four_categories_together(tmp_path):
    _write_index(tmp_path)
    for d in (MON, TUE, NEXT_WED):
        _healthy_day(tmp_path, d)
    written = _write_daily(tmp_path, THU, ("6488.TPEX",))
    _write_enriched(tmp_path, THU, written + 60)
    written = _write_daily(tmp_path, FRI)
    _write_enriched(tmp_path, FRI, written - 60)
    written = _write_daily(tmp_path, NEXT_TUE, written=_taipei_ts(NEXT_TUE, time(16, 40)))
    _write_enriched(tmp_path, NEXT_TUE, written + 60)

    assert _scan(tmp_path) == {
        (THU, "one_exchange"),
        (FRI, "enriched_stale"),
        (NEXT_MON, "missing"),
        (NEXT_TUE, "provisional"),
    }


# -- hooks: report only --------------------------------------------------------


class _QuoteServiceStub:
    def __init__(self):
        self.enabled = False

    @staticmethod
    def is_realtime_allowed():
        return True

    @staticmethod
    def is_paused():
        return False

    @staticmethod
    def realtime_mode():
        return "market"

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False


def _write_leftover_snapshot(data_dir) -> None:
    """A mid-session kline_daily partition on the previous weekday."""
    today = taipei_today()
    probe = today - timedelta(days=1)
    while probe.weekday() >= 5:
        probe -= timedelta(days=1)
    for d, t in ((probe, time(11, 0)), (today, time(10, 0))):
        _write(
            data_dir / "kline_daily" / f"date={d.isoformat()}" / "part.parquet",
            _bars(("2330.TWSE",), d, int(_taipei_ts(d, t) * 1000)),
            _taipei_ts(d, t),
        )


def test_enabling_realtime_is_not_blocked_by_integrity_gaps(tmp_path, monkeypatch):
    from app.api import settings as settings_api

    _write_leftover_snapshot(tmp_path)
    _write_index(tmp_path)
    _write_daily(tmp_path, THU, ("2330.TWSE",))
    saved: dict = {}
    monkeypatch.setattr("app.services.preferences.save", lambda payload: saved.update(payload))

    qs = _QuoteServiceStub()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                quote_service=qs,
                depth_service=None,
                repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            )
        )
    )

    result = settings_api.update_realtime_quotes(
        settings_api.RealtimeQuotesPrefs(realtime_quotes_enabled=True),
        request,
    )

    assert result == {"realtime_quotes_enabled": True, "realtime_allowed": True}
    assert qs.enabled is True
    assert saved == {"realtime_quotes_enabled": True}


def test_boot_check_logs_issues(tmp_path, monkeypatch, caplog):
    from app.services import data_integrity

    _write_leftover_snapshot(tmp_path)
    today = taipei_today()
    yesterday = today - timedelta(days=1)
    _write_index(tmp_path, (yesterday - timedelta(days=1), yesterday))
    _healthy_day(tmp_path, yesterday - timedelta(days=1))

    state = SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        quote_service=None,
    )
    with caplog.at_level(logging.INFO, logger="app.services.data_integrity"):
        data_integrity.boot_integrity_check(state)

    assert any(
        yesterday.isoformat() in r.getMessage() and "missing" in r.getMessage()
        for r in caplog.records
    )


def _boot_messages(data_dir, caplog) -> list[str]:
    from app.services import data_integrity

    state = SimpleNamespace(repo=SimpleNamespace(store=SimpleNamespace(data_dir=data_dir)))
    with caplog.at_level(logging.INFO, logger="app.services.data_integrity"):
        data_integrity.boot_integrity_check(state, today=TODAY)
    return [r.getMessage() for r in caplog.records if r.name == "app.services.data_integrity"]


def test_boot_log_states_the_checked_range(tmp_path, caplog):
    _write_index(tmp_path)
    for d in CALENDAR:
        _healthy_day(tmp_path, d)

    messages = _boot_messages(tmp_path, caplog)
    assert len(messages) == 1
    assert "7 Taiwan trading days 2026-09-14..2026-09-23" in messages[0]
    assert "TAIEX calendar through 2026-09-23" in messages[0]
    assert "no issues found" in messages[0]


def test_boot_log_without_an_index_does_not_claim_complete(tmp_path, caplog):
    for d in CALENDAR:
        _healthy_day(tmp_path, d)

    messages = _boot_messages(tmp_path, caplog)
    assert len(messages) == 1
    assert "no TAIEX bars in the checked range, missing days not detectable" in messages[0]
    assert "complete" not in messages[0]


def test_boot_log_without_daily_partitions_says_nothing_was_checked(tmp_path, caplog):
    _write_index(tmp_path)

    messages = _boot_messages(tmp_path, caplog)
    assert messages == [
        "boot integrity check: no stored Taiwan daily partitions before today, nothing checked"
    ]
