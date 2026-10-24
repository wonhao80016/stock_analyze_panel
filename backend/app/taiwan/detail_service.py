"""Unified Aggregation Service for Taiwan Stock Research Workspace.

Orchestrates:
  - Identity & Security Master (canonical symbol, exchange, type, listing, CFI)
  - Price Limits (date-aware limits, multiplier-aware leveraged/inverse ETF, NO_LIMIT)
  - Realtime Quotes (5-level depth bids/asks, Taipei time, provenance)
  - Daily Historical K-Line (bounded recent daily bars via TaiwanHybridProvider)
  - Institutional Investor Flows (Foreign, Investment Trust, Dealer, Total Net, 5D rolling)
  - Margin Trading & Short Selling (balances, changes, short-margin ratio)
  - Factors (institutional rolling, margin momentum, short-margin ratio)
  - Market Benchmark Context (TAIEX for TWSE, TPEx Index for TPEx)
  - Monitor Rules & Recent Alerts summary
  - Robust Partial-Data Fallback (individual section failure does not abort the whole request)
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import polars as pl

from app.taiwan.detail_models import (
    SectionMeta,
    TaiwanDailyRow,
    TaiwanFactorsData,
    TaiwanHistoricalDaily,
    TaiwanInstitutionalData,
    TaiwanMarginData,
    TaiwanMarketContext,
    TaiwanMonitorSummary,
    TaiwanRecentAlert,
    TaiwanStockDetailResponse,
    TaiwanStockIdentity,
    TaiwanStockPriceLimit,
    TaiwanStockRealtime,
)
from app.taiwan.benchmarks import TAIEX, TPEX_INDEX, get_benchmark, is_benchmark
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.index_daily import get_index_daily_service
from app.taiwan.enrichment.factors import short_margin_ratio
from app.taiwan.enrichment.index import TaiwanIndexProvider
from app.taiwan.enrichment.institutional import TaiwanInstitutionalProvider
from app.taiwan.enrichment.margin import TaiwanMarginProvider
from app.taiwan.universe.models import TDR_SECURITY_CLASS, MarketProfileBridge
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_now
from app.taiwan.realtime.service import get_realtime_service
from app.taiwan.symbol import TaiwanSymbol, parse_symbol
from app.taiwan.universe import get_security_master

if TYPE_CHECKING:
    from app.repository import KlineRepository

logger = logging.getLogger(__name__)

_STORE_LOOKBACK_DAYS = 20
_INDEX_LOOKBACK_DAYS = 20
_FLOW_FIELDS = ("foreign_net", "investment_trust_net", "dealer_net")
_NOT_IN_MARGIN_TABLE = "symbol_not_in_margin_table"


def _configured_data_dir():
    """The configured data dir; raises instead of resolving a relative path against the working directory."""
    from app.config import settings

    if not settings.data_dir:
        raise RuntimeError("settings.data_dir is not configured")
    return settings.data_dir


def _has_exchange(store: Any, day: date, canonical: str) -> bool:
    """Whether the stored *day* holds a row of *canonical*'s exchange.

    A date partition holds both exchanges, and the refresh can store one of them alone.
    Only the symbol column is read, since the detail page polls this path.
    """
    suffix = "." + canonical.rsplit(".", 1)[-1]
    data_dir = getattr(store, "_data_dir", None)
    if data_dir is None:
        symbols = store.read_range(None, day, day)
        return not symbols.is_empty() and any(str(s).endswith(suffix) for s in symbols.get_column("symbol"))
    part = Path(data_dir) / f"date={day.isoformat()}" / "part.parquet"
    if not part.exists():
        return False
    return bool(
        pl.scan_parquet(part).select(pl.col("symbol").cast(pl.String).str.ends_with(suffix).any()).collect().item()
    )


def _latest_with_exchange(store: Any, dates: list[date], canonical: str) -> date | None:
    return next((d for d in reversed(dates) if _has_exchange(store, d, canonical)), None)


class TaiwanStockDetailService:
    """Service that aggregates all Taiwan domain data for a single security."""

    def __init__(
        self,
        hybrid_provider: TaiwanHybridProvider | None = None,
        institutional_provider: TaiwanInstitutionalProvider | None = None,
        margin_provider: TaiwanMarginProvider | None = None,
        index_provider: TaiwanIndexProvider | None = None,
        daily_store: TaiwanDailyStore | None = None,
        corporate_actions: TaiwanCorporateActionStore | None = None,
        repository: KlineRepository | None = None,
    ) -> None:
        self.repository = repository
        self.daily_store = daily_store or TaiwanDailyStore()
        self.corporate_actions = corporate_actions or TaiwanCorporateActionStore()
        self.hybrid_provider = hybrid_provider or TaiwanHybridProvider()
        self.institutional_provider = institutional_provider or TaiwanInstitutionalProvider()
        self.margin_provider = margin_provider or TaiwanMarginProvider()
        self.index_provider = index_provider or TaiwanIndexProvider()
        self.security_master = get_security_master()
        self.realtime_service = get_realtime_service()

    def get_stock_detail(
        self,
        raw_symbol: str,
        days: int = 120,
    ) -> TaiwanStockDetailResponse:
        """Synchronous top-level entrypoint for stock detail aggregation."""
        canonical_sym = parse_symbol(raw_symbol)
        symbol_str = canonical_sym.canonical

        # Benchmark codes are case sensitive downstream (MIS channels, store keys).
        if (bench := get_benchmark(symbol_str)) is not None and symbol_str != bench.symbol:
            symbol_str = bench.symbol
            canonical_sym = parse_symbol(bench.symbol)

        # 1. Identity from Security Master
        master_item = self.security_master.get_instrument(symbol_str)
        if master_item is not None:
            identity = TaiwanStockIdentity(
                symbol=master_item.symbol,
                code=master_item.code,
                name=master_item.name,
                exchange=master_item.exchange,
                instrument_type=master_item.instrument_type,
                is_supported=master_item.is_supported,
                listing_status=master_item.listing_status,
                listing_date=master_item.listing_date,
                industry=master_item.industry,
                cfi_code=master_item.cfi_code,
                etf_category=master_item.etf_category if master_item.etf_category else None,
                security_class=TDR_SECURITY_CLASS if MarketProfileBridge.is_tdr(master_item) else None,
            )
        elif (benchmark := get_benchmark(symbol_str)) is not None:
            identity = TaiwanStockIdentity(
                symbol=benchmark.symbol,
                code=benchmark.code,
                name=benchmark.name,
                exchange=benchmark.exchange,
                instrument_type="index",
                is_supported=True,
            )
        else:
            # Fallback identity for unindexed or mock symbols
            identity = TaiwanStockIdentity(
                symbol=symbol_str,
                code=canonical_sym.code,
                name=canonical_sym.code,
                exchange=canonical_sym.exchange.value.upper(),
                instrument_type="stock",
                is_supported=True,
            )

        # 2. Canonical Price Limits via MarketProfileBridge, for the session the quote belongs to
        quote = self._fetch_quote(symbol_str)
        rule_date = quote.trade_date if quote is not None and quote.trade_date else taipei_now().date()
        rule_known = master_item is not None
        limit_pct = None
        if rule_known:
            try:
                limit_pct = MarketProfileBridge.get_price_limit_pct(master_item, rule_date)
            except ValueError:
                rule_known = False
        is_no_limit = rule_known and limit_pct is None
        if identity.instrument_type == "index":
            rule_type = "不適用（指數）"  # noqa: RUF001
        elif not rule_known:
            rule_type = "漲跌幅規則未知"
        elif is_no_limit:
            rule_type = "無漲跌幅限制"
        else:
            rule_type = f"±{int(limit_pct * 100)}%"
        price_limit = TaiwanStockPriceLimit(
            limit_up=None,
            limit_down=None,
            price_limit_pct=limit_pct,
            is_no_limit=is_no_limit,
            rule_type=rule_type,
        )

        # 3. Realtime Quote & 5-Level Depth
        realtime_data = self._aggregate_realtime(symbol_str, price_limit, quote)

        # 4. Daily Historical K-Line
        daily_data = self._aggregate_daily(symbol_str, days)

        # 5. Institutional Investors (Three Major Institutional Flow)
        # Chip datasets are reported per listed instrument, so an index has no row to fetch.
        benchmark_symbol = is_benchmark(symbol_str)
        institutional_data = (
            self._empty_institutional() if benchmark_symbol
            else self._aggregate_institutional(canonical_sym)
        )

        # 6. Margin Trading & Short Selling
        margin_data = (
            self._empty_margin() if benchmark_symbol
            else self._aggregate_margin(canonical_sym)
        )

        # 7. Factors
        factors_data = self._aggregate_factors(institutional_data, margin_data)

        # 8. Market Context Benchmark
        market_context = self._aggregate_market_context(canonical_sym.exchange.value.upper())

        # 9. Monitor Rules Summary
        monitor_summary = self._aggregate_monitor_summary(symbol_str)

        # 10. Recent Alerts
        recent_alerts = self._aggregate_recent_alerts(symbol_str)

        # Determine overall quality
        sections_statuses = [
            realtime_data.meta.status if realtime_data.meta else "unknown",
            daily_data.status,
            institutional_data.status,
            margin_data.status,
        ]
        if all(s == "available" for s in sections_statuses):
            overall_quality = "good"
        elif any(s == "stale" for s in sections_statuses):
            overall_quality = "stale"
        elif any(s == "available" for s in sections_statuses):
            overall_quality = "partial"
        else:
            overall_quality = "degraded"

        return TaiwanStockDetailResponse(
            symbol=symbol_str,
            identity=identity,
            price_limit=price_limit,
            realtime=realtime_data,
            daily_history=daily_data,
            institutional=institutional_data,
            margin=margin_data,
            factors=factors_data,
            market_context=market_context,
            monitor_summary=monitor_summary,
            recent_alerts=recent_alerts,
            overall_data_quality=overall_quality,
        )

    def _fetch_quote(self, symbol_str: str) -> Any | None:
        """The live quote of *symbol_str*, or None when the realtime service has none or fails."""
        try:
            return self.realtime_service.get_quotes([symbol_str]).get(symbol_str)
        except Exception as e:
            logger.warning("Realtime aggregation failed for %s: %s", symbol_str, e)
            return None

    def _aggregate_realtime(
        self,
        symbol_str: str,
        price_limit: TaiwanStockPriceLimit,
        q: Any | None,
    ) -> TaiwanStockRealtime:
        """Build the realtime section from the fetched quote and enrich price limits."""
        try:
            if q is not None:
                # Calculate limit up/down via authoritative MarketProfileBridge.calc_limits
                inst = self.security_master.get_instrument(symbol_str)
                if q.prev_close and price_limit.price_limit_pct is not None and inst:
                    lim_up, lim_dn = MarketProfileBridge.calc_limits(q.prev_close, inst, q.trade_date)
                    price_limit.limit_up = lim_up
                    price_limit.limit_down = lim_dn

                meta = None
                if q.source_meta:
                    f_at = q.source_meta.fetched_at
                    f_str = f_at.isoformat() if hasattr(f_at, "isoformat") else str(f_at) if f_at else None
                    meta = SectionMeta(
                        source=q.source_meta.source,
                        trade_date=str(q.source_meta.trade_date) if q.source_meta.trade_date else None,
                        fetched_at=f_str,
                        status="available" if not q.source_meta.is_stale else "stale",
                        is_stale=q.source_meta.is_stale,
                        fallback_reason=q.source_meta.fallback_reason,
                    )

                q_t = q.quote_time
                qt_str = q_t.isoformat() if hasattr(q_t, "isoformat") else str(q_t) if q_t else None

                return TaiwanStockRealtime(
                    last_price=q.last_price,
                    prev_close=q.prev_close,
                    open=q.open,
                    high=q.high,
                    low=q.low,
                    change=q.change,
                    change_pct=q.change_pct,
                    volume=q.volume,
                    amount=q.amount,
                    quote_time=qt_str,
                    market_status=q.market_status.value if hasattr(q.market_status, "value") else str(q.market_status),
                    bid_price=q.bid_price,
                    ask_price=q.ask_price,
                    bids=[[float(p), float(v)] for p, v in q.bids],
                    asks=[[float(p), float(v)] for p, v in q.asks],
                    meta=meta,
                )
        except Exception as e:
            logger.warning("Realtime aggregation failed for %s: %s", symbol_str, e)

        return TaiwanStockRealtime(
            market_status="closed",
            meta=SectionMeta(source="realtime_service", status="unavailable", is_stale=False),
        )

    def _local_daily(self, symbol_str: str, days: int):
        """Widen the window before tailing, since trading days are sparser than calendar days."""
        end = taipei_now().date()
        start = end - timedelta(days=days * 2 + 30)
        if is_benchmark(symbol_str):
            return get_index_daily_service().read_range(symbol_str, start, end)
        return self.daily_store.scan_range([symbol_str], start, end)

    def _forward_adjusted(self, symbol_str: str, df):
        """Bars on the enriched publish's scale: the events dated up to the latest stored session.

        Adds raw_close and prev_close, which is the exchange reference price on a
        corporate-action session.
        """
        from app.indicators.pipeline import compute_indicators, filter_halt_days
        from app.taiwan.corporate_actions import (
            attach_reference_prices,
            forward_adjusted,
            read_events_or_empty,
        )

        through = df["date"].max()
        stored = self.daily_store.latest_date()
        if isinstance(stored, date) and stored > through:
            through = stored
        events = read_events_or_empty(self.corporate_actions, end=through, symbols=[symbol_str])
        bars = attach_reference_prices(filter_halt_days(df), events)
        bars = bars.with_columns(bars["close"].alias("raw_close"))
        return compute_indicators(forward_adjusted(bars, events, through), needed={"prev_close"})

    def _aggregate_daily(self, symbol_str: str, days: int) -> TaiwanHistoricalDaily:
        """Fetch historical daily bars, preferring the local store over a live fetch.

        The live provider returns only the current calendar month, so it cannot serve
        the requested range on its own.
        """
        source = "taiwan_index_store" if is_benchmark(symbol_str) else "taiwan_daily_store"
        try:
            df = self._local_daily(symbol_str, days)
            # The stock provider cannot serve an index, so there is nothing to fall back to.
            if df.is_empty() and not is_benchmark(symbol_str):
                df = self.hybrid_provider.get_daily([symbol_str])
                source = "hybrid_daily"
            if not df.is_empty():
                if not is_benchmark(symbol_str):
                    df = self._forward_adjusted(symbol_str, df)
                rows: list[TaiwanDailyRow] = []
                # Sort ascending by date
                sorted_df = df.sort("date")
                tail_df = sorted_df.tail(days)
                turnover = self._published_turnover(symbol_str, tail_df)
                for item in tail_df.iter_rows(named=True):
                    dt_str = str(item.get("date", ""))
                    if len(dt_str) > 10:
                        dt_str = dt_str[:10]
                    rows.append(
                        TaiwanDailyRow(
                            date=dt_str,
                            open=float(item.get("open") or 0.0),
                            high=float(item.get("high") or 0.0),
                            low=float(item.get("low") or 0.0),
                            close=float(item.get("close") or 0.0),
                            volume=round(item.get("volume") or 0),
                            amount=float(item.get("amount")) if item.get("amount") is not None else None,
                            prev_close=item.get("prev_close"),
                            raw_close=item.get("raw_close"),
                            turnover_rate=turnover.get(dt_str),
                        )
                    )
                latest_date = rows[-1].date if rows else None
                meta = SectionMeta(
                    source=str(df.get_column("source")[0]) if "source" in df.columns else source,
                    trade_date=latest_date,
                    status="available",
                    is_stale=False,
                )
                return TaiwanHistoricalDaily(status="available", rows=rows, meta=meta)
        except Exception as e:
            logger.warning("Daily aggregation failed for %s: %s", symbol_str, e)

        return TaiwanHistoricalDaily(
            status="unavailable",
            rows=[],
            meta=SectionMeta(source="hybrid_daily", status="unavailable", is_stale=False),
        )

    def _published_turnover(self, symbol_str: str, bars) -> dict[str, float]:
        """The published enriched turnover_rate of *bars*' sessions, keyed by ISO date.

        Empty without a repository, for an index, or when the read fails: the bars are kept either way.
        """
        if self.repository is None or bars.is_empty() or is_benchmark(symbol_str):
            return {}
        try:
            sessions = [str(value)[:10] for value in bars.get_column("date").to_list()]
            published = self.repository.get_daily(
                symbol_str,
                date.fromisoformat(min(sessions)),
                date.fromisoformat(max(sessions)),
                columns=["date", "turnover_rate"],
            )
        except Exception as e:
            logger.warning("Published turnover read failed for %s: %s", symbol_str, e)
            return {}
        if published.is_empty() or "turnover_rate" not in published.columns:
            return {}
        return {
            str(day)[:10]: float(value)
            for day, value in published.select("date", "turnover_rate").iter_rows()
            if value is not None and math.isfinite(value)
        }

    def _aggregate_institutional(self, sym: TaiwanSymbol) -> TaiwanInstitutionalData:
        """Fetch institutional data for latest trading day, with safe timeout."""
        try:
            # Look up recent days
            t_now = taipei_now()
            today = t_now.date()
            flows = []
            trade_dt = today
            # Scan back up to 5 days for latest trading day
            for offset in range(5):
                check_d = today - timedelta(days=offset)
                if check_d.weekday() >= 5:  # Saturday/Sunday
                    continue
                try:
                    flows = self.institutional_provider.fetch_live_day(
                        exchange=sym.exchange.value.upper(),
                        trade_date=check_d,
                        target_code=sym.code,
                        timeout=3.0,
                    )
                    if flows:
                        trade_dt = check_d
                        break
                except Exception:
                    continue

            if flows:
                f = flows[0]
                total = f.computed_net if f.computed_net is not None else f.official_net
                meta = SectionMeta(
                    source=f.meta.source if f.meta else "twse:t86",
                    trade_date=trade_dt.isoformat(),
                    fetched_at=f.meta.fetched_at.isoformat() if f.meta and f.meta.fetched_at else None,
                    status="available" if not f.meta.is_stale else "stale",
                    is_stale=f.meta.is_stale if f.meta else False,
                )
                flows_5d = self._institutional_5d(sym.canonical, trade_dt)
                return TaiwanInstitutionalData(
                    status="available",
                    foreign_net=f.foreign_net,
                    investment_trust_net=f.investment_trust_net,
                    dealer_net=f.dealer_net,
                    total_net=total,
                    foreign_net_5d=flows_5d["foreign_net"],
                    investment_trust_net_5d=flows_5d["investment_trust_net"],
                    dealer_net_5d=flows_5d["dealer_net"],
                    meta=meta,
                )
        except Exception as e:
            logger.debug("Institutional aggregation skipped for %s: %s", sym.canonical, e)

        fallback = self._institutional_from_store(sym)
        if fallback is not None:
            return fallback

        return TaiwanInstitutionalData(
            status="unavailable",
            meta=SectionMeta(source="twse:t86", status="unavailable", is_stale=False),
        )

    def _institutional_from_store(self, sym: TaiwanSymbol) -> TaiwanInstitutionalData | None:
        """Backfilled rows already on disk, used when the live fetch comes back empty.

        The exchange throttles bursts of requests, and a blocked live call must not
        make a card that has local history render as "no data".
        """
        try:
            from app.taiwan.institutional_store import TaiwanInstitutionalStore

            store = TaiwanInstitutionalStore()
            lookback = sorted(store.available_dates())[-_STORE_LOOKBACK_DAYS:]
            if not lookback:
                return None
            latest = _latest_with_exchange(store, lookback, sym.canonical)
            if latest is None:
                return None
            df = store.read_range([sym.canonical], latest, latest)
            if df.is_empty():
                r: dict[str, Any] = {"date": latest, **dict.fromkeys(_FLOW_FIELDS, 0), "computed_net": 0}
            else:
                r = df.to_dicts()[-1]
        except Exception as e:  # noqa: BLE001
            logger.debug("Institutional store fallback failed for %s: %s", sym.canonical, e)
            return None

        flows_5d = self._institutional_5d(sym.canonical, latest)
        total = r.get("computed_net")
        if total is None:
            total = r.get("official_net")
        return TaiwanInstitutionalData(
            status="available",
            foreign_net=r.get("foreign_net"),
            investment_trust_net=r.get("investment_trust_net"),
            dealer_net=r.get("dealer_net"),
            total_net=total,
            foreign_net_5d=flows_5d["foreign_net"],
            investment_trust_net_5d=flows_5d["investment_trust_net"],
            dealer_net_5d=flows_5d["dealer_net"],
            meta=SectionMeta(
                source="taiwan_institutional_store",
                trade_date=str(r.get("date")),
                status="stale",
                is_stale=True,
            ),
        )

    @staticmethod
    def _institutional_5d(canonical: str, as_of: date) -> dict[str, int | None]:
        """Net flows summed over the five stored trading days ending at ``as_of``.

        T86 lists only securities the institutions traded that day, so a stored date
        without a row for the symbol counts as zero. A window the store cannot fill,
        or with a date lacking the symbol's exchange, is reported as unknown.
        """
        unknown: dict[str, int | None] = dict.fromkeys(_FLOW_FIELDS)
        try:
            from app.taiwan.institutional_store import TaiwanInstitutionalStore

            store = TaiwanInstitutionalStore()
            window = [d for d in sorted(store.available_dates()) if d <= as_of][-5:]
            if len(window) < 5 or window[-1] != as_of:
                return unknown
            if not all(_has_exchange(store, d, canonical) for d in reversed(window)):
                return unknown
            df = store.read_range([canonical], window[0], as_of)
            if df.is_empty():
                return dict.fromkeys(_FLOW_FIELDS, 0)
            return {
                field: None if df[field].null_count() else int(df[field].sum())
                for field in _FLOW_FIELDS
            }
        except Exception as e:
            logger.debug("Institutional 5-day sum failed for %s: %s", canonical, e)
            return unknown

    @staticmethod
    def _empty_institutional() -> TaiwanInstitutionalData:
        """An index has no chip flows; report it as not applicable rather than a fetch failure."""
        return TaiwanInstitutionalData(
            status="unavailable",
            meta=SectionMeta(source="not_applicable", status="unavailable", is_stale=False),
        )

    @staticmethod
    def _empty_margin() -> TaiwanMarginData:
        """An index has no margin balances; report it as not applicable rather than a fetch failure."""
        return TaiwanMarginData(
            status="unavailable",
            meta=SectionMeta(source="not_applicable", status="unavailable", is_stale=False),
        )

    def _aggregate_margin(self, sym: TaiwanSymbol) -> TaiwanMarginData:
        """Fetch margin data for latest trading day, with safe timeout."""
        try:
            t_now = taipei_now()
            today = t_now.date()
            table = []
            trade_dt = today
            for offset in range(5):
                check_d = today - timedelta(days=offset)
                if check_d.weekday() >= 5:
                    continue
                try:
                    # The whole table tells "not listed today" apart from "no table today".
                    table = self.margin_provider.fetch_live_day(
                        exchange=sym.exchange.value.upper(),
                        trade_date=check_d,
                        target_code=None,
                        timeout=3.0,
                    )
                    if table:
                        trade_dt = check_d
                        break
                except Exception:
                    continue

            margins = [m for m in table if m.symbol.split(".")[0] == sym.code]
            if table and not margins:
                source = table[0].meta.source if table[0].meta else "twse:mi_margn"
                return self._margin_not_listed(source, trade_dt)
            if margins:
                m = margins[0]
                meta = SectionMeta(
                    source=m.meta.source if m.meta else "twse:mi_margn",
                    trade_date=trade_dt.isoformat(),
                    fetched_at=m.meta.fetched_at.isoformat() if m.meta and m.meta.fetched_at else None,
                    status="available" if not m.meta.is_stale else "stale",
                    is_stale=m.meta.is_stale if m.meta else False,
                )
                return TaiwanMarginData(
                    status="available",
                    margin_balance=m.margin_balance,
                    margin_change=m.margin_change,
                    short_balance=m.short_balance,
                    short_change=m.short_change,
                    short_margin_ratio=short_margin_ratio(m.short_balance, m.margin_balance),
                    meta=meta,
                )
        except Exception as e:
            logger.debug("Margin aggregation skipped for %s: %s", sym.canonical, e)

        fallback = self._margin_from_store(sym)
        if fallback is not None:
            return fallback

        return TaiwanMarginData(
            status="unavailable",
            meta=SectionMeta(source="twse:mi_margn", status="unavailable", is_stale=False),
        )

    def _margin_from_store(self, sym: TaiwanSymbol) -> TaiwanMarginData | None:
        """Backfilled rows already on disk, used when the live fetch comes back empty."""
        try:
            from app.taiwan.margin_store import TaiwanMarginStore

            store = TaiwanMarginStore()
            lookback = sorted(store.available_dates())[-_STORE_LOOKBACK_DAYS:]
            if not lookback:
                return None
            latest = _latest_with_exchange(store, lookback, sym.canonical)
            if latest is None:
                return None
            df = store.read_range([sym.canonical], latest, latest)
            if df.is_empty():
                return self._margin_not_listed("taiwan_margin_store", latest)
            r = df.to_dicts()[-1]
        except Exception as e:  # noqa: BLE001
            logger.debug("Margin store fallback failed for %s: %s", sym.canonical, e)
            return None

        return TaiwanMarginData(
            status="available",
            margin_balance=r.get("margin_balance"),
            margin_change=r.get("margin_change"),
            short_balance=r.get("short_balance"),
            short_change=r.get("short_change"),
            short_margin_ratio=r.get("short_margin_ratio"),
            meta=SectionMeta(
                source="taiwan_margin_store",
                trade_date=str(r.get("date")),
                status="stale",
                is_stale=True,
            ),
        )

    @staticmethod
    def _margin_not_listed(source: str, trade_date: date) -> TaiwanMarginData:
        """The margin table of *trade_date* lists every margin-eligible security, so a missing row has no balances."""
        return TaiwanMarginData(
            status="unavailable",
            meta=SectionMeta(
                source=source,
                trade_date=trade_date.isoformat(),
                status="unavailable",
                is_stale=False,
                fallback_reason=_NOT_IN_MARGIN_TABLE,
            ),
        )

    def _aggregate_factors(
        self,
        inst: TaiwanInstitutionalData,
        margin: TaiwanMarginData,
    ) -> TaiwanFactorsData:
        """Collate factors from institutional and margin sections."""
        has_any = (
            inst.status == "available"
            or margin.status == "available"
        )
        if not has_any:
            return TaiwanFactorsData(
                status="unavailable",
                meta=SectionMeta(source="factors_pipeline", status="unavailable", is_stale=False),
            )

        return TaiwanFactorsData(
            status="available",
            foreign_net_5d=inst.foreign_net_5d,
            investment_trust_net_5d=inst.investment_trust_net_5d,
            dealer_net_5d=inst.dealer_net_5d,
            margin_balance_change=margin.margin_change,
            short_margin_ratio=margin.short_margin_ratio,
            meta=SectionMeta(
                source="factors_pipeline",
                trade_date=inst.meta.trade_date if inst.meta else None,
                status="available",
                is_stale=False,
            ),
        )

    def _aggregate_market_context(self, exchange: str) -> TaiwanMarketContext:
        """The benchmark's latest stored close, measured against the stored close of the session before it."""
        ex = exchange.upper()
        if ex == "TPEX":
            bench = TPEX_INDEX
            symbol = "TPEX_INDEX"
            name = "櫃買指數"
        else:
            bench = TAIEX
            symbol = "TAIEX"
            name = "發行量加權股價指數"

        try:
            today = taipei_now().date()
            bars = get_index_daily_service().store.scan_range(
                [bench.symbol], today - timedelta(days=_INDEX_LOOKBACK_DAYS), today,
            )
            closes = sorted(
                (row["date"], float(row["close"]))
                for row in bars.select("date", "close").iter_rows(named=True)
                if row["close"] is not None
            )
            if closes:
                latest_day, close = closes[-1]
                sessions = self.daily_store.available_dates()
                change = change_pct = None
                if len(closes) > 1:
                    prev_day, prev_close = closes[-2]
                    contiguous = not any(prev_day < d < latest_day for d in sessions)
                    if contiguous and prev_close > 0:
                        change = round(close - prev_close, 2)
                        change_pct = round(change / prev_close * 100.0, 2)
                is_stale = any(d > latest_day for d in sessions)
                return TaiwanMarketContext(
                    benchmark_symbol=symbol,
                    benchmark_name=name,
                    close=close,
                    change=change,
                    change_pct=change_pct,
                    meta=SectionMeta(
                        source="taiwan_index_store",
                        trade_date=latest_day.isoformat(),
                        status="stale" if is_stale else "available",
                        is_stale=is_stale,
                    ),
                )
        except Exception as e:
            logger.debug("Market context read failed for %s: %s", bench.symbol, e)

        return TaiwanMarketContext(
            benchmark_symbol=symbol,
            benchmark_name=name,
            close=None,
            change=None,
            change_pct=None,
            meta=SectionMeta(source="index_provider", status="unavailable", is_stale=False),
        )

    def _aggregate_monitor_summary(self, symbol_str: str) -> TaiwanMonitorSummary:
        """Find rules (from the unified monitor engine) that cover this symbol.

        A rule covers the symbol when scope=symbols and the symbol is listed,
        or when scope=all (market-wide rules apply to every symbol). Sector
        and watchlist_group scoped rules are excluded: membership can't be
        resolved cheaply here, and including them would misleadingly imply
        the rule specifically watches this symbol.
        """
        from app.strategy import monitor_rules as mr_store

        try:
            data_dir = _configured_data_dir()
            matched = [
                r for r in mr_store.load_all(data_dir)
                if (r.get("scope") == "symbols" and symbol_str in r.get("symbols", []))
                or r.get("scope") == "all"
            ]
            active = [r for r in matched if r.get("enabled", True)]
            return TaiwanMonitorSummary(
                rule_count=len(matched),
                active_count=len(active),
                rules=[
                    {
                        "rule_id": r.get("id"),
                        "name": r.get("name"),
                        "type": r.get("type"),
                        "scope": r.get("scope"),
                        "enabled": r.get("enabled", True),
                        "severity": r.get("severity", "info"),
                        "conditions": r.get("conditions", []),
                        "logic": r.get("logic", "and"),
                    }
                    for r in matched
                ],
            )
        except Exception as e:
            logger.warning("Monitor summary error for %s: %s", symbol_str, e)
            return TaiwanMonitorSummary()

    @staticmethod
    def _recent_alert(a: dict[str, Any]) -> TaiwanRecentAlert:
        ts = a.get("ts", 0)
        price = a.get("trigger_price", a.get("price"))
        return TaiwanRecentAlert(
            alert_id=str(a.get("alert_id", a.get("id", ""))),
            rule_id=str(a.get("rule_id", "")),
            rule_name=str(a.get("rule_name", a.get("name", ""))),
            rule_type=str(a.get("rule_type", a.get("type", ""))),
            severity=str(a.get("severity", "warning")),
            trigger_price=float(price) if price is not None else None,
            trigger_value=float(a.get("trigger_value") or 0.0),
            message=str(a.get("message", "")),
            triggered_at=datetime.fromtimestamp(ts / 1000.0, tz=TAIPEI_TZ).isoformat() if ts else "",
        )

    def _aggregate_recent_alerts(self, symbol_str: str) -> list[TaiwanRecentAlert]:
        """Fetch recent alerts for this symbol."""
        from app.services import alert_store
        alerts: list[TaiwanRecentAlert] = []
        try:
            raw_alerts = alert_store.list_recent(_configured_data_dir(), days=7, limit=alert_store.MAX_RECORDS)
            for a in raw_alerts:
                if a.get("symbol") != symbol_str and a.get("rule_symbol") != symbol_str:
                    continue
                try:
                    alerts.append(self._recent_alert(a))
                except (TypeError, ValueError) as e:
                    logger.warning("Skipping malformed alert %s for %s: %s", a.get("id"), symbol_str, e)
            # Limit to 10
            return alerts[:10]
        except Exception as e:
            logger.warning("Recent alerts aggregation error for %s: %s", symbol_str, e)
            return []


# Global singleton instance
_detail_service: TaiwanStockDetailService | None = None


def get_taiwan_stock_detail_service(
    repository: KlineRepository | None = None,
) -> TaiwanStockDetailService:
    """The shared service; a given repository is kept and serves the daily rows' published turnover."""
    global _detail_service
    if _detail_service is None:
        _detail_service = TaiwanStockDetailService()
    if repository is not None:
        _detail_service.repository = repository
    return _detail_service
