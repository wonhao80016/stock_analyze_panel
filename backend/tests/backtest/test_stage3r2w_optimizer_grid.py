"""Range grid specs {min, max, step} must expand inside [min, max]."""
from __future__ import annotations

import pytest

from app.backtest.optimizer import expand_param_grid

FLOAT_META = [{"id": "p", "type": "float", "default": 0.02, "min": 0.01, "max": 0.05, "step": 0.005}]
INT_META = [{"id": "n", "type": "int", "default": 2, "min": 1, "max": 20, "step": 1}]


def _values(meta, spec):
    return [combo[meta[0]["id"]] for combo in expand_param_grid(meta, {meta[0]["id"]: spec})]


def test_float_range_never_exceeds_user_max():
    assert _values(FLOAT_META, {"min": 0.01, "max": 0.03, "step": 0.025}) == [0.01]


def test_int_range_never_exceeds_user_max():
    assert _values(INT_META, {"min": 1, "max": 12, "step": 4}) == [1, 5, 9]


def test_legal_range_reaching_meta_max_is_not_rejected():
    assert _values(FLOAT_META, {"min": 0.01, "max": 0.05, "step": 0.025}) == [0.01, 0.035]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"min": 0.1, "max": 0.7, "step": 0.1}, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]),
        ({"min": 0.01, "max": 0.05, "step": 0.01}, [0.01, 0.02, 0.03, 0.04, 0.05]),
        ({"min": 0.03, "max": 0.03, "step": 0.01}, [0.03]),
    ],
)
def test_float_division_error_keeps_the_max_endpoint(spec, expected):
    meta = [{"id": "p", "type": "float", "default": 0.1, "min": 0.0, "max": 1.0, "step": 0.1}]
    values = _values(meta, spec)
    assert values == pytest.approx(expected)
    assert all(spec["min"] <= v <= spec["max"] for v in values)


def test_zero_step_is_rejected_instead_of_falling_back_to_meta_step():
    with pytest.raises(ValueError, match="step"):
        _values(FLOAT_META, {"min": 0.01, "max": 0.05, "step": 0})


def test_tiny_step_never_yields_candidates_below_min():
    meta = [{"id": "p", "type": "float", "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.1}]
    lo, hi = 0.5 + 4e-11, 0.5 + 7e-11
    values = _values(meta, {"min": lo, "max": hi, "step": 1e-11})
    assert values
    assert all(lo <= v <= hi for v in values)


def test_int_candidates_never_exceed_fractional_max():
    assert _values(INT_META, {"min": 1, "max": 2.6, "step": 1.6}) == [1]


def test_oversized_range_is_rejected_before_candidates_are_built(monkeypatch):
    import app.backtest.optimizer as optimizer_module

    built = []
    real_range = range
    monkeypatch.setattr(
        optimizer_module, "range", lambda *a: built.append(a) or real_range(*a), raising=False
    )
    meta = [{"id": "p", "type": "float", "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.1}]

    with pytest.raises(ValueError, match="上限"):
        expand_param_grid(meta, {"p": {"min": 0.0, "max": 1.0, "step": 1e-7}})
    assert built == []
