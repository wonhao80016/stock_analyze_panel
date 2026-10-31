"""Post-trade outcomes of recorded strategy picks, computed on demand from the daily K store.

Gross decimal returns (0.01 = 1%) on one forward-adjusted scale, never on unadjusted prices.
"""
from __future__ import annotations

import bisect
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.corporate_actions import (
    REF_LIMIT_UP,
    REF_PREV_CLOSE,
    TaiwanCorporateActionStore,
    _coverage,
    attach_reference_prices,
    corporate_action_schema,
    forward_adjusted,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enrichment.corporate_actions import default_adapters
from app.taiwan.market_rules import PriceLimitModel

logger = logging.getLogger(__name__)

HORIZONS: tuple[int, ...] = (1, 5, 10, 20)
LIMIT_TOLERANCE = 0.005
CORPORATE_ACTION_SOURCES: tuple[str, ...] = tuple(adapter.SOURCE for adapter in default_adapters())

PENDING_ENTRY = "pending_entry"
TRACKING = "tracking"
COMPLETED = "completed"
NOT_FILLED_LIMIT_UP = "not_filled_limit_up"
NOT_FILLED_NO_TRADE = "not_filled_no_trade"
PRICES_INCOMPLETE = "prices_incomplete"
DATA_INCOMPLETE = "data_incomplete"
ADJUSTMENT_NOT_COVERED = "adjustment_not_covered"
PENDING = "pending"

EVENTS_MISSING = "events_missing"
EVENTS_UNREADABLE = "events_unreadable"
COVERAGE_MISSING = "coverage_missing"

LIMIT_CHECKED = "checked"
LIMIT_UNKNOWN = "unknown"

_EXCHANGES = ("TWSE", "TPEX")
_NOT_FILLED = (NOT_FILLED_LIMIT_UP, NOT_FILLED_NO_TRADE)
_HORIZON_EXCLUSIONS = (ADJUSTMENT_NOT_COVERED, DATA_INCOMPLETE, PRICES_INCOMPLETE)
_PICK_FLAGS = ("recorded_late", "manifest_missing", "universe_complete")
_EXCLUSIONS = ("manifest_missing", "recorded_late", "universe_incomplete")
_BAR_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "date": pl.Date,
    **{c: pl.Float64 for c in ("open", "high", "low", "close", "volume")},
}


@dataclass(frozen=True)
class AdjustmentState:
    """Whether corporate-action data can be trusted, and the window every source covers."""

    status: str
    reason: str | None = None
    covered_from: date | None = None
    covered_through: date | None = None

    @property
    def available(self) -> bool:
        return self.status == "ok"

    def covers(self, d: date | None) -> bool:
        return (
            self.available
            and d is not None
            and self.covered_from is not None
            and self.covered_through is not None
            and self.covered_from <= d <= self.covered_through
        )


@dataclass(frozen=True)
class PriceCalendar:
    """Stored daily partition dates, and the exchanges each partition yielded when read.

    A stored date absent from *exchanges* was unreadable; both are market-wide, never per symbol.
    """

    stored_dates: tuple[date, ...]
    exchanges: Mapping[date, frozenset[str]]

    @classmethod
    def from_market(cls, stored_dates: Iterable[date], market: pl.DataFrame) -> PriceCalendar:
        found: dict[date, set[str]] = {}
        if not market.is_empty():
            pairs = (
                market.select(pl.col("date"), _exchange_expr().alias("exchange"))
                .drop_nulls("date")
                .unique()
            )
            for d, exchange in pairs.iter_rows():
                found.setdefault(d, set()).add(exchange)
        return cls(tuple(sorted(set(stored_dates))), {d: frozenset(v) for d, v in found.items()})

    @property
    def prices_through(self) -> date | None:
        return self.stored_dates[-1] if self.stored_dates else None

    def known_for(self, d: date, exchange: str) -> bool:
        """Whether the partition on *d* was read and holds the rows of *exchange* ("" = both)."""
        present = self.exchanges.get(d)
        if present is None:
            return False
        if exchange in _EXCHANGES:
            return exchange in present
        return all(e in present for e in _EXCHANGES)


@dataclass(frozen=True)
class PerformanceInputs:
    bars: pl.DataFrame
    events: pl.DataFrame
    adjustment: AdjustmentState
    calendar: PriceCalendar
    limit_profile: pl.DataFrame


