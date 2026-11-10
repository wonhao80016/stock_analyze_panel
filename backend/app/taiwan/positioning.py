"""Whole-market percentile positioning of one Taiwan stock on nine metrics, plus ranks within its industry.
Kept in memory once per set of dataset dates, never written; no availability time, so never for backtests.
"""
# ruff: noqa: RUF001 -- official placeholders contain full-width punctuation.
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from app.taiwan.benchmarks import get_benchmark
from app.taiwan.corporate_actions import (
    TaiwanCorporateActionStore,
    factors_between,
    read_events_or_empty,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_reader import ENRICHED_DIR_NAME, read_latest_enriched
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.monthly_revenue_store import TaiwanMonthlyRevenueStore
from app.taiwan.providers.taiwan_values import MISSING_VALUES, parse_number, parse_taiwan_date
from app.taiwan.quarterly_financials_store import TaiwanQuarterlyFinancialStore
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe.industry_classification import resolve_industry_name
from app.taiwan.universe.models import MarketProfileBridge, UniverseType

logger = logging.getLogger(__name__)

EXCHANGES = ("TWSE", "TPEX")
VALUATION_PATHS = {"TWSE": "exchangeReport/BWIBBU_ALL", "TPEX": "tpex_mainboard_peratio_analysis"}
LONG_MOMENTUM_SESSIONS = 240
SHORT_MOMENTUM_SESSIONS = 20
CHIP_WINDOW_SESSIONS = 20
MIN_INDUSTRY_PEERS = 3
INDUSTRY_PEER_COUNT = 3
# Seen for loss-making or not-applicable ratios; a dash is a placeholder, never a number.
_DASH_PLACEHOLDERS = {"－", "—", "–"}
# resolve_industry_name's fallback for an unmapped numeric code; not a trustworthy group.
_UNRESOLVED_INDUSTRY_PREFIX = "未知產業"

MetricId = Literal[
    "pe", "pb", "dividend_yield", "roe", "operating_margin", "revenue_yoy",
    "return_60d", "return_240d", "foreign_flow_20d",
]
DimensionId = Literal["valuation", "profitability", "growth", "momentum", "chips"]
MetricStatus = Literal["ranked", "not_ranked", "unavailable"]
Reason = Literal[
    "loss_or_not_applicable", "not_general_industry", "insufficient_history", "incomplete_window",
    "non_positive_equity", "no_value", "not_archived", "dataset_unavailable", "insufficient_peers",
]
IndustryMetricId = Literal["return_20d", "foreign_flow_20d", "trust_flow_20d", "pe", "pb", "dividend_yield"]
INDUSTRY_METRICS: tuple[IndustryMetricId, ...] = (
    "return_20d", "foreign_flow_20d", "trust_flow_20d", "pe", "pb", "dividend_yield",
)

DIMENSIONS: tuple[tuple[DimensionId, tuple[MetricId, ...]], ...] = (
    ("valuation", ("pe", "pb", "dividend_yield")),
    ("profitability", ("roe", "operating_margin")),
    ("growth", ("revenue_yoy",)),
    ("momentum", ("return_60d", "return_240d")),
    ("chips", ("foreign_flow_20d",)),
)
LOWER_IS_BETTER: frozenset[str] = frozenset({"pe", "pb"})
UNITS: dict[str, Literal["times", "percent"]] = {"pe": "times", "pb": "times"}
BASES: dict[str, str] = {"roe": "ytd_annualized", "operating_margin": "ytd"}

# Grade keys from best (tier 3) to worst (tier 0); thresholds follow finlab's stock page.
VALUATION_GRADES = ("expensive", "pricey", "fair", "cheap")
METRIC_GRADES = ("lagging", "average", "good", "top")
DIMENSION_GRADES = ("weak", "neutral", "good", "strong")
VALUATION_THRESHOLDS = (80.0, 50.0, 20.0)
DIMENSION_THRESHOLDS = (80.0, 60.0, 40.0)


def decimal_to_percent(value: float | None) -> float | None:
    """0.0366 -> 3.66; the only conversion between decimal and percent units here."""
    return None if value is None else value * 100.0


def mid_rank_percentile(frame: pl.DataFrame, group: list[str] | None = None) -> pl.DataFrame:
    """Rows with a non-null value, plus ``percentile`` and ``compared`` per *group*.
    percentile = (below + half of equal, itself included) / compared * 100; ties share the average rank.
    """
    over = group or []
    ranked = frame.filter(pl.col("value").is_not_null())
    rank = pl.col("value").rank("average")
    count = pl.len()
    if over:
        rank, count = rank.over(over), count.over(over)
    return ranked.with_columns(
        ((rank - 0.5) / count * 100.0).alias("percentile"),
        count.cast(pl.Int64).alias("compared"),
    )


def metric_score(percentile: float, metric: str) -> float:
    return 100.0 - percentile if metric in LOWER_IS_BETTER else percentile


def _tier(score: float, thresholds: tuple[float, float, float]) -> int:
    return next((3 - i for i, bound in enumerate(thresholds) if score >= bound), 0)


def metric_grade(score: float, metric: str) -> tuple[str, int]:
    tier = _tier(score, VALUATION_THRESHOLDS)
    return (VALUATION_GRADES if metric in LOWER_IS_BETTER else METRIC_GRADES)[tier], tier


def dimension_grade(dimension: str, score: float, metric_tiers: list[int]) -> tuple[str, int]:
    """The dimension's grade, kept between the tiers of its best and worst ranked metric."""
    if dimension == "valuation":
        grades, tier = VALUATION_GRADES, _tier(score, VALUATION_THRESHOLDS)
    else:
        grades, tier = DIMENSION_GRADES, _tier(score, DIMENSION_THRESHOLDS)
    tier = min(max(tier, min(metric_tiers)), max(metric_tiers))
    return grades[tier], tier


def segments(score: float) -> int:
    return min(5, max(1, math.ceil(score / 20.0)))



class PositioningMetric(BaseModel):
    """``value`` is in ``unit``: percent means 12.5 is 12.5%; ``percentile`` ranks the raw value ascending."""

    id: MetricId
    status: MetricStatus
    reason: Reason | None = None
    value: float | None = None
    unit: Literal["times", "percent"]
    lower_is_better: bool
    percentile: float | None = None
    score: float | None = None
    grade: str | None = None
    tier: int | None = None
    compared: int | None = None
    as_of: str | None = None
    period: str | None = None
    basis: str | None = None


class PositioningDimension(BaseModel):
    id: DimensionId
    score: float | None = None
    grade: str | None = None
    tier: int | None = None
    segments: int | None = None
    metrics: list[PositioningMetric]


class IndustryRankMetric(BaseModel):
    """``rank`` 1 is the best end (lowest PE and P/B, highest otherwise); tied values share the best rank.
    ``percentile`` points the same way as ``rank``: for PE and P/B it is 100 minus the ascending one.
    """

    id: IndustryMetricId
    status: MetricStatus
    reason: Reason | None = None
    value: float | None = None
    unit: Literal["times", "percent"]
    lower_is_better: bool
    rank: int | None = None
    compared: int | None = None
    percentile: float | None = None
    as_of: str | None = None
    period_mixed: bool = False


class IndustryPeer(BaseModel):
    symbol: str
    name: str | None = None
    value: float


class IndustryRanking(BaseModel):
    applicable: bool
    not_applicable_reason: Literal["unclassified"] | None = None
    industry: str | None = None
    members: int | None = None
    metrics: list[IndustryRankMetric] = Field(default_factory=list)
    peers_metric: Literal["return_20d"] = "return_20d"
    peers: list[IndustryPeer] = Field(default_factory=list)


class TaiwanPositioningResponse(BaseModel):
    symbol: str
    exchange: str
    applicable: bool
    not_applicable_reason: Literal["index", "etf", "tdr"] | None = None
    usage_scope: Literal["current_reference"] = "current_reference"
    available_at: None = None
    universe_size: int | None = None
    valuation_dates: dict[str, str | None] = Field(default_factory=dict)
    valuation_period_mixed: bool = False
    dimensions: list[PositioningDimension] = Field(default_factory=list)
    industry: IndustryRanking | None = None


_TABLE_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "value": pl.Float64,
    "reason": pl.Utf8,
    "as_of": pl.Utf8,
    "period": pl.Utf8,
}


