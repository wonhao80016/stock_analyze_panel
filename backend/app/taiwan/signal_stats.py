"""Outcomes of past signal triggers on one Taiwan listing, priced as strategy_pick_performance prices picks.
Excess returns use the archived TWSE total return index only, never a price index.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from app.enriched_generation import (
    EnrichedGenerationUnavailableError,
    enriched_publication_incomplete,
    get_enriched_generation,
)
from app.parquet import scan_parquet_compat
from app.services import strategy_pick_performance as spp
from app.strategy import custom_signals
from app.taiwan.benchmarks import get_benchmark
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.current_signals import Direction, signal_candidates
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_reader import ENRICHED_DIR_NAME, read_latest_enriched
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.monthly_revenue_store import TaiwanMonthlyRevenueStore
from app.taiwan.return_index import (
    TAIEX_TOTAL_RETURN,
    TaiwanReturnIndexStore,
    read_return_index_series,
)
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)

HORIZONS: tuple[int, ...] = (20, 60)
MIN_SAMPLES = 5
STATE_GAP_SESSIONS = 20
NEW_HIGH_SESSIONS = 252
FOREIGN_STREAK_SESSIONS = 5
REVENUE_YOY_THRESHOLD_PCT = 20.0

NEW_HIGH_252 = "high_252d"
FOREIGN_BUY_5D = "foreign_buy_5d"
_COMPUTED_NAMES = {NEW_HIGH_252: "252日新高", FOREIGN_BUY_5D: "外資連5買"}

EventKind = Literal["cross", "state"]

# Crossovers are one-session events counted every time; every other flag is a multi-day state.
_CROSS_SIGNALS = frozenset({
    "signal_ma_golden_5_20", "signal_ma_dead_5_20", "signal_ma_golden_20_60",
    "signal_macd_golden", "signal_macd_dead",
    "signal_ma20_breakout", "signal_ma20_breakdown",
    "signal_ma5_breakout", "signal_ma5_breakdown",
    "signal_ma10_breakout", "signal_ma10_breakdown",
})

_PARTITION_COLUMNS = ["close", "ma5"]
_RESULT_ENTRIES = 64
_FAR_PAST, _FAR_FUTURE = date(1900, 1, 1), date(9999, 12, 31)


def event_kind(signal_id: str) -> EventKind:
    return "cross" if signal_id in _CROSS_SIGNALS else "state"


class SignalHorizonStats(BaseModel):
    """Returns are decimals; ratios are shares of completed events, set only when the count reaches MIN_SAMPLES."""

    horizon: int
    event_count: int
    completed_count: int
    pending_count: int
    not_filled_limit_up: int
    not_filled_no_trade: int
    data_excluded_count: int
    sufficient: bool
    mean_return: float | None = None
    median_return: float | None = None
    up_ratio: float | None = None
    benchmark_count: int = 0
    benchmark_sufficient: bool = False
    excess_mean: float | None = None
    beat_ratio: float | None = None


class SignalStat(BaseModel):
    id: str
    source: Literal["builtin", "volume_pattern", "custom", "computed"]
    direction: Direction
    name: str | None = None
    event_kind: EventKind
    evaluated: bool
    current: bool | None = None
    first_event_date: str | None = None
    last_event_date: str | None = None
    horizons: list[SignalHorizonStats] = Field(default_factory=list)


class RevenueCondition(BaseModel):
    """Whether the latest stored month's year-over-year growth reaches the threshold; no history statistics."""

    status: Literal["ok", "no_data"]
    holds: bool | None = None
    period: str | None = None
    yoy_pct: float | None = None
    threshold_pct: float = REVENUE_YOY_THRESHOLD_PCT
    history_stats: Literal[False] = False