def _exchange_expr() -> pl.Expr:
    return pl.col("symbol").str.extract(r"\.(TWSE|TPEX)$", 1).fill_null("")


def read_adjustment(store: TaiwanCorporateActionStore) -> tuple[pl.DataFrame, AdjustmentState]:
    """Events plus their availability; any doubt about them makes the state unavailable."""
    empty = pl.DataFrame(schema=corporate_action_schema())
    if not store.path.exists():
        return empty, AdjustmentState("unavailable", EVENTS_MISSING)
    try:
        events = store.read()
    except Exception as exc:
        logger.warning("Corporate action table unreadable, pick returns withheld: %s", exc)
        return empty, AdjustmentState("unavailable", EVENTS_UNREADABLE)
    state = store.fetch_state()
    spans = [_coverage(state.get(source)) for source in CORPORATE_ACTION_SOURCES]
    if any(span is None for span in spans):
        return empty, AdjustmentState("unavailable", COVERAGE_MISSING)
    return events, AdjustmentState(
        "ok",
        covered_from=max(span[0] for span in spans if span is not None),
        covered_through=min(span[1] for span in spans if span is not None),
    )


def load_inputs(
    data_dir: Path,
    picks: pl.DataFrame,
    limit_profile: Callable[[list[str]], pl.DataFrame],
) -> PerformanceInputs:
    """Read compute_outcomes inputs from stores under *data_dir* without creating anything on disk.

    *limit_profile* maps symbols to symbol, limit_pct, is_etf_tick and optionally ipo_start, ipo_end
    (taiwan_limit_profile in production).
    """
    root = Path(data_dir)
    daily_dir = root / "taiwan" / "daily"
    # TaiwanDailyStore creates its directory on construction; a read must not.
    daily = TaiwanDailyStore(daily_dir) if daily_dir.is_dir() else None
    events, adjustment = read_adjustment(TaiwanCorporateActionStore(root / "taiwan" / "corporate_actions"))
    symbols = sorted(set(picks.get_column("symbol").to_list())) if not picks.is_empty() else []
    stored = daily.available_dates() if daily is not None else []
    if daily is not None and symbols and stored:
        start = picks.select(pl.col("signal_date").cast(pl.Date).min()).item()
        market = daily.scan_range(None, start, None)
    else:
        market = pl.DataFrame(schema=_BAR_SCHEMA)
    calendar = PriceCalendar.from_market(stored, market)
    bars = market.filter(pl.col("symbol").is_in(symbols))
    return PerformanceInputs(bars, events, adjustment, calendar, limit_profile(symbols))


def _priced_bars(
    bars: pl.DataFrame, events: pl.DataFrame, adjustment: AdjustmentState, through: date | None
) -> pl.DataFrame:
    """Traded bars on one forward-adjusted scale, keeping raw_open, raw_close and the event limit.

    A bar whose open is not positive (halt rows carry open=0, high=0) counts as no trade.
    """
    traded = (
        bars.filter(
            pl.all_horizontal([pl.col(c).is_not_null() for c in ("open", "high", "low", "close")])
            & (pl.col("open") > 0)
        )
        .unique(subset=["symbol", "date"], keep="last")
        .with_columns(pl.col("open").alias("raw_open"), pl.col("close").alias("raw_close"))
    )
    if not adjustment.available or through is None:
        return traded.with_columns(
            pl.lit(None, dtype=pl.Float64).alias(REF_PREV_CLOSE),
            pl.lit(None, dtype=pl.Float64).alias(REF_LIMIT_UP),
        )
    with_refs = attach_reference_prices(traded, events, with_limits=True)
    return forward_adjusted(with_refs, events, through)