@dataclass(frozen=True)
class MetricTable:
    """One metric over the universe: a row per stock with a value or a not-ranked reason.
    ``unavailable`` is set when the whole dataset could not be read; ``rows`` then is empty.
    """

    rows: pl.DataFrame
    unavailable: Reason | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    universe_size: int
    valuation_dates: dict[str, str | None]
    metrics: dict[str, MetricTable]
    industries: dict[str, str | None] = field(default_factory=dict)
    industry_ranks: dict[str, pl.DataFrame] = field(default_factory=dict)


def _unavailable(reason: Reason = "dataset_unavailable") -> MetricTable:
    return MetricTable(pl.DataFrame(schema=_TABLE_SCHEMA), reason)


def _table(records: list[dict[str, Any]]) -> MetricTable:
    """Rows ranked within their own period, with ``compared`` = ranked stocks of that period."""
    rows = pl.DataFrame(records, schema=_TABLE_SCHEMA).with_columns(
        pl.col("period").fill_null("").alias("_group")
    )
    ranked = mid_rank_percentile(rows.filter(pl.col("reason").is_null()), ["_group"])
    counts = ranked.group_by("_group").agg(pl.len().cast(pl.Int64).alias("compared"))
    rows = (
        rows.join(ranked.select("symbol", "_group", "percentile"), on=["symbol", "_group"], how="left")
        .join(counts, on="_group", how="left")
        .with_columns(pl.col("compared").fill_null(0))
        .drop("_group")
    )
    return MetricTable(rows)


