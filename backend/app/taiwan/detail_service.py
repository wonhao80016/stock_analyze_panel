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
from typing import Any, Optional

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
from app.taiwan.benchmarks import get_benchmark, is_benchmark
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.index_daily import get_index_daily_service
from app.taiwan.enrichment.index import TaiwanIndexProvider
from app.taiwan.enrichment.institutional import TaiwanInstitutionalProvider
from app.taiwan.enrichment.margin import TaiwanMarginProvider
from app.taiwan.universe.models import MarketProfileBridge
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.realtime.calendar import taipei_now
from app.taiwan.realtime.service import get_realtime_service
from app.taiwan.symbol import TaiwanSymbol, parse_symbol
from app.taiwan.universe import get_security_master

logger = logging.getLogger(__name__)

_STORE_LOOKBACK_DAYS = 20
_FLOW_FIELDS = ("foreign_net", "investment_trust_net", "dealer_net")


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
    ) -> None:
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

        # 2. Canonical Price Limits via MarketProfileBridge
        rule_known = master_item is not None
        limit_pct = None
        if rule_known:
            try:
                limit_pct = MarketProfileBridge.get_price_limit_pct(master_item)
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
        realtime_data = self._aggregate_realtime(symbol_str, price_limit)

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

    def _aggregate_realtime(
        self,
        symbol_str: str,
        price_limit: TaiwanStockPriceLimit,
    ) -> TaiwanStockRealtime:
        """Fetch live quote and enrich price limits."""
        try:
            quote_map = self.realtime_service.get_quotes([symbol_str])
            q = quote_map.get(symbol_str)
            if q is not None:
                # Calculate limit up/down via authoritative MarketProfileBridge.calc_limits
                inst = self.security_master.get_instrument(symbol_str)
                if q.prev_close and price_limit.price_limit_pct is not None and inst:
                    lim_up, lim_dn = MarketProfileBridge.calc_limits(q.prev_close, inst)
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
            dates = sorted(store.available_dates())
            if not dates:
                return None
            lookback = dates[-_STORE_LOOKBACK_DAYS:]
            df = store.read_range([sym.canonical], lookback[0], lookback[-1])
            if df.is_empty():
                return None
            r = df.sort("date").to_dicts()[-1]
        except Exception as e:  # noqa: BLE001
            logger.debug("Institutional store fallback failed for %s: %s", sym.canonical, e)
            return None

        flows_5d = self._institutional_5d(sym.canonical, r["date"])
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
        without a row for the symbol counts as zero. A window the store cannot fill
        is reported as unknown, never summed short.
        """
        unknown: dict[str, int | None] = dict.fromkeys(_FLOW_FIELDS)
        try:
            from app.taiwan.institutional_store import TaiwanInstitutionalStore

            store = TaiwanInstitutionalStore()
            window = [d for d in sorted(store.available_dates()) if d <= as_of][-5:]
            if len(window) < 5 or window[-1] != as_of:
                return unknown
            df = store.read_range([canonical], window[0], as_of)
            if df.is_empty() or as_of not in df.get_column("date").to_list():
                return unknown
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
            margins = []
            trade_dt = today
            for offset in range(5):
                check_d = today - timedelta(days=offset)
                if check_d.weekday() >= 5:
                    continue
                try:
                    margins = self.margin_provider.fetch_live_day(
                        exchange=sym.exchange.value.upper(),
                        trade_date=check_d,
                        target_code=sym.code,
                        timeout=3.0,
                    )
                    if margins:
                        trade_dt = check_d
                        break
                except Exception:
                    continue

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
                    short_margin_ratio=m.short_margin_ratio,
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
            dates = sorted(store.available_dates())
            if not dates:
                return None
            lookback = dates[-_STORE_LOOKBACK_DAYS:]
            df = store.read_range([sym.canonical], lookback[0], lookback[-1])
            if df.is_empty():
                return None
            r = df.sort("date").to_dicts()[-1]
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
        """Fetch benchmark index info for market context."""
        ex = exchange.upper()
        if ex == "TPEX":
            symbol = "TPEX_INDEX"
            name = "櫃買指數"
        else:
            symbol = "TAIEX"
            name = "發行量加權股價指數"

        try:
            # Look up recent index series
            if ex == "TPEX":
                indices = self.index_provider.parse_tpex_rows([])
            else:
                indices = self.index_provider.parse_taiex_rows([])
            if indices:
                latest = indices[-1]
                return TaiwanMarketContext(
                    benchmark_symbol=symbol,
                    benchmark_name=name,
                    close=latest.close,
                    change=latest.change,
                    change_pct=latest.change_pct,
                    meta=SectionMeta(
                        source=latest.meta.source if latest.meta else "twse:index",
                        trade_date=latest.date.isoformat(),
                        status="available",
                        is_stale=False,
                    ),
                )
        except Exception:
            pass

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
        from app.config import Settings

        try:
            settings = Settings()
            data_dir = settings.data_dir if hasattr(settings, "data_dir") else None
            if not data_dir:
                from pathlib import Path
                data_dir = Path("data")

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

    def _aggregate_recent_alerts(self, symbol_str: str) -> list[TaiwanRecentAlert]:
        """Fetch recent alerts for this symbol."""
        from app.services import alert_store
        from app.config import Settings
        alerts: list[TaiwanRecentAlert] = []
        try:
            settings = Settings()
            data_dir = settings.data_dir if hasattr(settings, "data_dir") else None
            if not data_dir:
                from pathlib import Path
                data_dir = Path("data")
            raw_alerts = alert_store.list_recent(data_dir, days=7, limit=200)
            for a in raw_alerts:
                if a.get("symbol") == symbol_str or a.get("rule_symbol") == symbol_str:
                    ts = a.get("ts", 0)
                    dt_str = datetime.fromtimestamp(ts / 1000.0).isoformat() if ts else ""
                    alerts.append(
                        TaiwanRecentAlert(
                            alert_id=str(a.get("alert_id", a.get("id", ""))),
                            rule_id=str(a.get("rule_id", "")),
                            rule_name=str(a.get("rule_name", a.get("name", ""))),
                            rule_type=str(a.get("rule_type", a.get("type", ""))),
                            severity=str(a.get("severity", "warning")),
                            trigger_price=float(a.get("trigger_price", a.get("price", 0.0))),
                            trigger_value=float(a.get("trigger_value", 0.0)),
                            message=str(a.get("message", "")),
                            triggered_at=dt_str,
                        )
                    )
            # Limit to 10
            return alerts[:10]
        except Exception as e:
            logger.debug("Recent alerts aggregation error for %s: %s", symbol_str, e)
            return []


# Global singleton instance
_detail_service: TaiwanStockDetailService | None = None


def get_taiwan_stock_detail_service() -> TaiwanStockDetailService:
    global _detail_service
    if _detail_service is None:
        _detail_service = TaiwanStockDetailService()
    return _detail_service
