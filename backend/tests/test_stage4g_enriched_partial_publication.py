"""A partial enriched publish commits through EnrichedPublication like a full one."""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.enriched_generation import (
    EnrichedGenerationUnavailableError,
    EnrichedPublication,
    enriched_publication_incomplete,
    get_enriched_generation,
)
from app.repository import DataStore, KlineRepository
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

SYMBOL = "2330.TWSE"
SESSIONS = [
    date(2026, 8, 31) + timedelta(days=i)
    for i in range(12)
    if (date(2026, 8, 31) + timedelta(days=i)).weekday() < 5
]


@pytest.fixture(autouse=True)
def _cached_security_master(monkeypatch) -> None:
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)


def _write_bars(daily: TaiwanDailyStore, closes: list[float], days: list[date]) -> None:
    daily.write_batch(
        pl.DataFrame(
            [
                {
                    "symbol": SYMBOL, "date": d, "open": c, "high": c, "low": c, "close": c,
                    "volume": 1_000_000.0, "amount": c * 1_000_000.0, "quote_ts": None,
                }
                for d, c in zip(days, closes, strict=True)
            ],
            schema_overrides={"quote_ts": pl.Int64},
        )
    )


@pytest.fixture
def published(tmp_path: Path) -> TaiwanEnrichedRefreshService:
    daily = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    _write_bars(daily, [1000.0 + i for i in range(len(SESSIONS))], SESSIONS)
    service = TaiwanEnrichedRefreshService(
        daily_store=daily,
        enriched_root=tmp_path / "kline_daily_enriched",
        share_capital=MagicMock(as_instruments=MagicMock(return_value=pl.DataFrame())),
        corporate_actions=TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions"),
    )
    service.refresh()
    return service


def _refetch_newest_day(service: TaiwanEnrichedRefreshService, tmp_path: Path, close: float) -> None:
    _write_bars(service.daily_store, [close], [SESSIONS[-1]])
    old = SESSIONS[-1]
    enriched = tmp_path / "kline_daily_enriched" / f"date={old.isoformat()}" / "part.parquet"
    stamp = enriched.stat().st_mtime - 60
    os.utime(enriched, (stamp, stamp))


def test_a_partial_publish_replaces_the_generation(published, tmp_path):
    before = get_enriched_generation(tmp_path)
    _refetch_newest_day(published, tmp_path, 1234.0)

    written = published.refresh_recent(days=2)

    assert written > 0
    assert get_enriched_generation(tmp_path) != before
    assert enriched_publication_incomplete(tmp_path) is False


def test_rows_cached_by_generation_are_reread_after_a_partial_publish(published, tmp_path):
    repo = KlineRepository(DataStore(tmp_path))
    first = repo._published_daily_symbol(SYMBOL, SESSIONS[0], SESSIONS[-1])
    assert first.filter(pl.col("date") == SESSIONS[-1])["raw_close"].to_list() == [1000.0 + len(SESSIONS) - 1]
    _refetch_newest_day(published, tmp_path, 1234.0)

    published.refresh_recent(days=2)

    again = repo._published_daily_symbol(SYMBOL, SESSIONS[0], SESSIONS[-1])
    assert again.filter(pl.col("date") == SESSIONS[-1])["raw_close"].to_list() == [1234.0]


def test_a_partial_publish_excludes_other_publications_while_it_writes(published, tmp_path, monkeypatch):
    _refetch_newest_day(published, tmp_path, 1234.0)
    seen: list[bool] = []
    original = published._publish_partitions

    def spy(enriched, publication=None):
        seen.append(enriched_publication_incomplete(tmp_path))
        with pytest.raises(EnrichedGenerationUnavailableError):
            EnrichedPublication(tmp_path, "stock", recover=True).begin()
        return original(enriched, publication)

    monkeypatch.setattr(published, "_publish_partitions", spy)

    published.refresh_recent(days=2)

    assert seen == [True]


@pytest.mark.skipif(os.name != "nt", reason="the retry covers Windows sharing violations only")
def test_a_partial_publish_retries_a_partition_held_open_by_a_reader(published, tmp_path, monkeypatch):
    _refetch_newest_day(published, tmp_path, 1234.0)
    real_replace = os.replace
    refused: list[str] = []

    def replace(source, target):
        if Path(target).name == "part.parquet" and not refused:
            refused.append(str(target))
            raise PermissionError(13, "The process cannot access the file", str(target), 32)
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr("app.enriched_generation.time.sleep", lambda _s: None)

    published.refresh_recent(days=2)

    assert refused
    newest = pl.read_parquet(tmp_path / "kline_daily_enriched" / f"date={SESSIONS[-1].isoformat()}" / "part.parquet")
    assert newest["raw_close"].to_list() == [1234.0]