def _field(row: dict[str, Any], *names: str) -> str:
    """The first non-blank field; a numeric 0 stays "0" rather than reading as blank."""
    texts = ("" if row.get(name) is None else str(row[name]).strip() for name in names)
    return next((text for text in texts if text), "")


def valuation_number(raw: str) -> float | None:
    """A ratio from the valuation lists; blanks, dashes and malformed text are missing, not zero."""
    if raw in MISSING_VALUES or raw in _DASH_PLACEHOLDERS:
        return None
    try:
        return parse_number(raw)
    except ValueError:
        return None


def _trade_date(raw: str) -> str | None:
    try:
        return parse_taiwan_date(raw).isoformat() if raw else None
    except ValueError:
        return None


def valuation_tables(
    lists: dict[str, list[dict[str, Any]] | None], universe: set[str]
) -> tuple[dict[str, MetricTable], dict[str, str | None]]:
    """PE, P/B and yield (already percent) tables; one failed list makes all three unavailable.
    PE that is blank, a dash, zero or negative is a loss or not applicable and is not ranked.
    """
    dates: dict[str, str | None] = {}
    if any(not lists.get(exchange) for exchange in EXCHANGES):
        for exchange in EXCHANGES:
            dates[exchange] = None
        return {metric: _unavailable() for metric in ("pe", "pb", "dividend_yield")}, dates
    records: dict[str, list[dict[str, Any]]] = {"pe": [], "pb": [], "dividend_yield": []}
    seen: set[str] = set()
    for exchange in EXCHANGES:
        row_dates: list[str] = []
        for row in lists[exchange] or []:
            code = _field(row, "Code", "SecuritiesCompanyCode", "公司代號")
            symbol = f"{code}.{exchange}"
            if not code or symbol not in universe or symbol in seen:
                continue
            seen.add(symbol)
            as_of = _trade_date(_field(row, "Date", "日期"))
            if as_of:
                row_dates.append(as_of)
            pe = valuation_number(_field(row, "PEratio", "PriceEarningRatio"))
            pb = valuation_number(_field(row, "PBratio", "PriceBookRatio"))
            raw_dy = _field(row, "DividendYield", "YieldRatio")
            # TWSE leaves the yield blank for a stock that paid no dividend, where TPEx publishes 0.00.
            has_dy = "DividendYield" in row or "YieldRatio" in row
            dy = 0.0 if raw_dy == "" and has_dy else valuation_number(raw_dy)
            base = {"symbol": symbol, "as_of": as_of, "period": None}
            pe_ok = pe is not None and pe > 0
            records["pe"].append({**base, "value": pe if pe_ok else None,
                                  "reason": None if pe_ok else "loss_or_not_applicable"})
            pb_ok = pb is not None and pb > 0
            records["pb"].append({**base, "value": pb if pb_ok else None, "reason": None if pb_ok else "no_value"})
            records["dividend_yield"].append(
                {**base, "value": dy, "reason": None if dy is not None else "no_value"}
            )
        dates[exchange] = max(row_dates) if row_dates else None
    return {metric: _table(rows) for metric, rows in records.items()}, dates


