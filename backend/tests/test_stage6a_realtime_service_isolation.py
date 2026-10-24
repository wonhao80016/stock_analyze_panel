"""TaiwanRealtimeService: per-symbol isolation, cache TTL and the third-party tier on the poll path."""
from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.realtime import service as service_mod
from app.taiwan.realtime.calendar import taipei_now
from app.taiwan.realtime.models import MarketStatus, RealtimeStatus, TaiwanRealtimeQuote
from app.taiwan.realtime.service import TaiwanRealtimeService
from app.taiwan.symbol import parse_symbol


class _ClosedCalendar:
    known_holidays: frozenset = frozenset()

    def get_market_status(self, *_args, **_kwargs):
        return MarketStatus.CLOSED

    def add_trading_day(self, _day) -> None:
        pass


def _quote(canonical: str, *, source: str = "twse:mis", realtime: bool = True) -> TaiwanRealtimeQuote:
    now = taipei_now()
    ts = parse_symbol(canonical)
    return TaiwanRealtimeQuote(
        symbol=canonical,
        name=canonical,
        exchange=ts.exchange.value,
        last_price=100.0,
        prev_close=99.0,
        open=99.5,
        high=101.0,
        low=99.0,
        change=1.0,
        change_pct=1.0101,
        volume=1000,
        amount=None,
        quote_time=datetime.combine(now.date(), now.time(), tzinfo=now.tzinfo),
        trade_date=now.date(),
        market_status=MarketStatus.CLOSED.value,
        source_meta=SourceMeta(
            source=source,
            source_url="",
            fetched_at=now.isoformat(),
            trade_date=now.date(),
            status=RealtimeStatus.REALTIME.value if realtime else RealtimeStatus.FALLBACK.value,
            is_realtime=realtime,
        ),
    )


class _FakeMis:
    def __init__(self, available: set[str]) -> None:
        self.available = available
        self.requested: list[list[str]] = []

    def fetch_quotes(self, symbols):
        canon = [s.canonical for s in symbols]
        self.requested.append(canon)
        return {c: _quote(c) for c in canon if c in self.available}


class _FakeYahoo:
    def __init__(self) -> None:
        self.requested: list[list[str]] = []

    def fetch_quotes(self, symbols):
        canon = [s.canonical for s in symbols]
        self.requested.append(canon)
        return {c: _quote(c, source="yahoo:chart", realtime=False) for c in canon}


def _service(mis: _FakeMis, yahoo: _FakeYahoo | None = None, ttl: float = 3.0) -> TaiwanRealtimeService:
    return TaiwanRealtimeService(
        cache_ttl_seconds=ttl,
        trading_calendar=_ClosedCalendar(),
        mis_provider=mis,
        yahoo_provider=yahoo or _FakeYahoo(),
    )


# ── B054: one unresolvable symbol must not blank the whole round ──


def test_unresolvable_symbols_are_unavailable_and_the_rest_still_quote(caplog):
    mis = _FakeMis({"2330.TWSE"})
    svc = _service(mis)

    with caplog.at_level(logging.WARNING, logger=service_mod.__name__):
        quotes = svc.get_quotes(["600519.SH", "2330.TWSE", "2330"])

    assert quotes["2330.TWSE"].last_price == 100.0
    for raw in ("600519.SH", "2330"):
        assert quotes[raw].last_price is None
        assert quotes[raw].source_meta.status == RealtimeStatus.UNAVAILABLE.value
    assert mis.requested == [["2330.TWSE"]]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "600519.SH" in logged and "'2330'" in logged


def test_all_unresolvable_symbols_never_reach_the_provider():
    mis = _FakeMis(set())
    quotes = _service(mis).get_quotes(["600519.SH"])

    assert quotes["600519.SH"].source_meta.status == RealtimeStatus.UNAVAILABLE.value
    assert mis.requested == []


def test_hybrid_live_frame_keeps_the_resolvable_symbols():
    from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider

    svc = _service(_FakeMis({"2330.TWSE"}))
    with (
        patch.object(service_mod, "get_realtime_service", return_value=svc),
        patch.object(TaiwanHybridProvider, "_official_fallback_allowed", staticmethod(lambda: False)),
    ):
        frame = TaiwanHybridProvider().get_realtime(symbols=["600519.SH", "2330.TWSE"])

    assert frame["symbol"].to_list() == ["2330.TWSE"]


# ── B211: a cache hit must not have its TTL renewed by a later mixed round ──


def test_cache_hit_keeps_its_original_expiry(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(service_mod, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    mis = _FakeMis({"2330.TWSE", "2317.TWSE"})
    svc = _service(mis, ttl=3.0)

    svc.get_quotes(["2330.TWSE"])
    clock[0] = 1002.0
    svc.get_quotes(["2330.TWSE", "2317.TWSE"])
    clock[0] = 1004.0
    svc.get_quotes(["2330.TWSE"])

    assert mis.requested == [["2330.TWSE"], ["2317.TWSE"], ["2330.TWSE"]]


# ── B210: the poll path never asks Yahoo, whose quotes the hybrid frame drops ──


def test_poll_records_never_call_the_third_party_tier():
    from app.services.quote_service import QuoteService
    from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider

    yahoo = _FakeYahoo()
    svc = _service(_FakeMis({"2330.TWSE"}), yahoo)
    with (
        patch.object(service_mod, "get_realtime_service", return_value=svc),
        patch.object(TaiwanHybridProvider, "_official_fallback_allowed", staticmethod(lambda: False)),
        patch("app.data_providers.registry.get_provider", return_value=TaiwanHybridProvider()),
    ):
        records = QuoteService._taiwan_realtime_records(["2330.TWSE", "8069.TPEX"])

    assert [r["symbol"] for r in records] == ["2330.TWSE"]
    assert yahoo.requested == []


@pytest.mark.parametrize("symbols", [["8069.TPEX"], ["2330.TWSE", "8069.TPEX"]])
def test_other_callers_keep_the_third_party_tier(symbols):
    yahoo = _FakeYahoo()
    quotes = _service(_FakeMis({"2330.TWSE"}), yahoo).get_quotes(symbols)

    assert yahoo.requested == [["8069.TPEX"]]
    assert quotes["8069.TPEX"].source_meta.source == "yahoo:chart"
