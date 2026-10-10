"""A published row is told from a storage-only row by its derived columns, not by its indicators.

Under the stage-4 indicator conventions a listing's first session has no indicator value at all, so
"every indicator is null" no longer marks a row written without the publish (a storage-only live
merge). Such rows carry no derived column at all; a published row always carries its limit flags.
"""

from __future__ import annotations

import math
import types
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from app.indicators import pipeline
from app.indicators.pipeline import ENRICHED_STORAGE_COLS, INDICATOR_COLUMNS
from app.repository import DataStore, KlineRepository
from app.services.screener import ScreenerService
from app.taiwan.enriched_refresh import add_taiwan_limit_signals

pytestmark = pytest.mark.usefixtures("deny_network")

LISTED = ("1101.TWSE", "6488.TPEX")
NEW_LISTING = "7777.TWSE"
LIVE_ONLY = "600000.SH"
EMPTY_PROFILE = pl.DataFrame(
    schema={
        "symbol": pl.Utf8,
        "limit_pct": pl.Float64,
        "is_etf_tick": pl.Boolean,
        "ipo_start": pl.Date,
        "ipo_end": pl.Date,
    }
)


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _weekdays(date(2025, 1, 6), 130)
TARGET = DAYS[-1]


def _bars(symbol: str, days: list[date], level: float) -> list[dict]:
    rows = []
    for offset, day in enumerate(days):
        close = level + 5.0 * math.sin(offset / 9.0) + offset * 0.01
        rows.append(
            {
                "symbol": symbol,
                "date": day,
                "open": close - 0.1,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "volume": 1_000_000.0,
                "amount": 5e7,
                "raw_close": close,
                "raw_high": close + 0.3,
                "raw_low": close - 0.3,
            }
        )
    return rows


def _published() -> pl.DataFrame:
    """Two listed stocks and one whose first session is TARGET, as the Taiwan publish writes them."""
    rows = _bars(LISTED[0], DAYS, 50.0) + _bars(LISTED[1], DAYS, 80.0)
    rows += _bars(NEW_LISTING, [TARGET], 30.0)
    storage = pl.DataFrame(rows).sort(["symbol", "date"])
    enriched = pipeline.compute_signals(pipeline.compute_indicators(storage))
    return add_taiwan_limit_signals(enriched, EMPTY_PROFILE)


def _write(data_dir: Path, frame: pl.DataFrame) -> None:
    for (day,), part in frame.partition_by("date", as_dict=True).items():
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)


def _merge_storage_only_row(data_dir: Path) -> None:
    """What a forced or non-Taiwan live merge leaves in a published partition."""
    path = data_dir / "kline_daily_enriched" / f"date={TARGET.isoformat()}" / "part.parquet"
    live = pl.DataFrame(_bars(LIVE_ONLY, [TARGET], 10.0))
    assert set(live.columns) <= set(ENRICHED_STORAGE_COLS)
    pl.concat([pl.read_parquet(path), live], how="diagonal_relaxed").write_parquet(path)


class _DiskRepo:
    def __init__(self, data_dir: Path) -> None:
        self.store = types.SimpleNamespace(data_dir=data_dir)

    def get_enriched_latest_asset(self, asset_type):
        return pl.DataFrame(), None

    def get_enriched_history(self, target_date, lookback_days):
        return None

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame()


@pytest.fixture(autouse=True)
def _clean_history_cache():
    ScreenerService.clear_history_cache()
    yield
    ScreenerService.clear_history_cache()


@pytest.fixture
def published() -> pl.DataFrame:
    frame = _published()
    first_bar = frame.filter(pl.col("symbol") == NEW_LISTING)
    assert first_bar.select(
        pl.all_horizontal(pl.col(c).is_null() for c in INDICATOR_COLUMNS)
    ).item()
    return frame


def _recompute_spy(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = pipeline.compute_indicators

    def spy(df, *args, **kwargs):
        calls.append(df.height)
        return real(df, *args, **kwargs)

    monkeypatch.setattr(pipeline, "compute_indicators", spy)
    return calls


def test_a_published_day_with_a_listing_day_row_is_served_as_stored(
    tmp_path, published, monkeypatch
):
    _write(tmp_path, published)
    recomputed = _recompute_spy(monkeypatch)

    current = ScreenerService(_DiskRepo(tmp_path))._load_enriched_for_date(TARGET)

    assert recomputed == []
    stored = published.filter(pl.col("date") == TARGET)
    assert_frame_equal(current.sort("symbol"), stored.sort("symbol"), check_column_order=False)


def test_a_published_day_holding_a_storage_only_row_is_recomputed(tmp_path, published):
    _write(tmp_path, published)
    _merge_storage_only_row(tmp_path)

    assert ScreenerService(_DiskRepo(tmp_path))._published_day(TARGET) is None


def test_storage_only_rows_are_the_rows_without_any_derived_value(published):
    from app.parquet import storage_only_rows

    live = pl.DataFrame(_bars(LIVE_ONLY, [TARGET], 10.0))
    frame = pl.concat([published.filter(pl.col("date") == TARGET), live], how="diagonal_relaxed")

    flagged = frame.filter(storage_only_rows(frame.columns))["symbol"].to_list()

    assert flagged == [LIVE_ONLY]


def test_the_predicate_knows_every_column_a_storage_only_write_keeps():
    from app.parquet import ENRICHED_STORAGE_SCHEMA

    assert set(ENRICHED_STORAGE_SCHEMA) == set(ENRICHED_STORAGE_COLS)


def test_a_frame_of_storage_columns_only_is_all_storage_only_rows():
    from app.parquet import storage_only_rows

    live = pl.DataFrame(_bars(LIVE_ONLY, DAYS[:3], 10.0))

    assert live.select(storage_only_rows(live.columns).all()).item()


def test_the_kline_serves_a_listing_day_row_as_published(tmp_path, published, monkeypatch):
    _write(tmp_path, published)
    repo = KlineRepository(DataStore(tmp_path))
    scans: list[str] = []
    real = repo._scan_daily_symbol

    def spy(symbol, *args, **kwargs):
        scans.append(symbol)
        return real(symbol, *args, **kwargs)

    monkeypatch.setattr(repo, "_scan_daily_symbol", spy)

    rows = repo._published_daily_symbol(NEW_LISTING, DAYS[-5], TARGET)

    assert scans == []
    assert rows["signal_limit_up"].to_list() == [False]
