"""Stock detail benchmark card reads the stored index daily bars (B046, B195)."""
from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from app.taiwan import detail_service
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment.index import TaiwanIndexProvider

D1, D2, D3 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)


def _bars(symbol: str, closes: dict[date, float]) -> pl.DataFrame:
    return pl.DataFrame([
        {"symbol": symbol, "date": d, "open": c - 10, "high": c + 10, "low": c - 20, "close": c}
        for d, c in closes.items()
    ])


class _IndexStore:
    def __init__(self, frames: dict[str, pl.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[str] = []

    def scan_range(self, symbols, start, end):
        self.calls.append(symbols[0])
        frame = self.frames.get(symbols[0])
        if frame is None:
            return pl.DataFrame()
        return frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))


class _IndexService:
    def __init__(self, store: _IndexStore) -> None:
        self.store = store

    def read_range(self, *args, **kwargs):
        raise AssertionError("the benchmark card must read stored bars, not fetch")


class _Sessions:
    def __init__(self, sessions: list[date]) -> None:
        self.sessions = sessions

    def available_dates(self):
        return list(self.sessions)


@pytest.fixture
def svc(monkeypatch):
    service = TaiwanStockDetailService()
    service.daily_store = _Sessions([D1, D2, D3])
    monkeypatch.setattr(detail_service, "taipei_now", lambda: datetime(2026, 10, 22, 18, 0))
    return service


def _use_store(monkeypatch, frames):
    store = _IndexStore(frames)
    monkeypatch.setattr(detail_service, "get_index_daily_service", lambda: _IndexService(store))
    return store


def test_twse_card_uses_stored_taiex_close_and_previous_close(svc, monkeypatch):
    store = _use_store(monkeypatch, {"t00.TWSE": _bars("t00.TWSE", {D1: 21900.0, D2: 22000.0, D3: 22200.0})})

    ctx = svc._aggregate_market_context("TWSE")

    assert store.calls == ["t00.TWSE"]
    assert ctx.benchmark_symbol == "TAIEX"
    assert ctx.close == 22200.0
    assert ctx.change == 200.0
    assert ctx.change_pct == round(200.0 / 22000.0 * 100.0, 2)
    assert ctx.meta.status == "available"
    assert ctx.meta.trade_date == D3.isoformat()


def test_tpex_card_uses_stored_tpex_index(svc, monkeypatch):
    _use_store(monkeypatch, {"o00.TPEX": _bars("o00.TPEX", {D2: 250.0, D3: 247.5})})

    ctx = svc._aggregate_market_context("TPEX")

    assert ctx.benchmark_symbol == "TPEX_INDEX"
    assert ctx.close == 247.5
    assert ctx.change == -2.5
    assert ctx.change_pct == -1.0
    assert ctx.meta.trade_date == D3.isoformat()


def test_change_is_unknown_when_a_session_is_missing_between_bars(svc, monkeypatch):
    _use_store(monkeypatch, {"t00.TWSE": _bars("t00.TWSE", {D1: 21900.0, D3: 22200.0})})

    ctx = svc._aggregate_market_context("TWSE")

    assert ctx.close == 22200.0
    assert ctx.change is None
    assert ctx.change_pct is None
    assert ctx.meta.trade_date == D3.isoformat()


def test_card_is_stale_when_a_later_session_has_no_stored_bar(svc, monkeypatch):
    svc.daily_store = _Sessions([D1, D2, D3])
    _use_store(monkeypatch, {"t00.TWSE": _bars("t00.TWSE", {D1: 21900.0, D2: 22000.0})})

    ctx = svc._aggregate_market_context("TWSE")

    assert ctx.close == 22000.0
    assert ctx.change == 100.0
    assert ctx.meta.trade_date == D2.isoformat()
    assert ctx.meta.status == "stale"
    assert ctx.meta.is_stale is True


def test_card_is_unavailable_without_stored_bars(svc, monkeypatch):
    _use_store(monkeypatch, {})

    ctx = svc._aggregate_market_context("TWSE")

    assert ctx.close is None
    assert ctx.meta.status == "unavailable"


def _taiex_row(raw_date: str, open_: str, close: str) -> dict[str, str]:
    return {"Date": raw_date, "OpeningIndex": open_, "HighestIndex": close, "LowestIndex": open_, "ClosingIndex": close}


def test_taiex_first_row_is_not_measured_against_its_own_open():
    rows = [_taiex_row("1151021", "21,950.00", "22,000.00"), _taiex_row("1151022", "22,100.00", "22,200.00")]

    series = TaiwanIndexProvider().parse_taiex_rows(rows)

    assert [idx.date for idx in series] == [D3]
    assert series[0].previous_close == 22000.0
    assert series[0].change == 200.0


def test_taiex_first_row_uses_the_given_previous_close():
    rows = [_taiex_row("1151022", "22,100.00", "22,200.00")]

    series = TaiwanIndexProvider().parse_taiex_rows(rows, previous_close=22000.0)

    assert len(series) == 1
    assert series[0].previous_close == 22000.0
    assert series[0].change == 200.0
    assert series[0].change_pct == round(200.0 / 22000.0 * 100.0, 2)
