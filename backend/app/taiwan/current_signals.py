"""Price, volume and strategy signals currently triggered for one Taiwan listing.

Signals read the newest published enriched partition and picks the /api/screener/cached payload;
fundamentals, revenue and valuation stay out because they have no availability time.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.indicators.pipeline import SIGNAL_DEPENDENCIES
from app.services import pick_grades, strategy_realtime
from app.strategy import custom_signals
from app.taiwan.benchmarks import get_benchmark
from app.taiwan.enriched_reader import read_latest_enriched
from app.taiwan.symbol import parse_symbol
from app.taiwan.volume_patterns import VOLUME_PATTERN_COLUMNS

logger = logging.getLogger(__name__)

SignalSource = Literal["builtin", "volume_pattern", "custom"]
Direction = Literal["bullish", "bearish", "neutral"]

BUILTIN_SIGNAL_COLUMNS: tuple[str, ...] = (
    *SIGNAL_DEPENDENCIES,
    "signal_limit_up",
    "signal_limit_down",
    "signal_limit_down_recovery",
    "signal_broken_limit_up",
)

# Built-in entries follow the entry / exit kinds in frontend/src/lib/signals.ts; custom signals stay neutral.
_BULLISH = frozenset({
    "signal_ma_golden_5_20", "signal_ma_golden_20_60", "signal_macd_golden",
    "signal_ma20_breakout", "signal_ma5_breakout", "signal_ma10_breakout",
    "signal_n_day_high", "signal_boll_breakout_upper",
    "signal_limit_up", "signal_limit_down_recovery",
    "vp_step_up",
})
_BEARISH = frozenset({
    "signal_ma_dead_5_20", "signal_macd_dead",
    "signal_ma20_breakdown", "signal_ma5_breakdown", "signal_ma10_breakdown",
    "signal_n_day_low", "signal_boll_breakdown_lower",
    "signal_limit_down", "signal_broken_limit_up",
    "vp_step_down", "vp_up_stall",
})

# Only selects the partition: one that lacks published indicators is not an evening publish.
_PARTITION_COLUMNS = ("close", "ma5")


def signal_direction(column: str) -> Direction:
    if column in _BULLISH:
        return "bullish"
    if column in _BEARISH:
        return "bearish"
    return "neutral"


class CurrentSignal(BaseModel):
    id: str
    source: SignalSource
    direction: Direction
    # Set for custom signals only; built-in and volume-pattern names live in the frontend catalogue.
    name: str | None = None


class CurrentSignalsSection(BaseModel):
    status: Literal["ok", "unpublished", "no_row"]
    as_of: str | None = None
    triggered: list[CurrentSignal] = Field(default_factory=list)
    not_evaluated: list[CurrentSignal] = Field(default_factory=list)


class StrategySelection(BaseModel):
    strategy_id: str
    strategy_name: str
    as_of: str | None = None
    rank: int
    total: int | None = None
    score: float | None = None
    grade: Literal["buy", "watch"] | None = None
    grade_reason: str | None = None
    limit_up_locked: bool = False
    missing_scoring_fields: list[str] | None = None
    missing_scoring_children: list[dict[str, Any]] | None = None


class StrategySelectionsSection(BaseModel):
    status: Literal["ok", "no_cache"]
    as_of: str | None = None
    updated_at: int | None = None
    market_state: dict[str, Any] | None = None
    grading: dict[str, Any] | None = None
    selections: list[StrategySelection] = Field(default_factory=list)


class TaiwanCurrentSignalsResponse(BaseModel):
    symbol: str
    exchange: str
    applicable: bool
    not_applicable_reason: Literal["index"] | None = None
    signals: CurrentSignalsSection | None = None
    strategies: StrategySelectionsSection | None = None


def _candidates(data_dir: Any) -> list[CurrentSignal]:
    items = [
        CurrentSignal(id=c, source="builtin", direction=signal_direction(c)) for c in BUILTIN_SIGNAL_COLUMNS
    ]
    items += [
        CurrentSignal(id=c, source="volume_pattern", direction=signal_direction(c))
        for c in VOLUME_PATTERN_COLUMNS
    ]
    for definition in custom_signals.load_all(data_dir):
        signal_id = definition.get("id")
        if not isinstance(signal_id, str) or definition.get("enabled") is False:
            continue
        column = custom_signals.column_name(signal_id)
        items.append(
            CurrentSignal(
                id=column,
                source="custom",
                direction=signal_direction(column),
                name=str(definition.get("name") or signal_id),
            )
        )
    return items


def read_signals(symbol: str, data_dir: Any) -> CurrentSignalsSection:
    """Triggered flags of *symbol*; a flag column the partition lacks is not evaluated, never untriggered.

    A null flag (e.g. a volume pattern whose window holds an untraded session) is not listed.
    """
    candidates = _candidates(data_dir)
    latest = read_latest_enriched(
        list(_PARTITION_COLUMNS), [symbol], optional_columns=tuple(c.id for c in candidates)
    )
    if latest is None:
        return CurrentSignalsSection(status="unpublished")
    as_of, frame = latest
    if frame.is_empty():
        return CurrentSignalsSection(status="no_row", as_of=as_of.isoformat())
    row = frame.row(0, named=True)
    triggered: list[CurrentSignal] = []
    not_evaluated: list[CurrentSignal] = []
    for candidate in candidates:
        if candidate.id not in frame.columns:
            not_evaluated.append(candidate)
        elif row[candidate.id] is True:
            triggered.append(candidate)
    return CurrentSignalsSection(
        status="ok", as_of=as_of.isoformat(), triggered=triggered, not_evaluated=not_evaluated
    )


def _strategy_name(engine: Any, strategy_id: str) -> str:
    try:
        if engine is not None and engine.has(strategy_id):
            return str(engine.get(strategy_id).meta.get("name") or strategy_id)
    except Exception:
        logger.warning("strategy name lookup failed for %s", strategy_id, exc_info=True)
    return strategy_id


def _score(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def read_strategy_selections(symbol: str, app_state: Any) -> StrategySelectionsSection:
    """Strategies picking *symbol*, graded over each strategy's whole list as /api/screener/cached grades them."""
    cached = strategy_realtime.cached_with_realtime(app_state)
    if not cached.get("results") and cached.get("as_of") is None:
        return StrategySelectionsSection(status="no_cache")
    engine = getattr(app_state, "strategy_engine", None)
    payload = pick_grades.grade_cached_payload(cached, engine, app_state.repo.store.data_dir)
    selections: list[StrategySelection] = []
    for strategy_id, result in (payload.get("results") or {}).items():
        if not isinstance(result, dict):
            continue
        for rank, row in enumerate(result.get("rows") or [], start=1):
            if not isinstance(row, dict) or row.get("symbol") != symbol:
                continue
            selections.append(
                StrategySelection(
                    strategy_id=strategy_id,
                    strategy_name=_strategy_name(engine, strategy_id),
                    as_of=result.get("as_of") or payload.get("as_of"),
                    rank=rank,
                    total=result.get("total"),
                    score=_score(row.get("score")),
                    grade=row.get("grade"),
                    grade_reason=row.get("grade_reason"),
                    limit_up_locked=row.get("limit_up_locked") is True,
                    missing_scoring_fields=row.get("missing_scoring_fields"),
                    missing_scoring_children=row.get("missing_scoring_children"),
                )
            )
            break
    return StrategySelectionsSection(
        status="ok",
        as_of=payload.get("as_of"),
        updated_at=payload.get("updated_at"),
        market_state=payload.get("market_state"),
        grading=payload.get("grading"),
        selections=selections,
    )


