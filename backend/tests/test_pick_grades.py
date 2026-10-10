from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app.services import pick_grades
from app.services.pick_grades import (
    assign_grades,
    grade_cached_payload,
    has_scoring,
    scored_strategy_ids,
)

pytestmark = pytest.mark.usefixtures("deny_network")

BELOW = {"as_of": "2026-09-30", "status": "ok", "below_ma": True, "close": 90.0, "ma": 100.0}
ABOVE = {"as_of": "2026-09-30", "status": "ok", "below_ma": False, "close": 110.0, "ma": 100.0}
UNKNOWN = {"as_of": "2026-09-30", "status": "unavailable", "below_ma": None, "reason": "index_gap"}


def _rows(*scores, key: str = "score") -> list[dict]:
    rows = []
    for i, score in enumerate(scores):
        row = {"symbol": f"{1000 + i}.TWSE"}
        if score is not ...:
            row[key] = score
        rows.append(row)
    return rows


def _grades(results, sid="s", market_state=None, scored=("s",)) -> list[tuple]:
    graded = assign_grades(results, market_state, set(scored))
    return [(r["grade"], r["grade_reason"]) for r in graded[sid]["rows"]]


BUY = ("buy", None)
OUT = ("watch", "outside_top_n")
UNAVAILABLE = ("watch", "score_unavailable")
MARKET = ("watch", "market_below_ma20")


def test_top_five_are_buy_and_the_rest_watch():
    results = {"s": {"total": 7, "rows": _rows(90, 80, 70, 60, 50, 40, 30)}}

    assert _grades(results) == [BUY] * 5 + [OUT] * 2


def test_fewer_than_five_picks_are_all_buy():
    results = {"s": {"total": 3, "rows": _rows(90, 50, 10)}}

    assert _grades(results) == [BUY] * 3


def test_ties_keep_the_cached_order_across_the_cut():
    results = {"s": {"total": 7, "rows": _rows(90, 50, 50, 50, 50, 50, 50)}}
    graded = assign_grades(results, None, {"s"})["s"]["rows"]

    assert [r["symbol"] for r in graded] == [r["symbol"] for r in results["s"]["rows"]]
    assert [(r["grade"], r["grade_reason"]) for r in graded] == [BUY] * 5 + [OUT] * 2


@pytest.mark.parametrize("low", [0, 0.0, -1.5])
def test_a_literal_zero_or_negative_score_is_a_valid_low_score(low):
    results = {"s": {"total": 6, "rows": _rows(90, 80, 70, low, low, low)}}

    assert _grades(results) == [BUY] * 5 + [OUT]


@pytest.mark.parametrize("missing", [None, ..., float("nan"), float("inf"), "12", True])
def test_scored_strategy_with_an_unusable_score_is_watch_without_raising(missing):
    results = {"s": {"total": 2, "rows": _rows(90, missing)}}

    assert _grades(results) == [BUY, UNAVAILABLE]


def test_null_scores_ranked_last_are_watch_as_score_unavailable():
    results = {"s": {"total": 8, "rows": _rows(90, 80, 70, 60, 50, 0, None, None)}}

    assert _grades(results) == [BUY] * 5 + [OUT] + [UNAVAILABLE] * 2


def test_null_scores_inside_the_top_five_never_become_buy():
    results = {"s": {"total": 3, "rows": _rows(90, 0, None)}}

    assert _grades(results) == [BUY, BUY, UNAVAILABLE]


@pytest.mark.parametrize(
    "rows",
    [
        _rows(-1.5, -2.0, -3.0, -4.0, -5.0, -6.0),
        _rows(..., ..., ..., ..., ..., ...),
        _rows(None, None, None, None, None, None),
        _rows(0, 0, 0, 0, 0, 0),
    ],
    ids=["negative", "no_score_field", "null", "zero"],
)
def test_strategy_without_scoring_is_graded_by_rank_only(rows):
    results = {"s": {"total": 6, "rows": rows}}

    assert _grades(results, scored=()) == [BUY] * 5 + [OUT]


def test_market_below_ma_downgrades_every_buy_and_keeps_other_reasons():
    results = {
        "s": {"total": 7, "rows": _rows(90, None, 70, 0, 50, 40, 30)},
        "t": {"total": 2, "rows": _rows(-1, -2)},
    }
    graded = assign_grades(results, BELOW, {"s"})

    assert [(r["grade"], r["grade_reason"]) for r in graded["s"]["rows"]] == (
        [MARKET, UNAVAILABLE, MARKET, MARKET, MARKET, OUT, OUT]
    )
    assert [(r["grade"], r["grade_reason"]) for r in graded["t"]["rows"]] == [MARKET, MARKET]


@pytest.mark.parametrize("state", [ABOVE, UNKNOWN, None], ids=["passed", "unavailable", "none"])
def test_no_downgrade_unless_the_filter_applies(state):
    results = {"s": {"total": 2, "rows": _rows(90, 80)}}

    assert _grades(results, market_state=state) == [BUY, BUY]


@pytest.mark.parametrize(
    ("state", "expected"),
    [(BELOW, "applied"), (ABOVE, "passed"), (UNKNOWN, "unavailable"), (None, "unavailable")],
)
def test_market_filter_values(state, expected):
    assert pick_grades.market_filter(state) == expected


def test_inputs_are_not_mutated():
    shared_row = {"symbol": "2330.TWSE", "score": 90}
    results = {"s": {"total": 1, "as_of": "2026-09-30", "rows": [shared_row]}, "bad": None}
    before = copy.deepcopy(results)

    graded = assign_grades(results, BELOW, {"s"})

    assert results == before
    assert "grade" not in shared_row
    assert graded["s"]["rows"][0] is not shared_row
    assert graded["s"]["as_of"] == "2026-09-30"
    assert graded["bad"] is None


