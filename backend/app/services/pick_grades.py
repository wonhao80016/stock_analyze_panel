"""Buy / watch grades for cached strategy picks, with the TAIEX moving-average filter."""
from __future__ import annotations

import logging
import math
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from app.strategy import config as strategy_config
from app.strategy.scoring import effective_scoring
from app.taiwan.market_state import STATUS_OK, compute_market_state

logger = logging.getLogger(__name__)

TOP_N = 5

GRADE_BUY = "buy"
GRADE_WATCH = "watch"

REASON_OUTSIDE_TOP_N = "outside_top_n"
REASON_SCORE_UNAVAILABLE = "score_unavailable"
REASON_MARKET_BELOW_MA = "market_below_ma20"

FILTER_APPLIED = "applied"
FILTER_PASSED = "passed"
FILTER_UNAVAILABLE = "unavailable"


def has_scoring(scoring: Mapping[str, Any] | None) -> bool:
    """Same test as build_matrix_score: a non-zero weight exists and the weights sum above 0."""
    weights: list[float] = []
    for weight in (scoring or {}).values():
        try:
            value = float(weight)
        except (TypeError, ValueError):
            continue
        if value != 0.0:
            weights.append(value)
    return bool(weights) and sum(weights) > 0


def scored_strategy_ids(engine: Any, data_dir: Path, strategy_ids: Collection[str]) -> set[str]:
    """Strategies ranked by a weighted score; unknown strategies are ranked only.

    A composite counts as scored when any of its children does, since only a scored child can
    leave a merged score missing. Overrides are read at request time, so a scoring edit made
    after the cached run already applies to rows that were ranked before it.
    """
    scored: set[str] = set()
    for sid in strategy_ids:
        try:
            if engine is None or not engine.has(sid):
                continue
            strategy = engine.get(sid)
            overrides = strategy_config.load_override(data_dir, sid)
            if strategy.execution_backend == "composite":
                if any(
                    engine.has(child_id) and _leaf_has_scoring(engine, data_dir, child_id)
                    for child_id in _composite_child_ids(strategy, overrides)
                ):
                    scored.add(sid)
            elif has_scoring(effective_scoring(strategy.meta.get("scoring"), overrides)):
                scored.add(sid)
        except Exception:
            logger.warning("scoring lookup failed for %s; grading by rank only", sid, exc_info=True)
    return scored


def _leaf_has_scoring(engine: Any, data_dir: Path, sid: str) -> bool:
    overrides = strategy_config.load_override(data_dir, sid)
    return has_scoring(effective_scoring(engine.get(sid).meta.get("scoring"), overrides))


def _composite_child_ids(strategy: Any, overrides: Mapping[str, Any]) -> list[str]:
    """Saved children replace the declared ones, as in StrategyEngine._run_composite_strategy."""
    saved = overrides.get("children")
    if isinstance(saved, list) and saved:
        return [
            str(child["strategy_id"])
            for child in saved
            if isinstance(child, Mapping) and child.get("strategy_id")
        ]
    spec = getattr(strategy, "composite", None)
    return [child.strategy_id for child in spec.children] if spec is not None else []


def _finite(score: Any) -> bool:
    return isinstance(score, int | float) and not isinstance(score, bool) and math.isfinite(score)


def market_filter(market_state: Mapping[str, Any] | None) -> str:
    if not market_state or market_state.get("status") != STATUS_OK:
        return FILTER_UNAVAILABLE
    return FILTER_APPLIED if market_state.get("below_ma") is True else FILTER_PASSED


def assign_grades(
    results: Mapping[str, Any],
    market_state: Mapping[str, Any] | None,
    scored_ids: Collection[str],
    top_n: int = TOP_N,
) -> dict[str, Any]:
    """Grade every row by its position in the cached list, which is the engine's ranking.

    A scored strategy's row without a finite score is watch wherever it ranks; 0 is a valid score.
    limit_up_locked marks a row whose price sits at the limit-up price (published flag after the
    close, live flag intraday); it is shown next to the grade and never changes it.
    Returns copies: realtime rows are shared with the monitor engine and must not be mutated.
    """
    downgrade = market_filter(market_state) == FILTER_APPLIED
    graded: dict[str, Any] = {}
    for sid, result in results.items():
        rows = result.get("rows") if isinstance(result, Mapping) else None
        if not isinstance(rows, list):
            graded[sid] = result
            continue
        new_rows: list[Any] = []
        for rank, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                new_rows.append(row)
                continue
            if sid in scored_ids and not _finite(row.get("score")):
                grade, reason = GRADE_WATCH, REASON_SCORE_UNAVAILABLE
            elif rank > top_n:
                grade, reason = GRADE_WATCH, REASON_OUTSIDE_TOP_N
            elif downgrade:
                grade, reason = GRADE_WATCH, REASON_MARKET_BELOW_MA
            else:
                grade, reason = GRADE_BUY, None
            new_rows.append(
                {
                    **row,
                    "grade": grade,
                    "grade_reason": reason,
                    "limit_up_locked": row.get("signal_limit_up") is True,
                }
            )
        graded[sid] = {**result, "rows": new_rows}
    return graded


def grade_cached_payload(
    payload: Mapping[str, Any], engine: Any, data_dir: Path, top_n: int = TOP_N
) -> dict[str, Any]:
    """The /cached payload with graded rows plus market_state and grading.

    The market state is that of the payload's as_of, the last post-close snapshot, also for
    realtime results of a later day. Without an as_of it is None and no row is downgraded.
    """
    market_state: dict[str, Any] | None = None
    as_of = payload.get("as_of")
    if as_of:
        try:
            market_state = compute_market_state(data_dir, date.fromisoformat(str(as_of)[:10]))
        except Exception:
            logger.warning("market state for %s could not be computed", as_of, exc_info=True)
    results = payload.get("results") or {}
    graded = assign_grades(
        results, market_state, scored_strategy_ids(engine, data_dir, list(results)), top_n
    )
    return {
        **payload,
        "results": graded,
        "market_state": market_state,
        "grading": {"top_n": top_n, "market_filter": market_filter(market_state)},
    }
