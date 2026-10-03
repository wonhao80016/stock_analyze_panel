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
REASON_ZERO_SCORE = "zero_score"
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
    """Strategies ranked by a weighted score; composite and unknown strategies are ranked only.

    Overrides are read at request time, so a scoring edit made after the cached run already
    applies to rows that were ranked before it.
    """
    scored: set[str] = set()
    for sid in strategy_ids:
        try:
            if engine is None or not engine.has(sid):
                continue
            strategy = engine.get(sid)
            if strategy.execution_backend == "composite":
                continue
            overrides = strategy_config.load_override(data_dir, sid)
            if has_scoring(effective_scoring(strategy.meta.get("scoring"), overrides)):
                scored.add(sid)
        except Exception:
            logger.warning("scoring lookup failed for %s; grading by rank only", sid, exc_info=True)
    return scored


def _positive(score: Any) -> bool:
    return (
        isinstance(score, int | float)
        and not isinstance(score, bool)
        and math.isfinite(score)
        and score > 0
    )


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

    Positions are never re-sorted by score, so null scores ranked first stay where they are.
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
            if rank > top_n:
                grade, reason = GRADE_WATCH, REASON_OUTSIDE_TOP_N
            elif sid in scored_ids and not _positive(row.get("score")):
                grade, reason = GRADE_WATCH, REASON_ZERO_SCORE
            elif downgrade:
                grade, reason = GRADE_WATCH, REASON_MARKET_BELOW_MA
            else:
                grade, reason = GRADE_BUY, None
            new_rows.append({**row, "grade": grade, "grade_reason": reason})
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
