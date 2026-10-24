"""A pick without a score names the scoring fields it lacked (D1), from selection to the graded cache.

A composite pick no child could score names each child and the fields that child lacked.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.services import pick_grades, strategy_cache
from app.strategy.engine import StrategyDataContext, StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

AS_OF = date(2026, 1, 2)
MISSING = "missing_scoring_fields"
CHILDREN = "missing_scoring_children"

# E lacks turnover_rate and f2; G lacks f1 and turnover_rate but has f2.
_VALUES = {
    "symbol": ["A", "B", "E", "G"],
    "f1": [5.0, 3.0, 6.0, None],
    "f2": [1.0, 2.0, None, 3.0],
    "turnover_rate": [2.0, 1.0, None, float("nan")],
}


def _frame() -> pl.DataFrame:
    count = len(_VALUES["symbol"])
    return pl.DataFrame(
        {
            **_VALUES,
            "name": _VALUES["symbol"],
            "date": [AS_OF] * count,
            "open": [10.0] * count,
            "high": [10.0] * count,
            "low": [10.0] * count,
            "close": [10.0] * count,
            "raw_close": [10.0] * count,
            "volume": [1_000.0] * count,
            "amount": [1e9] * count,
        }
    )


def _polars_code(strategy_id: str, scoring: dict) -> str:
    return f'''import polars as pl
META = {{"id": "{strategy_id}", "name": "{strategy_id}", "asset_types": ["stock"],
        "timeframes": ["1d"], "scoring": {scoring!r}, "basic_filter": {{"enabled": False}}}}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
'''


def _matrix_code(strategy_id: str, name: str, scoring: dict) -> str:
    return f'''import numpy as np
from app.backtest.matrix import make_signal_matrix
META = {{"id": "{strategy_id}", "name": "{name}", "asset_types": ["stock"],
        "timeframes": ["1d"], "scoring": {scoring!r}, "basic_filter": {{"enabled": False}}}}
EXECUTION_BACKEND = "matrix_native"
class _AllEntries:
    def required_fields(self):
        return frozenset({{"close"}})
    def required_warmup_bars(self, params):
        return 1
    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))
MATRIX_STRATEGY = _AllEntries()
'''


_COMPOSITE = """META = {"id": "blend", "name": "blend", "asset_types": ["stock"], "timeframes": ["1d"],
        "basic_filter": {"enabled": False},
        "params": [{"id": "merge_mode", "type": "select", "options": ["union", "intersect"], "default": "union"}],
        "children": [{"strategy_id": "mat", "weight": 1.0}, {"strategy_id": "mat_b", "weight": 1.0}]}
EXECUTION_BACKEND = "composite"
"""

SCORING = {"f1": 0.7, "turnover_rate": 0.3}


@pytest.fixture
def engine(tmp_path) -> StrategyEngine:
    custom = tmp_path / "custom"
    composite = tmp_path / "composite"
    custom.mkdir()
    composite.mkdir()
    (custom / "pol.py").write_text(_polars_code("pol", SCORING), encoding="utf-8")
    (custom / "mat.py").write_text(_matrix_code("mat", "子策略甲", SCORING), encoding="utf-8")
    (custom / "mat_b.py").write_text(
        _matrix_code("mat_b", "子策略乙", {"f2": 1.0}), encoding="utf-8"
    )
    (custom / "unscored.py").write_text(_matrix_code("unscored", "unscored", {}), encoding="utf-8")
    (composite / "blend.py").write_text(_COMPOSITE, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[custom, composite])
    assert engine.load_errors() == []
    return engine


def _context() -> StrategyDataContext:
    frame = _frame()
    return StrategyDataContext(
        asset_type="stock", timeframe="1d", as_of=AS_OF, current=frame, history=frame
    )


def _rows(engine: StrategyEngine, strategy_id: str) -> dict[str, dict]:
    result = engine.run(strategy_id, _context())
    return {str(row["symbol"]): row for row in result.rows}


@pytest.mark.parametrize("strategy_id", ["pol", "mat"])
def test_an_unscored_pick_names_its_missing_scoring_fields_in_scoring_order(engine, strategy_id):
    rows = _rows(engine, strategy_id)

    assert rows["E"]["score"] is None
    assert rows["E"][MISSING] == ["turnover_rate"]
    assert rows["G"]["score"] is None
    assert rows["G"][MISSING] == ["f1", "turnover_rate"]
    assert MISSING not in rows["A"] and MISSING not in rows["B"]
    assert all(CHILDREN not in row for row in rows.values())


def test_a_strategy_without_scoring_weights_flags_nothing(engine):
    rows = _rows(engine, "unscored")

    assert set(rows) == {"A", "B", "E", "G"}
    assert all(MISSING not in row for row in rows.values())


def test_a_composite_pick_no_child_could_score_names_each_child_and_its_fields(engine):
    rows = _rows(engine, "blend")

    assert rows["E"]["score"] is None
    assert rows["E"][CHILDREN] == [
        {"strategy_id": "mat", "name": "子策略甲", "fields": ["turnover_rate"]},
        {"strategy_id": "mat_b", "name": "子策略乙", "fields": ["f2"]},
    ]
    assert rows["E"][MISSING] == ["turnover_rate", "f2"]
    assert rows["G"]["score"] is not None
    assert MISSING not in rows["G"] and CHILDREN not in rows["G"]


def test_the_missing_fields_reach_the_graded_cached_rows(engine, tmp_path):
    result = engine.run("pol", _context())
    strategy_cache.write_cache(
        tmp_path,
        AS_OF.isoformat(),
        {"pol": {"total": result.total, "as_of": AS_OF.isoformat(), "rows": result.rows}},
    )

    cached = strategy_cache.read_cache(tmp_path)
    graded = pick_grades.assign_grades(cached["results"], None, {"pol"})
    rows = {row["symbol"]: row for row in graded["pol"]["rows"]}

    assert rows["E"]["grade"] == pick_grades.GRADE_WATCH
    assert rows["E"]["grade_reason"] == pick_grades.REASON_SCORE_UNAVAILABLE
    assert rows["E"][MISSING] == ["turnover_rate"]
    assert MISSING not in rows["A"]