class TaiwanCurrentSignalsService:
    """Reads published partitions and the strategy cache for one listing; never computes or fetches."""

    def __init__(self, app_state: Any, security_master=None) -> None:
        self.app_state = app_state
        if security_master is None:
            from app.taiwan.universe import get_security_master

            security_master = get_security_master()
        self.security_master = security_master

    def get(self, raw_symbol: str) -> TaiwanCurrentSignalsResponse:
        symbol = parse_symbol(raw_symbol)
        base = {"symbol": symbol.canonical, "exchange": symbol.exchange.value}
        if get_benchmark(symbol.canonical) is not None:
            return TaiwanCurrentSignalsResponse(**base, applicable=False, not_applicable_reason="index")
        instrument = self.security_master.get_instrument(symbol.canonical)
        if getattr(instrument, "instrument_type", None) not in {"stock", "etf"}:
            raise LookupError(f"supported security type not found for {symbol.canonical}")
        from app.config import settings

        return TaiwanCurrentSignalsResponse(
            **base,
            applicable=True,
            signals=read_signals(symbol.canonical, settings.data_dir),
            strategies=read_strategy_selections(symbol.canonical, self.app_state),
        )


def get_taiwan_current_signals_service(app_state: Any) -> TaiwanCurrentSignalsService:
    return TaiwanCurrentSignalsService(app_state)
