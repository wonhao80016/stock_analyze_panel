"""Repository reads keep enriched partitions whose price columns were stored as Float32 or Int64."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.repository import DataStore, KlineRepository

pytestmark = pytest.mark.usefixtures("deny_network")

DAYS = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]
CLOSE = {DAYS[0]: 10.5, DAYS[1]: 11.0, DAYS[2]: 11.25}
STORED_CLOSE_DTYPE = {DAYS[0]: pl.Float32, DAYS[1]: pl.Int64, DAYS[2]: pl.Float64}
STOCKS = ("2330.TWSE", "8069.TPEX")
ETF = "0050.TWSE"
INDEX = "TAIEX"


def _publish(data_dir: Path, directory: str, symbols: tuple[str, ...]) -> None:
    """One partition per day; the width the close column is stored with varies by day."""
    for day in DAYS:
        price = CLOSE[day]
        frame = pl.DataFrame(
            [
                {
                    "symbol": symbol,
                    "date": day,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 1000.0,
                    "amount": price * 1000.0,
                }
                for symbol in symbols
            ]
        ).with_columns(pl.col("close").cast(STORED_CLOSE_DTYPE[day]))
        path = data_dir / directory / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(path)


@pytest.fixture
def repo(tmp_path) -> KlineRepository:
    _publish(tmp_path, "kline_daily_enriched", STOCKS)
    _publish(tmp_path, "kline_etf_enriched", (ETF,))
    _publish(tmp_path, "kline_index_enriched", (INDEX,))
    return KlineRepository(DataStore(tmp_path))


def _closes(rows: pl.DataFrame) -> dict[date, float]:
    return dict(rows.select("date", "close").iter_rows())


def test_the_batch_daily_scan_keeps_every_stored_close_width(repo):
    rows = repo._scan_daily_batch(list(STOCKS), DAYS[0], DAYS[-1], ["symbol", "date", "close"])

    assert rows.schema["close"] == pl.Float64
    for symbol in STOCKS:
        assert _closes(rows.filter(pl.col("symbol") == symbol)) == CLOSE


@pytest.mark.parametrize(
    ("read", "symbol"),
    [("get_etf_daily", ETF), ("get_index_daily", INDEX)],
    ids=["etf", "index"],
)
def test_the_symbol_daily_reads_keep_every_stored_close_width(repo, read, symbol):
    rows = getattr(repo, read)(symbol, DAYS[0], DAYS[-1], columns=["date", "close"])

    assert _closes(rows) == CLOSE


@pytest.mark.parametrize(("asset_type", "symbol"), [("etf", ETF), ("index", INDEX)])
def test_the_latest_asset_cache_reads_history_of_every_stored_close_width(repo, asset_type, symbol):
    latest, as_of = repo.get_enriched_latest_asset(asset_type)

    assert as_of == DAYS[-1]
    assert latest["symbol"].to_list() == [symbol]
    assert latest["close"].to_list() == [CLOSE[DAYS[-1]]]
    assert "ma5" in latest.columns


def test_the_stock_latest_cache_computes_indicators_over_every_stored_close_width(repo):
    latest, as_of = repo.get_enriched_latest()

    assert as_of == DAYS[-1]
    assert sorted(latest["symbol"].to_list()) == sorted(STOCKS)
    assert latest["close"].to_list() == [CLOSE[DAYS[-1]]] * len(STOCKS)
    assert "ma5" in latest.columns


def test_the_live_aggregation_fallback_reads_every_stored_close_width(repo):
    history, state = repo._build_live_agg_from_parquet(DAYS[-1], DAYS[0])

    assert _closes(history.filter(pl.col("symbol") == STOCKS[0])) == CLOSE
    assert sorted(state["symbol"].to_list()) == sorted(STOCKS)
