from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService


def test_temp_file_is_invisible_to_the_enriched_reader_glob(tmp_path, monkeypatch):
    seen: list[list[str]] = []
    real_replace = Path.replace

    def _spy(self, target):
        seen.append(sorted(p.name for p in Path(target).parent.glob("*.parquet")))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _spy)
    partition = tmp_path / "date=2026-09-23"

    TaiwanEnrichedRefreshService._write_partition(partition, pl.DataFrame({"symbol": ["2330.TWSE"], "close": [1.0]}))
    TaiwanEnrichedRefreshService._write_partition(partition, pl.DataFrame({"symbol": ["2330.TWSE"], "close": [2.0]}))

    assert seen == [[], ["part.parquet"]]
    assert sorted(p.name for p in partition.iterdir()) == ["part.parquet"]


_DAYS = [date(2026, 9, d) for d in (14, 15, 16, 17, 18, 21, 22, 23)]


def _published_service(tmp_path) -> TaiwanEnrichedRefreshService:
    """Every day stored and published, each enriched copy written after its bars."""
    daily = TaiwanDailyStore(data_dir=tmp_path / "daily")
    root = tmp_path / "enriched"
    published = time.time() - 60
    for d in _DAYS:
        daily.write_batch(pl.DataFrame({
            "symbol": ["2330.TWSE"], "date": [d], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
            "volume": [1.0], "amount": [1.0], "quote_ts": pl.Series([None], dtype=pl.Int64),
        }))
        TaiwanEnrichedRefreshService._write_partition(root / f"date={d.isoformat()}", pl.DataFrame({"symbol": ["2330.TWSE"]}))
        os.utime(tmp_path / "daily" / f"date={d.isoformat()}" / "part.parquet", (published - 60, published - 60))
        os.utime(root / f"date={d.isoformat()}" / "part.parquet", (published, published))
    return TaiwanEnrichedRefreshService(daily_store=daily, enriched_root=root, share_capital=MagicMock())


def test_refresh_recent_keeps_to_the_newest_days_when_nothing_older_changed(tmp_path):
    service = _published_service(tmp_path)
    with patch.object(service, "refresh", return_value=0) as refresh:
        service.refresh_recent(days=2)
    refresh.assert_called_once_with(start=date(2026, 9, 22), end=date(2026, 9, 23))


def test_refresh_recent_reaches_back_to_a_day_refetched_after_its_publish(tmp_path):
    """Indicators of the refetched day and of every later day still come from the old bars."""
    service = _published_service(tmp_path)
    refetched = tmp_path / "daily" / "date=2026-09-16" / "part.parquet"
    now = time.time()
    os.utime(refetched, (now, now))

    with patch.object(service, "refresh", return_value=0) as refresh:
        service.refresh_recent(days=2)
    refresh.assert_called_once_with(start=date(2026, 9, 16), end=date(2026, 9, 23))
