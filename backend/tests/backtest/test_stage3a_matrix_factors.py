from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import (
    RealtimeMarketDataMatrix,
    build_basic_filter_mask,
    build_market_data_matrix,
    load_market_data_matrix_from_parquet,
    matrix_feature,
)
from app.backtest.regime_alignment import align_regime_t_minus_one, build_regime_filter_mask
from app.strategy.scoring import materialize_scoring_columns, scoring_dependencies

# Loader version of every disk cache written before vwap_bias and the turnover factors changed.
_LOADER_VERSION_BEFORE_FACTOR_FIX = 9


# B008: vwap_bias compares a close and a VWAP on one price scale.

def test_vwap_bias_uses_the_raw_close_against_the_raw_vwap_across_an_ex_dividend_date():
    # One trade price per day, so the true bias is 0; day 0 precedes a 5% cash dividend.
    panel = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE"],
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "open": [95.0, 100.0],
        "high": [95.0, 100.0],
        "low": [95.0, 100.0],
        "close": [95.0, 100.0],
        "raw_close": [100.0, 100.0],
        "volume": [1_000.0, 1_000.0],
        "amount": [100_000.0, 100_000.0],
    })
    market = build_market_data_matrix(panel, field_columns={"amount", "raw_close"})

    values = matrix_feature(market, "vwap_bias")[:, 0]

    assert values.tolist() == pytest.approx([0.0, 0.0], abs=1e-6)


def _ex_dividend_panel() -> pl.DataFrame:
    # One trade price per day, so the true bias is 0, 0 and -0.98%; day 0 precedes a cash dividend.
    return pl.DataFrame({
        "symbol": ["2330.TWSE"] * 3,
        "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "open": [90.0, 100.0, 101.0],
        "high": [90.0, 100.0, 101.0],
        "low": [90.0, 100.0, 101.0],
        "close": [90.0, 100.0, 101.0],
        "raw_close": [100.0, 100.0, 101.0],
        "volume": [1_000.0, 1_000.0, 1_000.0],
        "amount": [100_000.0, 100_000.0, 102_000.0],
    })


_EX_DIVIDEND_BIAS = [0.0, 0.0, 101.0 / 102.0 - 1.0]


def test_vwap_bias_matches_between_matrix_and_polars_paths_across_an_ex_dividend_date():
    panel = _ex_dividend_panel()
    market = build_market_data_matrix(panel, field_columns={"amount", "raw_close"})

    matrix_values = matrix_feature(market, "vwap_bias")[:, 0].astype(np.float64)
    polars_values = materialize_scoring_columns(panel, {"vwap_bias"})["vwap_bias"].to_numpy()

    np.testing.assert_allclose(matrix_values, _EX_DIVIDEND_BIAS, atol=1e-6)
    np.testing.assert_allclose(polars_values, _EX_DIVIDEND_BIAS, atol=1e-9)


def test_vwap_bias_built_from_its_declared_dependencies_is_correct():
    panel = _ex_dividend_panel()
    dependencies = scoring_dependencies({"vwap_bias": 1.0})
    market = build_market_data_matrix(panel, field_columns=dependencies)

    assert "raw_close" in dependencies
    np.testing.assert_allclose(
        matrix_feature(market, "vwap_bias")[:, 0].astype(np.float64), _EX_DIVIDEND_BIAS, atol=1e-6
    )


def test_vwap_bias_without_raw_close_is_not_computed_from_the_adjusted_close():
    panel = _ex_dividend_panel().drop("raw_close")
    market = build_market_data_matrix(panel, field_columns={"amount"})

    assert np.isnan(matrix_feature(market, "vwap_bias")).all()
    frame = materialize_scoring_columns(panel, {"vwap_bias"})
    assert "vwap_bias" not in frame.columns or frame["vwap_bias"].is_null().all()


# B036/B109: absolute price and market-cap bounds compare the raw price of that day.

def _filter_panel(with_raw_close: bool = True) -> pl.DataFrame:
    # Forward adjustment by a later dividend pulls day 0 below 3 while it traded at 3.2.
    panel = pl.DataFrame({
        "symbol": ["1101.TWSE"] * 2,
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "open": [2.8, 3.2],
        "high": [2.8, 3.2],
        "low": [2.8, 3.2],
        "close": [2.8, 3.2],
        "raw_close": [3.2, 3.2],
        "volume": [1_000.0, 1_000.0],
        "total_shares": [1_000_000_000.0, 1_000_000_000.0],
    })
    return panel if with_raw_close else panel.drop("raw_close")


@pytest.mark.parametrize("config", [
    {"price_min": 3.0},
    {"market_cap_min": 3.0e9},
])
def test_basic_filter_bounds_use_the_raw_close(config):
    market = build_market_data_matrix(_filter_panel(), field_columns={"total_shares"})

    assert build_basic_filter_mask(market, config)[:, 0].tolist() == [True, True]


@pytest.mark.parametrize("config", [
    {"price_max": 3.0},
    {"market_cap_max": 3.0e9},
])
def test_basic_filter_upper_bounds_use_the_raw_close(config):
    market = build_market_data_matrix(_filter_panel(), field_columns={"total_shares"})

    assert build_basic_filter_mask(market, config)[:, 0].tolist() == [False, False]