def statement_tables(frame: pl.DataFrame, universe: set[str]) -> dict[str, MetricTable]:
    """ROE and operating margin per stored quarter, each stock compared only within its own quarter.
    ROE = YTD parent net income * 4 / quarters / closing parent equity; the YTD margin ranks ``ci`` only.
    """
    if frame.is_empty():
        return {"roe": _unavailable("not_archived"), "operating_margin": _unavailable("not_archived")}
    roe: list[dict[str, Any]] = []
    margin: list[dict[str, Any]] = []
    for row in frame.filter(pl.col("symbol").is_in(list(universe))).iter_rows(named=True):
        year, quarter = row["fiscal_year"], row["fiscal_quarter"]
        base = {"symbol": row["symbol"], "as_of": row["period_end"], "period": f"{year:04d}Q{quarter}"}
        income, equity = row["parent_net_income"], row["parent_equity"]
        if income is None or equity is None:
            roe.append({**base, "value": None, "reason": "no_value"})
        elif equity <= 0:
            roe.append({**base, "value": None, "reason": "non_positive_equity"})
        else:
            roe.append({**base, "value": decimal_to_percent(income * 4 / quarter / equity), "reason": None})
        operating, revenue = row["operating_income"], row["revenue"]
        if row["accounting_category"] != "ci":
            margin.append({**base, "value": None, "reason": "not_general_industry"})
        elif operating is None or revenue is None or revenue <= 0:
            margin.append({**base, "value": None, "reason": "no_value"})
        else:
            margin.append({**base, "value": decimal_to_percent(operating / revenue), "reason": None})
    return {"roe": _table(roe), "operating_margin": _table(margin)}


def revenue_table(frame: pl.DataFrame, universe: set[str]) -> MetricTable:
    """Single-month revenue YoY (already percent), grouped by each row's own revenue month."""
    if frame.is_empty():
        return _unavailable("not_archived")
    rows = [
        {
            "symbol": row["symbol"],
            "value": row["yoy_pct"],
            "reason": None if row["yoy_pct"] is not None else "no_value",
            "as_of": row["report_date"],
            "period": f"{row['year']:04d}-{row['month']:02d}",
        }
        for row in frame.filter(pl.col("symbol").is_in(list(universe))).iter_rows(named=True)
    ]
    return _table(rows)


def momentum_60d_table(universe: set[str]) -> MetricTable:
    """enriched momentum_60d, a forward-adjusted decimal, converted to percent."""
    latest = read_latest_enriched(["momentum_60d"])
    if latest is None:
        return _unavailable()
    as_of, frame = latest
    frame = frame.filter(pl.col("symbol").is_in(list(universe)))
    rows = [
        {
            "symbol": symbol,
            "value": decimal_to_percent(value),
            "reason": None if value is not None else "insufficient_history",
            "as_of": as_of.isoformat(),
            "period": None,
        }
        for symbol, value in zip(frame["symbol"].to_list(), frame["momentum_60d"].to_list(), strict=True)
    ]
    return _table(rows)


def trailing_return_table(
    universe: set[str], daily: TaiwanDailyStore, actions: TaiwanCorporateActionStore, sessions_back: int
) -> MetricTable:
    """Return over *sessions_back* trading sessions, the older close moved onto today's scale by corporate actions.
    A stock without a close that many sessions back is not ranked (listed too recently, or untraded that day).
    """
    sessions = daily.available_dates()
    if len(sessions) <= sessions_back:
        return _unavailable("insufficient_history")
    target, lookback = sessions[-1], sessions[-1 - sessions_back]
    bars = daily.read_dates([lookback, target], list(universe))
    now = bars.filter(pl.col("date") == target).select("symbol", pl.col("close").alias("close_now"))
    then = bars.filter(pl.col("date") == lookback).select("symbol", pl.col("close").alias("close_then"))
    frame = now.join(then, on="symbol", how="left")
    factors = factors_between(read_events_or_empty(actions, end=target), lookback, target)
    if not factors.is_empty():
        frame = frame.join(factors.select("symbol", "ex_factor"), on="symbol", how="left")
    else:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("ex_factor"))
    frame = frame.with_columns(
        (pl.col("close_then") / pl.col("ex_factor").fill_null(1.0)).alias("close_then")
    )
    rows = []
    for symbol, close_now, close_then in frame.select("symbol", "close_now", "close_then").iter_rows():
        if close_now is None or close_now <= 0:
            value, reason = None, "no_value"
        elif close_then is None or close_then <= 0:
            value, reason = None, "insufficient_history"
        else:
            value, reason = decimal_to_percent(close_now / close_then - 1.0), None
        rows.append({"symbol": symbol, "value": value, "reason": reason, "as_of": target.isoformat(), "period": None})
    return _table(rows)