def _signal_windows(
    signal_dates: Iterable[date],
    calendar: PriceCalendar,
    adjustment: AdjustmentState,
    horizons: tuple[int, ...] = HORIZONS,
) -> pl.DataFrame:
    """Per signal date: the entry session and, per horizon, the exit session and its data checks."""
    stored = calendar.stored_dates
    schema: dict[str, pl.DataType] = {
        "signal_date": pl.Date,
        "entry_date": pl.Date,
        "_entry_read": pl.Boolean,
        "_entry_covered": pl.Boolean,
        **{f"_entry_known_{key}": pl.Boolean for key in (*_EXCHANGES, "")},
    }
    for k in horizons:
        schema |= {
            f"exit_date_{k}": pl.Date,
            f"_gap_{k}": pl.Boolean,
            f"_split_{k}": pl.Boolean,
            f"_exit_covered_{k}": pl.Boolean,
        }
    rows = []
    for t in signal_dates:
        start = bisect.bisect_right(stored, t)
        entry = stored[start] if start < len(stored) else None
        row: dict[str, Any] = {
            "signal_date": t,
            "entry_date": entry,
            "_entry_read": entry is not None and entry in calendar.exchanges,
            "_entry_covered": adjustment.covers(entry),
            **{
                f"_entry_known_{key}": entry is not None and calendar.known_for(entry, key)
                for key in (*_EXCHANGES, "")
            },
        }
        for k in horizons:
            end = start + k
            exit_date = stored[end - 1] if end <= len(stored) else None
            row[f"exit_date_{k}"] = exit_date
            row[f"_gap_{k}"] = exit_date is not None and any(
                d not in calendar.exchanges for d in stored[start:end]
            )
            row[f"_split_{k}"] = (
                exit_date is not None
                and exit_date in calendar.exchanges
                and not calendar.known_for(exit_date, "")
            )
            row[f"_exit_covered_{k}"] = (
                exit_date is not None
                and adjustment.covered_through is not None
                and exit_date <= adjustment.covered_through
            )
        rows.append(row)
    return pl.DataFrame(rows, schema=schema)


def _session_table(calendar: PriceCalendar) -> pl.DataFrame:
    """Per stored date: its position and, per exchange key, the running count of unknown partitions."""
    keys = (*_EXCHANGES, "")
    unknown = dict.fromkeys(keys, 0)
    rows = []
    for idx, d in enumerate(calendar.stored_dates):
        for key in keys:
            if not calendar.known_for(d, key):
                unknown[key] += 1
        rows.append({"date": d, "idx": idx, **{f"unknown_{key}": unknown[key] for key in keys}})
    schema = {"date": pl.Date, "idx": pl.Int64, **{f"unknown_{key}": pl.Int64 for key in keys}}
    return pl.DataFrame(rows, schema=schema)


def _by_exchange(prefix: str) -> pl.Expr:
    return (
        pl.when(pl.col("_exchange") == "TWSE").then(pl.col(f"{prefix}TWSE"))
        .when(pl.col("_exchange") == "TPEX").then(pl.col(f"{prefix}TPEX"))
        .otherwise(pl.col(prefix))
    )


def _limit_up_expr() -> pl.Expr:
    """The T+1 limit-up price on the raw scale: official on an event session, else T close rounded down to a tick."""
    computed, _ = PriceLimitModel.polars_limit_prices(
        pl.col("_signal_close"), pl.col("_limit_pct"), pl.col("_is_etf_tick").fill_null(False)
    )
    return pl.when(pl.col("_entry_ref_prev_close").is_not_null()).then(pl.col("_entry_ref_limit_up")).otherwise(computed)


def _join_exit_bars(frame: pl.DataFrame, priced: pl.DataFrame, k: int) -> pl.DataFrame:
    exit_col = f"exit_date_{k}"
    left = frame.select("_row", "symbol", exit_col).drop_nulls(exit_col).sort(exit_col)
    right = priced.select(
        "symbol", pl.col("date").alias(f"_last_date_{k}"), pl.col("close").alias(f"_exit_close_{k}")
    ).sort(f"_last_date_{k}")
    matched = left.join_asof(
        right,
        left_on=exit_col,
        right_on=f"_last_date_{k}",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    ).select("_row", f"_last_date_{k}", f"_exit_close_{k}")
    return frame.join(matched, on="_row", how="left")


def _join_sessions(frame: pl.DataFrame, sessions: pl.DataFrame, on: str, prefix: str) -> pl.DataFrame:
    renamed = sessions.rename({c: f"{prefix}{c}" for c in sessions.columns if c != "date"})
    return frame.join(renamed, left_on=on, right_on="date", how="left")


