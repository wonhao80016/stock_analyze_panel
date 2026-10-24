"""Taiwan Market Screener Service (Phase 6B).

POST /api/taiwan/screener/run

Architecture:
  TaiwanSecurityMaster (Universe)
  -> TaiwanDailyStore.read_dates() of each exchange's newest partition among the last five
  -> Published enriched partition for indicators (MA5/10/20, RSI14, Momentum5d, VolRatio5d, change_pct)
  -> Batch MarketProfile price limits off the session's raw-scale reference price
  -> Batch Institutional & Margin joins
  -> Strongly typed Pydantic filters (whitelist)
  -> Deterministic Sort with symbol ASC tie-breaker
  -> Total count
  -> Pagination (slice)
  -> Strongly typed API response

NO request-time HTTP calls to external providers.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import polars as pl
from pydantic import BaseModel, Field, StringConstraints

from app.taiwan.corporate_actions import REF_LIMIT_DOWN, REF_LIMIT_UP, REF_PREV_CLOSE
from app.taiwan.daily_store import TaiwanDailyStore, listing_sessions
from app.taiwan.enriched_reader import read_enriched_partition
from app.taiwan.industry_intelligence import UNCLASSIFIED_INDUSTRY, unclassified_industry_expr
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_rules import PriceLimitModel
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.models import MarketProfileBridge
from app.taiwan.volume_patterns import VOLUME_PATTERN_COLUMNS

logger = logging.getLogger(__name__)

ExchangeFilter = Literal["TWSE", "TPEX", "ALL"]
InstrumentFilter = Literal["stock", "etf", "ALL"]
SortField = Literal[
    "symbol", "close", "change_pct", "volume", "amount",
    "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d",
    "foreign_net", "investment_trust_net", "dealer_net",
    "margin_balance_change", "short_balance", "short_margin_ratio",
]
SortDir = Literal["asc", "desc"]
# Published built-in signal columns are named signal_<id>.
SignalId = Annotated[str, StringConstraints(pattern=r"^signal_[a-z0-9_]+$", max_length=64)]

_INDICATOR_COLUMNS = ["change_pct", "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d"]
_LIMIT_REFERENCE_SOURCE_COLUMNS = ["prev_close", "close", "raw_close", REF_PREV_CLOSE, REF_LIMIT_UP, REF_LIMIT_DOWN]
_LIMIT_REFERENCE_COLUMNS = ["_limit_ref_price", "_official_limit_up", "_official_limit_down"]
_CHIP_EXCHANGES = ("TWSE", "TPEX")
_CHIP_SET = frozenset(_CHIP_EXCHANGES)
_DAILY_LOOKBACK = 5
_DAILY_MIN_SHARE = 0.5
_FLOW_COLUMNS = ["foreign_net", "investment_trust_net", "dealer_net"]
_INSTITUTIONAL_COLUMNS = [
    *_FLOW_COLUMNS, "foreign_net_5d", "investment_trust_net_5d", "institutional_date", "institutional_status",
]
_MARGIN_COLUMNS = [
    "margin_balance", "margin_balance_change", "short_balance", "short_margin_ratio", "margin_date", "margin_status",
]


def _enriched_part(as_of: str | None) -> Path:
    from app.config import settings

    return Path(settings.data_dir) / "kline_daily_enriched" / f"date={as_of}" / "part.parquet"


class TaiwanScreenerRequest(BaseModel):
    """Strongly typed Taiwan Screener Request Body."""

    exchange: ExchangeFilter = "ALL"
    instrument: InstrumentFilter = "ALL"
    industry: str | None = None  # None or specific industry name

    # Price & Volume filters
    price_min: float | None = Field(None, ge=0)
    price_max: float | None = Field(None, ge=0)
    change_pct_min: float | None = Field(None, ge=-1, le=1)  # 0.05 = 5%
    change_pct_max: float | None = Field(None, ge=-1, le=1)
    volume_min: float | None = Field(None, ge=0)  # in shares
    volume_max: float | None = Field(None, ge=0)
    amount_min: float | None = Field(None, ge=0)  # in TWD
    amount_max: float | None = Field(None, ge=0)

    # Technical Indicators
    rsi_14_min: float | None = Field(None, ge=0, le=100)
    rsi_14_max: float | None = Field(None, ge=0, le=100)
    momentum_5d_min: float | None = None
    momentum_5d_max: float | None = None
    vol_ratio_5d_min: float | None = Field(None, ge=0)
    vol_ratio_5d_max: float | None = Field(None, ge=0)
    above_ma5: bool | None = None
    above_ma20: bool | None = None

    # Institutional (in shares)
    foreign_net_min: float | None = None
    foreign_net_max: float | None = None
    investment_trust_net_min: float | None = None
    investment_trust_net_max: float | None = None
    dealer_net_min: float | None = None
    dealer_net_max: float | None = None

    # Built-in signals: a row must carry every requested signal.
    signals: list[SignalId] | None = None

    # Margin & Short
    margin_balance_change_min: float | None = None
    margin_balance_change_max: float | None = None
    short_balance_min: float | None = Field(None, ge=0)
    short_balance_max: float | None = Field(None, ge=0)
    short_margin_ratio_min: float | None = Field(None, ge=0)  # 10.0 = 10%
    short_margin_ratio_max: float | None = Field(None, ge=0)

    # Price Limit proximity
    near_upper_limit: bool | None = None  # distance_to_upper <= 0.03
    near_lower_limit: bool | None = None  # distance_to_lower <= 0.03
    distance_to_upper_limit_max: float | None = Field(None, ge=0)
    distance_to_lower_limit_max: float | None = Field(None, ge=0)

    # Pagination & Sorting
    sort_by: SortField = "symbol"
    sort_order: SortDir = "asc"
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)


class TaiwanScreenerRunRequest(TaiwanScreenerRequest):
    """Run-endpoint request; fields here stay out of the NL translation whitelist."""

    # Volume pattern ids (VOLUME_PATTERN_COLUMNS): a row must carry every requested one.
    volume_patterns: list[str] | None = None


class ScreenerResultItem(BaseModel):
    """Single instrument screening result."""

    symbol: str
    name: str
    exchange: str
    instrument_type: str
    industry: str | None = None
    # Built-in signal ids that fired on the quote date.
    signals: list[str] = []
    volume_patterns: list[str] = []

    # Quotes & Volumes
    close: float | None = None
    change_pct: float | None = None  # decimal: 0.05 = 5%
    volume: float | None = None  # shares
    amount: float | None = None  # TWD
    quote_date: str | None = None

    # Price Limits
    price_limit_pct: float | None = None
    is_no_limit: bool = False
    limit_up: float | None = None
    limit_down: float | None = None
    distance_to_upper_limit: float | None = None
    distance_to_lower_limit: float | None = None

    # Indicators
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    rsi_14: float | None = None
    momentum_5d: float | None = None
    vol_ratio_5d: float | None = None

    # Institutional (shares)
    foreign_net: float | None = None
    foreign_net_5d: float | None = None
    investment_trust_net: float | None = None
    investment_trust_net_5d: float | None = None
    dealer_net: float | None = None
    institutional_date: str | None = None
    institutional_status: str = "unavailable"

    # Margin (shares & %)
    margin_balance: float | None = None
    margin_balance_change: float | None = None
    short_balance: float | None = None
    short_margin_ratio: float | None = None  # 10.0 = 10%
    margin_date: str | None = None
    margin_status: str = "unavailable"


class DataDatesInfo(BaseModel):
    daily_as_of: str | None = None
    # Universe symbols of a served exchange with no row in that exchange's daily partition.
    daily_unlisted_count: int = 0
    institutional_as_of: str | None = None
    margin_as_of: str | None = None


class TaiwanScreenerResponse(BaseModel):
    items: list[ScreenerResultItem]
    total: int
    page: int
    page_size: int
    sort_by: str
    sort_order: str
    data_dates: DataDatesInfo
    degraded_sections: list[str] = []


class TaiwanScreenerService:
    """Production Taiwan Market Screener Service (Batch & Local)."""

    def __init__(
        self,
        security_master: TaiwanSecurityMaster | None = None,
        daily_store: TaiwanDailyStore | None = None,
        institutional_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
    ) -> None:
        self.security_master = security_master or get_security_master()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.institutional_store = institutional_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()

    def run(self, req: TaiwanScreenerRequest) -> TaiwanScreenerResponse:
        # Step 1: Universe from TaiwanSecurityMaster
        universe_df = self._get_universe(req.exchange, req.instrument)
        if universe_df.is_empty():
            return TaiwanScreenerResponse(
                items=[], total=0, page=req.page, page_size=req.page_size,
                sort_by=req.sort_by, sort_order=req.sort_order,
                data_dates=DataDatesInfo(),
            )

        valid_symbols = universe_df["symbol"].to_list()

        latest_daily, daily_degraded, unlisted = self._latest_daily(
            valid_symbols, self._exchange_scope(universe_df)
        )
        if latest_daily.is_empty():
            return TaiwanScreenerResponse(
                items=[], total=0, page=req.page, page_size=req.page_size,
                sort_by=req.sort_by, sort_order=req.sort_order,
                data_dates=DataDatesInfo(), degraded_sections=daily_degraded,
            )

        daily_as_of = str(latest_daily["date"].max()) if not latest_daily.is_empty() else None

        # Indicators come from the published enriched partition, never recomputed here.
        df_indicators = read_enriched_partition(daily_as_of, _INDICATOR_COLUMNS, valid_symbols)

        # Step 4: Join Universe + Latest Daily + Indicators
        combined = universe_df.join(latest_daily, on="symbol", how="inner")
        if df_indicators is not None:
            combined = combined.join(df_indicators, on="symbol", how="left")
        else:
            combined = self._add_null_cols(combined, _INDICATOR_COLUMNS)

        combined, limit_refs_published = self._join_limit_reference(combined, daily_as_of, valid_symbols)

        # Built-in signals, as published by TaiwanEnrichedRefreshService.
        combined = self._join_signals(combined, daily_as_of)
        patterns_published = all(c in combined.columns for c in VOLUME_PATTERN_COLUMNS)

        # Step 5: MarketProfile Price Limits & Distance Calculation
        combined = self._enrich_price_limits(combined)

        # Step 6: Batch Join Institutional & Margin
        combined, inst_date, margin_date, degraded = self._join_institutional_margin(
            combined, valid_symbols, daily_as_of
        )
        degraded = [*daily_degraded, *degraded]
        if df_indicators is None:
            degraded = ["indicators", *degraded]
        if not patterns_published:
            degraded.append("volume_patterns")
        if not limit_refs_published and _enriched_part(daily_as_of).exists():
            degraded.append("price_limits")

        # Step 7: Apply Strongly Typed Filters
        filtered = self._apply_filters(combined, req)

        # Step 8: Total count (before pagination)
        total = filtered.height

        # Step 9: Deterministic Sort (with symbol ASC tie-breaker)
        sorted_df = self._apply_sort(filtered, req.sort_by, req.sort_order)

        # Step 10: Pagination
        offset = (req.page - 1) * req.page_size
        paged_df = sorted_df.slice(offset, req.page_size)

        # Step 11: Serialize items
        items = self._build_items(paged_df)

        return TaiwanScreenerResponse(
            items=items,
            total=total,
            page=req.page,
            page_size=req.page_size,
            sort_by=req.sort_by,
            sort_order=req.sort_order,
            data_dates=DataDatesInfo(
                daily_as_of=daily_as_of,
                institutional_as_of=inst_date,
                margin_as_of=margin_date,
                daily_unlisted_count=unlisted,
            ),
            degraded_sections=degraded,
        )

    def _get_universe(self, exchange: ExchangeFilter, instrument: InstrumentFilter) -> pl.DataFrame:
        """Fetch strictly supported symbols from TaiwanSecurityMaster as a Polars DataFrame."""
        df = self.security_master.to_dataframe(supported_only=True)
        if df.is_empty():
            return df

        # Filter by active status and supported instrument types (stock & etf only)
        df = df.filter(pl.col("listing_status") == "active")
        df = df.filter(pl.col("instrument_type").is_in(["stock", "etf"]))

        if exchange == "TWSE":
            df = df.filter(pl.col("exchange") == "TWSE")
        elif exchange == "TPEX":
            df = df.filter(pl.col("exchange") == "TPEX")

        if instrument == "stock":
            df = df.filter(pl.col("instrument_type") == "stock")
        elif instrument == "etf":
            df = df.filter(pl.col("instrument_type") == "etf")

        return df.select(["symbol", "name", "exchange", "instrument_type", "industry"]).unique(subset=["symbol"])

    def _exchange_rows(self, d: date, cache: dict[date, dict[str, int]]) -> dict[str, int]:
        """Rows per exchange of the partition for *d*; an unreadable partition counts as empty."""
        if d not in cache:
            suffixes = [f".{ex}" for ex in _CHIP_EXCHANGES]
            try:
                found = self.daily_store.symbol_suffix_counts(d, suffixes)
            except Exception as exc:
                logger.warning("daily partition %s unreadable in screener: %s", d, exc)
                found = {}
            cache[d] = {ex: found.get(f".{ex}", 0) for ex in _CHIP_EXCHANGES}
        return cache[d]

    def _latest_daily(
        self, symbols: list[str], scope: set[str]
    ) -> tuple[pl.DataFrame, list[str], int]:
        """Each exchange's rows from the newest of the last _DAILY_LOOKBACK partitions that holds it.

        A partition can hold one exchange alone, or a few of its symbols after a partial refresh.
        It holds an exchange when its rows of that exchange reach _DAILY_MIN_SHARE of the largest
        count among the _DAILY_LOOKBACK stored partitions before it; with no earlier rows, any row
        counts. Flags name exchanges served from an older partition (daily_stale_*) or found in
        none (daily_missing_*). Symbols of a served exchange absent from its partition are not
        listed; the int counts them.
        """
        stored = self.daily_store.available_dates()[-(2 * _DAILY_LOOKBACK):]
        if not stored:
            return self.daily_store.read_dates([], symbols), [], 0
        cache: dict[date, dict[str, int]] = {}
        frames: list[pl.DataFrame] = []
        flags: list[str] = []
        unlisted = 0
        for exchange in _CHIP_EXCHANGES:
            if exchange not in scope:
                continue
            day = None
            for i in range(len(stored) - 1, max(len(stored) - 1 - _DAILY_LOOKBACK, -1), -1):
                rows = self._exchange_rows(stored[i], cache)[exchange]
                earlier = stored[max(i - _DAILY_LOOKBACK, 0):i]
                floor = _DAILY_MIN_SHARE * max((self._exchange_rows(d, cache)[exchange] for d in earlier), default=0)
                if rows and rows >= floor:
                    day = stored[i]
                    break
            if day is None:
                flags.append(f"daily_missing_{exchange.lower()}")
                continue
            if day != stored[-1]:
                flags.append(f"daily_stale_{exchange.lower()}")
            wanted = [s for s in symbols if s.endswith(f".{exchange}")]
            part = self.daily_store.read_dates([day], wanted)
            unlisted += len(set(wanted)) - part.height
            frames.append(part)
        if not frames:
            return self.daily_store.read_dates([], symbols), flags, 0
        return pl.concat(frames, how="diagonal_relaxed").sort(["symbol", "date"]), flags, unlisted

    @staticmethod
    def _join_limit_reference(
        df: pl.DataFrame, as_of: str | None, symbols: list[str]
    ) -> tuple[pl.DataFrame, bool]:
        """Attach the raw-scale limit reference of the quote session; False with null columns when unpublished.

        The official reference wins; otherwise the previous close is moved onto the raw scale,
        matching TaiwanEnrichedRefreshService._limit_signals. The quote close is never a fallback.
        """
        refs = read_enriched_partition(as_of, _LIMIT_REFERENCE_SOURCE_COLUMNS, symbols)
        if refs is None:
            nulls = [pl.lit(None, dtype=pl.Float64).alias(c) for c in _LIMIT_REFERENCE_COLUMNS]
            return df.with_columns(nulls), False
        adj_close = pl.col("close").cast(pl.Float64)
        previous_raw_close = pl.col("prev_close") * pl.when(adj_close > 0).then(pl.col("raw_close") / adj_close)
        refs = refs.select(
            "symbol",
            pl.coalesce(pl.col(REF_PREV_CLOSE), previous_raw_close).cast(pl.Float64).alias("_limit_ref_price"),
            pl.col(REF_LIMIT_UP).cast(pl.Float64).alias("_official_limit_up"),
            pl.col(REF_LIMIT_DOWN).cast(pl.Float64).alias("_official_limit_down"),
        )
        return df.join(refs, on="symbol", how="left"), True

    def _enrich_price_limits(self, df: pl.DataFrame) -> pl.DataFrame:
        """Enrich with tick-size aware price limits and distance metrics."""
        sessions = listing_sessions(self.daily_store)
        rows = []
        for r in df.iter_rows(named=True):
            sym = r["symbol"]
            close = r.get("close")
            inst = self.security_master.get_instrument(sym)

            if inst is None or close is None:
                rows.append({
                    **r,
                    "price_limit_pct": None,
                    "is_no_limit": False,
                    "limit_up": None,
                    "limit_down": None,
                    "distance_to_upper_limit": None,
                    "distance_to_lower_limit": None,
                })
                continue

            try:
                limit_pct = MarketProfileBridge.get_price_limit_pct(inst, r.get("date"), sessions)
            except ValueError as e:
                logger.debug("Unconfirmed regulatory profile for %s: %s", inst.symbol, e)
                # Unconfirmed profile: cannot verify regulatory limit safely.
                # Must set price limit fields to None and NOT match near-limit filters.
                rows.append({
                    **r,
                    "price_limit_pct": None,
                    "is_no_limit": False,
                    "limit_up": None,
                    "limit_down": None,
                    "distance_to_upper_limit": None,
                    "distance_to_lower_limit": None,
                })
                continue

            is_no_limit = limit_pct is None

            if is_no_limit or limit_pct is None:
                rows.append({
                    **r,
                    "price_limit_pct": None,
                    "is_no_limit": True,
                    "limit_up": None,
                    "limit_down": None,
                    "distance_to_upper_limit": None,
                    "distance_to_lower_limit": None,
                })
                continue

            official_up, official_down = r.get("_official_limit_up"), r.get("_official_limit_down")
            ref_price = r.get("_limit_ref_price")
            if official_up is not None and official_down is not None:
                upper, lower = official_up, official_down
            elif ref_price is not None and ref_price > 0:
                upper, lower = PriceLimitModel.calc_limits_for_pct(
                    ref_price,
                    limit_pct,
                    MarketProfileBridge.get_tick_size_class(inst),
                    trade_date=r.get("date"),
                )
            else:
                upper, lower = None, None
            dist_up = (upper - close) / close if (upper and close > 0) else None
            dist_dn = (close - lower) / close if (lower and close > 0) else None

            rows.append({
                **r,
                "price_limit_pct": limit_pct,
                "is_no_limit": False,
                "limit_up": upper,
                "limit_down": lower,
                "distance_to_upper_limit": dist_up,
                "distance_to_lower_limit": dist_dn,
            })

        # Nullable boolean columns can be all-null in the first rows; infer from every row.
        return pl.DataFrame(rows, infer_schema_length=None).drop(_LIMIT_REFERENCE_COLUMNS, strict=False)

    @staticmethod
    def _chip_date(store: Any, as_of: str | None) -> tuple[date | None, list[date]]:
        """The store's latest date not after *as_of*, and its stored dates up to it."""
        dates = sorted(store.available_dates())
        if as_of:
            cap = date.fromisoformat(as_of)
            dates = [d for d in dates if d <= cap]
        return (dates[-1] if dates else None), dates

    @staticmethod
    def _with_exchange(df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(pl.col("symbol").str.split(".").list.last().alias("_exchange"))

    @staticmethod
    def _missing_exchanges(present: set[str], section: str, scope: set[str]) -> list[str]:
        """Flags for the exchanges in *scope* absent from *present*; *section* already names the dataset."""
        return [f"{section}_missing_{ex.lower()}" for ex in _CHIP_EXCHANGES if ex in scope and ex not in present]

    @staticmethod
    def _exchange_scope(df: pl.DataFrame) -> set[str]:
        return {str(s).rsplit(".", 1)[-1] for s in df["symbol"].to_list()}

    def _join_institutional(
        self, df: pl.DataFrame, as_of: str | None
    ) -> tuple[pl.DataFrame, str | None, list[str]]:
        """Institutional flows of the market-wide chip date, following T86.

        A partition can hold one exchange alone. On a date holding the symbol's exchange a
        missing row is zero; without that exchange the flow is unknown. A 5-session sum is
        null when the store has fewer than five dates or any of them lacks the exchange.
        """
        target, dates = self._chip_date(self.institutional_store, as_of)
        if target is None:
            return self._add_null_cols(df, _INSTITUTIONAL_COLUMNS), None, []
        window = dates[-5:]
        stored = self._with_exchange(self.institutional_store.read_range(None, window[0], target))
        if stored.is_empty():
            return self._add_null_cols(df, _INSTITUTIONAL_COLUMNS), None, []
        exchanges_by_date = {
            d: set(g["_exchange"].to_list())
            for (d,), g in stored.select("date", "_exchange").unique().group_by("date")
        }
        on_target = exchanges_by_date.get(target, set())
        full_window = {
            ex for ex in _CHIP_EXCHANGES
            if len(window) == 5 and all(ex in exchanges_by_date.get(d, set()) for d in window)
        }

        flows = [pl.col(c).cast(pl.Float64, strict=False) for c in _FLOW_COLUMNS]
        rows = stored.filter(pl.col("symbol").is_in(df["symbol"].to_list()))
        day = rows.filter(pl.col("date") == target).select(
            "symbol", *flows, pl.col("status").alias("institutional_status"),
        )
        sums = rows.group_by("symbol").agg(
            pl.col("foreign_net").cast(pl.Float64).sum().alias("foreign_net_5d"),
            pl.col("investment_trust_net").cast(pl.Float64).sum().alias("investment_trust_net_5d"),
            pl.col("foreign_net").null_count().alias("_null_foreign"),
            pl.col("investment_trust_net").null_count().alias("_null_trust"),
        )
        joined = (
            self._with_exchange(df.drop(_INSTITUTIONAL_COLUMNS, strict=False))
            .join(day, on="symbol", how="left")
            .join(sums, on="symbol", how="left")
        )
        has_table = pl.col("_exchange").is_in(list(on_target))
        has_window = pl.col("_exchange").is_in(list(full_window))
        joined = joined.with_columns(
            *[pl.when(has_table).then(pl.col(c).fill_null(0.0)).alias(c) for c in _FLOW_COLUMNS],
            pl.when(has_table).then(pl.lit(str(target))).alias("institutional_date"),
            pl.when(has_table).then(pl.col("institutional_status").fill_null("available"))
            .alias("institutional_status"),
            pl.when(has_window & has_table & (pl.col("_null_foreign").fill_null(0) == 0))
            .then(pl.col("foreign_net_5d").fill_null(0.0)).alias("foreign_net_5d"),
            pl.when(has_window & has_table & (pl.col("_null_trust").fill_null(0) == 0))
            .then(pl.col("investment_trust_net_5d").fill_null(0.0)).alias("investment_trust_net_5d"),
        ).drop("_exchange", "_null_foreign", "_null_trust")
        scope = self._exchange_scope(df)
        missing = self._missing_exchanges(on_target, "institutional", scope)
        if len(window) == 5:
            missing += self._missing_exchanges(full_window | (_CHIP_SET - on_target), "institutional_window", scope)
        return joined, str(target), missing

    def _join_margin(self, df: pl.DataFrame, as_of: str | None) -> tuple[pl.DataFrame, str | None, list[str]]:
        """Margin rows of the market-wide chip date; a symbol absent from the table stays null."""
        target, _ = self._chip_date(self.margin_store, as_of)
        margin_df = self.margin_store.read_range(None, target, target) if target is not None else pl.DataFrame()
        if margin_df.is_empty():
            return self._add_null_cols(df, _MARGIN_COLUMNS), None, []
        present = set(self._with_exchange(margin_df)["_exchange"].to_list())
        margin_join = margin_df.filter(pl.col("symbol").is_in(df["symbol"].to_list())).select([
            pl.col("symbol"),
            pl.col("margin_balance").cast(pl.Float64, strict=False).alias("margin_balance"),
            pl.col("margin_change").cast(pl.Float64, strict=False).alias("margin_balance_change"),
            pl.col("short_balance").cast(pl.Float64, strict=False).alias("short_balance"),
            pl.col("short_margin_ratio").cast(pl.Float64, strict=False).alias("short_margin_ratio"),
            pl.col("date").cast(pl.String).alias("margin_date"),
            pl.col("status").alias("margin_status"),
        ])
        missing = self._missing_exchanges(present, "margin", self._exchange_scope(df))
        return df.join(margin_join, on="symbol", how="left"), str(target), missing

    def _join_institutional_margin(
        self, df: pl.DataFrame, symbols: list[str], as_of: str | None = None
    ) -> tuple[pl.DataFrame, str | None, str | None, list[str]]:
        """Join chip rows of one market-wide date; degraded lists unreadable or missing-exchange sections."""
        degraded: list[str] = []
        inst_date = None
        margin_date = None
        try:
            df, inst_date, missing = self._join_institutional(df, as_of)
            degraded.extend(missing)
        except Exception as e:
            logger.warning("Batch read institutional failed in screener: %s", e)
            degraded.append("institutional")
            df = self._add_null_cols(df.drop(_INSTITUTIONAL_COLUMNS, strict=False), _INSTITUTIONAL_COLUMNS)
        try:
            df, margin_date, missing = self._join_margin(df, as_of)
            degraded.extend(missing)
        except Exception as e:
            logger.warning("Batch read margin failed in screener: %s", e)
            degraded.append("margin")
            df = self._add_null_cols(df.drop(_MARGIN_COLUMNS, strict=False), _MARGIN_COLUMNS)
        return df, inst_date, margin_date, degraded

    def _apply_filters(self, df: pl.DataFrame, req: TaiwanScreenerRequest) -> pl.DataFrame:
        """Apply strongly typed whitelist filters."""
        # Industry
        if req.industry == UNCLASSIFIED_INDUSTRY:
            # Mirrors the industry snapshot bucket, which counts stocks only.
            df = df.filter((pl.col("instrument_type") == "stock") & unclassified_industry_expr())
        elif req.industry and req.industry != "ALL":
            df = df.filter(pl.col("industry") == req.industry)

        # Price
        if req.price_min is not None:
            df = df.filter(pl.col("close") >= req.price_min)
        if req.price_max is not None:
            df = df.filter(pl.col("close") <= req.price_max)

        # Change pct (0.05 = 5%)
        if req.change_pct_min is not None:
            df = df.filter(pl.col("change_pct") >= req.change_pct_min)
        if req.change_pct_max is not None:
            df = df.filter(pl.col("change_pct") <= req.change_pct_max)

        # Volume (shares)
        if req.volume_min is not None:
            df = df.filter(pl.col("volume") >= req.volume_min)
        if req.volume_max is not None:
            df = df.filter(pl.col("volume") <= req.volume_max)

        # Amount (TWD)
        if req.amount_min is not None:
            df = df.filter(pl.col("amount") >= req.amount_min)
        if req.amount_max is not None:
            df = df.filter(pl.col("amount") <= req.amount_max)

        # Indicators
        if req.rsi_14_min is not None:
            df = df.filter(pl.col("rsi_14") >= req.rsi_14_min)
        if req.rsi_14_max is not None:
            df = df.filter(pl.col("rsi_14") <= req.rsi_14_max)

        if req.momentum_5d_min is not None:
            df = df.filter(pl.col("momentum_5d") >= req.momentum_5d_min)
        if req.momentum_5d_max is not None:
            df = df.filter(pl.col("momentum_5d") <= req.momentum_5d_max)

        if req.vol_ratio_5d_min is not None:
            df = df.filter(pl.col("vol_ratio_5d") >= req.vol_ratio_5d_min)
        if req.vol_ratio_5d_max is not None:
            df = df.filter(pl.col("vol_ratio_5d") <= req.vol_ratio_5d_max)

        if req.above_ma5 is True:
            df = df.filter(pl.col("close") > pl.col("ma5"))
        elif req.above_ma5 is False:
            df = df.filter(pl.col("close") <= pl.col("ma5"))

        if req.above_ma20 is True:
            df = df.filter(pl.col("close") > pl.col("ma20"))
        elif req.above_ma20 is False:
            df = df.filter(pl.col("close") <= pl.col("ma20"))

        # Price limits
        # Note: NO_LIMIT products have distance = null and will not match near_upper/lower
        if req.near_upper_limit is True:
            df = df.filter(pl.col("distance_to_upper_limit") <= 0.03)
        if req.near_lower_limit is True:
            df = df.filter(pl.col("distance_to_lower_limit") <= 0.03)
        if req.distance_to_upper_limit_max is not None:
            df = df.filter(pl.col("distance_to_upper_limit") <= req.distance_to_upper_limit_max)
        if req.distance_to_lower_limit_max is not None:
            df = df.filter(pl.col("distance_to_lower_limit") <= req.distance_to_lower_limit_max)

        # Institutional
        if req.foreign_net_min is not None:
            df = df.filter(pl.col("foreign_net") >= req.foreign_net_min)
        if req.foreign_net_max is not None:
            df = df.filter(pl.col("foreign_net") <= req.foreign_net_max)
        if req.investment_trust_net_min is not None:
            df = df.filter(pl.col("investment_trust_net") >= req.investment_trust_net_min)
        if req.investment_trust_net_max is not None:
            df = df.filter(pl.col("investment_trust_net") <= req.investment_trust_net_max)
        if req.dealer_net_min is not None:
            df = df.filter(pl.col("dealer_net") >= req.dealer_net_min)
        if req.dealer_net_max is not None:
            df = df.filter(pl.col("dealer_net") <= req.dealer_net_max)

        # Margin
        if req.margin_balance_change_min is not None:
            df = df.filter(pl.col("margin_balance_change") >= req.margin_balance_change_min)
        if req.margin_balance_change_max is not None:
            df = df.filter(pl.col("margin_balance_change") <= req.margin_balance_change_max)
        if req.short_balance_min is not None:
            df = df.filter(pl.col("short_balance") >= req.short_balance_min)
        if req.short_balance_max is not None:
            df = df.filter(pl.col("short_balance") <= req.short_balance_max)
        if req.short_margin_ratio_min is not None:
            df = df.filter(pl.col("short_margin_ratio") >= req.short_margin_ratio_min)
        if req.short_margin_ratio_max is not None:
            df = df.filter(pl.col("short_margin_ratio") <= req.short_margin_ratio_max)

        # Signals: an unknown or unpublished id matches nothing rather than everything.
        for sig in req.signals or []:
            if sig not in df.columns:
                return df.clear()
            df = df.filter(pl.col(sig).fill_null(False))

        # Undecidable (null) patterns never match; an unknown or unpublished id matches nothing.
        for pattern in getattr(req, "volume_patterns", None) or []:
            if pattern not in VOLUME_PATTERN_COLUMNS or pattern not in df.columns:
                return df.clear()
            df = df.filter(pl.col(pattern).fill_null(False))

        return df

    def _apply_sort(self, df: pl.DataFrame, sort_by: str, sort_order: str) -> pl.DataFrame:
        """Sort with deterministic symbol ASC tie-breaker; null and NaN values go last either way."""
        descending = sort_order == "desc"
        if sort_by not in df.columns:
            sort_by = "symbol"
            descending = False

        if sort_by == "symbol":
            return df.sort("symbol", descending=descending)
        key = pl.col(sort_by)
        if df.schema[sort_by].is_float():
            key = key.fill_nan(None)
        return df.sort([key, pl.col("symbol")], descending=[descending, False], nulls_last=True)

    @staticmethod
    def _signal_columns(df: pl.DataFrame) -> list[str]:
        return [c for c in df.columns if c.startswith("signal_")]

    def _join_signals(self, combined: pl.DataFrame, as_of: str | None) -> pl.DataFrame:
        """Attach the published signal columns for the quote date, if they exist."""
        if not as_of:
            return combined
        part = _enriched_part(as_of)
        if not part.exists():
            logger.debug("no published signals for %s", as_of)
            return combined
        try:
            frame = pl.read_parquet(part)
        except Exception as exc:
            logger.warning("signal partition unreadable for %s: %s", as_of, exc)
            return combined
        cols = [
            "symbol",
            *self._signal_columns(frame),
            *(c for c in VOLUME_PATTERN_COLUMNS if c in frame.columns),
        ]
        if len(cols) == 1:
            return combined
        return combined.join(frame.select(cols), on="symbol", how="left")

    def _build_items(self, df: pl.DataFrame) -> list[ScreenerResultItem]:
        items = []
        signal_cols = self._signal_columns(df)
        pattern_cols = [c for c in VOLUME_PATTERN_COLUMNS if c in df.columns]
        for r in df.iter_rows(named=True):
            items.append(ScreenerResultItem(
                signals=[c for c in signal_cols if r.get(c)],
                volume_patterns=[c for c in pattern_cols if r.get(c)],
                symbol=r["symbol"],
                name=r.get("name") or r["symbol"],
                exchange=r.get("exchange") or "TWSE",
                instrument_type=r.get("instrument_type") or "stock",
                industry=r.get("industry"),
                close=r.get("close"),
                change_pct=r.get("change_pct"),
                volume=r.get("volume"),
                amount=r.get("amount"),
                quote_date=str(r["date"]) if r.get("date") else None,
                price_limit_pct=r.get("price_limit_pct"),
                is_no_limit=r.get("is_no_limit", False),
                limit_up=r.get("limit_up"),
                limit_down=r.get("limit_down"),
                distance_to_upper_limit=r.get("distance_to_upper_limit"),
                distance_to_lower_limit=r.get("distance_to_lower_limit"),
                ma5=r.get("ma5"),
                ma10=r.get("ma10"),
                ma20=r.get("ma20"),
                rsi_14=r.get("rsi_14"),
                momentum_5d=r.get("momentum_5d"),
                vol_ratio_5d=r.get("vol_ratio_5d"),
                foreign_net=r.get("foreign_net"),
                foreign_net_5d=r.get("foreign_net_5d"),
                investment_trust_net=r.get("investment_trust_net"),
                investment_trust_net_5d=r.get("investment_trust_net_5d"),
                dealer_net=r.get("dealer_net"),
                institutional_date=r.get("institutional_date"),
                institutional_status=r.get("institutional_status") or "unavailable",
                margin_balance=r.get("margin_balance"),
                margin_balance_change=r.get("margin_balance_change"),
                short_balance=r.get("short_balance"),
                short_margin_ratio=r.get("short_margin_ratio"),
                margin_date=r.get("margin_date"),
                margin_status=r.get("margin_status") or "unavailable",
            ))
        return items

    def _add_null_cols(self, df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
        for c in cols:
            if c not in df.columns:
                df = df.with_columns(pl.lit(None).alias(c))
        return df