def institutional_flow_table(
    universe: set[str], daily: TaiwanDailyStore, institutional: TaiwanInstitutionalStore, net_column: str
) -> MetricTable:
    """*net_column* net buy / volume over the 20 daily-store sessions ending on the newest flow date, in percent.
    A missing flow partition makes the metric unavailable; a stock without a daily bar in every session is not
    ranked, and a session the flow list omits for a traded stock counts as zero net.
    """
    end = institutional.latest_date()
    if end is None:
        return _unavailable()
    window = [d for d in daily.available_dates() if d <= end][-CHIP_WINDOW_SESSIONS:]
    stored = set(institutional.available_dates())
    if len(window) < CHIP_WINDOW_SESSIONS or window[-1] != end or any(d not in stored for d in window):
        return _unavailable("incomplete_window")
    symbols = list(universe)
    flows = (
        institutional.read_range(symbols, window[0], end)
        .filter(pl.col("date").is_in(window))
        .group_by("symbol")
        .agg(pl.col(net_column).sum().alias("net"))
    )
    volume = (
        daily.read_dates(window, symbols)
        .group_by("symbol")
        .agg(pl.col("volume").sum().alias("volume"), pl.len().alias("sessions"))
    )
    frame = volume.join(flows, on="symbol", how="full", coalesce=True)
    rows = []
    for symbol, total, sessions, net in frame.select("symbol", "volume", "sessions", "net").iter_rows():
        if sessions is None or sessions < CHIP_WINDOW_SESSIONS:
            value, reason = None, "incomplete_window"
        elif total is None or total <= 0:
            value, reason = None, "no_value"
        else:
            value, reason = decimal_to_percent((net or 0) / total), None
        rows.append({"symbol": symbol, "value": value, "reason": reason, "as_of": end.isoformat(), "period": None})
    return _table(rows)


def _guarded(name: str, build) -> MetricTable:
    try:
        return build()
    except Exception:
        logger.warning("taiwan positioning: %s unavailable", name, exc_info=True)
        return _unavailable()


def _guarded_many(names: tuple[str, ...], build) -> dict[str, MetricTable]:
    try:
        return build()
    except Exception:
        logger.warning("taiwan positioning: %s unavailable", ", ".join(names), exc_info=True)
        return {name: _unavailable() for name in names}


def industry_group(raw: str | None) -> str | None:
    """The official industry name; blank values and unresolved numeric codes count as unclassified."""
    name = resolve_industry_name(raw)
    return None if name is None or name.startswith(_UNRESOLVED_INDUSTRY_PREFIX) else name


def industry_rank_table(table: MetricTable, industries: dict[str, str | None], metric: str) -> pl.DataFrame:
    """Rankable rows of classified stocks with ``rank``, ``percentile`` and ``compared`` within their industry.
    ``rank`` is the competition rank from the best end; ``percentile`` is the mid-rank one in the same direction.
    """
    mapping = pl.DataFrame(
        {"symbol": list(industries), "industry": list(industries.values())},
        schema={"symbol": pl.Utf8, "industry": pl.Utf8},
    ).filter(pl.col("industry").is_not_null())
    rows = (
        table.rows.filter(pl.col("reason").is_null())
        .select("symbol", "value", "as_of")
        .join(mapping, on="symbol", how="inner")
    )
    lower_is_better = metric in LOWER_IS_BETTER
    best_first = pl.col("value") if lower_is_better else -pl.col("value")
    return mid_rank_percentile(rows, ["industry"]).with_columns(
        best_first.rank("min").over("industry").cast(pl.Int64).alias("rank"),
        (100.0 - pl.col("percentile") if lower_is_better else pl.col("percentile")).alias("percentile"),
    )


