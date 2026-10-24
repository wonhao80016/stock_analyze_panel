"""AI strategy validation matches what the engine loads and scores."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.strategy.ai_generator import (
    _MATRIX_SCORING_FIELDS,
    _POLARS_SCORING_FIELDS,
    AIStrategyGenerator,
)
from app.strategy.engine import StrategyEngine

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"


def _polars(meta_extra: str = "", body: str = "def filter(df, params):\n    return pl.lit(True)\n") -> str:
    return (
        "import polars as pl\n\n"
        f'META = {{"id": "ai_x", "name": "x", "params": [], "scoring": {{}}{meta_extra}}}\n\n'
        + body
    )


# ── B160 ────────────────────────────────────────────────


def test_every_builtin_strategy_passes_the_scoring_check():
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    assert engine.load_errors() == []

    failures = {}
    for strategy in engine.strategy_definitions():
        code = strategy.file_path.read_text(encoding="utf-8")
        try:
            AIStrategyGenerator._validate_meta_semantics(code, strategy.meta)
        except ValueError as e:
            failures[strategy.meta["id"]] = str(e)

    assert failures == {}


def test_matrix_scoring_accepts_engine_features_and_rejects_absent_share_counts():
    for name in ("distance_from_low_60d", "vol_ratio_10d", "ma120", "ema5_bias", "turnover_rate"):
        assert name in _MATRIX_SCORING_FIELDS, name
    for name in ("total_shares", "float_shares", "volume_surge"):
        assert name not in _MATRIX_SCORING_FIELDS, name


def test_polars_scoring_rejects_join_and_live_only_columns():
    assert not {"name", "total_shares", "float_shares"} & _POLARS_SCORING_FIELDS
    assert not {"distance_to_upper_limit", "distance_to_lower_limit"} & _POLARS_SCORING_FIELDS
    assert {"turnover_rate", "change_pct", "ma20_bias"} <= _POLARS_SCORING_FIELDS


# ── B161 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        (
            _polars(body=(
                "def filter(df, params):\n    return pl.lit(True)\n\n"
                "def filter_history(df, params):\n    return df\n"
            )),
            "filter_history",
        ),
        (
            'EXECUTION_BACKEND = "matrix_native"\n' + _polars() + "MATRIX_STRATEGY = object()\n",
            "matrix_native",
        ),
        (
            'EXECUTION_BACKEND = "polars_expr"\n'
            + _polars(body="def filter_history(df, params):\n    return df\n"),
            "polars_expr",
        ),
        ('EXECUTION_BACKEND = "composite"\n' + _polars(), "composite"),
    ],
)
def test_entrypoints_the_engine_refuses_are_invalid(code, fragment):
    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is False
    assert fragment in result["error"]
    assert AIStrategyGenerator.needs_structural_repair(result)


@pytest.mark.parametrize(
    ("meta_extra", "fragment"),
    [
        (', "asset_types": ["bond"]', "asset_types"),
        (', "asset_types": []', "asset_types"),
        (', "timeframes": "1d"', "timeframes"),
        (', "scoring": {"close": 1.0}, "scoring_directions": {"close": "down"}', "scoring_directions"),
    ],
)
def test_meta_values_the_engine_refuses_are_invalid_and_repaired(meta_extra, fragment):
    code = _polars(meta_extra).replace('"scoring": {}, "scoring"', '"scoring"')

    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is False
    assert fragment in result["error"]
    assert AIStrategyGenerator.needs_structural_repair(result)


def test_a_valid_polars_strategy_still_validates():
    result = AIStrategyGenerator().validate_code(_polars(', "asset_types": ["stock", "etf"]'))

    assert result["valid"] is True, result["error"]
