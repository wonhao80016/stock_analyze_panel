from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.fundamentals import attach_fundamental_factors, build_fundamental_matrices
from app.backtest.matrix import build_market_data_matrix

SYMBOL = "2330.TWSE"
NAMES = ["roe_latest", "pb_latest"]


def _panel(days: int = 8) -> pl.DataFrame:
    # Forward-adjusted closes are half the raw closes, as after a large dividend history.
    return pl.DataFrame({
        "symbol": [SYMBOL] * days,
        "date": [date(2026, 4, 1) + timedelta(days=offset) for offset in range(days)],
        "open": [5.0] * days,
        "high": [5.0] * days,
        "low": [5.0] * days,
        "close": [5.0] * days,
        "raw_close": [10.0] * days,
        "volume": [1000.0] * days,
    })


def _snapshot(
    rows: list[tuple[date, float | None, float | None]], *, typed_dates: bool = False,
) -> pl.DataFrame:
    announce = [day for day, _roe, _bps in rows]
    return pl.DataFrame({
        "symbol": [SYMBOL] * len(rows),
        "announce_date": (
            pl.Series(announce, dtype=pl.Date)
            if typed_dates
            else pl.Series([day.isoformat() for day in announce], dtype=pl.Utf8)
        ),
        "roe": pl.Series([roe for _day, roe, _bps in rows], dtype=pl.Float64),
        "bps": pl.Series([bps for _day, _roe, bps in rows], dtype=pl.Float64),
        "_announce": pl.Series(announce, dtype=pl.Date),
    }).sort(["symbol", "_announce"])


def _both_paths(panel: pl.DataFrame, snapshot: pl.DataFrame) -> tuple[dict, dict]:
    attached = attach_fundamental_factors(panel, snapshot, NAMES).sort("date")
    market = build_market_data_matrix(panel, field_columns={"raw_close"})
    matrices = build_fundamental_matrices(market, snapshot, NAMES)
    polars_values = {name: attached[name].to_list() for name in NAMES}
    matrix_values = {
        name: [None if np.isnan(value) else float(value) for value in matrices[name][:, 0]]
        for name in NAMES
    }
    return polars_values, matrix_values


# B105: pb reads the raw close; both paths gate and null the same way.

def test_pb_divides_the_raw_close_by_bps_on_both_paths() -> None:
    polars_values, matrix_values = _both_paths(_panel(), _snapshot([(date(2026, 4, 2), 10.0, 4.0)]))

    expected = [None, None] + [2.5] * 6
    assert polars_values["pb_latest"] == pytest.approx(expected)
    assert matrix_values["pb_latest"] == pytest.approx(expected)


def test_a_newer_report_with_null_values_nulls_the_factor_on_both_paths() -> None:
    snapshot = _snapshot([(date(2026, 4, 2), 10.0, 4.0), (date(2026, 4, 5), None, None)])

    polars_values, matrix_values = _both_paths(_panel(), snapshot)

    expected = [None, None, 10.0, 10.0, 10.0, None, None, None]
    assert polars_values["roe_latest"] == expected
    assert matrix_values["roe_latest"] == expected


def test_the_previous_report_stays_in_force_on_a_new_announcement_day() -> None:
    snapshot = _snapshot([(date(2026, 4, 2), 10.0, 4.0), (date(2026, 4, 5), 20.0, 5.0)])

    polars_values, matrix_values = _both_paths(_panel(), snapshot)

    expected = [None, None, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0]
    assert polars_values["roe_latest"] == expected
    assert matrix_values["roe_latest"] == expected


def test_a_date_typed_announce_column_gates_the_matrix_path_like_polars() -> None:
    snapshot = _snapshot([(date(2026, 4, 2), 10.0, 4.0)], typed_dates=True)

    polars_values, matrix_values = _both_paths(_panel(), snapshot)

    assert matrix_values["roe_latest"] == polars_values["roe_latest"]