class SignalStatsMethod(BaseModel):
    entry: Literal["next_session_open"] = "next_session_open"
    price_adjustment: Literal["forward_adjusted_incl_dividends"] = "forward_adjusted_incl_dividends"
    costs_included: Literal[False] = False
    return_unit: Literal["decimal"] = "decimal"
    horizons: list[int] = Field(default_factory=lambda: list(HORIZONS))
    min_samples: int = MIN_SAMPLES
    up_definition: Literal["return_above_zero"] = "return_above_zero"
    state_gap_sessions: int = STATE_GAP_SESSIONS
    new_high_sessions: int = NEW_HIGH_SESSIONS
    new_high_price: Literal["forward_adjusted_close"] = "forward_adjusted_close"
    foreign_streak_sessions: int = FOREIGN_STREAK_SESSIONS
    benchmark: Literal["twse_total_return_index"] = "twse_total_return_index"
    benchmark_window: Literal["signal_close_to_exit_close"] = "signal_close_to_exit_close"
    overlapping_holding_periods: Literal[True] = True


class SignalStatsBenchmark(BaseModel):
    index_name: str = TAIEX_TOTAL_RETURN
    covered_from: str | None = None
    covered_through: str | None = None


class TaiwanSignalStatsResponse(BaseModel):
    symbol: str
    exchange: str
    applicable: bool
    not_applicable_reason: Literal["index"] | None = None
    status: Literal["ok", "unpublished"] | None = None
    as_of: str | None = None
    history_from: str | None = None
    prices_through: str | None = None
    institutional_from: str | None = None
    adjustment_status: str | None = None
    adjustment_reason: str | None = None
    method: SignalStatsMethod | None = None
    benchmark: SignalStatsBenchmark | None = None
    signals: list[SignalStat] = Field(default_factory=list)
    revenue: RevenueCondition | None = None


def signal_events(
    dates: Sequence[date], flags: Sequence[bool | None], kind: EventKind, sessions: Sequence[date]
) -> list[date]:
    """Event dates of a flag series ordered by date: a cross on every true day, a state on a day it
    turns true unless fewer than STATE_GAP_SESSIONS sessions after the previous counted event.
    """
    events: list[date] = []
    previous: bool | None = None
    last_index: int | None = None
    for d, flag in zip(dates, flags, strict=True):
        starts = flag is True and (kind == "cross" or previous is not True)
        previous = flag
        if not starts:
            continue
        index = bisect.bisect_left(sessions, d)
        if kind == "state" and last_index is not None and index - last_index < STATE_GAP_SESSIONS:
            continue
        events.append(d)
        last_index = index
    return events


def new_high_flags(history: pl.DataFrame) -> pl.DataFrame:
    """(date, flag): the close is the highest of its last NEW_HIGH_SESSIONS rows; null until that many exist."""
    closes = history.select("date", "close").drop_nulls("close").sort("date")
    rolling = pl.col("close").rolling_max(NEW_HIGH_SESSIONS, min_samples=NEW_HIGH_SESSIONS)
    return closes.select("date", (pl.col("close") >= rolling).alias("flag"))