def _absent_since(frame: pl.DataFrame, bars: pl.DataFrame, calendar: PriceCalendar) -> pl.DataFrame:
    """First readable session after T from which the symbol has no row through the latest one."""
    last_rows = bars.group_by("symbol").agg(pl.col("date").max().alias("_last_row"))
    known = pl.DataFrame(
        [
            {"_exchange": key, "absent_since": d}
            for key in (*_EXCHANGES, "")
            for d in calendar.stored_dates
            if calendar.known_for(d, key)
        ],
        schema={"_exchange": pl.String, "absent_since": pl.Date},
    ).sort("absent_since")
    left = (
        frame.select("_row", "symbol", "signal_date", "entry_date", "_exchange")
        .filter(pl.col("entry_date").is_not_null())
        .join(last_rows, on="symbol", how="left")
        .with_columns(
            (pl.max_horizontal("_last_row", "signal_date") + pl.duration(days=1)).alias("_absent_from")
        )
        .sort("_absent_from")
    )
    matched = left.join_asof(
        known,
        left_on="_absent_from",
        right_on="absent_since",
        by="_exchange",
        strategy="forward",
        check_sortedness=False,
    ).select("_row", "absent_since")
    return frame.join(matched, on="_row", how="left")


def _prepare_picks(picks: pl.DataFrame) -> pl.DataFrame:
    """Picks must carry signal_date, strategy_id, symbol and the nullable manifest flags."""
    missing = [c for c in ("signal_date", "strategy_id", "symbol", *_PICK_FLAGS) if c not in picks.columns]
    if missing:
        raise ValueError(f"picks lack columns: {', '.join(missing)}")
    if "provenance" not in picks.columns:
        picks = picks.with_columns(pl.lit("recorded").alias("provenance"))
    return picks.with_columns(pl.col("signal_date").cast(pl.Date), *[pl.col(c).cast(pl.Boolean) for c in _PICK_FLAGS])


def _excluded_reason_expr() -> pl.Expr:
    # Exclusive in this order; a null flag cannot be confirmed, so it excludes the pick.
    return (
        pl.when(pl.col("manifest_missing").fill_null(True)).then(pl.lit("manifest_missing"))
        .when(pl.col("recorded_late").fill_null(True) & (pl.col("provenance") != "reconstructed").fill_null(True))
        .then(pl.lit("recorded_late"))
        .when(~pl.col("universe_complete").fill_null(False)).then(pl.lit("universe_incomplete"))
        .otherwise(pl.lit(None, dtype=pl.String))
    )


