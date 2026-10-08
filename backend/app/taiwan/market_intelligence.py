"""Taiwan Market Intelligence Snapshot Service (Phase 7A).

Provides deterministic, traceable, structured market intelligence for a given trading date:
- Daily Breadth: Advances, Declines, Flats, Upper/Lower Limit Counts, Traded Counts, Turnover.
- Change Distribution: Target-date change buckets over rows with a usable previous close.
- Trend Participation: Closes at or above MA5/MA20/MA60, plus 60-session new highs and lows.
- Market Slices: Total Market, By Exchange (TWSE, TPEx), By Instrument (Stock, ETF).
- Institutional Flows: Market-wide Foreign, Investment Trust, and Dealer net buy amounts (TWD) from
  the exchanges' official summaries.
- Margin Balances: Market-wide aggregate of Margin Balance/Change and Short Balance/Change.
- Market Benchmarks: TAIEX and TPEx Index status (if available).
- Data Quality & Provenance: Target date resolution, per-dataset freshness, coverage counts.

STRICT CONSTRAINTS:
- 100% Deterministic: NO AI, NO sentiment score, NO mysterious ranking.
- Zero-HTTP at request time: Reads exclusively from local persisted Parquet stores and TradingCalendar.
- Historical Support: Exact-date snapshot with NO look-ahead.
- Authoritative Price Limits: Uses MarketProfileBridge/PriceLimitModel, never naive 10%.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Literal
from pydantic import BaseModel, Field
import polars as pl

from app.taiwan.corporate_actions import (
    TaiwanCorporateActionStore,
    forward_adjusted,
    read_events_or_empty,
    reference_prices,
)
from app.taiwan.daily_store import TaiwanDailyStore, listing_sessions
from app.taiwan.daily_update import (
    DatasetFreshnessStatus,
    resolve_target_latest_trading_date,
)
from app.taiwan.enrichment.market_institutional import (
    DEALER_HEDGE,
    DEALER_PROPRIETARY,
    FOREIGN_EXCL_DEALER,
    INVESTMENT_TRUST,
    TOTAL,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_institutional_store import TaiwanMarketInstitutionalStore
from app.taiwan.providers.taiwan_values import parse_taiwan_date
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)


def _listing_date(raw: object) -> date | None:
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        try:
            return parse_taiwan_date(value)
        except ValueError:
            return None


def _trading_on(row: dict, target: date) -> bool:
    listed = _listing_date(row.get("listing_date"))
    if listed is not None and listed > target:
        return False
    if row.get("listing_status") != "delisted":
        return True
    delisted = _listing_date(row.get("delisted_at"))
    return delisted is not None and delisted > target


def delisted_after(universe: pl.DataFrame, target: date) -> pl.DataFrame:
    """Delisted rows of *universe* that still traded on *target*: their delisted_at is after it."""
    if universe.is_empty() or not {"listing_status", "delisted_at"} <= set(universe.columns):
        return universe.clear()
    delisted = universe.filter(pl.col("listing_status") == "delisted")
    still_trading = [
        row["symbol"]
        for row in delisted.select(["symbol", "delisted_at"]).iter_rows(named=True)
        if (day := _listing_date(row["delisted_at"])) is not None and day > target
    ]
    return delisted.filter(pl.col("symbol").is_in(still_trading))


def listed_by(universe: pl.DataFrame, target: date) -> pl.DataFrame:
    """Rows of *universe* trading on *target*: listed on or before it and not delisted by then.

    An unknown listing date counts as listed; a delisted row counts only before its delisted_at date.
    """
    columns = [
        c for c in ("symbol", "listing_date", "listing_status", "delisted_at") if c in universe.columns
    ]
    if universe.is_empty() or columns == ["symbol"]:
        return universe
    eligible_symbols = [
        row["symbol"]
        for row in universe.select(columns).iter_rows(named=True)
        if _trading_on(row, target)
    ]
    return universe.filter(pl.col("symbol").is_in(eligible_symbols))


def daily_coverage_complete(universe: pl.DataFrame, snapshot_symbols: set[str]) -> bool:
    """Whether the target-date daily rows cover the listed *universe* on both exchanges.

    *universe* must already be limited by listed_by() to the target date; *snapshot_symbols*
    are the symbols holding a daily row on that date.
    """
    if universe.is_empty():
        return False
    supported = set(universe["symbol"].to_list())
    twse_symbols = set(universe.filter(pl.col("exchange") == "TWSE")["symbol"].to_list())
    tpex_symbols = set(universe.filter(pl.col("exchange") == "TPEX")["symbol"].to_list())
    matched_count = len(snapshot_symbols & supported)
    return (
        matched_count > 0
        and matched_count / len(supported) >= DAILY_COVERAGE_COMPLETE_RATIO
        and bool(twse_symbols)
        and bool(tpex_symbols)
        and bool(snapshot_symbols & twse_symbols)
        and bool(snapshot_symbols & tpex_symbols)
    )


def stored_partition_status(
    daily_store: TaiwanDailyStore, dates: list[date]
) -> Literal["complete", "provisional", "partial"]:
    """Worst state among the stored daily partitions of *dates*.

    A partition missing TWSE or TPEx rows is partial; one last written on its own trading
    day before the daily final cutoff is provisional. Dates without a partition are left to
    the caller.
    """
    if set(daily_store.incomplete_dates(dates)):
        return "partial"
    if set(daily_store.provisional_dates(dates)):
        return "provisional"
    return "complete"


# Band bounds are decimal change (0.05 = 5%), lower inclusive / upper exclusive.
CHANGE_DISTRIBUTION_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("<-5%", None, -0.05),
    ("-5~-3%", -0.05, -0.03),
    ("-3~-1%", -0.03, -0.01),
    ("-1~0%", -0.01, 0.0),
    ("0~1%", 0.0, 0.01),
    ("1~3%", 0.01, 0.03),
    ("3~5%", 0.03, 0.05),
    (">5%", 0.05, None),
)

TREND_MA_WINDOWS: tuple[int, ...] = (5, 20, 60)
TREND_EXTREME_WINDOW = 60
# Longest rolling window above decides how many sessions the trend read must cover.
TREND_LOOKBACK_SESSIONS = max(*TREND_MA_WINDOWS, TREND_EXTREME_WINDOW)

# The security master keeps terminated or halted symbols without daily bars, so 100% is unreachable.
DAILY_COVERAGE_COMPLETE_RATIO = 0.98


# ── Pydantic Response Contract Models ─────────────────────────


class MarketBreadthStats(BaseModel):
    """Deterministic price movement and trading activity statistics."""

    supported_count: int = 0
    snapshot_row_count: int = 0
    traded_count: int = 0
    advance_count: int = 0
    decline_count: int = 0
    flat_count: int = 0
    uncompared_count: int = 0
    upper_limit_count: int = 0
    lower_limit_count: int = 0
    turnover: float = 0.0  # in TWD (元)


class MarketExchangeBreakdown(BaseModel):
    """Market breadth separated by exchange."""

    twse: MarketBreadthStats
    tpex: MarketBreadthStats


class MarketInstrumentBreakdown(BaseModel):
    """Market breadth separated by instrument category."""

    stock: MarketBreadthStats
    etf: MarketBreadthStats


class ChangeDistributionBand(BaseModel):
    """One bucket of the target-date change distribution."""

    label: str
    count: int = 0
    pct: float = 0.0  # share of the bucketed rows, 0-100


class MarketTrendStats(BaseModel):
    """Trend participation on the target date: closes versus their own moving averages and 60-session extremes.

    Percentages use the count of rows whose indicator is defined as denominator, so a
    symbol without enough history lowers *_comparable_count instead of the ratio.
    """

    lookback_start_date: str | None = None
    lookback_sessions: int = 0
    ma5_comparable_count: int = 0
    above_ma5_count: int = 0
    above_ma5_pct: float | None = None
    ma20_comparable_count: int = 0
    above_ma20_count: int = 0
    above_ma20_pct: float | None = None
    ma60_comparable_count: int = 0
    above_ma60_count: int = 0
    above_ma60_pct: float | None = None
    extreme_60d_comparable_count: int = 0
    new_high_60d_count: int = 0
    new_low_60d_count: int = 0


class InstitutionalNetAmounts(BaseModel):
    """Institutional net buy amounts in TWD, as officially published.

    Foreign excludes foreign dealers (already counted under dealers); dealer is
    proprietary plus hedge; total is the official total row. has_discrepancy is set
    when a single official row's buy minus sell differs from its net, or when the
    official components do not add up to their official subtotals or total.
    """

    foreign_net_amount: int | None = None
    investment_trust_net_amount: int | None = None
    dealer_net_amount: int | None = None
    total_net_amount: int | None = None
    has_discrepancy: bool = False


class InstitutionalMarketAggregate(InstitutionalNetAmounts):
    """TWSE + TPEx institutional net buy amounts from the official summaries, split by exchange.

    Amounts are null when the day has no stored summary; they never fall back to share counts.
    """

    trade_date: str | None = None
    twse: InstitutionalNetAmounts | None = None
    tpex: InstitutionalNetAmounts | None = None
    status: DatasetFreshnessStatus = "unavailable"


class MarginMarketAggregate(BaseModel):
    """Aggregate market-wide margin trading and short selling balances (in shares)."""

    trade_date: str | None = None
    row_count: int = 0
    margin_balance: float | None = None  # in shares
    margin_balance_change: float | None = None  # in shares
    short_balance: float | None = None  # in shares
    short_balance_change: float | None = None  # in shares
    aggregate_short_margin_ratio: float | None = None  # short_balance / margin_balance * 100
    status: DatasetFreshnessStatus = "unavailable"


class IndexSnapshot(BaseModel):
    """Benchmark index point (TAIEX or TPEx Index)."""

    symbol: str
    name: str
    trade_date: str | None = None
    close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    status: str = "unavailable"


class MarketIndexesSnapshot(BaseModel):
    """Market benchmark indexes."""

    taiex: IndexSnapshot | None = None
    tpex_index: IndexSnapshot | None = None


class DatasetQualityMeta(BaseModel):
    """Freshness and provenance metadata for a single dataset."""

    dataset: str
    as_of: str | None = None
    status: Literal["current", "stale", "provisional", "unavailable"] = "unavailable"
    source: str = "local_store"


class DataQualityReport(BaseModel):
    """Data quality and provenance tracking."""

    target_trade_date: str
    previous_trade_date: str | None = None
    overall_status: Literal["complete", "provisional", "partial", "unavailable"] = "unavailable"
    daily: DatasetQualityMeta
    institutional: DatasetQualityMeta
    institutional_amount: DatasetQualityMeta
    margin: DatasetQualityMeta
    indexes: DatasetQualityMeta
    universe_supported_symbols: int = 0
    daily_snapshot_symbols: int = 0
    missing_symbols_count: int = 0
    twse_supported_symbols: int = 0
    twse_snapshot_symbols: int = 0
    tpex_supported_symbols: int = 0
    tpex_snapshot_symbols: int = 0


class TaiwanMarketIntelligenceSnapshot(BaseModel):
    """Unified deterministic Taiwan Market Intelligence Snapshot response contract."""

    trade_date: str
    generated_at: str
    market_totals: MarketBreadthStats
    by_exchange: MarketExchangeBreakdown
    by_instrument: MarketInstrumentBreakdown
    change_distribution: list[ChangeDistributionBand] = Field(default_factory=list)
    trend: MarketTrendStats = Field(default_factory=MarketTrendStats)
    institutional: InstitutionalMarketAggregate
    margin: MarginMarketAggregate
    indexes: MarketIndexesSnapshot
    data_quality: DataQualityReport


# ── Service Implementation ────────────────────────────────────


class TaiwanMarketIntelligenceService:
    """Computes deterministic market breadth, institutional & margin aggregates from local stores."""

    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        inst_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        market_inst_store: TaiwanMarketInstitutionalStore | None = None,
        corporate_actions: TaiwanCorporateActionStore | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.inst_store = inst_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()
        self.security_master = security_master or get_security_master()
        self.market_inst_store = market_inst_store or TaiwanMarketInstitutionalStore()
        self.corporate_actions = corporate_actions or TaiwanCorporateActionStore()

    def get_previous_trading_date(self, target_date: date) -> date | None:
        """Find the immediately preceding trading date using TaiwanTradingCalendar and store history."""
        available = self.daily_store.available_dates()
        prior_dates = [d for d in available if d < target_date]
        if prior_dates:
            return max(prior_dates)

        # Fall back to calendar iteration if daily store doesn't have prior dates
        cur = target_date - timedelta(days=1)
        while self.calendar.is_trading_day(cur) is False:
            cur -= timedelta(days=1)
        return cur

    def _institutional_amounts(self, target: date) -> tuple[InstitutionalMarketAggregate, date | None]:
        """Amounts for *target* and the latest stored day up to it; current only when *target* has both exchanges."""
        as_of = max((d for d in self.market_inst_store.available_dates() if d <= target), default=None)
        rows = self.market_inst_store.read_day(target)
        twse = _net_amounts(rows.filter(pl.col("exchange") == "TWSE"))
        tpex = _net_amounts(rows.filter(pl.col("exchange") == "TPEX"))
        if twse is None or tpex is None:
            return InstitutionalMarketAggregate(status="stale" if as_of else "unavailable"), as_of

        def combined(field: str) -> int:
            return getattr(twse, field) + getattr(tpex, field)

        return InstitutionalMarketAggregate(
            trade_date=str(target),
            foreign_net_amount=combined("foreign_net_amount"),
            investment_trust_net_amount=combined("investment_trust_net_amount"),
            dealer_net_amount=combined("dealer_net_amount"),
            total_net_amount=combined("total_net_amount"),
            has_discrepancy=twse.has_discrepancy or tpex.has_discrepancy,
            twse=twse,
            tpex=tpex,
            status="current",
        ), as_of

    def _with_reference_prices(self, joined: pl.DataFrame, prev_d: date | None, target: date) -> pl.DataFrame:
        """A corporate-action session is compared with the exchange reference price and its published limits."""
        events = reference_prices(read_events_or_empty(self.corporate_actions, end=target), prev_d, target)
        if events.is_empty():
            return joined
        return (
            joined.join(
                events.rename({"ref_price": "_ref_price", "limit_up": "_ref_limit_up", "limit_down": "_ref_limit_down"}),
                on="symbol",
                how="left",
            )
            .with_columns(pl.coalesce(pl.col("_ref_price"), pl.col("prev_close")).alias("prev_close"))
            .drop("_ref_price")
        )

    def _calc_breadth(self, df: pl.DataFrame) -> MarketBreadthStats:
        """Compute MarketBreadthStats from joined current & previous dataframe."""
        if df.is_empty():
            return MarketBreadthStats()

        snapshot_rows = len(df)
        traded = df.filter(pl.col("volume") > 0)
        traded_count = len(traded)
        turnover = float(df["amount"].sum() or 0.0)

        # Comparable with previous close
        comparable = df.filter(pl.col("prev_close").is_not_null())
        uncompared_count = snapshot_rows - len(comparable)

        advance_count = len(comparable.filter(pl.col("close") > pl.col("prev_close")))
        decline_count = len(comparable.filter(pl.col("close") < pl.col("prev_close")))
        flat_count = len(comparable.filter(pl.col("close") == pl.col("prev_close")))

        # Limit counts
        limit_ups = len(df.filter(pl.col("is_limit_up") == True))
        limit_downs = len(df.filter(pl.col("is_limit_down") == True))

        return MarketBreadthStats(
            supported_count=snapshot_rows,
            snapshot_row_count=snapshot_rows,
            traded_count=traded_count,
            advance_count=advance_count,
            decline_count=decline_count,
            flat_count=flat_count,
            uncompared_count=uncompared_count,
            upper_limit_count=limit_ups,
            lower_limit_count=limit_downs,
            turnover=turnover,
        )

    def _calc_change_distribution(self, df: pl.DataFrame) -> list[ChangeDistributionBand]:
        """Bucket close/prev_close changes of the target-date rows into CHANGE_DISTRIBUTION_BANDS."""
        if df.is_empty() or "prev_close" not in df.columns or "close" not in df.columns:
            return []

        changes = (
            df.with_columns(
                (
                    pl.col("close").cast(pl.Float64, strict=False)
                    / pl.col("prev_close").cast(pl.Float64, strict=False)
                    - 1.0
                ).alias("_change")
            )
            .filter(pl.col("_change").is_finite())
            .get_column("_change")
            .to_list()
        )
        total = len(changes)
        if total == 0:
            return []

        bands: list[ChangeDistributionBand] = []
        for label, low, high in CHANGE_DISTRIBUTION_BANDS:
            if low is None:
                count = sum(1 for v in changes if v < high)
            elif high is None:
                count = sum(1 for v in changes if v >= low)
            else:
                count = sum(1 for v in changes if low <= v < high)
            bands.append(
                ChangeDistributionBand(label=label, count=count, pct=round(count / total * 100.0, 2))
            )
        return bands

    def _calc_trend(
        self,
        history: pl.DataFrame,
        target: date,
        window_start: date | None,
        lookback_sessions: int,
    ) -> MarketTrendStats:
        """Compute MA participation and 60-session extremes for the target-date rows of *history*."""
        fields: dict[str, Any] = {
            "lookback_start_date": str(window_start) if window_start else None,
            "lookback_sessions": lookback_sessions,
        }
        if history.is_empty() or not {"symbol", "date", "close"}.issubset(history.columns):
            return MarketTrendStats(**fields)

        rolled = (
            history.with_columns(pl.col("close").cast(pl.Float64, strict=False))
            .sort(["symbol", "date"])
            .with_columns(
                [
                    pl.col("close")
                    .rolling_mean(w, min_samples=w)
                    .over("symbol")
                    .alias(f"ma{w}")
                    for w in TREND_MA_WINDOWS
                ]
                + [
                    pl.col("close")
                    .rolling_max(TREND_EXTREME_WINDOW, min_samples=TREND_EXTREME_WINDOW)
                    .over("symbol")
                    .alias("high_extreme"),
                    pl.col("close")
                    .rolling_min(TREND_EXTREME_WINDOW, min_samples=TREND_EXTREME_WINDOW)
                    .over("symbol")
                    .alias("low_extreme"),
                ]
            )
            .filter(pl.col("date") == target)
        )
        if rolled.is_empty():
            return MarketTrendStats(**fields)

        for w in TREND_MA_WINDOWS:
            comparable = rolled.filter(pl.col("close").is_finite() & pl.col(f"ma{w}").is_finite())
            comparable_count = len(comparable)
            above_count = len(comparable.filter(pl.col("close") >= pl.col(f"ma{w}")))
            fields[f"ma{w}_comparable_count"] = comparable_count
            fields[f"above_ma{w}_count"] = above_count
            fields[f"above_ma{w}_pct"] = (
                round(above_count / comparable_count * 100.0, 2) if comparable_count else None
            )

        extremes = rolled.filter(
            pl.col("close").is_finite()
            & pl.col("high_extreme").is_finite()
            & pl.col("low_extreme").is_finite()
        )
        fields["extreme_60d_comparable_count"] = len(extremes)
        fields["new_high_60d_count"] = len(extremes.filter(pl.col("close") >= pl.col("high_extreme")))
        fields["new_low_60d_count"] = len(extremes.filter(pl.col("close") <= pl.col("low_extreme")))
        return MarketTrendStats(**fields)

    def get_snapshot(self, target_date: date | None = None) -> TaiwanMarketIntelligenceSnapshot:
        """Build a deterministic Taiwan Market Intelligence Snapshot for target_date."""
        target = target_date or resolve_target_latest_trading_date(self.calendar)
        prev_d = self.get_previous_trading_date(target)

        # 1. Load Security Master supported universe
        universe_df = self.security_master.to_dataframe(supported_only=True)
        if not universe_df.is_empty():
            universe_df = universe_df.filter(pl.col("listing_status").is_in(["active", "delisted"]))
            universe_df = universe_df.filter(pl.col("instrument_type").is_in(["stock", "etf"]))
        universe_df = listed_by(universe_df, target)
        supported_symbols = universe_df["symbol"].to_list() if not universe_df.is_empty() else []
        supported_count = len(supported_symbols)

        # 2. Load DailyStore current date and previous date (NO look-ahead)
        curr_daily = self.daily_store.read_range(supported_symbols, target, target)
        prev_daily = self.daily_store.read_range(supported_symbols, prev_d, prev_d) if prev_d else pl.DataFrame()

        daily_available = self.daily_store.available_dates()
        daily_as_of = max((d for d in daily_available if d <= target), default=None)
        provisional_dates = self.daily_store.provisional_dates([target])
        is_daily_provisional = target in provisional_dates
        if curr_daily.is_empty():
            daily_status = "unavailable"
        elif is_daily_provisional:
            daily_status = "provisional"
        elif daily_as_of == target:
            daily_status = "current"
        else:
            daily_status = "stale"

        snapshot_symbols = set(curr_daily["symbol"].to_list()) if not curr_daily.is_empty() else set()
        matched_symbols_count = len(snapshot_symbols)
        missing_count = max(0, supported_count - matched_symbols_count)
        twse_symbols = (
            set(universe_df.filter(pl.col("exchange") == "TWSE")["symbol"].to_list())
            if not universe_df.is_empty()
            else set()
        )
        tpex_symbols = (
            set(universe_df.filter(pl.col("exchange") == "TPEX")["symbol"].to_list())
            if not universe_df.is_empty()
            else set()
        )
        twse_snapshot_count = len(snapshot_symbols & twse_symbols)
        tpex_snapshot_count = len(snapshot_symbols & tpex_symbols)

        # 3. Compute price limits and comparisons
        if not curr_daily.is_empty():
            # Join previous close
            if not prev_daily.is_empty():
                prev_sub = prev_daily.select([
                    pl.col("symbol"),
                    pl.col("close").alias("prev_close"),
                ])
                joined = curr_daily.join(prev_sub, on="symbol", how="left")
            else:
                joined = curr_daily.with_columns(pl.lit(None).cast(pl.Float64).alias("prev_close"))
            joined = self._with_reference_prices(joined, prev_d, target)

            # Join metadata
            meta_sub = universe_df.select(["symbol", "exchange", "instrument_type"])
            joined = joined.join(meta_sub, on="symbol", how="left")

            # Determine limit-up / limit-down per row
            limit_up_flags = []
            limit_down_flags = []
            sessions = listing_sessions()
            for r in joined.iter_rows(named=True):
                sym = r["symbol"]
                close = r.get("close")
                prev_close = r.get("prev_close")
                inst = self.security_master.get_instrument(sym)

                if inst is None or close is None or prev_close is None or prev_close <= 0:
                    limit_up_flags.append(False)
                    limit_down_flags.append(False)
                    continue

                try:
                    ipo_session = MarketProfileBridge.in_ipo_no_limit_window(inst, target, sessions)
                except ValueError:
                    ipo_session = False
                official_up, official_down = r.get("_ref_limit_up"), r.get("_ref_limit_down")
                if official_up is not None and official_down is not None and not ipo_session:
                    limit_up_flags.append(bool(close >= official_up))
                    limit_down_flags.append(bool(close <= official_down))
                    continue

                try:
                    limit_pct = MarketProfileBridge.get_price_limit_pct(inst, target, sessions)
                except ValueError:
                    limit_up_flags.append(False)
                    limit_down_flags.append(False)
                    continue

                if limit_pct is None:  # NO_LIMIT instruments (e.g. foreign ETF) never count as hit limit
                    limit_up_flags.append(False)
                    limit_down_flags.append(False)
                    continue

                upper, lower = MarketProfileBridge.calc_limits(prev_close, inst, target, sessions)
                is_up = bool(upper is not None and close >= upper)
                is_dn = bool(lower is not None and close <= lower)
                limit_up_flags.append(is_up)
                limit_down_flags.append(is_dn)

            joined = joined.with_columns([
                pl.Series("is_limit_up", limit_up_flags),
                pl.Series("is_limit_down", limit_down_flags),
            ])

            # Compute totals & breakdowns
            totals = self._calc_breadth(joined)
            totals.supported_count = supported_count

            twse_df = joined.filter(pl.col("exchange") == "TWSE")
            tpex_df = joined.filter(pl.col("exchange") == "TPEX")
            twse_breadth = self._calc_breadth(twse_df)
            twse_breadth.supported_count = len(universe_df.filter(pl.col("exchange") == "TWSE"))
            tpex_breadth = self._calc_breadth(tpex_df)
            tpex_breadth.supported_count = len(universe_df.filter(pl.col("exchange") == "TPEX"))

            stock_df = joined.filter(pl.col("instrument_type") == "stock")
            etf_df = joined.filter(pl.col("instrument_type") == "etf")
            stock_breadth = self._calc_breadth(stock_df)
            stock_breadth.supported_count = len(universe_df.filter(pl.col("instrument_type") == "stock"))
            etf_breadth = self._calc_breadth(etf_df)
            etf_breadth.supported_count = len(universe_df.filter(pl.col("instrument_type") == "etf"))

            change_distribution = self._calc_change_distribution(joined)

            # Second daily read: MA / extreme windows need history the target-date snapshot lacks.
            trend_window = sorted(d for d in daily_available if d <= target)[-TREND_LOOKBACK_SESSIONS:]
            if trend_window:
                trend = self._calc_trend(
                    forward_adjusted(
                        self.daily_store.read_range(supported_symbols, trend_window[0], target),
                        read_events_or_empty(self.corporate_actions, end=target),
                        target,
                    ),
                    target,
                    trend_window[0],
                    len(trend_window),
                )
            else:
                trend = MarketTrendStats()

        else:
            totals = MarketBreadthStats(supported_count=supported_count)
            twse_breadth = MarketBreadthStats()
            tpex_breadth = MarketBreadthStats()
            stock_breadth = MarketBreadthStats()
            etf_breadth = MarketBreadthStats()
            change_distribution = []
            trend = MarketTrendStats()

        by_exchange = MarketExchangeBreakdown(twse=twse_breadth, tpex=tpex_breadth)
        by_instrument = MarketInstrumentBreakdown(stock=stock_breadth, etf=etf_breadth)

        # 4. Institutional Aggregation (NO look-ahead)
        inst_available = self.inst_store.available_dates()
        inst_as_of = max((d for d in inst_available if d <= target), default=None)
        inst_status: DatasetFreshnessStatus = (
            "current" if inst_as_of == target
            else ("stale" if inst_as_of else "unavailable")
        )
        inst_agg, amount_as_of = self._institutional_amounts(target)

        # 5. Margin Aggregation (NO look-ahead)
        m_df = self.margin_store.read_range(None, target, target)
        m_available = self.margin_store.available_dates()
        m_as_of = max((d for d in m_available if d <= target), default=None)
        m_status: DatasetFreshnessStatus = (
            "current" if m_as_of == target
            else ("stale" if m_as_of else "unavailable")
        )

        if not m_df.is_empty():
            mb = float(m_df["margin_balance"].sum() or 0.0)
            mc = float(m_df["margin_change"].sum() or 0.0) if "margin_change" in m_df.columns else None
            sb = float(m_df["short_balance"].sum() or 0.0)
            sc = float(m_df["short_change"].sum() or 0.0) if "short_change" in m_df.columns else None
            # Compute aggregate ratio safely: sum(short) / sum(margin) * 100
            ratio = round((sb / mb * 100.0), 2) if mb > 0 else None

            margin_agg = MarginMarketAggregate(
                trade_date=str(target),
                row_count=len(m_df),
                margin_balance=mb,
                margin_balance_change=mc,
                short_balance=sb,
                short_balance_change=sc,
                aggregate_short_margin_ratio=ratio,
                status=m_status,
            )
        else:
            margin_agg = MarginMarketAggregate(
                trade_date=str(target) if m_as_of else None,
                row_count=0,
                status=m_status,
            )

        # 6. Indexes Integration (Pure offline / persisted fallback)
        # Note: In accordance with zero-HTTP acceptance rules, do not fetch network index data.
        indexes_snapshot = MarketIndexesSnapshot(
            taiex=IndexSnapshot(
                symbol="TAIEX",
                name="發行量加權股價指數",
                trade_date=str(target),
                status="unavailable",
            ),
            tpex_index=IndexSnapshot(
                symbol="TPEX_INDEX",
                name="櫃買指數",
                trade_date=str(target),
                status="unavailable",
            ),
        )

        coverage_complete = daily_coverage_complete(universe_df, snapshot_symbols)
        dataset_statuses = [daily_status, inst_status, m_status]
        if supported_count == 0 or matched_symbols_count == 0:
            overall_status = "unavailable"
        elif not coverage_complete:
            overall_status = "partial"
        elif daily_status == "provisional" and all(s == "current" for s in (inst_status, m_status)):
            overall_status = "provisional"
        elif all(s == "current" for s in dataset_statuses):
            overall_status = "complete"
        elif any(s in ("current", "stale", "provisional") for s in dataset_statuses):
            overall_status = "partial"
        else:
            overall_status = "unavailable"

        dq = DataQualityReport(
            target_trade_date=str(target),
            previous_trade_date=str(prev_d) if prev_d else None,
            overall_status=overall_status,
            daily=DatasetQualityMeta(
                dataset="daily",
                as_of=str(daily_as_of) if daily_as_of else None,
                status=daily_status,
                source="taiwan_daily_store",
            ),
            institutional=DatasetQualityMeta(
                dataset="institutional",
                as_of=str(inst_as_of) if inst_as_of else None,
                status=inst_status,
                source="taiwan_institutional_store",
            ),
            institutional_amount=DatasetQualityMeta(
                dataset="institutional_amount",
                as_of=str(amount_as_of) if amount_as_of else None,
                status=inst_agg.status,
                source="taiwan_market_institutional_store",
            ),
            margin=DatasetQualityMeta(
                dataset="margin",
                as_of=str(m_as_of) if m_as_of else None,
                status=m_status,
                source="taiwan_margin_store",
            ),
            indexes=DatasetQualityMeta(
                dataset="indexes",
                as_of=None,
                status="unavailable",
                source="taiwan_index_provider",
            ),
            universe_supported_symbols=supported_count,
            daily_snapshot_symbols=matched_symbols_count,
            missing_symbols_count=missing_count,
            twse_supported_symbols=len(twse_symbols),
            twse_snapshot_symbols=twse_snapshot_count,
            tpex_supported_symbols=len(tpex_symbols),
            tpex_snapshot_symbols=tpex_snapshot_count,
        )

        return TaiwanMarketIntelligenceSnapshot(
            trade_date=str(target),
            generated_at=taipei_now().isoformat(),
            market_totals=totals,
            by_exchange=by_exchange,
            by_instrument=by_instrument,
            change_distribution=change_distribution,
            trend=trend,
            institutional=inst_agg,
            margin=margin_agg,
            indexes=indexes_snapshot,
            data_quality=dq,
        )


def _net_amounts(rows: pl.DataFrame) -> InstitutionalNetAmounts | None:
    """Official net amounts for one exchange's summary rows, or None when a required category is absent."""
    by_category = dict(zip(rows["category"].to_list(), rows["net_amount"].to_list(), strict=True))
    needed = (FOREIGN_EXCL_DEALER, INVESTMENT_TRUST, DEALER_PROPRIETARY, DEALER_HEDGE, TOTAL)
    if any(by_category.get(c) is None for c in needed):
        return None
    return InstitutionalNetAmounts(
        foreign_net_amount=by_category[FOREIGN_EXCL_DEALER],
        investment_trust_net_amount=by_category[INVESTMENT_TRUST],
        dealer_net_amount=by_category[DEALER_PROPRIETARY] + by_category[DEALER_HEDGE],
        total_net_amount=by_category[TOTAL],
        has_discrepancy=bool(rows["has_discrepancy"].any()),
    )