def foreign_streak_flags(
    sessions: Sequence[date], stored: set[date], net: dict[date, int | None]
) -> list[bool | None]:
    """Per session, whether foreigners net bought on each of the last FOREIGN_STREAK_SESSIONS sessions.
    A session without a stored partition is unknown; a stored partition lacking the row is no buying.
    """
    positive: list[bool | None] = []
    for d in sessions:
        if d not in stored:
            positive.append(None)
        else:
            value = net.get(d, 0)
            positive.append(None if value is None else value > 0)
    flags: list[bool | None] = []
    for i in range(len(sessions)):
        if i + 1 < FOREIGN_STREAK_SESSIONS:
            flags.append(None)
            continue
        window = positive[i + 1 - FOREIGN_STREAK_SESSIONS: i + 1]
        if all(v is True for v in window):
            flags.append(True)
        elif any(v is False for v in window):
            flags.append(False)
        else:
            flags.append(None)
    return flags


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def horizon_stats(rows: pl.DataFrame, k: int, benchmark: pl.DataFrame) -> SignalHorizonStats:
    """Statistics of one signal's outcome rows at horizon *k*; *benchmark* holds (date, close)."""
    reasons = rows.get_column(f"reason_{k}")
    completed = rows.filter(pl.col(f"ret_{k}").is_not_null())
    n = completed.height
    known = {spp.PENDING, spp.NOT_FILLED_LIMIT_UP, spp.NOT_FILLED_NO_TRADE}
    stats = SignalHorizonStats(
        horizon=k,
        event_count=rows.height,
        completed_count=n,
        pending_count=int((reasons == spp.PENDING).sum()),
        not_filled_limit_up=int((reasons == spp.NOT_FILLED_LIMIT_UP).sum()),
        not_filled_no_trade=int((reasons == spp.NOT_FILLED_NO_TRADE).sum()),
        data_excluded_count=int((reasons.is_not_null() & ~reasons.is_in(list(known))).sum()),
        sufficient=n >= MIN_SAMPLES,
    )
    if stats.sufficient:
        returns = completed.get_column(f"ret_{k}")
        stats.mean_return = _float(returns.mean())
        stats.median_return = _float(returns.median())
        stats.up_ratio = float((returns > 0).sum()) / n
    start = benchmark.rename({"date": "signal_date", "close": "_start"})
    end = benchmark.rename({"date": f"exit_date_{k}", "close": "_end"})
    excess = (
        completed.join(start, on="signal_date", how="left")
        .join(end, on=f"exit_date_{k}", how="left")
        .drop_nulls(["_start", "_end"])
        .select((pl.col(f"ret_{k}") - (pl.col("_end") / pl.col("_start") - 1.0)).alias("excess"))
        .get_column("excess")
    )
    stats.benchmark_count = excess.len()
    stats.benchmark_sufficient = stats.benchmark_count >= MIN_SAMPLES
    if stats.benchmark_sufficient:
        stats.excess_mean = _float(excess.mean())
        stats.beat_ratio = float((excess > 0).sum()) / stats.benchmark_count
    return stats


def _picks(symbol: str, events: dict[str, list[date]]) -> pl.DataFrame:
    ids = [sid for sid, dates in events.items() for _ in dates]
    dates = [d for found in events.values() for d in found]
    n = len(ids)
    return pl.DataFrame(
        {
            "signal_date": dates,
            "strategy_id": ids,
            "symbol": [symbol] * n,
            "recorded_late": [False] * n,
            "manifest_missing": [False] * n,
            "universe_complete": [True] * n,
        },
        schema={
            "signal_date": pl.Date, "strategy_id": pl.String, "symbol": pl.String,
            "recorded_late": pl.Boolean, "manifest_missing": pl.Boolean, "universe_complete": pl.Boolean,
        },
    )


@dataclass(frozen=True)
class MarketInputs:
    calendar: spp.PriceCalendar
    events: pl.DataFrame
    adjustment: spp.AdjustmentState


@dataclass(frozen=True)
class SymbolInputs:
    """Everything one symbol's statistics need, already read from the stores."""

    symbol: str
    as_of: date | None
    latest: dict[str, Any]
    candidates: list[Any]
    history: pl.DataFrame
    bars: pl.DataFrame
    limit_profile: pl.DataFrame
    institutional_dates: list[date]
    foreign_net: dict[date, int | None]
    benchmark: pl.DataFrame