def _industry_metric(metric: str, snapshot: MarketSnapshot, symbol: str, industry: str) -> IndustryRankMetric:
    base = {"id": metric, "unit": UNITS.get(metric, "percent"), "lower_is_better": metric in LOWER_IS_BETTER}
    table = snapshot.metrics.get(metric)
    if table is None or table.unavailable is not None:
        reason = table.unavailable if table is not None else "dataset_unavailable"
        return IndustryRankMetric(**base, status="unavailable", reason=reason)
    peers = snapshot.industry_ranks[metric].filter(pl.col("industry") == industry)
    context: dict[str, Any] = {"compared": peers.height, "period_mixed": peers["as_of"].n_unique() > 1}
    own = table.rows.filter(pl.col("symbol") == symbol)
    if own.is_empty():
        return IndustryRankMetric(**base, **context, status="not_ranked", reason="no_value")
    entry = own.row(0, named=True)
    context.update(value=entry["value"], as_of=entry["as_of"])
    if entry["reason"] is not None:
        return IndustryRankMetric(**base, **context, status="not_ranked", reason=entry["reason"])
    if peers.height < MIN_INDUSTRY_PEERS:
        return IndustryRankMetric(**base, **context, status="not_ranked", reason="insufficient_peers")
    mine = peers.filter(pl.col("symbol") == symbol).row(0, named=True)
    return IndustryRankMetric(
        **base, **context, status="ranked", rank=mine["rank"], percentile=mine["percentile"]
    )


def top_industry_peers(snapshot: MarketSnapshot, symbol: str, industry: str) -> list[tuple[str, float]]:
    """Other stocks of the industry with the highest 20-day return; ties fall back to symbol order."""
    ranked = snapshot.industry_ranks.get("return_20d")
    if ranked is None:
        return []
    top = (
        ranked.filter((pl.col("industry") == industry) & (pl.col("symbol") != symbol))
        .sort(["rank", "symbol"])
        .head(INDUSTRY_PEER_COUNT)
    )
    return list(zip(top["symbol"].to_list(), top["value"].to_list(), strict=True))



def _metric(metric: str, table: MetricTable | None, symbol: str) -> PositioningMetric:
    """The stock's entry: ranked within its own period, which is the newest period it appears in."""
    base = {
        "id": metric,
        "unit": UNITS.get(metric, "percent"),
        "lower_is_better": metric in LOWER_IS_BETTER,
        "basis": BASES.get(metric),
    }
    if table is None or table.unavailable is not None:
        reason = table.unavailable if table is not None else "dataset_unavailable"
        return PositioningMetric(**base, status="unavailable", reason=reason)
    rows = table.rows
    if rows.is_empty():
        return PositioningMetric(**base, status="unavailable", reason="not_archived")
    own = rows.filter(pl.col("symbol") == symbol).sort("period", descending=True, nulls_last=True)
    if own.is_empty():
        newest = rows.sort("period", descending=True, nulls_last=True).row(0, named=True)
        return PositioningMetric(
            **base, status="not_ranked", reason="no_value", compared=newest["compared"],
            as_of=newest["as_of"] if newest["period"] is None else None, period=newest["period"],
        )
    entry = own.row(0, named=True)
    context = {
        "value": entry["value"], "compared": entry["compared"], "as_of": entry["as_of"], "period": entry["period"],
    }
    if entry["reason"] is not None:
        return PositioningMetric(**base, **context, status="not_ranked", reason=entry["reason"])
    percentile = entry["percentile"]
    score = metric_score(percentile, metric)
    grade, tier = metric_grade(score, metric)
    return PositioningMetric(
        **base, **context, status="ranked", percentile=percentile, score=score, grade=grade, tier=tier
    )


def _dimension(dimension: str, metrics: list[PositioningMetric]) -> PositioningDimension:
    scored = [m for m in metrics if m.score is not None and m.tier is not None]
    if not scored:
        return PositioningDimension(id=dimension, metrics=metrics)
    score = sum(m.score for m in scored) / len(scored)
    grade, tier = dimension_grade(dimension, score, [m.tier for m in scored])
    return PositioningDimension(
        id=dimension, score=score, grade=grade, tier=tier, segments=segments(score), metrics=metrics
    )


