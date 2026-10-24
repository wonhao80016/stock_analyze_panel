from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.backtest.strategy import _basic_filter_dependencies
from app.services import screener
from app.strategy.engine import StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(screener.__file__).resolve().parents[1] / "strategy" / "builtin"
PRICE_AND_CAP_BOUNDS = [
    {"price_min": 3.0},
    {"price_max": 3.0},
    {"market_cap_min": 1.0},
    {"market_cap_max": 1.0},
    {"float_cap_min": 1.0},
    {"float_cap_max": 1.0},
]


def _frame(with_raw_close: bool = True) -> pl.DataFrame:
    # Forward adjustment by a later dividend pulls day 0 below 3 while it traded at 3.2.
    frame = pl.DataFrame({
        "symbol": ["1101.TWSE", "1101.TWSE"],
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "close": [2.8, 3.2],
        "raw_close": [3.2, 3.2],
        "total_shares": [1_000_000_000.0, 1_000_000_000.0],
        "float_shares": [1_000_000_000.0, 1_000_000_000.0],
    })
    return frame if with_raw_close else frame.drop("raw_close")


def _kept(frame: pl.DataFrame, bound: dict) -> list[bool]:
    expr = StrategyEngine._basic_filter_expr(frame, bound)
    return frame.select(expr.alias("kept"))["kept"].to_list()


@pytest.mark.parametrize(("bound", "expected"), [
    ({"price_min": 3.0}, [True, True]),
    ({"price_max": 3.0}, [False, False]),
    ({"market_cap_min": 3.0e9}, [True, True]),
    ({"market_cap_max": 3.0e9}, [False, False]),
    ({"float_cap_min": 3.0e9}, [True, True]),
])
def test_polars_basic_filter_bounds_use_the_raw_close(bound, expected):
    assert _kept(_frame(), bound) == expected


@pytest.mark.parametrize("bound", [{"price_min": 1.0}, {"market_cap_min": 1.0}])
def test_polars_basic_filter_bound_without_raw_close_raises(bound):
    with pytest.raises(ValueError, match="raw_close"):
        _kept(_frame(with_raw_close=False), bound)


def test_polars_basic_filter_rejects_rows_whose_raw_close_is_missing():
    frame = _frame().with_columns(pl.Series("raw_close", [None, 3.2], dtype=pl.Float64))

    assert _kept(frame, {"price_min": 3.0}) == [False, True]


@pytest.mark.parametrize("bound", PRICE_AND_CAP_BOUNDS)
def test_backtest_basic_filter_dependencies_load_raw_close_for_price_and_cap_bounds(bound):
    assert "raw_close" in _basic_filter_dependencies({"enabled": True, **bound})


@pytest.mark.parametrize("bound", PRICE_AND_CAP_BOUNDS)
def test_matrix_field_columns_load_raw_close_for_price_and_cap_bounds(bound):
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    strategy = engine.get("macd_golden")
    params = engine.resolve_params(strategy, None, None)

    fields = engine._matrix_field_columns(strategy, {"basic_filter": bound}, params)

    assert "raw_close" in fields
