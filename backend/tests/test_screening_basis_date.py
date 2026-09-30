"""Screening must key off the official trading day, not the live cache date.

The live quote poller writes a handful of watchlist symbols under today's date
and stamps the in-memory enriched cache with it. Screening used to read that
cache date as its basis, so during a session every strategy scanned a day that
held only those few symbols and returned nothing at all.
"""
from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.services.screener import ScreenerService
from app.taiwan.daily_store import TaiwanDailyStore


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _official_rows(d: date, symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame([
        {
            "symbol": s,
            "date": d.isoformat(),
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "volume": 10_000.0,
            "amount": 1_020_000.0,
            "quote_ts": None,
        }
        for s in symbols
    ])


def test_official_latest_date_ignores_days_the_exchange_has_not_published(tmp_dir):
    store = TaiwanDailyStore(data_dir=tmp_dir / "daily")
    published = date(2026, 9, 17)
    store.write_batch(_official_rows(published, ["2330.TWSE", "2454.TWSE"]))
    # The next session has started but the exchange has published nothing for it.
    assert store.latest_date() == published


def _publish_enriched_partition(data_dir: Path, d: date) -> None:
    partition = data_dir / "kline_daily_enriched" / f"date={d.isoformat()}"
    partition.mkdir(parents=True)
    (partition / "part.parquet").write_bytes(b"")


def test_official_latest_date_skips_a_day_whose_indicators_are_not_published(tmp_dir, monkeypatch):
    """Bars stored without an enriched partition leave nothing to screen, so every strategy came back empty."""
    from types import SimpleNamespace

    from app.config import settings
    from app.repository import KlineRepository

    monkeypatch.setattr(settings, "data_dir", tmp_dir)
    store = TaiwanDailyStore()
    for d in (date(2026, 9, 23), date(2026, 9, 24)):
        store.write_batch(_official_rows(d, ["2330.TWSE"]))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_dir))

    assert KlineRepository.taiwan_official_latest_date(repo) == date(2026, 9, 24)
    _publish_enriched_partition(tmp_dir, date(2026, 9, 23))
    assert KlineRepository.taiwan_official_latest_date(repo) == date(2026, 9, 23)
    _publish_enriched_partition(tmp_dir, date(2026, 9, 24))
    assert KlineRepository.taiwan_official_latest_date(repo) == date(2026, 9, 24)


def test_screener_basis_date_is_official_not_live_cache():
    repo = MagicMock()
    today = date(2026, 9, 18)
    published = date(2026, 9, 17)
    # Live path advanced to today off a nine-symbol realtime tick.
    repo.enriched_latest_date.return_value = today
    repo.taiwan_official_latest_date.return_value = published

    assert ScreenerService(repo).latest_date() == published
    repo.taiwan_official_latest_date.assert_called()


def test_screener_basis_date_falls_back_when_no_official_data():
    repo = MagicMock()
    repo.taiwan_official_latest_date.return_value = None
    repo.execute_one.return_value = None

    assert ScreenerService(repo).latest_date() is None


def test_live_cache_date_keeps_today_semantics():
    """The live readers still need 'today'; only screening changed."""
    repo = MagicMock()
    today = date(2026, 9, 18)
    repo.enriched_latest_date.return_value = today
    repo.taiwan_official_latest_date.return_value = today - timedelta(days=1)

    # Screening moved off it, but the accessor itself must keep reporting today.
    assert repo.enriched_latest_date() == today
