"""Taiwan Realtime Market Data Service.

Orchestrates multi-tier real-time data fetching, in-memory caching,
rate limiting protection, freshness / stale detection, and fallback chain.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import date, datetime, time as dt_time

import logging
import threading
import time
from typing import Any

from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime.calendar import (
    TAIPEI_TZ,
    TaiwanTradingCalendar,
    taipei_now,
)
from app.taiwan.realtime.mis_provider import TwseMisRealtimeProvider
from app.taiwan.realtime.models import (
    MarketStatus,
    RealtimeFreshnessPolicy,
    RealtimeStatus,
    TaiwanRealtimeQuote,
)
from app.taiwan.realtime.yahoo_provider import YahooRealtimeProvider
from app.taiwan.symbol import TaiwanSymbol, parse_symbol

logger = logging.getLogger(__name__)

# Canonical close time for Taiwan market (13:30 Taipei)
_MARKET_CLOSE_TIME = dt_time(13, 30, 0)

_third_party_fallback_enabled: ContextVar[bool] = ContextVar(
    "taiwan_realtime_third_party_fallback", default=True,
)


@contextmanager
def without_third_party_fallback() -> Iterator[None]:
    """Skip the Yahoo tier of get_quotes in this context, for callers that accept only live quotes.

    Yahoo quotes are delayed and never marked realtime, so a live-only caller discards them anyway.
    """
    token = _third_party_fallback_enabled.set(False)
    try:
        yield
    finally:
        _third_party_fallback_enabled.reset(token)


class TaiwanRealtimeService:
    """Thread-safe Real-time Quotation Service with a Fallback Chain."""

    def __init__(
        self,
        cache_ttl_seconds: float = 3.0,
        freshness_policy: RealtimeFreshnessPolicy | None = None,
        trading_calendar: TaiwanTradingCalendar | None = None,
        mis_provider: TwseMisRealtimeProvider | None = None,
        yahoo_provider: YahooRealtimeProvider | None = None,
    ) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self.freshness_policy = freshness_policy or RealtimeFreshnessPolicy()
        self.trading_calendar = trading_calendar or TaiwanTradingCalendar()
        self.mis_provider = mis_provider or TwseMisRealtimeProvider()
        self.yahoo_provider = yahoo_provider or YahooRealtimeProvider()

        # In-memory hot cache: (canonical, market_session) -> (monotonic_expiry, quote)
        self._cache: dict[tuple[str, str], tuple[float, TaiwanRealtimeQuote]] = {}
        self._lock = threading.Lock()

        # Metrics
        self.cache_hits = 0
        self.cache_misses = 0
        self.provider_requests = 0

        self._unresolvable_logged: set[str] = set()

    def get_quotes(
        self,
        symbols: list[TaiwanSymbol | str],
        force_refresh: bool = False,
        daily_kline_fallback_fn: Any | None = None,
    ) -> dict[str, TaiwanRealtimeQuote]:
        """Fetch quotes in batch for specified symbols adhering to fallback chain.

        Fallback Order:
          1. In-memory Hot Cache (if fresh and not force_refresh)
          2. Primary Provider: TWSE MIS (Live L1/L2)
          3. Secondary Provider: Yahoo Finance
          4. Latest Daily K-Line (if daily_kline_fallback_fn provided)
        """
        if not symbols:
            return {}

        now_mono = time.monotonic()
        now_tpe = taipei_now()
        cur_market_status = self.trading_calendar.get_market_status(
            now_tpe, require_verified_trading_day=True,
        )
        parsed_symbols: list[TaiwanSymbol] = []
        unavailable: dict[str, TaiwanRealtimeQuote] = {}
        for s in symbols:
            if isinstance(s, TaiwanSymbol):
                parsed_symbols.append(s)
                continue
            try:
                parsed_symbols.append(parse_symbol(s))
            except (ValueError, AttributeError) as e:
                self._log_unresolvable(s, e)
                unavailable[str(s)] = _unavailable_quote(str(s), now_tpe, cur_market_status, str(e))
        results: dict[str, TaiwanRealtimeQuote] = {}
        to_fetch: list[TaiwanSymbol] = []

        # 1. Check in-memory cache (scoped by market session to prevent cross-session leakage)
        with self._lock:
            for ts in parsed_symbols:
                canonical = ts.canonical
                cache_key = (canonical, cur_market_status.value)
                if not force_refresh and cache_key in self._cache:
                    expiry, cached_quote = self._cache[cache_key]
                    if now_mono < expiry:
                        results[canonical] = cached_quote
                        self.cache_hits += 1
                        continue
                self.cache_misses += 1
                to_fetch.append(ts)

        if not to_fetch:
            return {**results, **unavailable}


        # 2. Primary Provider: TWSE MIS
        self.provider_requests += 1
        fetched_quotes: dict[str, TaiwanRealtimeQuote] = {}
        try:
            fetched_quotes = self.mis_provider.fetch_quotes(to_fetch)
        except Exception as e:
            logger.warning("Primary MIS provider failed: %s", e)

        # Confirm today as trading day if first-party MIS provides verified quotes for today
        has_first_party_today = any(
            q.trade_date == now_tpe.date() and not q.source_meta.is_stale
            for q in fetched_quotes.values()
        )
        if has_first_party_today and now_tpe.weekday() < 5 and now_tpe.date() not in self.trading_calendar.known_holidays:
            self.trading_calendar.add_trading_day(now_tpe.date())
            cur_market_status = self.trading_calendar.get_market_status(
                now_tpe, require_verified_trading_day=True,
            )

        for sym, q in fetched_quotes.items():
            q.market_status = cur_market_status.value
            results[sym] = q

        missing_after_primary = [ts for ts in to_fetch if ts.canonical not in results]

        # 3. Secondary Provider: Yahoo Finance
        if missing_after_primary and _third_party_fallback_enabled.get():
            self.provider_requests += 1
            try:
                yahoo_quotes = self.yahoo_provider.fetch_quotes(missing_after_primary)
                for sym, q in yahoo_quotes.items():
                    q.market_status = cur_market_status.value
                    results[sym] = q
            except Exception as e:
                logger.warning("Secondary Yahoo provider failed: %s", e)

        missing_after_secondary = [ts for ts in to_fetch if ts.canonical not in results]

        # 4. Latest Daily K-Line Fallback
        if missing_after_secondary and daily_kline_fallback_fn:
            for ts in missing_after_secondary:
                try:
                    daily_row = daily_kline_fallback_fn(ts.canonical)
                    if daily_row:
                        now_tpe = taipei_now()
                        raw_date = daily_row.get("date")

                        # Timestamp safety: derive from daily row date, never use fetch time
                        if isinstance(raw_date, date):
                            t_date = raw_date
                            # Derive quote_time at canonical 13:30 close
                            daily_quote_time = datetime.combine(
                                t_date, _MARKET_CLOSE_TIME, tzinfo=TAIPEI_TZ,
                            )
                            fallback_reason = "All realtime & snapshot sources failed; fell back to latest Daily K"
                        else:
                            # Date missing or unreliable — refuse to fake
                            t_date = now_tpe.date()
                            daily_quote_time = None
                            fallback_reason = (
                                "All realtime & snapshot sources failed; fell back to latest Daily K; "
                                "daily row date missing, refused to fake quote timestamp"
                            )

                        meta = SourceMeta(
                            source="daily_kline",
                            source_url="",
                            fetched_at=now_tpe.isoformat(),
                            trade_date=t_date,
                            status=RealtimeStatus.DAILY_FALLBACK.value,
                            is_realtime=False,
                            fallback_reason=fallback_reason,
                            available_fields=("last_price", "prev_close", "volume"),
                            is_stale=True,
                            source_type="local_store",
                            freshness_class="daily_cached",
                            is_best_effort=False,
                            documented_sla=False,
                        )
                        results[ts.canonical] = TaiwanRealtimeQuote(
                            symbol=ts.canonical,
                            name=ts.canonical,
                            exchange=ts.exchange,
                            last_price=daily_row.get("close"),
                            prev_close=daily_row.get("prev_close"),
                            open=daily_row.get("open"),
                            high=daily_row.get("high"),
                            low=daily_row.get("low"),
                            change=daily_row.get("change"),
                            change_pct=daily_row.get("change_pct"),
                            volume=daily_row.get("volume"),
                            amount=daily_row.get("amount"),
                            quote_time=daily_quote_time,
                            trade_date=t_date,
                            market_status=cur_market_status.value,
                            source_meta=meta,
                        )
                except Exception as e:
                    logger.debug("Daily fallback failed for %s: %s", ts.canonical, e)

        # 5. Apply Freshness / Stale Policy & populate Cache
        # Quote times stop at ~13:25 for the closing auction and its result never updates again.
        fetched_this_call = {ts.canonical for ts in to_fetch}
        ages_out = (
            cur_market_status in (MarketStatus.OPEN, MarketStatus.SCHEDULED_OPEN_UNVERIFIED)
            and self.trading_calendar.get_market_status(now_tpe) != MarketStatus.CLOSING_AUCTION
        )
        with self._lock:
            for sym, q in results.items():
                threshold = self.freshness_policy.get_threshold_for_source(q.source_meta.source)
                if (
                    ages_out
                    and q.quote_time
                    and q.source_meta.status != RealtimeStatus.CLOSE_FINAL.value
                ):
                    age_seconds = (now_tpe - q.quote_time).total_seconds()
                    if age_seconds > threshold:
                        new_status = (
                            RealtimeStatus.STALE.value
                            if q.source_meta.status == RealtimeStatus.REALTIME.value
                            else q.source_meta.status
                        )
                        q.source_meta = replace(q.source_meta, is_stale=True, status=new_status)

                # A cache hit keeps its original expiry; re-stamping it would let it live forever.
                if sym in fetched_this_call:
                    self._cache[(sym, cur_market_status.value)] = (now_mono + self.cache_ttl_seconds, q)

        return {**results, **unavailable}

    def _log_unresolvable(self, raw: object, error: Exception) -> None:
        """Warn once per symbol; pools are polled every few seconds."""
        key = str(raw)
        with self._lock:
            first = key not in self._unresolvable_logged
            self._unresolvable_logged.add(key)
        if first:
            logger.warning("Unresolvable Taiwan symbol %r marked unavailable: %s", raw, error)
        else:
            logger.debug("Unresolvable Taiwan symbol %r marked unavailable", raw)


    def get_quote(
        self,
        symbol: TaiwanSymbol | str,
        force_refresh: bool = False,
        daily_kline_fallback_fn: Any | None = None,
    ) -> TaiwanRealtimeQuote | None:
        """Fetch quote for a single symbol."""
        res = self.get_quotes(
            [symbol],
            force_refresh=force_refresh,
            daily_kline_fallback_fn=daily_kline_fallback_fn,
        )
        ts = symbol if isinstance(symbol, TaiwanSymbol) else parse_symbol(symbol)
        return res.get(ts.canonical)


def _unavailable_quote(
    raw: str, now_tpe: datetime, market_status: MarketStatus, reason: str,
) -> TaiwanRealtimeQuote:
    return TaiwanRealtimeQuote(
        symbol=raw,
        name=raw,
        exchange="",
        last_price=None,
        prev_close=None,
        open=None,
        high=None,
        low=None,
        change=None,
        change_pct=None,
        volume=None,
        amount=None,
        quote_time=None,
        trade_date=now_tpe.date(),
        market_status=market_status.value,
        source_meta=SourceMeta(
            source="taiwan:symbol",
            source_url="",
            fetched_at=now_tpe.isoformat(),
            trade_date=None,
            status=RealtimeStatus.UNAVAILABLE.value,
            fallback_reason=f"Unresolvable symbol: {reason}",
            is_stale=True,
        ),
    )


_default_service: TaiwanRealtimeService | None = None


def get_realtime_service() -> TaiwanRealtimeService:
    """Get or instantiate global singleton TaiwanRealtimeService."""
    global _default_service
    if _default_service is None:
        _default_service = TaiwanRealtimeService()
    return _default_service

