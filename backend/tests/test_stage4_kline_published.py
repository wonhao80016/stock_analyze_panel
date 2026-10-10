"""The daily K-line serves the published indicator columns and computes only what a partition lacks."""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from app.indicators.pipeline import ENRICHED_STORAGE_COLS, compute_indicators, compute_signals
from app.repository import DataStore, KlineRepository

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOL = "1101.TWSE"
CHECKED = [
    "ma60",
    "ema60",
    "macd_dif",
    "macd_dea",
    "rsi_14",
    "rsi_24",
    "kdj_k",
    "kdj_d",
    "atr_14",
    "boll_upper",
]


def _days(count: int) -> list[date]:
    days: list[date] = []
    day = date(2025, 1, 6)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _days(330)


def _published() -> pl.DataFrame:
    rows = []
    for offset, day in enumerate(DAYS):
        close = 60.0 + 8.0 * math.sin(offset / 11.0) + 3.0 * math.cos(offset / 4.0)
        rows.append(
            {
                "symbol": SYMBOL,
                "date": day,
                "open": close - 0.2,
                "high": close + 0.8,
                "low": close - 0.9,
                "close": close,
                "volume": 2_000_000.0 + 10_000.0 * (offset % 13),
                "amount": 1.2e8,
                "raw_close": close,
                "raw_high": close + 0.8,
                "raw_low": close - 0.9,
            }
        )
    return compute_signals(compute_indicators(pl.DataFrame(rows)))


def _write(data_dir: Path, frame: pl.DataFrame, storage_only: set[date] = frozenset()) -> None:
    for (day,), part in frame.partition_by("date", as_dict=True).items():
        if day in storage_only:
            part = part.select([c for c in ENRICHED_STORAGE_COLS if c in part.columns])
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)


@pytest.fixture
def published() -> pl.DataFrame:
    return _published()


def _repo(data_dir: Path) -> KlineRepository:
    return KlineRepository(DataStore(data_dir))


def test_kline_equals_the_stored_indicator_columns(tmp_path, published):
    _write(tmp_path, published)
    start, end = DAYS[250], DAYS[-1]

    rows = _repo(tmp_path).get_daily(SYMBOL, start, end)

    stored = published.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    assert_frame_equal(rows.select(["date", *CHECKED]), stored.select(["date", *CHECKED]))


def test_kline_starting_inside_a_short_history_cache_still_equals_stored(tmp_path, published):
    _write(tmp_path, published)
    repo = _repo(tmp_path)
    late = published.filter(pl.col("date") >= DAYS[150]).select(
        [c for c in ENRICHED_STORAGE_COLS if c in published.columns]
    )
    cache = compute_signals(compute_indicators(late))
    repo._enriched_history_cache = cache
    repo._enriched_history_start = cache["date"].min()
    start, end = DAYS[152], DAYS[200]

    rows = repo.get_daily(SYMBOL, start, end)

    stored = published.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    assert_frame_equal(rows.select(["date", *CHECKED]), stored.select(["date", *CHECKED]))


def test_kline_computes_indicators_only_for_partitions_without_them(tmp_path, published):
    unpublished = set(DAYS[-6:-3])
    _write(tmp_path, published, storage_only=unpublished)
    start, end = DAYS[280], DAYS[-1]

    rows = _repo(tmp_path).get_daily(SYMBOL, start, end)

    stored = published.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    assert rows["date"].to_list() == stored["date"].to_list()
    keep = ~pl.col("date").is_in(sorted(unpublished))
    assert_frame_equal(
        rows.filter(keep).select(["date", *CHECKED]), stored.filter(keep).select(["date", *CHECKED])
    )
    filled = rows.filter(~keep)
    assert filled.select(CHECKED).null_count().sum_horizontal().item() == 0
    assert_frame_equal(
        filled.select(["date", "ma60", "boll_upper"]),
        stored.filter(~keep).select(["date", "ma60", "boll_upper"]),
    )


def _count_scans(monkeypatch) -> list[list[str]]:
    from app import repository

    real = repository.scan_parquet_compat
    calls: list[list[str]] = []

    def spy(source, **kwargs):
        calls.append(
            [Path(path).parent.name for path in source] if isinstance(source, list) else []
        )
        return real(source, **kwargs)

    monkeypatch.setattr(repository, "scan_parquet_compat", spy)
    return calls


def _warm_repo(data_dir: Path) -> KlineRepository:
    """A repository whose latest-day cache, which get_daily overlays, is already loaded."""
    repo = _repo(data_dir)
    repo.get_enriched_latest()
    return repo


def test_kline_reads_only_the_partitions_of_its_range(tmp_path, published, monkeypatch):
    _write(tmp_path, published)
    repo = _warm_repo(tmp_path)
    calls = _count_scans(monkeypatch)
    start, end = DAYS[250], DAYS[260]

    repo.get_daily(SYMBOL, start, end)

    assert calls == [[f"date={day.isoformat()}" for day in DAYS[250:261]]]


def test_repeated_kline_request_is_served_from_cache_until_a_publication(
    tmp_path, published, monkeypatch
):
    _write(tmp_path, published)
    repo = _warm_repo(tmp_path)
    calls = _count_scans(monkeypatch)
    start, end = DAYS[250], DAYS[-1]

    first = repo.get_daily(SYMBOL, start, end)
    second = repo.get_daily(SYMBOL, start, end)
    assert len(calls) == 1
    assert_frame_equal(first, second)

    repo._bump_matrix_data_generation("stock")
    repo.get_daily(SYMBOL, start, end)
    assert len(calls) == 2