@pytest.mark.parametrize(
    ("scoring", "expected"),
    [
        ({}, False),
        (None, False),
        ({"rsi_14": 0}, False),
        ({"rsi_14": 1}, True),
        ({"rsi_14": 0, "ma20_bias": 0.5}, True),
        ({"rsi_14": -1}, False),
        ({"rsi_14": "x"}, False),
    ],
)
def test_has_scoring_matches_the_matrix_condition(scoring, expected):
    assert has_scoring(scoring) is expected


class _Engine:
    def __init__(self, strategies: dict[str, SimpleNamespace], broken: frozenset[str] = frozenset()):
        self.strategies = strategies
        self.broken = broken

    def has(self, sid):
        return sid in self.strategies or sid in self.broken

    def get(self, sid):
        if sid in self.broken:
            raise ValueError(f"unknown strategy: {sid}")
        return self.strategies[sid]


def _strategy(scoring, backend="matrix_native"):
    return SimpleNamespace(execution_backend=backend, meta={"scoring": scoring})


def _override(data_dir, sid, payload):
    path = data_dir / "user_data" / "strategy_overrides" / f"{sid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_scored_ids_follow_meta_and_overrides(tmp_path):
    engine = _Engine({
        "builtin": _strategy({"rsi_14": 1}),
        "replaced_empty": _strategy({"rsi_14": 1}),
        "custom_plain": _strategy({}),
        "custom_scored_later": _strategy({}),
    })
    _override(tmp_path, "replaced_empty", {"scoring_replace": True, "scoring": {}})
    _override(tmp_path, "custom_scored_later", {"scoring": {"ma20_bias": 1}})

    scored = scored_strategy_ids(engine, tmp_path, list(engine.strategies))

    assert scored == {"builtin", "custom_scored_later"}


def test_composite_is_graded_by_rank_only_even_with_saved_scoring(tmp_path):
    engine = _Engine({"combo": _strategy({}, backend="composite")})
    _override(tmp_path, "combo", {"scoring": {"rsi_14": 1}})
    results = {"combo": {"total": 2, "rows": _rows(80, 0)}}

    scored = scored_strategy_ids(engine, tmp_path, ["combo"])

    assert scored == set()
    assert _grades(results, sid="combo", scored=scored) == [BUY, BUY]


def test_strategies_the_engine_cannot_resolve_are_graded_by_rank_only(tmp_path):
    engine = _Engine({}, broken=frozenset({"broken_custom"}))

    assert scored_strategy_ids(engine, tmp_path, ["broken_custom", "gone"]) == set()
    assert scored_strategy_ids(None, tmp_path, ["any"]) == set()


def test_unreadable_overrides_fall_back_to_rank_only(tmp_path, monkeypatch):
    engine = _Engine({"s": _strategy({"rsi_14": 1})})

    def fail(*_args):
        raise OSError("locked")

    monkeypatch.setattr(pick_grades.strategy_config, "load_override", fail)

    assert scored_strategy_ids(engine, tmp_path, ["s"]) == set()


def test_reading_scoring_creates_nothing_on_disk(tmp_path):
    engine = _Engine({"s": _strategy({"rsi_14": 1})})

    scored_strategy_ids(engine, tmp_path, ["s"])

    assert list(tmp_path.iterdir()) == []


def test_payload_without_as_of_has_no_market_state_and_is_graded_by_rank():
    payload = {"as_of": None, "results": {"s": {"total": 6, "rows": _rows(1, 2, 3, 4, 5, 6)}}}

    out = grade_cached_payload(payload, None, None)

    assert out["market_state"] is None
    assert out["grading"] == {"top_n": 5, "market_filter": "unavailable"}
    assert [r["grade"] for r in out["results"]["s"]["rows"]] == ["buy"] * 5 + ["watch"]


def test_empty_payload_still_reports_grading():
    out = grade_cached_payload({"as_of": None, "results": {}, "updated_at": None}, None, None)

    assert out == {
        "as_of": None,
        "results": {},
        "updated_at": None,
        "market_state": None,
        "grading": {"top_n": 5, "market_filter": "unavailable"},
    }


def test_market_state_is_read_for_the_payload_as_of(tmp_path, monkeypatch):
    seen = []

    def state(data_dir, as_of):
        seen.append((data_dir, as_of.isoformat()))
        return BELOW

    monkeypatch.setattr(pick_grades, "compute_market_state", state)
    payload = {"as_of": "2026-09-30", "results": {"s": {"total": 1, "rows": _rows(9)}}}

    out = grade_cached_payload(payload, None, tmp_path)

    assert seen == [(tmp_path, "2026-09-30")]
    assert out["market_state"] is BELOW
    assert out["grading"]["market_filter"] == "applied"
    assert out["results"]["s"]["rows"][0]["grade_reason"] == "market_below_ma20"


def test_a_failing_market_state_leaves_the_filter_unavailable(tmp_path, monkeypatch):
    def fail(*_args):
        raise RuntimeError("parquet unreadable")

    monkeypatch.setattr(pick_grades, "compute_market_state", fail)
    payload = {"as_of": "2026-09-30", "results": {"s": {"total": 1, "rows": _rows(9)}}}

    out = grade_cached_payload(payload, None, tmp_path)

    assert out["market_state"] is None
    assert out["grading"]["market_filter"] == "unavailable"
    assert out["results"]["s"]["rows"][0]["grade"] == "buy"
