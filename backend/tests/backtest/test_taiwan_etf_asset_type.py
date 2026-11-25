"""asset_type=etf reads the Taiwan ETF rows of the shared daily enriched partitions.

Taiwan ETFs are published into kline_daily_enriched next to the stocks; only the security
master tells them apart. Backtests and ETF strategy rules asking for etf must see
those rows and nothing else.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.indicators.pipeline import compute_indicators, compute_signals
from app.repository import DataStore, KlineRepository
from app.services.screener import ScreenerService
from app.strategy.engine import StrategyEngine
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

pytestmark = pytest.mark.usefixtures("deny_network")

ETF = "0050.TWSE"
STOCKS = ("1101.TWSE", "2330.TWSE")


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _weekdays(date(2025, 1, 6), 120)

_MATRIX = """import numpy as np
from app.backtest.matrix import make_signal_matrix
META = {"id": "etf_matrix", "name": "etf_matrix", "asset_types": ["stock", "etf"], "timeframes": ["1d"],
        "scoring": {"amount": 1.0}}
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "matrix_native"
MAX_HOLD_DAYS = 2
class _EveryBar:
    def required_fields(self):
        return frozenset({"close"})
    def required_warmup_bars(self, params):
        return 1
    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.isfinite(market.close).astype(np.uint8))
MATRIX_STRATEGY = _EveryBar()
"""

_POLARS = """import polars as pl
META = {"id": "etf_polars", "name": "etf_polars", "asset_types": ["stock", "etf"], "timeframes": ["1d"],
        "scoring": {"amount": 1.0}}
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "polars_expr"
MAX_HOLD_DAYS = 2
def filter(df, params):
    return pl.col("close") > 0
"""


def _write_published(data_dir: Path) -> None:
    rows = []
    for rank, symbol in enumerate((ETF, *STOCKS)):
        for index, day in enumerate(DAYS):
            close = (20.0 + 10.0 * rank) * (1.0 + 0.02 * math.sin(index / 5.0 + rank))
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": close * 0.995,
                    "high": close * 1.005,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 5_000_000.0,
                    "amount": close * 5_000_000.0 * (1.0 + 0.3 * math.cos(index + rank)),
                    "raw_close": close,
                    "raw_high": close * 1.005,
                    "raw_low": close * 0.99,
                }
            )
    service = object.__new__(TaiwanEnrichedRefreshService)
    published = service._add_limit_signals(
        compute_signals(compute_indicators(pl.DataFrame(rows).sort(["symbol", "date"])))
    ).with_columns(pl.lit(None, dtype=pl.Float64).alias("turnover_rate"))
    for (day,), part in published.partition_by("date", as_dict=True).items():
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)


@pytest.fixture
def market(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _write_published(tmp_path)
    custom = tmp_path / "strategies" / "custom"
    custom.mkdir(parents=True)
    (custom / "etf_matrix.py").write_text(_MATRIX, encoding="utf-8")
    (custom / "etf_polars.py").write_text(_POLARS, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[custom])
    assert engine.load_errors() == []
    ScreenerService.clear_history_cache()
    yield engine, KlineRepository(DataStore(tmp_path))
    ScreenerService.clear_history_cache()


def _traded_symbols(engine: StrategyEngine, repo: KlineRepository, strategy_id: str, asset_type: str) -> set[str]:
    result = StrategyBacktestService(BacktestEngine(repo), engine).run(
        StrategyBacktestConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=DAYS[70],
            end=DAYS[-1],
            mode="position",
            max_positions=3,
            matching="open_t+1",
            asset_type=asset_type,
        )
    )
    assert result.error is None, result.error
    assert result.trades
    return {trade["symbol"] for trade in result.trades}


@pytest.mark.parametrize("strategy_id", ["etf_matrix", "etf_polars"])
def test_an_etf_backtest_trades_only_the_taiwan_etf(market, strategy_id):
    engine, repo = market

    assert _traded_symbols(engine, repo, strategy_id, "etf") == {ETF}


@pytest.mark.parametrize("strategy_id", ["etf_matrix", "etf_polars"])
def test_a_stock_backtest_trades_only_stocks(market, strategy_id):
    engine, repo = market

    assert _traded_symbols(engine, repo, strategy_id, "stock") == set(STOCKS)


def test_the_etf_history_loader_returns_only_the_etf_rows(market):
    _, repo = market

    history = ScreenerService(repo, asset_type="etf")._load_enriched_history(DAYS[-1], 5)

    assert set(history["symbol"].to_list()) == {ETF}
    assert history["date"].max() == DAYS[-1]