@pytest.mark.parametrize("config", [{"price_min": 3.0}, {"market_cap_min": 3.0e9}])
def test_basic_filter_price_bound_without_raw_close_raises(config):
    market = build_market_data_matrix(_filter_panel(with_raw_close=False), field_columns={"total_shares"})

    with pytest.raises(ValueError, match="raw_close"):
        build_basic_filter_mask(market, config)


def test_historical_panel_without_raw_close_cannot_supply_a_requested_raw_close():
    with pytest.raises(ValueError, match="raw_close"):
        build_market_data_matrix(_filter_panel(with_raw_close=False), field_columns={"raw_close"})


def test_live_quote_rows_take_raw_close_from_their_close():
    history = _filter_panel()
    live = RealtimeMarketDataMatrix(history, field_columns={"raw_close", "total_shares"})
    quote = history.filter(pl.col("date") == date(2024, 1, 3)).drop("raw_close").with_columns(
        pl.lit(date(2024, 1, 4)).alias("date"), pl.lit(3.4).alias("close"),
    )

    live.update(quote)
    snapshot = live.snapshot()

    assert float(snapshot.field("raw_close")[-1, 0]) == pytest.approx(3.4)
    assert build_basic_filter_mask(snapshot, {"price_min": 3.0})[:, 0].tolist() == [True, True, True]


def _write_enriched(root, days: list[date]) -> None:
    for current in days:
        partition = root / f"date={current.isoformat()}"
        partition.mkdir(parents=True)
        pl.DataFrame({
            "symbol": ["000001.SZ"],
            "date": [current],
            "open": [10.0],
            "high": [10.0],
            "low": [10.0],
            "close": [10.0],
            "volume": [1_000.0],
            "amount": [1_000_000.0],
            "raw_close": [10.0],
        }).write_parquet(partition / "part.parquet")


def test_matrix_disk_cache_written_before_the_factor_fix_is_not_reused(tmp_path):
    market_root = tmp_path / "kline_daily_enriched"
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    _write_enriched(market_root, days)
    cache_root = tmp_path / "matrix_cache"

    def load():
        return load_market_data_matrix_from_parquet(
            market_root,
            days[0],
            days[-1],
            field_columns={"amount", "raw_close"},
            cache_root=cache_root,
        )

    assert load().cache_status == "built"
    assert load().cache_status == "exact"

    for path in list(cache_root.glob("v*-*")):
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = _LOADER_VERSION_BEFORE_FACTOR_FIX
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        legacy = cache_root / f"v{_LOADER_VERSION_BEFORE_FACTOR_FIX}-{path.name.split('-', 1)[1]}"
        if legacy != path:
            path.rename(legacy)

    assert load().cache_status == "built"


# B016: turnover factors with a missing turnover agree between the matrix and Polars paths.

def _turnover_panel() -> pl.DataFrame:
    start = date(2024, 1, 1)
    count = 140
    turnover = [1.0 + (index % 7) * 0.1 + index * 0.001 for index in range(count)]
    for gap in (70, 71):
        turnover[gap] = None
    return pl.DataFrame({
        "symbol": ["000001.SZ"] * count,
        "date": [start + timedelta(days=index) for index in range(count)],
        "open": [10.0] * count,
        "high": [10.0] * count,
        "low": [10.0] * count,
        "close": [10.0] * count,
        "volume": [1_000.0] * count,
        "turnover_rate": turnover,
    })


@pytest.mark.parametrize("name", ["turnover_ratio_5d", "turnover_z_60d"])
def test_turnover_factor_with_missing_turnover_matches_the_polars_path(name):
    panel = _turnover_panel()
    market = build_market_data_matrix(panel, field_columns={"turnover_rate"})

    matrix_values = matrix_feature(market, name)[:, 0].astype(np.float64)
    polars_values = (
        materialize_scoring_columns(panel, {name})[name]
        .cast(pl.Float64)
        .fill_null(np.nan)
        .to_numpy()
    )

    np.testing.assert_array_equal(np.isnan(matrix_values), np.isnan(polars_values))
    finite = np.isfinite(polars_values)
    assert finite.any()
    np.testing.assert_allclose(matrix_values[finite], polars_values[finite], rtol=1e-4, atol=1e-5)


# B017: T-1 is the market's previous session, not the previous matrix label.

def test_regime_t_minus_one_uses_the_market_previous_session_when_labels_skip_days():
    labels = ("2026-10-12", "2026-10-13", "2026-10-16")
    regimes = {
        "2026-10-12": ("strong", 80),
        "2026-10-13": ("strong", 80),
        "2026-10-14": ("weak", 20),
        "2026-10-15": ("weak", 20),
        "2026-10-16": ("weak", 20),
    }

    aligned = align_regime_t_minus_one(
        labels, regimes, required_start=date(2026, 10, 13), required_end=None,
    )
    mask = build_regime_filter_mask(
        labels,
        {"states": ["strong"]},
        regimes,
        required_start=date(2026, 10, 13),
        required_end=None,
    )

    assert aligned == [None, ("strong", 80.0), ("weak", 20.0)]
    assert mask is not None
    assert mask.tolist() == [True, True, False]