def compute_outcomes(
    picks: pl.DataFrame,
    bars: pl.DataFrame,
    events: pl.DataFrame,
    adjustment_state: AdjustmentState,
    calendar: PriceCalendar,
    limit_profile: pl.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
) -> pl.DataFrame:
    """One row per pick: its input columns plus status, limit check, excluded_reason and per-horizon results.

    *calendar* must come from the whole market, not from *bars*; ret_k is null exactly when reason_k is set.
    """
    adjustment = adjustment_state
    frame = (
        _prepare_picks(picks)
        .with_row_index("_row")
        .with_columns(_exchange_expr().alias("_exchange"), _excluded_reason_expr().alias("excluded_reason"))
    )
    priced = _priced_bars(bars, events, adjustment, calendar.prices_through)

    windows = _signal_windows(frame.get_column("signal_date").unique().to_list(), calendar, adjustment, horizons)
    frame = frame.join(windows, on="signal_date", how="left")
    entry_bars = priced.select(
        "symbol",
        pl.col("date").alias("_entry_bar_date"),
        pl.col("open").alias("_entry_open"),
        pl.col("raw_open").alias("entry_price_raw"),
        pl.col(REF_PREV_CLOSE).alias("_entry_ref_prev_close"),
        pl.col(REF_LIMIT_UP).alias("_entry_ref_limit_up"),
    )
    signal_bars = priced.select(
        "symbol", pl.col("date").alias("_signal_bar_date"), pl.col("raw_close").alias("_signal_close")
    )
    profile = limit_profile.select(
        "symbol",
        pl.col("limit_pct").cast(pl.Float64).alias("_limit_pct"),
        pl.col("is_etf_tick").cast(pl.Boolean).alias("_is_etf_tick"),
        *(
            (pl.col(c) if c in limit_profile.columns else pl.lit(None)).cast(pl.Date).alias(f"_{c}")
            for c in ("ipo_start", "ipo_end")
        ),
    ).unique(subset=["symbol"], keep="last")
    frame = (
        frame.join(entry_bars, left_on=["symbol", "entry_date"], right_on=["symbol", "_entry_bar_date"], how="left")
        .join(signal_bars, left_on=["symbol", "signal_date"], right_on=["symbol", "_signal_bar_date"], how="left")
        .join(profile, on="symbol", how="left")
        .with_columns(
            PriceLimitModel.polars_session_limit_pct(
                pl.col("_limit_pct"), pl.col("entry_date"), pl.col("_ipo_start"), pl.col("_ipo_end")
            ).alias("_limit_pct")
        )
        .drop("_ipo_start", "_ipo_end")
    )

    limit_up = _limit_up_expr()
    checked = (
        pl.lit(adjustment.available)
        & pl.col("_entry_covered")
        & pl.col("_limit_pct").is_not_null()
        & pl.col("entry_price_raw").is_not_null()
        & limit_up.is_not_null()
    ).fill_null(False)
    hit_limit = checked & (pl.col("entry_price_raw") >= limit_up - LIMIT_TOLERANCE)
    frame = frame.with_columns(
        pl.when(pl.col("entry_date").is_null()).then(pl.lit(PENDING_ENTRY))
        .when(~pl.col("_entry_read")).then(pl.lit(PRICES_INCOMPLETE))
        .when(~_by_exchange("_entry_known_")).then(pl.lit(DATA_INCOMPLETE))
        .when(pl.col("_entry_open").is_null()).then(pl.lit(NOT_FILLED_NO_TRADE))
        .when(hit_limit).then(pl.lit(NOT_FILLED_LIMIT_UP))
        .when(pl.col(f"exit_date_{horizons[-1]}").is_not_null()).then(pl.lit(COMPLETED))
        .otherwise(pl.lit(TRACKING))
        .alias("status"),
        pl.when(checked).then(pl.lit(LIMIT_CHECKED)).otherwise(pl.lit(LIMIT_UNKNOWN)).alias("limit_check"),
    )

    sessions = _session_table(calendar)
    status = pl.col("status")
    for k in horizons:
        exit_col = f"exit_date_{k}"
        frame = _join_exit_bars(frame, priced, k)
        frame = _join_sessions(frame, sessions, exit_col, f"_x{k}_")
        frame = _join_sessions(frame, sessions, f"_last_date_{k}", f"_l{k}_")
        stale = (pl.col(f"_last_date_{k}") < pl.col(exit_col)).fill_null(False)
        unknown_in_stale_span = stale & (
            (_by_exchange(f"_x{k}_unknown_") - _by_exchange(f"_l{k}_unknown_")) > 0
        ).fill_null(False)
        reason = pl.when(status.is_in(_NOT_FILLED)).then(status).when(
            pl.col(exit_col).is_null()
        ).then(pl.lit(PENDING))
        if not adjustment.available:
            reason = reason.when(pl.lit(True)).then(pl.lit(adjustment.reason))
        reason = (
            reason.when((status == PRICES_INCOMPLETE) | pl.col(f"_gap_{k}")).then(pl.lit(PRICES_INCOMPLETE))
            .when((status == DATA_INCOMPLETE) | pl.col(f"_split_{k}") | unknown_in_stale_span)
            .then(pl.lit(DATA_INCOMPLETE))
            .when(~pl.col("_entry_covered") | ~pl.col(f"_exit_covered_{k}"))
            .then(pl.lit(ADJUSTMENT_NOT_COVERED))
            .otherwise(pl.lit(None, dtype=pl.String))
        )
        frame = frame.with_columns(reason.alias(f"reason_{k}"))
        settled = pl.col(f"reason_{k}").is_null()
        frame = frame.with_columns(
            pl.when(settled).then(pl.col(f"_exit_close_{k}") / pl.col("_entry_open") - 1.0).alias(f"ret_{k}"),
            (settled & stale).alias(f"stale_{k}"),
            pl.when(settled & stale)
            .then(pl.col(f"_x{k}_idx") - pl.col(f"_l{k}_idx"))
            .otherwise(0)
            .alias(f"stale_days_{k}"),
        )

    frame = _absent_since(frame, bars, calendar)
    frame = frame.with_columns(pl.any_horizontal([pl.col(f"stale_{k}") for k in horizons]).alias("stale_exit"))
    return frame.sort("_row").select([c for c in frame.columns if not c.startswith("_")])


