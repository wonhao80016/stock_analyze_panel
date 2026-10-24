"""Custom realtime ratio fields follow the unit the dataset declares.

The contract is decimal (0.0366 = 3.66%); a source quoting percent declares it in units.
"""
from __future__ import annotations

import polars as pl
import pytest

from app.data_providers.custom.config import CustomSourceConfig, DatasetConfig
from app.data_providers.custom.provider import GenericHTTPProvider, _apply_declared_units

PERCENT = {"change_pct": "percent", "amplitude": "percent", "turnover_rate": "percent"}


def _df(pcts, amps=None, turnovers=None):
    data = {"change_pct": pcts}
    if amps is not None:
        data["amplitude"] = amps
    if turnovers is not None:
        data["turnover_rate"] = turnovers
    return pl.DataFrame(data)


def test_declared_percent_batch_is_divided_by_100():
    out = _apply_declared_units(_df(
        [1.5, -2.2, 0.9, 2.8, -1.1, 0.6, 3.3, -0.8],
        amps=[2.0, 3.5, 1.8, 4.0, 2.5, 1.2, 5.0, 1.6],
        turnovers=[0.5, 1.2, 0.8, 2.0, 0.9, 0.4, 1.5, 0.7],
    ), PERCENT)
    assert out["change_pct"][0] == pytest.approx(0.015)
    assert out["amplitude"][0] == pytest.approx(0.02)
    assert out["turnover_rate"][0] == pytest.approx(0.005)


def test_undeclared_batch_untouched():
    pcts = [0.015, -0.022, 0.009, 0.028, -0.011, 0.006, 0.033, -0.008]
    out = _apply_declared_units(_df(pcts, amps=[0.02, 0.035, 0.018, 0.04, 0.025, 0.012, 0.05, 0.016]), {})
    assert out["change_pct"].to_list() == pcts
    assert out["amplitude"][0] == pytest.approx(0.02)


def test_declared_decimal_is_never_divided():
    out = _apply_declared_units(_df([0.5, 0.2]), {"change_pct": "decimal"})
    assert out["change_pct"].to_list() == [0.5, 0.2]


def test_string_values_are_cast():
    out = _apply_declared_units(_df(["1.5", "-2.2", "0.9", "2.8", "3.3", "0.6"]), PERCENT)
    assert out["change_pct"][0] == pytest.approx(0.015)


def test_missing_or_null_columns_noop():
    out = _apply_declared_units(pl.DataFrame({"close": [1.0, 2.0]}), PERCENT)
    assert out.columns == ["close"]
    out2 = _apply_declared_units(_df([None, None, None, None, None, None]), PERCENT)
    assert out2["change_pct"].null_count() == 6


def _realtime_provider(rows, units):
    provider = GenericHTTPProvider(CustomSourceConfig(
        name="pct_source",
        display_name="Pct Source",
        datasets={"realtime": DatasetConfig(
            url="https://example.test/realtime",
            field_map={
                "code": "symbol", "price": "last_price", "pre_close": "prev_close",
                "pct": "change_pct", "amp": "amplitude", "turnover": "turnover_rate",
            },
            units=units,
        )},
    ))
    provider._request_rows = lambda cfg, **kwargs: rows
    return provider


def test_get_realtime_converts_declared_percent_source():
    provider = _realtime_provider([
        {"code": "S1", "price": 10.0, "pre_close": 9.85, "pct": 1.52, "amp": 2.4, "turnover": 1.1},
        {"code": "S2", "price": 20.0, "pre_close": 20.44, "pct": -2.15, "amp": 3.1, "turnover": 0.8},
    ], PERCENT)
    try:
        rows = provider.get_realtime()
    finally:
        provider.close()
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["S1"]["change_pct"] == pytest.approx(0.0152)
    assert by_sym["S1"]["amplitude"] == pytest.approx(0.024)
    assert by_sym["S1"]["turnover_rate"] == pytest.approx(0.011)
    assert by_sym["S2"]["change_pct"] == pytest.approx(-0.0215)