def build_signal_stats(inputs: SymbolInputs, market: MarketInputs) -> list[SignalStat]:
    """Per-signal statistics; a candidate column the newest published partition lacks is not evaluated."""
    sessions = market.calendar.stored_dates
    history = inputs.history.sort("date")
    series: dict[str, tuple[list[date], list[bool | None]]] = {}
    stats: list[SignalStat] = []
    for candidate in inputs.candidates:
        evaluated = candidate.id in inputs.latest
        stats.append(SignalStat(
            id=candidate.id, source=candidate.source, direction=candidate.direction, name=candidate.name,
            event_kind=event_kind(candidate.id), evaluated=evaluated,
            current=inputs.latest.get(candidate.id) if evaluated else None,
        ))
        if evaluated and candidate.id in history.columns:
            series[candidate.id] = (history.get_column("date").to_list(), history.get_column(candidate.id).to_list())
    highs = new_high_flags(history) if "close" in history.columns else pl.DataFrame(
        schema={"date": pl.Date, "flag": pl.Boolean}
    )
    series[NEW_HIGH_252] = (highs.get_column("date").to_list(), highs.get_column("flag").to_list())
    foreign = foreign_streak_flags(sessions, set(inputs.institutional_dates), inputs.foreign_net)
    series[FOREIGN_BUY_5D] = (list(sessions), foreign)
    for computed in (NEW_HIGH_252, FOREIGN_BUY_5D):
        dates, flags = series[computed]
        current = flags[dates.index(inputs.as_of)] if inputs.as_of in dates else None
        stats.append(SignalStat(
            id=computed, source="computed", direction="bullish", name=_COMPUTED_NAMES[computed],
            event_kind=event_kind(computed), evaluated=True, current=current,
        ))

    events = {sid: signal_events(dates, flags, event_kind(sid), sessions) for sid, (dates, flags) in series.items()}
    outcomes = spp.compute_outcomes(
        _picks(inputs.symbol, events), inputs.bars, market.events, market.adjustment, market.calendar,
        inputs.limit_profile, horizons=HORIZONS,
    )
    for stat in stats:
        if not stat.evaluated:
            continue
        found = events.get(stat.id, [])
        rows = outcomes.filter(pl.col("strategy_id") == stat.id)
        stat.first_event_date = found[0].isoformat() if found else None
        stat.last_event_date = found[-1].isoformat() if found else None
        stat.horizons = [horizon_stats(rows, k, inputs.benchmark) for k in HORIZONS]
    return stats


def revenue_condition(rows: list[dict[str, Any]]) -> RevenueCondition:
    """*rows* are the stored months newest first; yoy_pct is in percent (25.0 means 25%)."""
    if not rows:
        return RevenueCondition(status="no_data")
    latest = rows[0]
    yoy = latest.get("yoy_pct")
    return RevenueCondition(
        status="ok",
        holds=None if yoy is None else yoy >= REVENUE_YOY_THRESHOLD_PCT,
        period=f"{latest['year']:04d}-{latest['month']:02d}",
        yoy_pct=yoy,
    )


def _limit_profile(symbols: list[str]) -> pl.DataFrame:
    """Statutory limit per symbol; a lookup failure leaves every limit check unknown instead of failing."""
    from app.indicators.pipeline import taiwan_limit_profile

    try:
        return taiwan_limit_profile(symbols)
    except Exception as exc:
        logger.warning("signal stats: limit profile lookup failed: %s", exc)
        return pl.DataFrame(
            {"symbol": symbols, "limit_pct": [None] * len(symbols), "is_etf_tick": [False] * len(symbols)},
            schema={"symbol": pl.String, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean},
        )


_market_lock = threading.Lock()
_market: tuple[tuple, MarketInputs] | None = None
_results_lock = threading.Lock()
_results: OrderedDict[tuple, TaiwanSignalStatsResponse] = OrderedDict()


def clear_signal_stats_cache() -> None:
    global _market
    with _market_lock:
        _market = None
    with _results_lock:
        _results.clear()


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _partitions_signature(root: Path) -> tuple:
    parts = sorted(root.glob("date=*/part.parquet")) if root.is_dir() else []
    return (len(parts), str(parts[-1]), _mtime(parts[-1])) if parts else (0,)


