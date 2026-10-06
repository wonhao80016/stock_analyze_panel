from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.factor import (
    DERIVED_FACTOR_DEPENDENCIES,
    FACTOR_COLUMNS,
    FactorBacktestService,
)


def test_factor_catalog_covers_normalized_indicator_families():
    factor_ids = [item["id"] for item in FACTOR_COLUMNS]

    assert len(factor_ids) == len(set(factor_ids))
    assert len(factor_ids) > 16
    assert {
        "ma5_bias",
        "ema60_bias",
        "macd_hist_pct",
        "boll_position",
        "atr_pct",
        "kdj_d",
        "vol_ratio_10d",
        "turnover_ratio_5d",
        "log_amount",
        "gap_return",
        "distance_to_high_60d",
        "max_ret_20d",
        "ret_skew_20d",
        "up_days_20d",
        "amihud_20d",
        "turnover_z_60d",
        "vol_price_corr_20d",
        "vwap_bias",
        "vol_trend_5_60",
        "limit_up_count_20d",
        "limit_up_count_60d",
        "pb_latest",
        "roe_latest",
        "revenue_yoy_latest",
        "debt_ratio_latest",
    } <= set(factor_ids)
    assert set(DERIVED_FACTOR_DEPENDENCIES) <= set(factor_ids)


def test_derived_factors_are_computed_from_shared_base_panel():
    start = date(2026, 1, 1)
    rows = []
    for day in range(70):
        close = 10.0 + day
        rows.append({
            "symbol": "000001.SZ",
            "date": start + timedelta(days=day),
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": 1000.0 + day,
            "amount": (1000.0 + day) * close,
            "turnover_rate": 2.0 + day * 0.01,
        })
    factor_names = [
        "ma20_bias",
        "atr_pct",
        "boll_position",
        "vol_ratio_10d",
        "turnover_ratio_5d",
        "log_amount",
        "gap_return",
        "intraday_return",
        "close_position",
        "distance_to_high_60d",
    ]

    panel = FactorBacktestService._compute_missing_factors(
        pl.DataFrame(rows),
        set(factor_names),
    )
    last = panel.tail(1).to_dicts()[0]

    assert set(factor_names) <= set(panel.columns)
    assert last["ma20_bias"] == pytest.approx(79.0 / 69.5 - 1)
    assert last["atr_pct"] == pytest.approx(last["atr_14"] / 79.0)
    assert last["boll_position"] == pytest.approx(
        (79.0 - last["boll_lower"]) / (last["boll_upper"] - last["boll_lower"]),
    )
    assert last["vol_ratio_10d"] == pytest.approx(1069.0 / 1063.5)
    assert last["turnover_ratio_5d"] == pytest.approx(2.69 / 2.66 - 1)
    assert last["log_amount"] == pytest.approx(float(np.log1p(1069.0 * 79.0)))
    assert last["gap_return"] == pytest.approx((79.0 * 0.99) / 78.0 - 1)
    assert last["intraday_return"] == pytest.approx(1 / 0.99 - 1)
    assert last["close_position"] == pytest.approx(2 / 3)
    assert last["distance_to_high_60d"] == pytest.approx(0.0)
