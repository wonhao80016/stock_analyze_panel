from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import build_basic_filter_mask, build_market_data_matrix
from app.backtest.strategy import _basic_filter_dependencies
from app.services import screener
from app.strategy.engine import StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(screener.__file__).resolve().parents[1] / "strategy" / "builtin"
BOUNDS = [
    {"price_min": 3.0},
    {"price_max": 3.0},
    {"market_cap_min": 1.0},
    {"float_cap_max": 1.0},
    {"amount_min": 1.0},
    {"turnover_max": 1.0},
]


# A disabled basic filter neither loads the fields of its bounds nor applies them.

@pytest.mark.parametrize("bound", BOUNDS)
def test_disabled_basic_filter_adds_no_backtest_dependencies(bound) -> None:
    assert _basic_filter_dependencies({"enabled": False, **bound}) == set()


@pytest.mark.parametrize("bound", BOUNDS)
def test_disabled_basic_filter_adds_no_matrix_fields(bound) -> None:
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    strategy = engine.get("macd_golden")
    params = engine.resolve_params(strategy, None, None)
    unfiltered = dataclasses.replace(strategy, basic_filter={})
    expected = engine._matrix_field_columns(unfiltered, None, params)

    fields = engine._matrix_field_columns(
        strategy, {"basic_filter": {"enabled": False, **bound}}, params,
    )

    assert fields == expected


def test_disabled_basic_filter_applies_no_bound_on_the_matrix_path() -> None:
    panel = pl.DataFrame({
        "symbol": ["510300", "510300"],
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "open": [2.8, 3.2],
        "high": [2.8, 3.2],
        "low": [2.8, 3.2],
        "close": [2.8, 3.2],
        "volume": [100.0, 100.0],
    })
    mask = build_basic_filter_mask(
        build_market_data_matrix(panel), {"enabled": False, "price_min": 3.0},
    )

    assert np.asarray(mask).all()
