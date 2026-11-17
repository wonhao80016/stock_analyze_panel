"""A custom signal compares with finite numbers only: Polars orders NaN above every number."""

from __future__ import annotations

import logging
from datetime import date

import polars as pl
import pytest

from app.indicators import pipeline
from app.strategy import custom_signals


def _signal(right: object) -> dict:
    return {
        "id": "cheap",
        "name": "cheap",
        "kind": "entry",
        "conditions": [{"left": "close", "op": "<", "right": right}],
    }


@pytest.mark.parametrize(
    "right",
    ["nan", "NaN", " nan ", "inf", "-inf", "Infinity", "1e400", float("nan"), float("inf"), float("-inf")],
)
def test_validation_rejects_a_value_that_is_not_finite(right):
    with pytest.raises(ValueError, match="有限数字"):
        custom_signals.validate(_signal(right))


@pytest.mark.parametrize("right", ["12.5", 12.5, 0, "-3", "1e3", "field:ma5", "ma5"])
def test_validation_accepts_finite_values_and_fields(right):
    custom_signals.validate(_signal(right))


@pytest.fixture
def stored_signals(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    pipeline.invalidate_custom_signals()
    yield tmp_path
    pipeline.invalidate_custom_signals()


def test_a_stored_nan_value_never_fires(stored_signals, caplog):
    custom_signals.save_one(stored_signals, _signal("nan"))
    custom_signals.save_one(stored_signals, {**_signal(50.0), "id": "below_fifty"})
    frame = pl.DataFrame(
        {
            "symbol": ["2330.TWSE"] * 2,
            "date": [date(2026, 11, 2), date(2026, 11, 3)],
            "close": [40.0, 60.0],
        }
    )

    with caplog.at_level(logging.WARNING):
        out = pipeline.compute_signals(frame, needed={"csg_cheap", "csg_below_fifty"})

    cheap = custom_signals.column_name("cheap")
    assert cheap not in out.columns or not out[cheap].fill_null(False).any()
    assert out[custom_signals.column_name("below_fifty")].to_list() == [True, False]
    assert "cheap" in caplog.text
