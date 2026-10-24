"""Custom signals on distance_to_upper_limit / distance_to_lower_limit (Taiwan limit prices on raw prices)."""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.indicators import pipeline
from app.strategy import custom_signals

SYMBOL = "2330.TWSE"


def _signal(signal_id: str, field: str, op: str, value: str) -> dict:
    return {
        "id": signal_id,
        "name": signal_id,
        "kind": "entry",
        "enabled": True,
        "conditions": [{"left": field, "op": op, "right": value, "leftDays": 0, "rightDays": 0}],
    }


@pytest.fixture
def near_limit_signals(monkeypatch):
    pipeline.invalidate_taiwan_limit_profile_cache()
    exprs = custom_signals.build_expressions([
        _signal("near_up", "distance_to_upper_limit", "<", "0.05"),
        _signal("near_down", "distance_to_lower_limit", "<", "0.05"),
    ])
    monkeypatch.setattr(pipeline, "_custom_signal_exprs", exprs)
    yield
    pipeline.invalidate_taiwan_limit_profile_cache()


def _raw() -> pl.DataFrame:
    closes = [100.0, 105.0, 95.0, 96.0]
    return pl.DataFrame({
        "symbol": [SYMBOL] * 4,
        "date": [date(2026, 10, d) for d in (19, 20, 21, 22)],
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [1_000_000.0] * 4,
        "amount": [c * 1_000_000.0 for c in closes],
    })


def test_compute_enriched_evaluates_custom_signals_on_limit_distance(near_limit_signals):
    out = pipeline.compute_enriched(_raw()).sort("date")

    assert "csg_near_up" in out.columns
    assert "csg_near_down" in out.columns
    # 105 against the 110 limit-up off 100: (110 - 105) / 110 < 0.05.
    assert out["csg_near_up"].to_list()[1:] == [True, False, False]
    # 95 against the 94.5 limit-down off 105: (95 - 94.5) / 94.5 < 0.05.
    assert out["csg_near_down"].to_list()[1:] == [False, True, False]


def test_limit_distance_stays_out_of_the_stored_columns(near_limit_signals):
    out = pipeline.compute_enriched(_raw())

    assert "distance_to_upper_limit" not in out.columns
    assert "distance_to_lower_limit" not in out.columns


def test_limit_distance_uses_raw_prices_on_adjusted_rows(near_limit_signals):
    adjusted = pipeline.compute_indicators(_raw()).with_columns(
        (pl.col(c) * 0.5).alias(c) for c in ("close", "prev_close")
    ).with_columns(pl.Series("raw_close", [100.0, 105.0, 95.0, 96.0]))

    out = pipeline.compute_signals(adjusted.sort("date"), needed={"csg_near_up"})

    assert out["csg_near_up"].to_list()[1:] == [True, False, False]


def test_a_corporate_action_session_measures_against_the_published_limit(near_limit_signals):
    rows = pipeline.compute_indicators(_raw()).sort("date").with_columns(
        pl.Series("raw_close", [100.0, 105.0, 95.0, 96.0]),
        pl.Series("ref_limit_up", [None, None, None, 99.0], dtype=pl.Float64),
        pl.Series("ref_limit_down", [None, None, None, None], dtype=pl.Float64),
    )

    out = pipeline.compute_signals(rows, needed={"csg_near_up"})

    # 96 is 3% under the published 99 but 8% under the statutory 104.5 off 95.
    assert out["csg_near_up"].to_list()[1:] == [True, False, True]


def test_backtest_dependencies_resolve_limit_distance_to_its_inputs(near_limit_signals):
    deps = pipeline.get_signal_dependencies()["csg_near_up"]

    assert "distance_to_upper_limit" not in deps
    assert {"close", "prev_close", "raw_close"} <= deps
