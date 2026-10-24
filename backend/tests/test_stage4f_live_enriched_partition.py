"""A live enriched merge that stays in memory must not leave an empty partition directory on disk."""
from __future__ import annotations

import polars as pl
import pytest

from app.market_time import cn_today
from app.tickflow.repository import DataStore, KlineRepository


def _live_row(symbol: str, close: float) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol],
        "date": [cn_today()],
        "open": [close],
        "high": [close],
        "low": [close],
        "close": [close],
        "volume": [1000.0],
        "amount": [close * 1000.0],
        "raw_close": [close],
        "raw_high": [close],
        "raw_low": [close],
    })


@pytest.mark.parametrize(
    ("asset_type", "symbol", "table"),
    [
        ("stock", "2330.TWSE", "kline_daily_enriched"),
        ("stock", "600000.SH", "kline_daily_enriched"),
        ("etf", "0050.TWSE", "kline_etf_enriched"),
        ("index", "TAIEX.TWSE", "kline_index_enriched"),
    ],
)
def test_memory_only_merge_creates_no_partition_directory(tmp_path, asset_type, symbol, table):
    repo = KlineRepository(DataStore(tmp_path))

    repo.merge_live_enriched_asset(asset_type, _live_row(symbol, 10.0))

    assert not (tmp_path / table / f"date={cn_today().isoformat()}").exists()
    assert repo.latest_enriched_date(asset_type) is None


def test_forced_merge_still_writes_the_partition(tmp_path):
    repo = KlineRepository(DataStore(tmp_path))

    repo.merge_live_enriched_asset("index", _live_row("TAIEX.TWSE", 10.0), force=True)

    out = tmp_path / "kline_index_enriched" / f"date={cn_today().isoformat()}" / "part.parquet"
    assert pl.read_parquet(out)["symbol"].to_list() == ["TAIEX.TWSE"]
