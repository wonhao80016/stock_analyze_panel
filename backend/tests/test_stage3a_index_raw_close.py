from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.strategy.engine import DEFAULT_BASIC_FILTER, StrategyDataContext, StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

AS_OF = date(2026, 1, 2)
# Index levels with no raw_close column, as the index enriched store keeps them.
CLOSES = {"A": 2.0, "B": 4.0, "C": 2500.0, "D": 18000.0}

_POLARS = """import polars as pl
META = {
    "id": "index_polars",
    "name": "index_polars",
    "asset_types": ["index"],
    "timeframes": ["1d"],
    "basic_filter": {"boards": None},
}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
"""

_MATRIX = """import numpy as np
from app.backtest.matrix import make_signal_matrix
META = {
    "id": "index_matrix",
    "name": "index_matrix",
    "asset_types": ["index"],
    "timeframes": ["1d"],
    "basic_filter": {"boards": None},
}
EXECUTION_BACKEND = "matrix_native"
class _AllEntries:
    def required_fields(self):
        return frozenset({"close"})
    def required_warmup_bars(self, params):
        return 1
    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))
MATRIX_STRATEGY = _AllEntries()
"""


def _frame() -> pl.DataFrame:
    count = len(CLOSES)
    closes = list(CLOSES.values())
    return pl.DataFrame({
        "symbol": list(CLOSES),
        "name": list(CLOSES),
        "date": [AS_OF] * count,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [1_000_000.0] * count,
        "amount": [1e9] * count,
    })


@pytest.fixture
def engine(tmp_path) -> StrategyEngine:
    (tmp_path / "index_polars.py").write_text(_POLARS, encoding="utf-8")
    (tmp_path / "index_matrix.py").write_text(_MATRIX, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[tmp_path])
    assert engine.load_errors() == []
    return engine


def test_index_context_takes_raw_close_from_close():
    context = StrategyDataContext(
        asset_type="index", timeframe="1d", as_of=AS_OF, current=_frame(), history=_frame(),
    )

    for frame in (context.current, context.history):
        assert frame["raw_close"].to_list() == frame["close"].to_list()


def test_stock_context_still_needs_a_real_raw_close():
    context = StrategyDataContext(asset_type="stock", timeframe="1d", as_of=AS_OF, current=_frame())

    assert "raw_close" not in context.current.columns


@pytest.mark.parametrize("strategy_id", ["index_polars", "index_matrix"])
def test_index_strategy_screens_under_the_default_price_minimum(engine, strategy_id):
    assert DEFAULT_BASIC_FILTER["price_min"] == 3
    context = StrategyDataContext(
        asset_type="index", timeframe="1d", as_of=AS_OF, current=_frame(), history=_frame(),
    )

    result = engine.run(strategy_id, context)

    assert sorted(row["symbol"] for row in result.rows) == ["B", "C", "D"]
