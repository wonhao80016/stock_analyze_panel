"""Composite selection and backtest merges must score every candidate the same way."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import make_signal_matrix
from app.services import pick_grades
from app.strategy import composite
from app.strategy.engine import (
    CompositeChild,
    CompositeSpec,
    StrategyDataContext,
    StrategyEngine,
    StrategyResult,
)

pytestmark = pytest.mark.usefixtures("deny_network")

AS_OF = date(2026, 1, 2)
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _selection(children, weights, mode="union", min_confirm=0) -> dict[str, float | None]:
    results = [
        StrategyResult(as_of=AS_OF, strategy_id=f"c{i}", scores=dict(scores))
        for i, scores in enumerate(children)
    ]
    merged = composite.merge_results(
        results, list(weights), mode, min_confirm, as_of=AS_OF, strategy_id="blend"
    )
    return dict(merged.scores)


def _backtest(children, weights, mode="union", min_confirm=0) -> dict[str, float | None]:
    symbols = sorted(set().union(*children))
    shape = (1, len(symbols))
    sigs = []
    for scores in children:
        entry = np.zeros(shape, dtype=np.uint8)
        score = np.zeros(shape, dtype=np.float32)
        for asset, symbol in enumerate(symbols):
            if symbol in scores:
                entry[0, asset] = 1
                score[0, asset] = np.nan if scores[symbol] is None else scores[symbol]
        sigs.append(make_signal_matrix(shape, entry=entry, score=score))
    merged = composite.merge_signal_matrices(
        shape,
        sigs,
        [(f"c{i}", weight) for i, weight in enumerate(weights)],
        mode,
        min_confirm,
        max_hold=1,
    )
    return {
        symbol: None if np.isnan(value) else float(value)
        for symbol, value, hit in zip(symbols, merged.score[0], merged.entry[0], strict=True)
        if hit
    }


def _assert_scores(actual: dict[str, float | None], expected: dict[str, float | None]) -> None:
    assert set(actual) == set(expected)
    for symbol, value in expected.items():
        if value is None:
            assert actual[symbol] is None, symbol
        else:
            assert actual[symbol] == pytest.approx(value, abs=1e-3), symbol


def _assert_both_paths(children, weights, expected, mode="union", min_confirm=0) -> None:
    _assert_scores(_selection(children, weights, mode, min_confirm), expected)
    _assert_scores(_backtest(children, weights, mode, min_confirm), expected)


def test_single_candidate_child_scores_neutral_in_selection_and_backtest():
    children = [{"X": 55.0}, {"X": 90.0, "Y": 80.0, "Z": 10.0}]

    _assert_both_paths(children, [1.0, 1.0], {"X": 75.0, "Y": 25.0, "Z": 0.0})


def test_tied_scores_take_the_average_rank_in_both_paths():
    _assert_both_paths(
        [{"A": 3.0, "B": 2.0, "C": 2.0, "D": 1.0}],
        [1.0],
        {"A": 100.0, "B": 50.0, "C": 50.0, "D": 0.0},
    )
    _assert_both_paths(
        [dict.fromkeys(["1101", "2330", "2454", "3034"], 0.0)],
        [1.0],
        dict.fromkeys(["1101", "2330", "2454", "3034"], 50.0),
    )


def test_unscorable_child_scores_add_nothing_and_merge_to_none_only_without_any_score():
    children = [
        {"X": None, "Y": 5.0, "Z": 1.0},
        {"X": None, "W": 2.0, "Y": None},
    ]

    _assert_both_paths(children, [1.0, 1.0], {"X": None, "Y": 50.0, "Z": 0.0, "W": 25.0})


def test_union_divides_by_the_weight_of_every_child():
    _assert_both_paths(
        [{"S1": 10.0, "S2": 1.0}, {"S3": 10.0, "S2": 1.0}],
        [0.9, 0.1],
        {"S1": 90.0, "S2": 0.0, "S3": 10.0},
    )


def test_three_children_with_missing_scores_blend_identically():
    children = [
        {"P": 3.0, "Q": 1.0},
        {"P": None, "R": 2.0, "Q": 5.0},
        {"R": 1.0, "S": None},
    ]

    _assert_both_paths(children, [2.0, 1.0, 1.0], {"P": 50.0, "Q": 25.0, "R": 12.5, "S": None})


def test_zero_weight_child_adds_nothing_and_all_zero_weights_count_equally():
    children = [{"X": 1.0, "Y": 2.0}, {"Z": 5.0}]

    _assert_both_paths(children, [1.0, 0.0], {"X": 0.0, "Y": 100.0, "Z": 0.0})
    _assert_both_paths(children, [0.0, 0.0], {"X": 0.0, "Y": 50.0, "Z": 25.0})


def test_intersect_with_partial_confirmation_divides_by_every_child_weight():
    children = [
        {"X": 2.0, "Y": 1.0},
        {"X": 1.0, "Y": 2.0, "Z": 3.0},
        {"Z": 1.0, "X": 4.0, "V": 9.0},
    ]

    _assert_both_paths(
        children,
        [1.0, 1.0, 2.0],
        {"X": 50.0, "Y": 12.5, "Z": 25.0},
        mode="intersect",
        min_confirm=2,
    )


def test_reordering_a_child_s_scores_changes_neither_values_nor_output_order():
    forward = {"B": 2.0, "A": 2.0, "C": 1.0, "D": None}
    backward = dict(reversed(list(forward.items())))

    merged = list(_selection([forward], [1.0]).items())

    assert merged == list(_selection([backward], [1.0]).items())
    assert merged == [("A", 75.0), ("B", 75.0), ("C", 0.0), ("D", None)]


_HASH_SEED_SCRIPT = """
import json
from datetime import date
from app.strategy import composite
from app.strategy.engine import StrategyResult, _rank_key