class TaiwanSignalStatsService:
    """Computes one listing's signal statistics once per set of dataset dates; never fetches."""

    def __init__(
        self,
        security_master=None,
        daily_store: TaiwanDailyStore | None = None,
        institutional_store: TaiwanInstitutionalStore | None = None,
        corporate_actions: TaiwanCorporateActionStore | None = None,
        return_index_store: TaiwanReturnIndexStore | None = None,
        revenue_store: TaiwanMonthlyRevenueStore | None = None,
        limit_profile: Callable[[list[str]], pl.DataFrame] | None = None,
    ) -> None:
        if security_master is None:
            from app.taiwan.universe import get_security_master

            security_master = get_security_master()
        self.security_master = security_master
        self.daily_store = daily_store or TaiwanDailyStore()
        self.institutional_store = institutional_store or TaiwanInstitutionalStore()
        self.corporate_actions = corporate_actions or TaiwanCorporateActionStore()
        self.return_index_store = return_index_store or TaiwanReturnIndexStore()
        self.revenue_store = revenue_store or TaiwanMonthlyRevenueStore()
        self.limit_profile = limit_profile or _limit_profile

    @staticmethod
    def _data_dir() -> Path:
        from app.config import settings

        return Path(settings.data_dir)

    def _enriched_signature(self) -> tuple:
        data_dir = self._data_dir()
        if enriched_publication_incomplete(data_dir):
            raise EnrichedGenerationUnavailableError("enriched data is being published")
        try:
            generation = get_enriched_generation(data_dir, "stock", initialize=False)
        except EnrichedGenerationUnavailableError:
            generation = None
        return generation, _partitions_signature(data_dir / ENRICHED_DIR_NAME)

    def _market_signature(self) -> tuple:
        dates = self.daily_store.available_dates()
        latest = dates[-1] if dates else None
        return (
            len(dates), latest, self.daily_store.partition_mtime(latest) if latest else None,
            _mtime(self.corporate_actions.path), _mtime(self.corporate_actions.fetch_state_path),
        )

    def _signature(self) -> tuple:
        inst_dates = self.institutional_store.available_dates()
        inst_latest = inst_dates[-1] if inst_dates else None
        definitions = json.dumps(custom_signals.load_all(self._data_dir()), sort_keys=True, default=str)
        return (
            self._enriched_signature(),
            self._market_signature(),
            (len(inst_dates), inst_latest, tuple(self.institutional_store.provisional_dates([inst_latest]))
             if inst_latest else ()),
            _partitions_signature(self.return_index_store.root),
            hashlib.sha256(definitions.encode("utf-8")).hexdigest(),
            self.revenue_store.signature(),
        )

    def market_inputs(self) -> MarketInputs:
        """The market-wide calendar and corporate actions, shared by every symbol of the same daily data."""
        global _market
        key = self._market_signature()
        with _market_lock:
            cached = _market
        if cached is not None and cached[0] == key:
            return cached[1]
        events, adjustment = spp.read_adjustment(self.corporate_actions)
        stored = self.daily_store.available_dates()
        market = self.daily_store.scan_range(None, None, None) if stored else pl.DataFrame(
            schema={"symbol": pl.String, "date": pl.Date}
        )
        built = MarketInputs(spp.PriceCalendar.from_market(stored, market), events, adjustment)
        with _market_lock:
            _market = (key, built)
        return built

    def _history(self, symbol: str, as_of: date, columns: list[str]) -> pl.DataFrame:
        root = self._data_dir() / ENRICHED_DIR_NAME
        last = f"date={as_of.isoformat()}"
        files = [p for p in sorted(root.glob("date=*/part.parquet")) if p.parent.name <= last]
        schema: dict[str, pl.DataType] = {
            "symbol": pl.String, "date": pl.Date, "close": pl.Float64, **{c: pl.Boolean for c in columns},
        }
        if not files:
            return pl.DataFrame(schema=schema)
        return (
            scan_parquet_compat(
                files, schema=schema,
                cast_options=pl.ScanCastOptions(integer_cast="allow-float", float_cast="upcast"),
            )
            .filter(pl.col("symbol") == symbol)
            .sort("date")
            .collect()
        )

    def _symbol_inputs(self, symbol: str) -> tuple[SymbolInputs, date | None] | None:
        candidates = signal_candidates(self._data_dir())
        latest = read_latest_enriched(_PARTITION_COLUMNS, [symbol], optional_columns=tuple(c.id for c in candidates))
        if latest is None:
            return None
        as_of, frame = latest
        present = [c.id for c in candidates if c.id in frame.columns]
        row = frame.row(0, named=True) if not frame.is_empty() else {}
        history = self._history(symbol, as_of, present)
        inst_dates = self.institutional_store.available_dates()
        flows = self.institutional_store.read_range([symbol], None, None)
        foreign = (
            dict(zip(flows.get_column("date").to_list(), flows.get_column("foreign_net").to_list(), strict=True))
            if not flows.is_empty() else {}
        )
        inputs = SymbolInputs(
            symbol=symbol,
            as_of=as_of if row else None,
            latest={c: row.get(c) for c in present},
            candidates=candidates,
            history=history,
            bars=self.daily_store.scan_range([symbol], None, None),
            limit_profile=self.limit_profile([symbol]),
            institutional_dates=inst_dates,
            foreign_net=foreign,
            benchmark=read_return_index_series(_FAR_PAST, _FAR_FUTURE, store=self.return_index_store),
        )
        return inputs, as_of

    def _build(self, symbol: str, base: dict[str, Any], instrument: Any) -> TaiwanSignalStatsResponse:
        stock = getattr(instrument, "instrument_type", None) == "stock" and not MarketProfileBridge.is_tdr(instrument)
        revenue = revenue_condition(self.revenue_store.rows_for(symbol)) if stock else None
        read = self._symbol_inputs(symbol)
        if read is None:
            return TaiwanSignalStatsResponse(**base, applicable=True, status="unpublished", revenue=revenue)
        inputs, as_of = read
        market = self.market_inputs()
        bench_dates = inputs.benchmark.get_column("date")
        history_dates = inputs.history.get_column("date")
        return TaiwanSignalStatsResponse(
            **base,
            applicable=True,
            status="ok",
            as_of=as_of.isoformat(),
            history_from=history_dates.min().isoformat() if not history_dates.is_empty() else None,
            prices_through=_iso(market.calendar.prices_through),
            institutional_from=inputs.institutional_dates[0].isoformat() if inputs.institutional_dates else None,
            adjustment_status=market.adjustment.status,
            adjustment_reason=market.adjustment.reason,
            method=SignalStatsMethod(),
            benchmark=SignalStatsBenchmark(
                covered_from=bench_dates.min().isoformat() if not bench_dates.is_empty() else None,
                covered_through=bench_dates.max().isoformat() if not bench_dates.is_empty() else None,
            ),
            signals=build_signal_stats(inputs, market),
            revenue=revenue,
        )

    def get(self, raw_symbol: str) -> TaiwanSignalStatsResponse:
        symbol = parse_symbol(raw_symbol)
        base = {"symbol": symbol.canonical, "exchange": symbol.exchange.value}
        if get_benchmark(symbol.canonical) is not None:
            return TaiwanSignalStatsResponse(**base, applicable=False, not_applicable_reason="index")
        instrument = self.security_master.get_instrument(symbol.canonical)
        if getattr(instrument, "instrument_type", None) not in {"stock", "etf"}:
            raise LookupError(f"supported security type not found for {symbol.canonical}")
        key = (symbol.canonical, getattr(instrument, "instrument_type", None), self._signature())
        with _results_lock:
            cached = _results.get(key)
            if cached is not None:
                _results.move_to_end(key)
                return cached
        built = self._build(symbol.canonical, base, instrument)
        with _results_lock:
            _results[key] = built
            while len(_results) > _RESULT_ENTRIES:
                _results.popitem(last=False)
        return built


def get_taiwan_signal_stats_service() -> TaiwanSignalStatsService:
    return TaiwanSignalStatsService()
