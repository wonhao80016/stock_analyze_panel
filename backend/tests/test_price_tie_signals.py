"""Price-level signals treat forward-adjusted prices within CROSS_TOLERANCE as equal."""

from __future__ import annotations

from datetime import date

import polars as pl

from app.indicators.pipeline import compute_signals

LEVEL_SIGNALS = {
    "signal_n_day_high",
    "signal_n_day_low",
    "signal_boll_breakout_upper",
    "signal_boll_breakdown_lower",
}


def _signals(close: float, *, high: float, low: float, upper: float, lower: float) -> dict:
    frame = pl.DataFrame(
        {
            "symbol": ["00930.TWSE"],
            "date": [date(2026, 10, 5)],
            "close": [close],
            "high_60d": [high],
            "low_60d": [low],
            "boll_upper": [upper],
            "boll_lower": [lower],
        }
    )
    return compute_signals(frame, needed=LEVEL_SIGNALS).row(0, named=True)


def test_a_close_one_ulp_below_the_60_day_high_is_a_new_high():
    row = _signals(23.99, high=23.990000000000002, low=20.0, upper=30.0, lower=10.0)

    assert row["signal_n_day_high"] is True


def test_a_close_one_ulp_above_the_60_day_low_is_a_new_low():
    row = _signals(27.0, high=30.0, low=26.999999999999996, upper=40.0, lower=10.0)

    assert row["signal_n_day_low"] is True


def test_a_close_a_tick_away_from_the_60_day_extremes_is_neither():
    row = _signals(23.98, high=23.99, low=23.97, upper=30.0, lower=10.0)

    assert row["signal_n_day_high"] is False
    assert row["signal_n_day_low"] is False


def test_a_close_on_a_bollinger_band_up_to_rounding_does_not_break_it():
    upper = _signals(15.782936488545774, high=16.0, low=15.0, upper=15.782936488545644, lower=15.3)
    lower = _signals(71.87668649559896, high=75.0, low=70.0, upper=74.3, lower=71.87668649559946)

    assert upper["signal_boll_breakout_upper"] is False
    assert lower["signal_boll_breakdown_lower"] is False


def test_a_close_a_tick_beyond_a_bollinger_band_breaks_it():
    above = _signals(15.80, high=16.0, low=15.0, upper=15.78, lower=14.0)
    below = _signals(13.95, high=16.0, low=13.0, upper=15.78, lower=14.0)

    assert above["signal_boll_breakout_upper"] is True
    assert below["signal_boll_breakdown_lower"] is True


def test_missing_levels_leave_the_signals_null():
    frame = pl.DataFrame(
        {
            "symbol": ["00930.TWSE"],
            "date": [date(2026, 10, 5)],
            "close": [23.99],
            "high_60d": [None],
            "low_60d": [None],
            "boll_upper": [None],
            "boll_lower": [None],
        },
        schema_overrides={c: pl.Float64 for c in ("high_60d", "low_60d", "boll_upper", "boll_lower")},
    )

    row = compute_signals(frame, needed=LEVEL_SIGNALS).row(0, named=True)

    assert all(row[name] is None for name in LEVEL_SIGNALS)
