"""Realtime writes may only create a partition for a day the exchange published.

The live poller carries the watchlist, not the market. Letting it create a
partition for an unpublished day produced a day that looked complete but held a
handful of symbols, and anything scanning the market off that day found nothing.
The in-memory cache is deliberately still updated: live readers want today.
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.repository import DataStore, KlineRepository

PUBLISHED = date(2026, 9, 17)
UNPUBLISHED = date(2026, 9, 18)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = KlineRepository(DataStore(tmp_path))
    monkeypatch.setattr(
        r, "_official_stock_dates", lambda: frozenset({PUBLISHED}), raising=False
    )
    return r


def _row(symbol: str, d: date, close: float = 10.0) -> pl.DataFrame:
    return pl.DataFrame([{
        "symbol": symbol, "date": d, "open": close, "high": close + 1,
        "low": close - 1, "close": close, "volume": 1000.0, "amount": close * 1000,
    }])


def _partition(repo: KlineRepository, table: str, d: date):
    return repo.store.data_dir / table / f"date={d.isoformat()}" / "part.parquet"


def test_enriched_write_for_unpublished_day_still_updates_cache(repo):
    repo.flush_live_enriched_asset("stock", _row("2330.TWSE", UNPUBLISHED))

    assert not _partition(repo, "kline_daily_enriched", UNPUBLISHED).exists()
    # Live readers must still see today even before the exchange publishes it.
    assert repo.enriched_latest_date() == UNPUBLISHED


def test_force_bypasses_the_gate(repo):
    repo.flush_live_enriched_asset("stock", _row("2330.TWSE", UNPUBLISHED), force=True)
    assert _partition(repo, "kline_daily_enriched", UNPUBLISHED).exists()


def test_write_merges_instead_of_replacing_the_partition(repo):
    """A watchlist-sized write must not drop the symbols already on disk."""
    repo.flush_live_enriched_asset("stock", _row("2330.TWSE", PUBLISHED, 100.0), force=True)
    repo.flush_live_enriched_asset("stock", _row("2454.TWSE", PUBLISHED, 200.0), force=True)

    persisted = pl.read_parquet(_partition(repo, "kline_daily_enriched", PUBLISHED))
    assert set(persisted["symbol"].to_list()) == {"2330.TWSE", "2454.TWSE"}
