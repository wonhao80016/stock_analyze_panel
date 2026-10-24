"""Share-count floors compare with what traded that day, not a volume restated by a later event."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import build_market_data_matrix
from app.strategy.builtin import pullback_resume, swing_trend_confirmation

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOL = "2330.TWSE"
DAYS = [date(2025, 3, 3) + timedelta(days=offset) for offset in range(5)]
SPLIT_DAY = date(2025, 3, 17)
RAW_CLOSE = 100.0
TRADED_SHARES = 1_000_000.0
SPLIT_RATIO = 2.0
STRATEGIES = pytest.mark.parametrize(
    "module", [pullback_resume, swing_trend_confirmation], ids=lambda module: module.__name__
)


def _publication(*, after_split: bool) -> pl.DataFrame:
    """Enriched rows for DAYS as published before, or after, a 1:2 split on SPLIT_DAY.

    After the split the earlier bars are forward-adjusted: prices halve and volume doubles,
    while raw prices and traded value keep what the market printed on each day.
    """
    price_ratio = 1.0 / SPLIT_RATIO if after_split else 1.0
    volume_ratio = SPLIT_RATIO if after_split else 1.0
    count = len(DAYS)
    close = np.full(count, RAW_CLOSE * price_ratio)
    return pl.DataFrame(
        {
            "symbol": [SYMBOL] * count,
            "name": ["TSMC"] * count,
            "date": DAYS,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "raw_close": np.full(count, RAW_CLOSE),
            "raw_high": np.full(count, RAW_CLOSE),
            "raw_low": np.full(count, RAW_CLOSE),
            "volume": np.full(count, TRADED_SHARES * volume_ratio),
            "amount": np.full(count, RAW_CLOSE * TRADED_SHARES),
        }
    )


def _market(module, *, after_split: bool):
    fields = set(module.MATRIX_STRATEGY.required_fields()) - {
        "open", "high", "low", "close", "volume",
    }
    return build_market_data_matrix(_publication(after_split=after_split), field_columns=fields)


@STRATEGIES
def test_a_later_split_does_not_change_which_past_days_pass_the_volume_floor(module):
    assert all(day < SPLIT_DAY for day in DAYS)
    params = {"min_amount": 0.0, "min_volume": 1.5 * TRADED_SHARES}
    known_then = module._liquid(_market(module, after_split=False), params)[:, 0]
    restated = module._liquid(_market(module, after_split=True), params)[:, 0]

    np.testing.assert_array_equal(restated, known_then)
    assert not known_then.any()


@STRATEGIES
def test_the_volume_floor_admits_a_day_whose_traded_shares_reach_it(module):
    params = {"min_amount": 0.0, "min_volume": TRADED_SHARES}
    assert module._liquid(_market(module, after_split=True), params).all()
