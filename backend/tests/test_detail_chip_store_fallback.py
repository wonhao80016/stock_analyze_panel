"""個股詳情的籌碼卡片在即時抓取失敗時要改用本地回補資料。

_aggregate_institutional / _aggregate_margin 原本只打官方即時端點。交易所對
密集請求會回 428 或「沒有 Location 的 307」, 抓取因而失敗, 卡片就顯示「暫無
資料」—— 即使使用者早已把該區間回補到本地 store。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.detail_models import SectionMeta, TaiwanMarginData
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment.models import InstitutionalFlow, SourceMeta
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


# ── B9: institutional/margin fallback must pick the latest date that actually has
# a row for the symbol (T86 omits stocks with zero net that day, which is not the
# same as "no data"), and 5-day fields must sum a real store window instead of
# echoing the 1-day figure. ──────────────────────────────────────────────────


class _RangeStore:
    """Store double honoring the real store's available_dates()/read_range(symbols,
    start, end) contract: a partition can exist for a date without the target
    symbol having a row in it (T86 only lists symbols with nonzero net).
    """

    def __init__(self, partition_dates: list[date], rows_by_date: dict[date, dict]) -> None:
        self._partition_dates = sorted(partition_dates)
        self._rows_by_date = rows_by_date

    def available_dates(self):
        return list(self._partition_dates)

    def read_range(self, symbols, start, end):
        if not symbols:
            # Every partition holds another TWSE row, so the exchange's table is present each day.
            recs = [{"symbol": "1101.TWSE", "date": d} for d in self._partition_dates if start <= d <= end]
            return pl.DataFrame(recs) if recs else pl.DataFrame()
        symbol = symbols[0]
        recs = [
            {"symbol": symbol, "date": d, **row}
            for d, row in self._rows_by_date.items()
            if start <= d <= end
        ]
        return pl.DataFrame(recs) if recs else pl.DataFrame()


def test_institutional_from_store_counts_a_missing_latest_row_as_zero(svc, monkeypatch):
    d1, d2, d3, d4, d5, d6 = [date(2026, 9, n) for n in range(10, 16)]
    rows = {
        d1: {"foreign_net": 100, "investment_trust_net": 10, "dealer_net": 1, "computed_net": 111, "official_net": 111},
        d2: {"foreign_net": 200, "investment_trust_net": 20, "dealer_net": 2, "computed_net": 222, "official_net": 222},
        d3: {"foreign_net": 300, "investment_trust_net": 30, "dealer_net": 3, "computed_net": 333, "official_net": 333},
        d4: {"foreign_net": 400, "investment_trust_net": 40, "dealer_net": 4, "computed_net": 444, "official_net": 444},
        d5: {"foreign_net": 500, "investment_trust_net": 50, "dealer_net": 5, "computed_net": 555, "official_net": 555},
        # d6 partition exists (other symbols traded) but this symbol has no row.
    }
    store = _RangeStore([d1, d2, d3, d4, d5, d6], rows)
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)

    out = svc._institutional_from_store(parse_symbol("2330.TWSE"))

    assert out is not None
    assert out.status == "available"
    assert out.meta.trade_date == str(d6)
    assert (out.foreign_net, out.total_net) == (0, 0)
    assert out.foreign_net_5d == 200 + 300 + 400 + 500
    assert out.investment_trust_net_5d == 20 + 30 + 40 + 50
    assert out.dealer_net_5d == 2 + 3 + 4 + 5


def test_institutional_from_store_5d_window_treats_missing_days_as_zero(svc, monkeypatch):
    d1, d2, d3, d4, d5, d6 = [date(2026, 9, n) for n in range(10, 16)]
    rows = {
        d2: {"foreign_net": 200, "investment_trust_net": 20, "dealer_net": 2, "computed_net": 222, "official_net": 222},
        d4: {"foreign_net": 400, "investment_trust_net": 40, "dealer_net": 4, "computed_net": 444, "official_net": 444},
        d6: {"foreign_net": 600, "investment_trust_net": 60, "dealer_net": 6, "computed_net": 666, "official_net": 666},
    }
    store = _RangeStore([d1, d2, d3, d4, d5, d6], rows)
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)

    out = svc._institutional_from_store(parse_symbol("2330.TWSE"))

    assert out is not None
    assert out.foreign_net == 600
    assert out.foreign_net_5d == 200 + 400 + 600
    assert out.investment_trust_net_5d == 20 + 40 + 60
    assert out.dealer_net_5d == 2 + 4 + 6


def test_institutional_from_store_5d_fields_none_when_fewer_than_5_store_dates(svc, monkeypatch):
    d1, d2, d3 = [date(2026, 9, n) for n in range(10, 13)]
    rows = {
        d1: {"foreign_net": 100, "investment_trust_net": 10, "dealer_net": 1, "computed_net": 111, "official_net": 111},
        d2: {"foreign_net": 200, "investment_trust_net": 20, "dealer_net": 2, "computed_net": 222, "official_net": 222},
        d3: {"foreign_net": 300, "investment_trust_net": 30, "dealer_net": 3, "computed_net": 333, "official_net": 333},
    }
    store = _RangeStore([d1, d2, d3], rows)
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)

    out = svc._institutional_from_store(parse_symbol("2330.TWSE"))

    assert out is not None
    assert out.foreign_net == 300
    assert out.foreign_net_5d is None
    assert out.investment_trust_net_5d is None
    assert out.dealer_net_5d is None


def _flow(t: date, foreign_net: int, investment_trust_net: int, dealer_net: int) -> InstitutionalFlow:
    return InstitutionalFlow(
        symbol="2330.TWSE", trade_date=t,
        foreign_buy=0, foreign_sell=0, foreign_net=foreign_net,
        investment_trust_buy=0, investment_trust_sell=0, investment_trust_net=investment_trust_net,
        dealer_buy=0, dealer_sell=0, dealer_net=dealer_net,
        official_net=foreign_net + investment_trust_net + dealer_net,
        computed_net=foreign_net + investment_trust_net + dealer_net,
        meta=SourceMeta(
            source="twse:t86", source_url="https://example.invalid/t86",
            fetched_at=datetime(t.year, t.month, t.day, 14, 0), trade_date=t, status="realtime",
        ),
    )


class _LiveOnDate:
    """Live provider double that only has data on one trade_date, like the real
    exchange endpoint queried for a specific day."""

    def __init__(self, hit_date: date, flow: InstitutionalFlow) -> None:
        self._hit_date = hit_date
        self._flow = flow

    def fetch_live_day(self, exchange, trade_date, target_code=None, timeout=10.0):
        return [self._flow] if trade_date == self._hit_date else []


def test_aggregate_institutional_live_path_5d_sums_store_window_ending_today(svc, monkeypatch):
    t = date(2026, 9, 15)
    window = [t - timedelta(days=i) for i in range(4, -1, -1)]
    d_4, d_3, d_2, d_1, d0 = window
    assert d0 == t
    store_rows = {
        d_4: {"foreign_net": 10, "investment_trust_net": 1, "dealer_net": 100, "computed_net": 111, "official_net": 111},
        d_3: {"foreign_net": 20, "investment_trust_net": 2, "dealer_net": 200, "computed_net": 222, "official_net": 222},
        d_2: {"foreign_net": 30, "investment_trust_net": 3, "dealer_net": 300, "computed_net": 333, "official_net": 333},
        d_1: {"foreign_net": 40, "investment_trust_net": 4, "dealer_net": 400, "computed_net": 444, "official_net": 444},
        d0: {"foreign_net": 50, "investment_trust_net": 5, "dealer_net": 500, "computed_net": 555, "official_net": 555},
    }
    store = _RangeStore(window, store_rows)
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)
    monkeypatch.setattr("app.taiwan.detail_service.taipei_now", lambda: datetime(t.year, t.month, t.day, 10, 0))
    monkeypatch.setattr(svc, "institutional_provider", _LiveOnDate(t, _flow(t, 99999, 8888, 777)))

    out = svc._aggregate_institutional(parse_symbol("2330.TWSE"))

    assert out.status == "available"
    assert out.foreign_net == 99999  # 1-day field still comes from the live flow
    assert out.foreign_net_5d == 10 + 20 + 30 + 40 + 50
    assert out.investment_trust_net_5d == 1 + 2 + 3 + 4 + 5
    assert out.dealer_net_5d == 100 + 200 + 300 + 400 + 500

    margin_stub = TaiwanMarginData(status="unavailable", meta=SectionMeta(source="x", status="unavailable"))
    factors = svc._aggregate_factors(out, margin_stub)
    assert factors.foreign_net_5d == out.foreign_net_5d
    assert factors.investment_trust_net_5d == out.investment_trust_net_5d
    assert factors.dealer_net_5d == out.dealer_net_5d


def test_aggregate_institutional_live_path_5d_none_when_store_lacks_today(svc, monkeypatch):
    t = date(2026, 9, 15)
    window = [t - timedelta(days=i) for i in range(4, 0, -1)]  # T-4..T-1, store has no row for T
    store_rows = {
        d: {"foreign_net": 1, "investment_trust_net": 1, "dealer_net": 1, "computed_net": 3, "official_net": 3}
        for d in window
    }
    store = _RangeStore(window, store_rows)
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)
    monkeypatch.setattr("app.taiwan.detail_service.taipei_now", lambda: datetime(t.year, t.month, t.day, 10, 0))
    monkeypatch.setattr(svc, "institutional_provider", _LiveOnDate(t, _flow(t, 99999, 8888, 777)))

    out = svc._aggregate_institutional(parse_symbol("2330.TWSE"))

    assert out.status == "available"
    assert out.foreign_net_5d is None
    assert out.investment_trust_net_5d is None
    assert out.dealer_net_5d is None


def test_margin_from_store_reports_a_missing_latest_row_as_not_listed(svc, monkeypatch):
    d1, d2, d3 = [date(2026, 9, n) for n in range(10, 13)]
    rows = {
        d1: {"margin_balance": 1000, "margin_change": 10, "short_balance": 5, "short_change": 1, "short_margin_ratio": 0.5},
        d2: {"margin_balance": 2000, "margin_change": 20, "short_balance": 6, "short_change": 2, "short_margin_ratio": 0.3},
        # d3 partition exists but this symbol has no row.
    }
    store = _RangeStore([d1, d2, d3], rows)
    monkeypatch.setattr("app.taiwan.margin_store.TaiwanMarginStore", lambda *a, **k: store)

    out = svc._margin_from_store(parse_symbol("2330.TWSE"))

    assert out is not None
    assert out.status == "unavailable"
    assert out.margin_balance is None
    assert out.meta.trade_date == str(d3)
    assert out.meta.fallback_reason == "symbol_not_in_margin_table"
