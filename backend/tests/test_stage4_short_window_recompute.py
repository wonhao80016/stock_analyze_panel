"""Readers of a short enriched window serve the published indicators (stage 4, D7).

The publish computes EMA/MACD/RSI/ATR/KD over each symbol's whole history. A reader that recomputes
them on a window of a few hundred days seeds them at the window's first bar and disagrees with the
published values, so readers keep published rows as stored, and recompute only rows written without
the publish, from enough earlier sessions for every recursive indicator to leave its warm-up.
"""

from __future__ import annotations

import types
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from app.indicators import pipeline
from app.indicators.pipeline import INDICATOR_COLUMNS
from app.repository import DataStore, KlineRepository
from app.services.screener import ScreenerService
from app.strategy.scoring import recursive_warmup_bars
from app.taiwan import enriched_refresh
from app.taiwan.enriched_refresh import add_taiwan_limit_signals
from app.taiwan.market_rules import ListingSessions

pytestmark = pytest.mark.usefixtures("deny_network")

LISTED = ("1101.TWSE", "2330.TWSE")
# Rows of this symbol carry storage columns only, as a non-Taiwan live merge writes them.
STORAGE_ONLY = "600000.SH"
EMPTY_PROFILE = pl.DataFrame(
    schema={
        "symbol": pl.Utf8,
        "limit_pct": pl.Float64,
        "is_etf_tick": pl.Boolean,
        "ipo_start": pl.Date,
        "ipo_end": pl.Date,
    }
)
INDICATORS = sorted(INDICATOR_COLUMNS)
RECURSIVE = ["ema60", "macd_dea", "rsi_24", "atr_14", "kdj_d"]


def _weekdays(start: date, count: int, weekdays=(0, 1, 2, 3, 4)) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() in weekdays:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _weekdays(date(2023, 1, 2), 700)


def _bars(symbol: str, days: list[date], level: float, seed: int) -> pl.DataFrame:
    """A steep first year then a random walk, so an indicator seeded inside the climb stays off."""
    rng = np.random.default_rng(seed)
    n = len(days)
    drift = np.where(np.arange(n) < 250, 0.006, 0.0)
    close = level * np.exp(np.cumsum(drift + rng.normal(0.0, 0.02, n)))
    spread = close * rng.uniform(0.005, 0.03, n)
    return pl.DataFrame(
        {
            "symbol": [symbol] * n,
            "date": days,
            "open": close * (1.0 + rng.normal(0.0, 0.005, n)),
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": rng.uniform(5e5, 3e6, n),
            "amount": close * 1e6,
            "raw_close": close,
            "raw_high": close + spread,
            "raw_low": close - spread,
        }
    )


def _storage(days: list[date] = DAYS) -> pl.DataFrame:
    frames = [
        _bars(symbol, days, 40.0 + 30.0 * index, index) for index, symbol in enumerate(LISTED)
    ]
    frames.append(_bars(STORAGE_ONLY, days, 12.0, 9))
    return pl.concat(frames).sort(["symbol", "date"])


STORAGE = _storage()
# What the publish computes for every symbol over its whole stored history.
FULL = pipeline.compute_signals(pipeline.compute_indicators(STORAGE))


def _write(data_dir: Path, storage: pl.DataFrame = STORAGE) -> pl.DataFrame:
    """The Taiwan publish's partitions, with the non-Taiwan symbol merged in storage-only rows."""
    listed = storage.filter(pl.col("symbol") != STORAGE_ONLY)
    published = add_taiwan_limit_signals(
        pipeline.compute_signals(pipeline.compute_indicators(listed)), EMPTY_PROFILE
    )
    merged = pl.concat(
        [published, storage.filter(pl.col("symbol") == STORAGE_ONLY)], how="diagonal_relaxed"
    )
    for (day,), part in merged.partition_by("date", as_dict=True).items():
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)
    return published


class _DiskRepo:
    """Serves nothing from caches, so the screener reads the partitions."""

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


def _assert_published(frame: pl.DataFrame, published: pl.DataFrame) -> None:
    listed = frame.filter(pl.col("symbol").is_in(LISTED)).sort(["symbol", "date"])
    expected = published.join(listed.select("symbol", "date"), on=["symbol", "date"], how="semi")
    expected = expected.sort(["symbol", "date"])
    for columns in (
        ["symbol", "date", *INDICATORS, "signal_macd_golden"],
        ["signal_limit_up", "signal_limit_down"],
    ):
        assert_frame_equal(listed.select(columns), expected.select(columns))


def _assert_recomputed_like_full_history(frame: pl.DataFrame) -> None:
    rows = frame.filter(pl.col("symbol") == STORAGE_ONLY).select(["date", *RECURSIVE]).sort("date")
    full = FULL.filter(pl.col("symbol") == STORAGE_ONLY).join(
        rows.select("date"), on="date", how="semi"
    )
    full = full.select(["date", *RECURSIVE]).sort("date")
    assert rows.height == full.height > 0
    for name in RECURSIVE:
        np.testing.assert_allclose(
            rows[name].to_numpy(), full[name].to_numpy(), rtol=1e-3, atol=1e-3
        )


