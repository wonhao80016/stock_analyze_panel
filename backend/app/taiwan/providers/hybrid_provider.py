"""Taiwan Hybrid Market Data Provider.

Implements the MarketDataProvider protocol:
  - Base protocol: app.data_providers.base.MarketDataProvider
  - Primary provider for daily: TWSE/TPEx official endpoints
  - Secondary fallback: FinMind, then Yahoo Finance
  - Canonical domain symbols enforced at boundaries.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

import httpx
import polars as pl

from app.data_providers.base import AssetType, ProviderCapabilities
from app.taiwan.providers.finmind_provider import FinMindAdapter
from app.taiwan.providers.official_provider import OfficialTaiwanAdapter
from app.taiwan.providers.yahoo_provider import YahooFinanceAdapter
from app.taiwan.symbol import TaiwanSymbol, parse_symbol

logger = logging.getLogger(__name__)

# Provider instances are built per request; one fundamentals source keeps its per-day list cache across them.
_official_fundamentals_provider = None
_official_fundamentals_lock = threading.Lock()

_OFFICIAL_DAILY_ATTEMPTS = 3
_OFFICIAL_DAILY_BACKOFF_SECONDS = 1.0


def _official_fundamentals():
    from app.taiwan.fundamentals import TaiwanOfficialFundamentals

    global _official_fundamentals_provider
    with _official_fundamentals_lock:
        if _official_fundamentals_provider is None:
            _official_fundamentals_provider = TaiwanOfficialFundamentals()
        return _official_fundamentals_provider


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.TransportError)


class _RetryingOfficialAdapter(OfficialTaiwanAdapter):
    """Retries only the monthly table that failed transiently, so a run never refetches months it already has."""

    def _fetch_month(self, symbol: TaiwanSymbol, month: object) -> pl.DataFrame:
        attempt = 1
        while True:
            try:
                return super()._fetch_month(symbol, month)
            except Exception as exc:
                if attempt >= _OFFICIAL_DAILY_ATTEMPTS or not _is_transient(exc):
                    raise
                logger.info(
                    "Taiwan official month %s %s failed (attempt %d/%d), retrying: %s",
                    symbol.canonical, month, attempt, _OFFICIAL_DAILY_ATTEMPTS, exc,
                )
                time.sleep(_OFFICIAL_DAILY_BACKOFF_SECONDS * attempt)
                attempt += 1


class TaiwanHybridProvider:
    """Unified Taiwan Market Data Provider conforming to MarketDataProvider protocol."""
    name = "taiwan"
    capabilities = ProviderCapabilities(
        instruments=True,
        daily=True,
        adj_factor=True,
        minute=False,
        realtime=True,
        financial=True,
    )

    def __init__(
        self,
        finmind_token: str = "",
        primary: str = "official",
    ) -> None:
        self.finmind = FinMindAdapter(token=finmind_token)
        self.yahoo = YahooFinanceAdapter()
        self.official = _RetryingOfficialAdapter()
        self.primary = primary.lower()

    def get_fundamentals(
        self,
        symbol: str,
        dataset: str,
        *,
        exchange: str,
        security_type: str = "stock",
        start_date: date | None = None,
        end_date: date | None = None,
    ):
        """Use the authoritative Taiwan fundamentals seam without changing factors."""
        provider = _official_fundamentals()
        if dataset == "monthly_revenue":
            return provider.monthly_revenue(symbol, exchange, security_type=security_type)
        if dataset == "financial_statement":
            return provider.financial_statement(symbol, exchange, security_type=security_type)
        if dataset == "valuation":
            return provider.valuation(symbol, exchange, security_type=security_type)
        if dataset == "share_capital_record":
            return provider.share_capital(symbol, exchange, security_type=security_type)
        if dataset == "dividend_lifecycle_event":
            if start_date is None or end_date is None:
                raise ValueError("dividend lifecycle events require start_date and end_date")
            return provider.dividend_lifecycle_events(
                symbol,
                exchange,
                start_date,
                end_date,
                security_type=security_type,
            )
        raise ValueError(f"unsupported Taiwan fundamentals dataset: {dataset}")

    def get_etf_data(self, symbol: str, dataset: str, *, exchange: str):
        """Return ETF-domain records without routing through company fundamentals."""
        from app.taiwan.etf_data import TaiwanOfficialETFData

        provider = TaiwanOfficialETFData()
        if dataset == "profile":
            return provider.profile(symbol, exchange)
        if dataset == "snapshot":
            return provider.snapshot(symbol, exchange)
        raise ValueError(f"unsupported Taiwan ETF dataset: {dataset}")

    def get_instruments(self, asset_type: AssetType = "stock") -> pl.DataFrame:
        """Return canonical instruments dataframe from official TaiwanSecurityMaster.

        Production path uses authoritative TWSE + TPEx security master.
        """
        from app.taiwan.universe import get_security_master

        master = get_security_master()
        return master.to_provider_dataframe(asset_type=asset_type)


    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: AssetType = "stock",
        on_chunk_done: object | None = None,
    ) -> pl.DataFrame:
        """Fetch normalized daily K rows with primary -> fallback orchestration."""
        if not symbols:
            return pl.DataFrame()
        del on_chunk_done

        # Parse into canonical TaiwanSymbols
        canonical_symbols: list[TaiwanSymbol] = []
        for s in symbols:
            try:
                canonical_symbols.append(parse_symbol(s))
            except Exception as e:
                logger.warning("Skipping invalid Taiwan symbol %r: %s", s, e)

        if not canonical_symbols:
            return pl.DataFrame()

        adapters = [self.official, self.finmind, self.yahoo] if self.primary == "official" else [self.finmind, self.yahoo]
        for index, adapter in enumerate(adapters):
            try:
                df = adapter.fetch_daily(
                    canonical_symbols, start_time=start_time, end_time=end_time, asset_type=asset_type
                )
                if not df.is_empty():
                    if index:
                        df = df.with_columns(pl.lit("third_party_fallback").alias("status"))
                    return df
            except Exception as e:
                logger.warning("Taiwan adapter %s failed: %s", adapter.metadata.source_name, e)
        return pl.DataFrame()

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: AssetType = "stock",
    ) -> pl.DataFrame:
        """Forward-adjustment factors from the Taiwan corporate-action table.

        Daily bars are stored raw; the enriched publish divides them by these factors. Only
        events on or before end_time (default today, Asia/Taipei) are returned, because a
        later one would also scale the newest bar. start_time keeps events on or after it.
        Columns: symbol, trade_date, ex_factor, volume_factor. The factors are never
        written to data/adj_factor.
        """
        from app.taiwan.corporate_actions import TaiwanCorporateActionStore, adjustment_factors
        from app.taiwan.realtime.calendar import taipei_today

        through = end_time.date() if end_time is not None else taipei_today()
        canonical = [parse_symbol(s).canonical for s in symbols] if symbols else None
        factors = adjustment_factors(TaiwanCorporateActionStore().read(end=through, symbols=canonical), through)
        if start_time is not None:
            factors = factors.filter(pl.col("trade_date") >= start_time.date())
        return factors

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        asset_type: AssetType = "stock",
        freq: str = "1m",
        on_chunk_done: object | None = None,
    ) -> pl.DataFrame:
        return pl.DataFrame()

    def _live_quote_frame(self, symbols: list[str]) -> pl.DataFrame:
        """Intraday quotes shaped like the daily frame the enrichment pipeline consumes.

        Only quotes the realtime layer marks as live are kept: continuous-trading quotes
        ("realtime") and today's closing-auction result ("close_final"). The official endpoint
        below publishes the previous session's close during trading hours, so a stale
        quote that reached this frame would be indistinguishable from a live one and
        would freeze every price at yesterday's close.

        Next to closing results, a today-dated MIS quote from before 13:30 marks a symbol the
        auction did not trade (e.g. halted) as "settled_no_close"; it is never today's bar.
        """
        from app.taiwan.realtime.calendar import REGULAR_OPEN_END, taipei_now
        from app.taiwan.realtime.models import RealtimeStatus
        from app.taiwan.realtime.service import get_realtime_service

        quotes = get_realtime_service().get_quotes(symbols)
        now = taipei_now()
        rows: list[dict] = []
        for canonical, q in quotes.items():
            if q.last_price is None:
                continue
            settled_no_close = (
                q.source_meta.status == RealtimeStatus.OFFICIAL_SNAPSHOT.value
                and q.source_meta.source == "twse:mis"
                and q.quote_time is not None
                and q.trade_date == now.date()
                and q.quote_time.time() < REGULAR_OPEN_END <= now.time()
            )
            if not settled_no_close and not q.source_meta.is_realtime:
                continue
            # A quote past its freshness threshold keeps is_realtime set, so it has
            # to be rejected explicitly or it would be relabelled as live below.
            if q.source_meta.is_stale and not settled_no_close:
                continue
            if settled_no_close:
                status = "settled_no_close"
            elif q.source_meta.status == RealtimeStatus.CLOSE_FINAL.value:
                status = RealtimeStatus.CLOSE_FINAL.value
            else:
                status = RealtimeStatus.REALTIME.value
            quote_time = q.quote_time
            rows.append({
                "symbol": canonical,
                "date": q.trade_date,
                "open": q.open,
                "high": q.high,
                "low": q.low,
                "close": q.last_price,
                "volume": float(q.volume) if q.volume is not None else None,
                "amount": float(q.amount) if q.amount is not None else None,
                "prev_close": q.prev_close,
                "change_pct": q.change_pct,
                "quote_ts": int(quote_time.timestamp() * 1000) if quote_time else None,
                "timestamp": quote_time,
                "provider": "twse",
                "source": q.source_meta.source,
                "source_url": q.source_meta.source_url,
                "retrieved_at": q.source_meta.fetched_at,
                "trade_date": q.trade_date.isoformat() if q.trade_date else None,
                "status": status,
            })
        if all(row["status"] == "settled_no_close" for row in rows):
            return pl.DataFrame()
        return pl.DataFrame(rows)

    @staticmethod
    def _official_fallback_allowed() -> bool:
        """Whether the official and daily fallbacks may run: never from the pre-open to the close.

        In that window they return the previous session, which the realtime gate drops.
        """
        from app.taiwan.realtime.calendar import get_market_status
        from app.taiwan.realtime.models import MarketStatus

        return get_market_status() in (
            MarketStatus.POST_CLOSE,
            MarketStatus.CLOSED,
            MarketStatus.NON_TRADING_DAY,
        )

    def get_realtime(
        self,
        universes: list[str] | None = None,
        symbols: list[str] | None = None,
    ) -> pl.DataFrame:
        del universes
        requested = symbols or []
        if not requested:
            return pl.DataFrame()
        try:
            live = self._live_quote_frame(requested)
            if not live.is_empty():
                return live
        except Exception as exc:
            logger.warning("Taiwan live quote failed: %s", exc)
        if not self._official_fallback_allowed():
            return pl.DataFrame()
        try:
            quote = self.official.fetch_quote(requested)
            if not quote.is_empty():
                return quote
        except Exception as exc:
            logger.warning("Official Taiwan quote failed: %s", exc)
        fallback = self.get_daily(requested)
        if fallback.is_empty():
            return fallback
        return fallback.sort("date").group_by("symbol", maintain_order=True).tail(1).with_columns(
            pl.lit("third_party_fallback").alias("status")
        )