def _horizon_summary(kept: pl.DataFrame, k: int) -> dict[str, Any]:
    values = kept.get_column(f"ret_{k}").drop_nulls()
    samples = kept.filter(pl.col(f"ret_{k}").is_not_null())
    reasons = kept.get_column(f"reason_{k}")
    n = values.len()
    return {
        "sample_count": n,
        "sample_days": samples.get_column("signal_date").n_unique(),
        "mean_return": float(values.mean()) if n else None,
        "median_return": float(values.median()) if n else None,
        "positive_ratio": float((values > 0).sum()) / n if n else None,
        **{name: int((reasons == name).sum()) for name in _HORIZON_EXCLUSIONS},
        "stale_exit_count": int(kept.get_column(f"stale_{k}").sum()),
        "pending_count": int((reasons == PENDING).sum()),
    }


def _strategy_summary(strategy_id: str, rows: pl.DataFrame) -> dict[str, Any]:
    excluded = rows.get_column("excluded_reason")
    kept = rows.filter(excluded.is_null())
    statuses = kept.get_column("status")
    filled = kept.filter(pl.col("status").is_in((TRACKING, COMPLETED)))
    return {
        "strategy_id": strategy_id,
        "signal_count": rows.height,
        "signal_days": rows.get_column("signal_date").n_unique(),
        "reconstructed_count": int((rows.get_column("provenance") == "reconstructed").sum()),
        "scoring_unavailable_count": int((rows.get_column("calculation_status") == "scoring_unavailable").sum())
        if "calculation_status" in rows.columns else 0,
        "filled_count": filled.height,
        "not_filled_limit_up": int((statuses == NOT_FILLED_LIMIT_UP).sum()),
        "not_filled_no_trade": int((statuses == NOT_FILLED_NO_TRADE).sum()),
        "limit_check_unknown": int((filled.get_column("limit_check") == LIMIT_UNKNOWN).sum()),
        "pending_entry_count": int((statuses == PENDING_ENTRY).sum()),
        "tracking_count": int((statuses == TRACKING).sum()),
        "completed_count": int((statuses == COMPLETED).sum()),
        "entry_prices_incomplete_count": int((statuses == PRICES_INCOMPLETE).sum()),
        "entry_data_incomplete_count": int((statuses == DATA_INCOMPLETE).sum()),
        "absent_count": int(kept.get_column("absent_since").is_not_null().sum()),
        "excluded": {
            name: int((excluded == name).sum())
            for name in _EXCLUSIONS
        },
        "horizons": {str(k): _horizon_summary(kept, k) for k in HORIZONS},
        "history_degraded_count": int(kept.get_column("history_degraded").fill_null(False).sum())
        if "history_degraded" in kept.columns
        else 0,
    }


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def summarize(
    outcomes: pl.DataFrame, adjustment_state: AdjustmentState, prices_through: date | None,
    runs: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Per-strategy statistics; only picks with a null excluded_reason enter counts and horizons.

    The excluded counts plus the per-status counts sum to signal_count; positive_ratio counts returns > 0.
    """
    signal_dates = outcomes.get_column("signal_date")
    ids = set(outcomes.get_column("strategy_id").unique().to_list())
    issues = []
    if runs is not None and "calculation_status" in runs.columns:
        issues = [
            {"strategy_id": row["strategy_id"], "date": _iso(row["signal_date"]),
             "status": row["calculation_status"], "missing_fields": json.loads(row["missing_fields"] or "[]")}
            for row in runs.filter(pl.col("calculation_status") != "ok").iter_rows(named=True)
        ]
        ids.update(issue["strategy_id"] for issue in issues)
    strategies = []
    for sid in sorted(ids):
        stats = _strategy_summary(sid, outcomes.filter(pl.col("strategy_id") == sid))
        stats["input_unavailable_days"] = sum(issue["strategy_id"] == sid and issue["status"] == "input_unavailable" for issue in issues)
        strategies.append(stats)
    return {
        "method": {
            "entry": "next_session_open",
            "horizons": list(HORIZONS),
            "price_adjustment": "forward_adjusted_incl_dividends",
            "costs_included": False,
            "slippage_included": False,
            "exit_fill_simulated": False,
            "return_unit": "decimal",
        },
        "adjustment": {
            "status": adjustment_state.status,
            "reason": adjustment_state.reason,
            "covered_through": _iso(adjustment_state.covered_through),
        },
        "first_signal_date": _iso(signal_dates.min()) if not outcomes.is_empty() else None,
        "last_signal_date": _iso(signal_dates.max()) if not outcomes.is_empty() else None,
        "prices_through": _iso(prices_through),
        "strategies": strategies,
        "calculation_issues": issues,
    }
