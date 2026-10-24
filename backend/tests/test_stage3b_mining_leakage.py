from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.backtest.factor import FACTOR_COLUMNS
from app.backtest.mining import (
    FactorMetric,
    MiningBudget,
    MiningCandidate,
    MiningRequest,
    MiningService,
    NestedValidationConfig,
    compute_candidate_signature,
    generate_nested_folds,
)
from app.backtest.mining_runtime import _candidate_name

_BOUNDARY_INDEX = 12


def _labels(days: int) -> list[str]:
    start = date(2024, 1, 2)
    return [(start + timedelta(days=index * 2)).isoformat() for index in range(days)]


def _panel(days: int, assets: int = 6) -> pl.DataFrame:
    rows = []
    for day_id, label in enumerate(_labels(days)):
        for asset_id in range(assets):
            target = float(asset_id + (day_id % 2) * 0.1)
            rows.append({
                "symbol": f"{asset_id:06d}.TWSE",
                "date": date.fromisoformat(label),
                "good": target,
                "noise": float((asset_id * 7 + day_id * 3) % assets),
                "_next_return": target,
            })
    return pl.DataFrame(rows)


class _RegimeShiftEvaluator:
    """Prefer ``good`` on windows before the boundary date and ``noise`` afterwards."""

    def __init__(self, boundary: str) -> None:
        self.boundary = boundary

    def evaluate_candidate(self, train, test, definition):
        raise AssertionError("label evaluation is expected")

    def evaluate_candidate_labels(self, train_labels, test_labels, definition):
        prefer_good = test_labels[0] < self.boundary
        names = definition.get("factor_names") or ()
        is_good = bool(names) and names[0] == "good"
        return {
            "score": 2.0 if is_good == prefer_good else 1.0,
            "metrics": {"total_return": 0.01, "sharpe": 1.0, "n_trades": 3},
        }


def _overlapping_request() -> MiningRequest:
    return MiningRequest(
        factor_names=("good", "noise"),
        budget=MiningBudget(max_combination_size=1, max_proxy_trials=64, max_trials=128),
        validation=NestedValidationConfig(
            outer_train_bars=8,
            outer_test_bars=2,
            outer_step_bars=4,
            inner_train_bars=4,
            inner_test_bars=2,
            inner_step_bars=2,
            purge_bars=0,
            embargo_bars=0,
            min_train_bars=4,
        ),
        profile="exploratory",
    )


def test_cross_evaluation_never_scores_a_winner_inside_its_selection_window() -> None:
    days = 24
    labels = _labels(days)
    request = _overlapping_request()
    nested = generate_nested_folds(labels, request.validation)

    result = MiningService().run(
        _panel(days),
        request,
        metric_provider=lambda _train, _names: (
            FactorMetric("good", 1.0, 1.0, 1.0, 0.1),
            FactorMetric("noise", 0.9, 0.9, 1.0, 0.1),
        ),
        evaluator=_RegimeShiftEvaluator(labels[_BOUNDARY_INDEX]),
    )

    winners = [fold.selected_candidate_id for fold in result.folds]
    assert len(set(winners)) == 2
    first_selection: dict[str, int] = {}
    for index, candidate_id in enumerate(winners):
        first_selection.setdefault(candidate_id, index)
    late_winner = max(first_selection, key=first_selection.get)
    assert first_selection[late_winner] > 0

    cross_scored = 0
    for fold_index, fold in enumerate(result.folds):
        test_labels = nested[fold_index].outer.test_labels
        for candidate_id, _evaluation in fold.cross_evaluations:
            origin = nested[first_selection[candidate_id]].outer
            selection_window = set(origin.train_labels) | set(origin.purge_labels)
            assert not selection_window.intersection(test_labels), (
                f"fold {fold_index} scores {candidate_id} inside its selection window"
            )
            assert test_labels[0] > origin.train_end
            cross_scored += 1
    assert cross_scored > 0


def _four_longest_factor_ids() -> list[str]:
    return sorted(
        (str(item["id"]) for item in FACTOR_COLUMNS),
        key=lambda value: (-len(value), value),
    )[:5]


def _factor_candidate(names: list[str]) -> MiningCandidate:
    definition = {
        "kind": "factor_rank",
        "factor_names": names,
        "scoring": dict.fromkeys(names, 1.0),
        "directions": dict.fromkeys(names, "high"),
    }
    return MiningCandidate(
        candidate_id=compute_candidate_signature(definition),
        kind="factor_rank",
        factor_names=tuple(names),
        weights=tuple(1.0 for _ in names),
        directions=tuple(1 for _ in names),
    )


def test_four_factor_candidate_names_fit_the_publication_limit_and_stay_unique() -> None:
    longest = _four_longest_factor_ids()
    first = _factor_candidate(longest[:4])
    second = _factor_candidate([*longest[:3], longest[4]])

    first_name = _candidate_name(first)
    second_name = _candidate_name(second)

    assert len(first_name) <= 80
    assert len(second_name) <= 80
    assert first_name != second_name
    assert first_name.startswith("因子组合 · " + longest[0])


def test_short_candidate_names_are_unchanged() -> None:
    candidate = _factor_candidate(["rsi_14", "turnover_rate"])
    assert _candidate_name(candidate) == "因子组合 · rsi_14 + turnover_rate"
