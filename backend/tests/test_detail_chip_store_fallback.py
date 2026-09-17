"""個股詳情的籌碼卡片在即時抓取失敗時要改用本地回補資料。

_aggregate_institutional / _aggregate_margin 原本只打官方即時端點。交易所對
密集請求會回 428 或「沒有 Location 的 307」, 抓取因而失敗, 卡片就顯示「暫無
資料」—— 即使使用者早已把該區間回補到本地 store。
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.symbol import parse_symbol


class _BlockedProvider:
    """Mimics the exchange refusing every live call."""

    def fetch_live_day(self, **kwargs):
        raise RuntimeError("HTTP Error 428: Precondition Required")


class _EmptyProvider:
    """Mimics a call that succeeds but returns nothing for the symbol."""

    def fetch_live_day(self, **kwargs):
        return []


def _margin_frame(symbol: str) -> pl.DataFrame:
    return pl.DataFrame([{
        "symbol": symbol, "date": date(2026, 9, 16),
        "margin_balance": 29766000, "margin_change": 484000,
        "short_balance": 1000, "short_change": 1000, "short_margin_ratio": 0.0,
    }])


def _institutional_frame(symbol: str) -> pl.DataFrame:
    return pl.DataFrame([{
        "symbol": symbol, "date": date(2026, 9, 16),
        "foreign_net": 4794190, "investment_trust_net": 534410,
        "dealer_net": 509161, "computed_net": 5837761, "official_net": 5837761,
    }])


class _FakeStore:
    def __init__(self, frame: pl.DataFrame) -> None:
        self._frame = frame

    def available_dates(self):
        return [date(2026, 9, 16)]

    def read_range(self, symbols, start, end):
        return self._frame


@pytest.fixture
def svc():
    return TaiwanStockDetailService()


@pytest.mark.parametrize("provider", [_BlockedProvider(), _EmptyProvider()])
def test_margin_falls_back_to_store(svc, monkeypatch, provider):
    monkeypatch.setattr(svc, "margin_provider", provider)
    monkeypatch.setattr(
        "app.taiwan.margin_store.TaiwanMarginStore",
        lambda *a, **k: _FakeStore(_margin_frame("2330.TWSE")),
    )
    out = svc._aggregate_margin(parse_symbol("2330.TWSE"))
    assert out.status == "available"
    assert out.margin_balance == 29766000
    assert out.meta.source == "taiwan_margin_store"
    assert out.meta.is_stale is True, "store data is older than live; say so"
    assert out.meta.trade_date == "2026-09-16"


@pytest.mark.parametrize("provider", [_BlockedProvider(), _EmptyProvider()])
def test_institutional_falls_back_to_store(svc, monkeypatch, provider):
    monkeypatch.setattr(svc, "institutional_provider", provider)
    monkeypatch.setattr(
        "app.taiwan.institutional_store.TaiwanInstitutionalStore",
        lambda *a, **k: _FakeStore(_institutional_frame("2330.TWSE")),
    )
    out = svc._aggregate_institutional(parse_symbol("2330.TWSE"))
    assert out.status == "available"
    assert out.foreign_net == 4794190
    assert out.total_net == 5837761
    assert out.meta.source == "taiwan_institutional_store"
    assert out.meta.is_stale is True


def test_margin_reports_unavailable_when_store_is_empty_too(svc, monkeypatch):
    monkeypatch.setattr(svc, "margin_provider", _BlockedProvider())

    class _NoDates:
        def available_dates(self):
            return []

        def read_range(self, *a):
            return pl.DataFrame()

    monkeypatch.setattr("app.taiwan.margin_store.TaiwanMarginStore", lambda *a, **k: _NoDates())
    out = svc._aggregate_margin(parse_symbol("2330.TWSE"))
    assert out.status == "unavailable", "no live data and no local data is genuinely unavailable"


def test_store_failure_does_not_break_the_response(svc, monkeypatch):
    """A broken store must degrade to 'unavailable', never raise into the endpoint."""
    monkeypatch.setattr(svc, "margin_provider", _BlockedProvider())

    def _explode(*a, **k):
        raise OSError("parquet unreadable")

    monkeypatch.setattr("app.taiwan.margin_store.TaiwanMarginStore", _explode)
    out = svc._aggregate_margin(parse_symbol("2330.TWSE"))
    assert out.status == "unavailable"
