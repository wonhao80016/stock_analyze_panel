"""Taiwan daily integrity report: ordering, window and log text."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

import polars as pl
import pytest

from app.services.data_integrity import (
    IntegrityIssue,
    describe_issues,
    integrity_report,
)
from app.taiwan.realtime.calendar import TAIPEI_TZ

pytestmark = pytest.mark.usefixtures("deny_network")

TODAY = date(2026, 9, 24)
DAYS = (
    date(2026, 9, 17),
    date(2026, 9, 18),
    date(2026, 9, 21),
    date(2026, 9, 22),
    date(2026, 9, 23),
)


def _write(path, symbols, d: date, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(symbols)
    pl.DataFrame(
        {
            "symbol": list(symbols),
            "date": [d] * n,
            "close": [10.0] * n,
        }
    ).write_parquet(path)
    os.utime(path, (mtime, mtime))


def _final_mtime(d: date) -> float:
    return datetime.combine(d + timedelta(days=1), time(18, 0), tzinfo=TAIPEI_TZ).timestamp()


def _store(data_dir, days=DAYS) -> None:
    for d in days:
        _write(
            data_dir / "taiwan" / "index_daily" / f"date={d}" / "part.parquet",
            ("t00.TWSE",),
            d,
            _final_mtime(d),
        )
        _write(
            data_dir / "taiwan" / "daily" / f"date={d}" / "part.parquet",
            ("2330.TWSE", "6488.TPEX"),
            d,
            _final_mtime(d),
        )
        _write(
            data_dir / "kline_daily_enriched" / f"date={d}" / "part.parquet",
            ("2330.TWSE",),
            d,
            _final_mtime(d) + 60,
        )


def test_complete_history_reports_nothing(tmp_path):
    _store(tmp_path)
    assert list(integrity_report(tmp_path, today=TODAY).issues) == []


def test_an_unreadable_partition_counts_as_one_exchange(tmp_path):
    _store(tmp_path)
    (tmp_path / "taiwan" / "daily" / "date=2026-09-21" / "part.parquet").write_bytes(b"not parquet")
    os.utime(
        tmp_path / "taiwan" / "daily" / "date=2026-09-21" / "part.parquet",
        (_final_mtime(DAYS[2]),) * 2,
    )

    kinds = {(i.day, i.kind) for i in integrity_report(tmp_path, today=TODAY).issues}
    assert (DAYS[2], "one_exchange") in kinds


def test_issues_are_sorted_by_day_then_kind(tmp_path):
    _store(tmp_path)
    (tmp_path / "kline_daily_enriched" / "date=2026-09-18" / "part.parquet").unlink()
    _write(
        tmp_path / "taiwan" / "daily" / "date=2026-09-18" / "part.parquet",
        ("2330.TWSE",),
        DAYS[1],
        datetime.combine(DAYS[1], time(16, 40), tzinfo=TAIPEI_TZ).timestamp(),
    )
    (tmp_path / "taiwan" / "daily" / "date=2026-09-17" / "part.parquet").unlink()
    (tmp_path / "taiwan" / "daily" / "date=2026-09-17").rmdir()
    _store(tmp_path, (date(2026, 9, 10),))

    issues = list(integrity_report(tmp_path, today=TODAY).issues)
    assert [(i.day, i.table, i.kind) for i in issues] == [
        (DAYS[0], "taiwan_daily", "missing"),
        (DAYS[1], "taiwan_daily", "one_exchange"),
        (DAYS[1], "taiwan_daily", "provisional"),
        (DAYS[1], "kline_daily_enriched", "enriched_stale"),
    ]


def test_only_the_newest_sessions_are_scanned(tmp_path):
    _store(tmp_path)
    (tmp_path / "kline_daily_enriched" / "date=2026-09-17" / "part.parquet").unlink()

    assert list(integrity_report(tmp_path, today=TODAY, sessions=4).issues) == []
    assert [i.day for i in integrity_report(tmp_path, today=TODAY, sessions=5).issues] == [DAYS[0]]


def test_describe_issues_groups_days_by_kind():
    issues = [
        IntegrityIssue(DAYS[2], "taiwan_daily", "missing"),
        IntegrityIssue(DAYS[0], "taiwan_daily", "missing"),
        IntegrityIssue(DAYS[1], "kline_daily_enriched", "enriched_stale"),
    ]
    assert describe_issues(issues) == "missing: 2026-09-17, 2026-09-21; enriched_stale: 2026-09-18"
    assert describe_issues([]) == ""


def test_disabling_realtime_still_saves(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.api import settings as settings_api

    saved: dict = {}
    monkeypatch.setattr("app.services.preferences.save", lambda payload: saved.update(payload))
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                quote_service=None,
                repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            )
        )
    )

    result = settings_api.update_realtime_quotes(
        settings_api.RealtimeQuotesPrefs(realtime_quotes_enabled=False),
        request,
    )
    assert result == {"realtime_quotes_enabled": False, "realtime_allowed": True}
    assert saved == {"realtime_quotes_enabled": False}