def positioning_for(symbol: str, snapshot: MarketSnapshot) -> list[PositioningDimension]:
    return [
        _dimension(dimension, [_metric(m, snapshot.metrics.get(m), symbol) for m in metric_ids])
        for dimension, metric_ids in DIMENSIONS
    ]


_snapshot_lock = threading.Lock()
_snapshot: tuple[tuple, MarketSnapshot] | None = None


def clear_snapshot_cache() -> None:
    global _snapshot
    with _snapshot_lock:
        _snapshot = None


def _enriched_signature() -> tuple[str, int] | None:
    from app.config import settings

    root = Path(settings.data_dir) / ENRICHED_DIR_NAME
    if not root.is_dir():
        return None
    newest = max((p for p in root.glob("date=*/part.parquet")), key=lambda p: p.parent.name, default=None)
    return None if newest is None else (str(newest), newest.stat().st_mtime_ns)


def _file_signature(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class TaiwanPositioningService:
    """Ranks one stock against today's cross-section; only the valuation lists may be fetched."""

    def __init__(
        self,
        fundamentals=None,
        security_master=None,
        quarterly_store: TaiwanQuarterlyFinancialStore | None = None,
        revenue_store: TaiwanMonthlyRevenueStore | None = None,
        daily_store: TaiwanDailyStore | None = None,
        institutional_store: TaiwanInstitutionalStore | None = None,
        corporate_actions: TaiwanCorporateActionStore | None = None,
    ) -> None:
        if fundamentals is None:
            from app.taiwan.providers.hybrid_provider import _official_fundamentals

            fundamentals = _official_fundamentals()
        if security_master is None:
            from app.taiwan.universe import get_security_master

            security_master = get_security_master()
        self.fundamentals = fundamentals
        self.security_master = security_master
        self.quarterly_store = quarterly_store or TaiwanQuarterlyFinancialStore()
        self.revenue_store = revenue_store or TaiwanMonthlyRevenueStore()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.institutional_store = institutional_store or TaiwanInstitutionalStore()
        self.corporate_actions = corporate_actions or TaiwanCorporateActionStore()

    def universe(self) -> set[str]:
        """Active listed and OTC ordinary stocks; ETFs and TDRs are not ranked."""
        symbols = self.security_master.get_universe(UniverseType.TAIWAN_STOCKS)
        return {
            s for s in symbols
            if (inst := self.security_master.get_instrument(s)) is not None and not MarketProfileBridge.is_tdr(inst)
        }

    def industries(self, universe: set[str]) -> dict[str, str | None]:
        return {
            s: industry_group(getattr(self.security_master.get_instrument(s), "industry", None)) for s in universe
        }

    def _valuation_lists(self) -> dict[str, list[dict[str, Any]] | None]:
        lists: dict[str, list[dict[str, Any]] | None] = {}
        for exchange in EXCHANGES:
            try:
                _, rows, _ = self.fundamentals.market_rows(exchange, VALUATION_PATHS[exchange])
                lists[exchange] = rows
            except Exception as exc:
                logger.warning("taiwan positioning: %s valuation list unavailable: %s", exchange, type(exc).__name__)
                lists[exchange] = None
        return lists

    def _key(
        self,
        universe: set[str],
        lists: dict[str, list[dict[str, Any]] | None],
        industries: dict[str, str | None],
    ) -> tuple:
        def valuation_marker(rows):
            if not rows:
                return None
            first = rows[0]
            return (len(rows), _field(first, "Date", "日期"), _field(first, "Code", "SecuritiesCompanyCode"))

        daily_latest = self.daily_store.latest_date()
        inst_latest = self.institutional_store.latest_date()
        return (
            hash(frozenset(universe)),
            hash(frozenset(industries.items())),
            tuple(valuation_marker(lists[e]) for e in EXCHANGES),
            _enriched_signature(),
            (daily_latest, self.daily_store.partition_mtime(daily_latest) if daily_latest else None),
            (inst_latest, tuple(self.institutional_store.provisional_dates([inst_latest])) if inst_latest else ()),
            _file_signature(self.corporate_actions.path),
            self.quarterly_store.signature(),
            self.revenue_store.signature(),
        )

    def build_snapshot(
        self,
        universe: set[str],
        lists: dict[str, list[dict[str, Any]] | None],
        industries: dict[str, str | None],
    ) -> MarketSnapshot:
        valuation, dates = valuation_tables(lists, universe)
        metrics: dict[str, MetricTable] = dict(valuation)
        metrics.update(_guarded_many(
            ("roe", "operating_margin"),
            lambda: statement_tables(self.quarterly_store.read_all(), universe),
        ))
        metrics["revenue_yoy"] = _guarded("revenue_yoy", lambda: revenue_table(self.revenue_store.read_all(), universe))
        metrics["return_60d"] = _guarded("return_60d", lambda: momentum_60d_table(universe))
        for metric, sessions_back in (("return_20d", SHORT_MOMENTUM_SESSIONS), ("return_240d", LONG_MOMENTUM_SESSIONS)):
            metrics[metric] = _guarded(metric, lambda n=sessions_back: trailing_return_table(
                universe, self.daily_store, self.corporate_actions, n
            ))
        for metric, column in (("foreign_flow_20d", "foreign_net"), ("trust_flow_20d", "investment_trust_net")):
            metrics[metric] = _guarded(metric, lambda c=column: institutional_flow_table(
                universe, self.daily_store, self.institutional_store, c
            ))
        industry_ranks = {
            metric: industry_rank_table(metrics[metric], industries, metric) for metric in INDUSTRY_METRICS
        }
        return MarketSnapshot(
            universe_size=len(universe), valuation_dates=dates, metrics=metrics,
            industries=industries, industry_ranks=industry_ranks,
        )

    def snapshot(self) -> MarketSnapshot:
        """The cross-section for the current dataset dates, built outside the lock and swapped in whole."""
        global _snapshot
        universe = self.universe()
        industries = self.industries(universe)
        lists = self._valuation_lists()
        key = self._key(universe, lists, industries)
        with _snapshot_lock:
            cached = _snapshot
        if cached is not None and cached[0] == key:
            return cached[1]
        built = self.build_snapshot(universe, lists, industries)
        with _snapshot_lock:
            _snapshot = (key, built)
        return built

    def get(self, raw_symbol: str) -> TaiwanPositioningResponse:
        symbol = parse_symbol(raw_symbol)
        base = {"symbol": symbol.canonical, "exchange": symbol.exchange.value}
        if get_benchmark(symbol.canonical) is not None:
            return TaiwanPositioningResponse(**base, applicable=False, not_applicable_reason="index")
        instrument = self.security_master.get_instrument(symbol.canonical)
        instrument_type = getattr(instrument, "instrument_type", None)
        if instrument_type not in {"stock", "etf"}:
            raise LookupError(f"supported security type not found for {symbol.canonical}")
        if instrument_type == "etf":
            return TaiwanPositioningResponse(**base, applicable=False, not_applicable_reason="etf")
        if MarketProfileBridge.is_tdr(instrument):
            return TaiwanPositioningResponse(**base, applicable=False, not_applicable_reason="tdr")
        snapshot = self.snapshot()
        return TaiwanPositioningResponse(
            **base,
            applicable=True,
            universe_size=snapshot.universe_size,
            valuation_dates=snapshot.valuation_dates,
            valuation_period_mixed=len(set(snapshot.valuation_dates.values())) > 1,
            dimensions=positioning_for(symbol.canonical, snapshot),
            industry=self._industry_ranking(symbol.canonical, instrument, snapshot),
        )

    def _industry_ranking(self, symbol: str, instrument: Any, snapshot: MarketSnapshot) -> IndustryRanking:
        if symbol in snapshot.industries:
            industry = snapshot.industries[symbol]
        else:
            industry = industry_group(getattr(instrument, "industry", None))
        if industry is None:
            return IndustryRanking(applicable=False, not_applicable_reason="unclassified")
        peers = [
            IndustryPeer(symbol=s, name=getattr(self.security_master.get_instrument(s), "name", None), value=v)
            for s, v in top_industry_peers(snapshot, symbol, industry)
        ]
        return IndustryRanking(
            applicable=True,
            industry=industry,
            members=sum(1 for name in snapshot.industries.values() if name == industry),
            metrics=[_industry_metric(m, snapshot, symbol, industry) for m in INDUSTRY_METRICS],
            peers=peers,
        )


def get_taiwan_positioning_service() -> TaiwanPositioningService:
    return TaiwanPositioningService()
