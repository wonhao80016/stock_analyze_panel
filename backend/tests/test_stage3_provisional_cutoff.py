"""A daily partition written on its trading day after the publication cutoff is final."""

from __future__ import annotations

import os
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.market_intelligence import stored_partition_status
from app.taiwan.realtime.calendar import TAIPEI_TZ

D = date(2026, 9, 15)
PREV = date(2026, 9, 14)
BOTH = ["2330.TWSE", "6488.TPEX"]


def _at(d: date, hour: int, minute: int = 0, second: int = 0) -> float:
    return datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=TAIPEI_TZ).timestamp()


def _rows(symbols: list[str], d: date) -> pl.DataFrame:
    n = len(symbols)
    return pl.DataFrame(
        {
            "symbol": symbols,
            "date": [d] * n,
            "open": [10.0] * n,
            "high": [10.5] * n,
            "low": [9.5] * n,
            "close": [10.0] * n,
            "volume": [1000.0] * n,
            "amount": [10000.0] * n,
            "quote_ts": pl.Series([None] * n, dtype=pl.Int64),
        }
    )


def _set_mtime(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def _part(root: Path, d: date) -> Path:
    return root / f"date={d.isoformat()}" / "part.parquet"


@pytest.fixture
def store(tmp_path) -> TaiwanDailyStore:
    return TaiwanDailyStore(data_dir=tmp_path / "daily")


def _written(store: TaiwanDailyStore, d: date, when: float, symbols: list[str] = BOTH) -> None:
    store.write_batch(_rows(symbols, d))
    _set_mtime(_part(store._data_dir, d), when)


def test_default_cutoff_is_half_past_nine_in_the_evening(deny_network):
    assert settings.taiwan_daily_final_cutoff == time(21, 30)


def test_cutoff_is_read_from_the_environment(deny_network, monkeypatch):
    monkeypatch.setenv("TAIWAN_DAILY_FINAL_CUTOFF", "18:15")
    assert Settings().taiwan_daily_final_cutoff == time(18, 15)


@pytest.mark.parametrize("raw", ["", "  "])
def test_a_blank_cutoff_keeps_the_default(deny_network, monkeypatch, raw):
    monkeypatch.setenv("TAIWAN_DAILY_FINAL_CUTOFF", raw)
    assert Settings().taiwan_daily_final_cutoff == time(21, 30)


@pytest.mark.parametrize("raw", ["21:30+08:00", "21:30Z"])
def test_a_cutoff_with_a_timezone_is_rejected(deny_network, monkeypatch, raw):
    monkeypatch.setenv("TAIWAN_DAILY_FINAL_CUTOFF", raw)
    with pytest.raises(ValidationError, match="Taipei wall-clock time without a timezone"):
        Settings()


@pytest.mark.parametrize(
    ("hour", "minute", "second", "provisional"),
    [
        (16, 35, 0, True),
        (21, 29, 59, True),
        (21, 30, 0, False),
        (21, 45, 0, False),
        (23, 59, 59, False),
    ],
    ids=["afternoon", "just-before", "at-cutoff", "evening", "last-second"],
)
def test_a_same_day_write_is_final_from_the_cutoff_on(
    deny_network, store, hour, minute, second, provisional
):
    _written(store, D, _at(D, hour, minute, second))

    assert store.provisional_dates([D]) == ([D] if provisional else [])


def test_the_cutoff_follows_the_setting(deny_network, store, monkeypatch):
    monkeypatch.setattr(settings, "taiwan_daily_final_cutoff", time(22, 0))
    _written(store, D, _at(D, 21, 45))

    assert store.provisional_dates([D]) == [D]


def test_a_write_on_a_later_day_stays_final_and_one_before_the_day_stays_provisional(
    deny_network, store
):
    _written(store, D, _at(date(2026, 9, 16), 9, 0))
    _written(store, PREV, _at(date(2026, 9, 13), 22, 0))

    assert store.provisional_dates([PREV, D]) == [PREV]


def test_repair_dates_skip_a_day_written_after_the_cutoff(deny_network, store):
    _written(store, PREV, _at(PREV, 21, 45))
    _written(store, D, _at(D, 16, 35))

    assert store.repair_dates(D) == [D]


def test_market_partition_status_is_complete_after_the_evening_write(deny_network, store):
    _written(store, D, _at(D, 21, 45))
    assert stored_partition_status(store, [D]) == "complete"

    _set_mtime(_part(store._data_dir, D), _at(D, 16, 35))
    assert stored_partition_status(store, [D]) == "provisional"


def test_institutional_partitions_keep_the_calendar_day_rule(deny_network, tmp_path):
    inst = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    inst.write_batch(
        pl.DataFrame(
            [
                {
                    "symbol": "2330.TWSE",
                    "date": D,
                    "foreign_net": 1,
                    "investment_trust_net": 1,
                    "dealer_net": 1,
                },
                {
                    "symbol": "8069.TPEX",
                    "date": D,
                    "foreign_net": 1,
                    "investment_trust_net": 1,
                    "dealer_net": 1,
                },
            ]
        )
    )
    _set_mtime(_part(tmp_path / "institutional", D), _at(D, 21, 45))

    assert inst.provisional_dates([D]) == [D]


def test_skip_existing_keeps_a_provisional_partition_provisional_before_the_cutoff(
    deny_network, store
):
    _written(store, D, _at(D, 16, 35), symbols=["2330.TWSE"])

    store.write_batch(_rows(["6488.TPEX"], D), skip_existing=True)

    assert store.provisional_dates([D]) == [D]
    assert store.partition_mtime(D) == _at(D, 21, 29, 59)
    assert pl.read_parquet(_part(store._data_dir, D))["symbol"].to_list() == BOTH


def test_skip_existing_leaves_a_final_evening_partition_final(deny_network, store):
    _written(store, D, _at(D, 21, 45), symbols=["2330.TWSE"])

    store.write_batch(_rows(["6488.TPEX"], D), skip_existing=True)

    assert store.provisional_dates([D]) == []


def test_skip_existing_on_a_provisional_day_still_marks_its_enriched_partition_stale(
    deny_network, store, tmp_path
):
    root = tmp_path / "enriched"
    _written(store, D, _at(D, 16, 35), symbols=["2330.TWSE"])
    TaiwanEnrichedRefreshService._write_partition(
        root / f"date={D.isoformat()}", pl.DataFrame({"symbol": ["2330.TWSE"]})
    )
    _set_mtime(_part(root, D), _at(D, 16, 45))
    enriched = TaiwanEnrichedRefreshService(
        daily_store=store, enriched_root=root, share_capital=MagicMock()
    )
    assert enriched.stale_since() is None

    store.write_batch(_rows(["6488.TPEX"], D), skip_existing=True)

    assert store.provisional_dates([D]) == [D]
    assert enriched.stale_since() == D