def test_the_repository_history_cache_holds_the_published_values(tmp_path):
    published = _write(tmp_path)
    repo = KlineRepository(DataStore(tmp_path))

    repo._refresh_enriched()

    cache = repo._enriched_history_cache
    assert cache["date"].min() == min(day for day in DAYS if day >= DAYS[-1] - timedelta(days=300))
    _assert_published(cache, published)
    latest, latest_date = repo.get_enriched_latest()
    assert latest_date == DAYS[-1]
    _assert_published(latest, published)


def test_the_repository_history_cache_recomputes_storage_only_rows_from_their_warmup(tmp_path):
    _write(tmp_path)
    repo = KlineRepository(DataStore(tmp_path))

    repo._refresh_enriched()

    _assert_recomputed_like_full_history(repo._enriched_history_cache)


def test_a_rejected_historical_day_keeps_its_published_rows(tmp_path):
    published = _write(tmp_path)
    target = DAYS[-120]
    screener = ScreenerService(_DiskRepo(tmp_path))
    assert screener._published_day(target) is None

    current = screener._load_enriched_for_date(target)

    assert current["date"].unique().to_list() == [target]
    _assert_published(current, published)
    _assert_recomputed_like_full_history(current)


def test_a_recomputed_day_reads_the_recursive_warmup_in_sessions(tmp_path, monkeypatch):
    # Three sessions a week: a calendar-day warm-up would hold too few sessions.
    days = _weekdays(date(2022, 1, 3), 500, weekdays=(0, 2, 4))
    _write(tmp_path, _storage(days))
    real = pipeline.compute_indicators
    seen: list[int] = []

    def spy(df, *args, **kwargs):
        seen.append(df["date"].n_unique())
        return real(df, *args, **kwargs)

    monkeypatch.setattr(pipeline, "compute_indicators", spy)

    ScreenerService(_DiskRepo(tmp_path))._load_enriched_for_date(days[-1])

    assert seen and min(seen) >= recursive_warmup_bars("ema60")


def test_the_history_slow_path_serves_the_published_values(tmp_path):
    published = _write(tmp_path)
    target = DAYS[-30]

    history = ScreenerService(_DiskRepo(tmp_path))._load_enriched_history(target, 300)

    sessions = history["date"].unique().sort().to_list()
    assert sessions == DAYS[DAYS.index(target) - 300 : DAYS.index(target) + 1]
    _assert_published(history, published)
    _assert_recomputed_like_full_history(history)


def test_the_kline_recomputes_storage_only_rows_from_their_warmup(tmp_path):
    _write(tmp_path)
    repo = KlineRepository(DataStore(tmp_path))

    rows = repo.get_daily(STORAGE_ONLY, DAYS[-100], DAYS[-1])

    assert rows["date"].to_list() == DAYS[-100:]
    _assert_recomputed_like_full_history(rows.with_columns(pl.lit(STORAGE_ONLY).alias("symbol")))


class _LocalStore:
    """A TaiwanDailyStore holding STORAGE."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def scan_range(self, symbols, start, end):
        return STORAGE.filter(
            pl.col("symbol").is_in(list(symbols)) & pl.col("date").is_between(start, end)
        ).select("symbol", "date", "open", "high", "low", "close", "volume", "amount")

    def has_date(self, day):
        return day in DAYS


def test_the_local_daily_k_fallback_matches_the_full_history(monkeypatch):
    from app.api import kline
    from app.taiwan import daily_store

    monkeypatch.setattr(daily_store, "TaiwanDailyStore", _LocalStore)
    calendar = ListingSessions(tuple(DAYS), {symbol: DAYS[0] for symbol in LISTED})
    monkeypatch.setattr(enriched_refresh, "listing_sessions", lambda _store=None: calendar)
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.get_daily_asset.return_value = pl.DataFrame()
    request = MagicMock()
    request.app.state.repo = repo
    request.app.state.quote_service = None
    symbol = LISTED[0]

    resp = kline.get_daily(
        request,
        symbol,
        days=120,
        start_date=str(DAYS[-100]),
        end_date=str(DAYS[-1]),
        ext_columns=None,
    )

    assert resp["source"] == "taiwan_local"
    rows = (
        pl.DataFrame(resp["rows"])
        .with_columns(pl.col("date").cast(pl.Utf8).str.slice(0, 10).str.to_date())
        .sort("date")
    )
    assert rows["date"].to_list() == DAYS[-100:]
    full = FULL.filter((pl.col("symbol") == symbol) & (pl.col("date") >= DAYS[-100])).sort("date")
    for name in RECURSIVE:
        np.testing.assert_allclose(
            rows[name].to_numpy(), full[name].to_numpy(), rtol=1e-3, atol=1e-3
        )
