"""Strategy rows carry the limit flags published for their date, which pick grading reads."""

from __future__ import annotations

import types
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.api.screener import compute_and_cache_all
from app.services import strategy_cache
from app.services.screener import ScreenerService
from app.strategy.engine import StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

DAYS = [date(2026, 9, 1) + timedelta(days=offset) for offset in range(8)]
AS_OF = DAYS[-1]
SYMBOLS = ("1101.TWSE", "6488.TPEX")
PUBLISHED = {
    "1101.TWSE": {
        "signal_limit_up": True,
        "signal_limit_down": False,
        "signal_broken_limit_up": False,
    },
    "6488.TPEX": {
        "signal_limit_up": False,
        "signal_limit_down": False,
        "signal_broken_limit_up": True,
    },
}

POLARS_STRATEGY = """
import polars as pl

META = {"id": "every_symbol", "name": "every symbol", "asset_types": ["stock"], "limit": 0}
BASIC_FILTER = {"enabled": False}


def filter(df, params):
    return pl.lit(True)
"""

MATRIX_STRATEGY = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "every_bar", "name": "every bar", "asset_types": ["stock"], "limit": 0}
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "matrix_native"


class _EveryBar:
    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return 3

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


MATRIX_STRATEGY = _EveryBar()
"""


def _frame(days) -> pl.DataFrame:
    rows = []
    for day in days:
        for symbol in SYMBOLS:
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": day,
                    "open": 50.0,
                    "high": 55.0,
                    "low": 49.0,
                    "close": 55.0,
                    "raw_close": 55.0,
                    "volume": 1_000_000.0,
                    "amount": 1e9,
                    # Recomputed flags the published ones must replace.
                    "signal_limit_up": not PUBLISHED[symbol]["signal_limit_up"],
                    "signal_limit_down": True,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


class _Repo:
    def __init__(self, data_dir: Path) -> None:
        self.store = types.SimpleNamespace(data_dir=data_dir)

    def taiwan_official_latest_date(self):
        return AS_OF

    def get_enriched_latest_asset(self, asset_type):
        return _frame([AS_OF]), AS_OF

    def get_enriched_history(self, target_date, lookback_days):
        return _frame(DAYS)

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame()


def _publish(data_dir: Path) -> None:
    path = data_dir / "kline_daily_enriched" / f"date={AS_OF.isoformat()}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [
            {"symbol": symbol, "date": AS_OF, "close": 55.0, **flags}
            for symbol, flags in PUBLISHED.items()
        ]
    ).write_parquet(path)


@pytest.fixture
def engine(tmp_path) -> StrategyEngine:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    (strategy_dir / "every_symbol.py").write_text(POLARS_STRATEGY, encoding="utf-8")
    (strategy_dir / "every_bar.py").write_text(MATRIX_STRATEGY, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[strategy_dir])
    assert engine.load_errors() == []
    return engine


@pytest.fixture(autouse=True)
def _clean_history_cache():
    ScreenerService.clear_history_cache()
    yield
    ScreenerService.clear_history_cache()


def _flags(rows: list[dict]) -> dict[str, dict]:
    return {
        str(row["symbol"]): {name: row.get(name) for name in PUBLISHED[str(row["symbol"])]}
        for row in rows
    }


@pytest.mark.parametrize("strategy_id", ["every_symbol", "every_bar"])
def test_strategy_rows_carry_the_published_limit_flags(tmp_path, engine, strategy_id):
    _publish(tmp_path)
    context = ScreenerService(_Repo(tmp_path)).build_strategy_context(engine, AS_OF, [strategy_id])

    result = engine.run(strategy_id, context)

    assert _flags(result.rows) == PUBLISHED


def test_cached_rows_carry_the_published_limit_flags(tmp_path, engine):
    _publish(tmp_path)

    compute_and_cache_all(_Repo(tmp_path), engine, as_of=AS_OF)

    cached = strategy_cache.read_cache(tmp_path)
    for strategy_id in ("every_symbol", "every_bar"):
        assert _flags(cached["results"][strategy_id]["rows"]) == PUBLISHED


def test_rows_keep_their_frame_values_without_a_published_partition(tmp_path, engine):
    context = ScreenerService(_Repo(tmp_path)).build_strategy_context(
        engine, AS_OF, ["every_symbol"]
    )

    result = engine.run("every_symbol", context)

    assert {str(row["symbol"]): row["signal_limit_up"] for row in result.rows} == {
        symbol: not flags["signal_limit_up"] for symbol, flags in PUBLISHED.items()
    }