as_of = date(2026, 1, 2)
symbols = [f"{code}.TW" for code in (1101, 2330, 2379, 2454, 3034, 6196)]
tied = StrategyResult(as_of=as_of, strategy_id="tied", scores=dict.fromkeys(symbols, 0.0))
partial = StrategyResult(
    as_of=as_of,
    strategy_id="partial",
    scores={"1101.TW": 2.0, "2454.TW": 2.0, "3034.TW": 1.0},
)
merged = composite.merge_results(
    [tied, partial], [1.0, 1.0], "union", 0, as_of=as_of, strategy_id="blend"
)
ranked = sorted(merged.scores, key=lambda s: _rank_key(merged.scores[s], s, True))
print(json.dumps({"scores": list(merged.scores.items()), "top2": ranked[:2]}))
"""


def test_selection_scores_do_not_depend_on_the_hash_seed():
    outputs = []
    for seed in ("0", "1", "7"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(BACKEND_DIR)}
        completed = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_SCRIPT],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout.strip().splitlines()[-1])

    assert len(set(outputs)) == 1
    result = json.loads(outputs[0])
    assert dict(result["scores"]) == {
        "1101.TW": 62.5,
        "2330.TW": 25.0,
        "2379.TW": 25.0,
        "2454.TW": 62.5,
        "3034.TW": 25.0,
        "6196.TW": 25.0,
    }
    assert result["top2"] == ["1101.TW", "2454.TW"]


def _scored_child_code(strategy_id: str) -> str:
    return f'''import polars as pl
META = {{
    "id": "{strategy_id}",
    "name": "{strategy_id}",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "scoring": {{"close": 1.0}},
}}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
'''


def _composite_code(strategy_id: str, child_id: str) -> str:
    return f'''META = {{
    "id": "{strategy_id}",
    "name": "{strategy_id}",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {{"id": "merge_mode", "type": "select", "options": ["union", "intersect"], "default": "union"}},
        {{"id": "min_confirm", "type": "int", "default": 0}},
    ],
    "children": [{{"strategy_id": "{child_id}", "weight": 1.0}}],
}}
EXECUTION_BACKEND = "composite"
'''


def test_engine_composite_keeps_an_unscorable_pick_with_null_score_ranked_last(tmp_path):
    child_dir = tmp_path / "strategies" / "custom"
    comp_dir = tmp_path / "strategies" / "composite"
    child_dir.mkdir(parents=True)
    comp_dir.mkdir(parents=True)
    (child_dir / "close_child.py").write_text(_scored_child_code("close_child"), encoding="utf-8")
    (comp_dir / "close_blend.py").write_text(
        _composite_code("close_blend", "close_child"), encoding="utf-8"
    )
    engine = StrategyEngine(strategy_dirs=[child_dir, comp_dir])
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=AS_OF,
        current=pl.DataFrame(
            {
                "symbol": ["A.TW", "B.TW", "C.TW"],
                "date": [AS_OF] * 3,
                "close": [10.0, None, 20.0],
            }
        ),
    )

    result = engine.run("close_blend", context, overrides={"basic_filter": {"enabled": False}})

    assert [row["symbol"] for row in result.rows] == ["C.TW", "A.TW", "B.TW"]
    assert [row["score"] for row in result.rows] == [100.0, 0.0, None]
    assert result.scores == {"C.TW": 100.0, "A.TW": 0.0, "B.TW": None}
    assert all(value is None or math.isfinite(value) for value in result.scores.values())


class _GradeEngine:
    def __init__(self, strategies: dict[str, SimpleNamespace]):
        self.strategies = strategies

    def has(self, sid):
        return sid in self.strategies

    def get(self, sid):
        return self.strategies[sid]


def _leaf(scoring) -> SimpleNamespace:
    return SimpleNamespace(execution_backend="matrix_native", meta={"scoring": scoring})


def _blend(*child_ids: str) -> SimpleNamespace:
    return SimpleNamespace(
        execution_backend="composite",
        meta={},
        composite=CompositeSpec(children=tuple(CompositeChild(cid, 1.0) for cid in child_ids)),
    )


def _save_override(data_dir, sid, payload) -> None:
    path = data_dir / "user_data" / "strategy_overrides" / f"{sid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_composite_with_a_scored_child_grades_an_unscorable_top_pick_watch(tmp_path):
    engine = _GradeEngine(
        {
            "scored": _leaf({"rsi_14": 1}),
            "plain": _leaf({}),
            "blend": _blend("plain", "scored"),
        }
    )
    rows = [{"symbol": "2330.TWSE", "score": None}, {"symbol": "2317.TWSE", "score": 40.0}]

    scored = pick_grades.scored_strategy_ids(engine, tmp_path, ["blend"])
    graded = pick_grades.assign_grades({"blend": {"rows": rows}}, None, scored)

    assert scored == {"blend"}
    assert [(r["grade"], r["grade_reason"]) for r in graded["blend"]["rows"]] == [
        ("watch", "score_unavailable"),
        ("buy", None),
    ]


def test_composite_follows_child_overrides_and_saved_children(tmp_path):
    engine = _GradeEngine(
        {
            "plain_a": _leaf({}),
            "plain_b": _leaf({}),
            "blend": _blend("plain_a"),
            "rank_only": _blend("plain_b"),
        }
    )
    _save_override(tmp_path, "plain_b", {"scoring": {"ma20_bias": 1}})
    _save_override(tmp_path, "blend", {"children": [{"strategy_id": "plain_b", "weight": 1.0}]})
    _save_override(tmp_path, "rank_only", {"children": [{"strategy_id": "plain_a", "weight": 1.0}]})

    scored = pick_grades.scored_strategy_ids(engine, tmp_path, ["blend", "rank_only"])

    assert scored == {"blend"}
