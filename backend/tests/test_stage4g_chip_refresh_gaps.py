"""Chip refreshes never store a day past a missing one, and margin waits for both exchanges' publication."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.enrichment.models import InstitutionalFlow, MarginTrading
from app.taiwan.institutional_margin_refresh import (
    TaiwanInstitutionalRefreshService,
    TaiwanMarginRefreshService,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TAIPEI_TZ

STORED = date(2026, 9, 14)
DAYS = [date(2026, 9, d) for d in (15, 16, 17, 18)]
FAILING = DAYS[1]


class _Calendar:
    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5


def _flow(symbol: str, d: date) -> InstitutionalFlow:
    return InstitutionalFlow(
        symbol=symbol, trade_date=d,
        foreign_buy=1000, foreign_sell=500, foreign_net=500,
        investment_trust_buy=0, investment_trust_sell=0, investment_trust_net=0,
        dealer_buy=0, dealer_sell=0, dealer_net=0,
    )


def _margin(symbol: str, d: date) -> MarginTrading:
    return MarginTrading(
        symbol=symbol, trade_date=d, unit="shares", source_unit="lots", lot_multiplier=1000,
        margin_previous_balance=0, margin_buy=0, margin_sell=0, margin_cash_redemption=0,
        margin_balance=1000, margin_change=0,
        short_previous_balance=0, short_sell=0, short_cover=0, short_stock_redemption=0,
        short_balance=0, short_change=0, short_margin_ratio=None,
    )


class _Provider:
    """Both exchanges answer one row a day; days in *raise_on* fail, days in *tpex_empty* have no TPEx rows."""

    def __init__(self, row, raise_on=(), tpex_empty=()):
        self.row, self.raise_on, self.tpex_empty = row, set(raise_on), set(tpex_empty)
        self.fetched: list[date] = []
        self.twse = _Adapter(self, "2330.TWSE")
        self.tpex = _Adapter(self, "8069.TPEX")


class _Adapter:
    def __init__(self, outer: _Provider, symbol: str):
        self.outer, self.symbol = outer, symbol

    def build_url(self, d: date) -> str:
        return f"mock://{self.symbol}/{d}"

    def parse_payload(self, payload, d: date, url: str):
        if d in self.outer.raise_on:
            raise RuntimeError(f"simulated upstream error for {d}")
        if self.symbol.endswith(".TWSE"):
            self.outer.fetched.append(d)
        elif d in self.outer.tpex_empty:
            return []
        return [self.outer.row(self.symbol, d)]


@pytest.fixture(autouse=True)
def _no_spacing(monkeypatch):
    monkeypatch.setattr("app.taiwan.institutional_margin_refresh.INTER_REQUEST_DELAY_SEC", 0)


def _institutional(tmp_path: Path, provider: _Provider) -> tuple[TaiwanInstitutionalRefreshService, TaiwanInstitutionalStore]:
    store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    store.write_batch(pl.DataFrame([{"symbol": "2330.TWSE", "date": STORED, "foreign_net": 1}]), partition_date=STORED)
    service = TaiwanInstitutionalRefreshService(store=store, provider=provider, calendar=_Calendar())
    service._fetch_json = lambda url: {}
    return service, store


def _margin_service(tmp_path: Path, provider: _Provider) -> tuple[TaiwanMarginRefreshService, TaiwanMarginStore]:
    store = TaiwanMarginStore(data_dir=tmp_path / "margin")
    store.write_batch(pl.DataFrame([{"symbol": "2330.TWSE", "date": STORED, "margin_balance": 1}]), partition_date=STORED)
    service = TaiwanMarginRefreshService(store=store, provider=provider, calendar=_Calendar())
    service._fetch_json = lambda url: {}
    return service, store


def test_institutional_stops_at_a_failed_new_day(tmp_path):
    service, store = _institutional(tmp_path, _Provider(_flow, raise_on={FAILING}))

    res = service.refresh_dates(DAYS[0], DAYS[-1])

    assert store.available_dates() == [STORED, DAYS[0]]
    assert [f["date"] for f in res["failed_dates"]] == [str(FAILING)]


def test_institutional_refetches_the_failed_day_on_the_next_run(tmp_path):
    provider = _Provider(_flow, raise_on={FAILING})
    service, store = _institutional(tmp_path, provider)
    service.refresh_dates(DAYS[0], DAYS[-1])
    provider.raise_on.clear()

    service.refresh_dates(max(store.available_dates()), DAYS[-1])

    assert store.available_dates() == [STORED, *DAYS]


def test_margin_stops_at_a_failed_new_day(tmp_path):
    service, store = _margin_service(tmp_path, _Provider(_margin, raise_on={FAILING}))

    res = service.refresh_dates(DAYS[0], DAYS[-1])

    assert store.available_dates() == [STORED, DAYS[0]]
    assert [f["date"] for f in res["failed_dates"]] == [str(FAILING)]


def test_margin_never_stores_a_day_with_one_exchange(tmp_path):
    service, store = _margin_service(tmp_path, _Provider(_margin, tpex_empty={FAILING}))

    res = service.refresh_dates(DAYS[0], DAYS[-1])

    assert store.available_dates() == [STORED, DAYS[0]]
    assert [f["date"] for f in res["failed_dates"]] == [str(FAILING)]
    assert "TPEx" in res["failed_dates"][0]["error"]


def test_margin_refetches_a_one_exchange_day_on_the_next_run(tmp_path):
    provider = _Provider(_margin, tpex_empty={FAILING})
    service, store = _margin_service(tmp_path, provider)
    service.refresh_dates(DAYS[0], DAYS[-1])
    provider.tpex_empty.clear()

    service.refresh_dates(max(store.available_dates()), DAYS[-1])

    assert store.available_dates() == [STORED, *DAYS]
    day = pl.read_parquet(tmp_path / "margin" / f"date={FAILING.isoformat()}" / "part.parquet")
    assert sorted(day["symbol"]) == ["2330.TWSE", "8069.TPEX"]


def _clock(monkeypatch, now: datetime) -> None:
    monkeypatch.setattr("app.taiwan.institutional_margin_refresh.taipei_now", lambda: now, raising=False)


def test_margin_waits_for_the_publication_time_of_the_session(tmp_path, monkeypatch):
    session = DAYS[-1]
    _clock(monkeypatch, datetime(session.year, session.month, session.day, 17, 0, tzinfo=TAIPEI_TZ))
    provider = _Provider(_margin)
    service, store = _margin_service(tmp_path, provider)

    res = service.refresh_dates(session, session)

    assert provider.fetched == []
    assert res["dates_fetched"] == 0
    assert session not in store.available_dates()


def test_margin_fetches_the_session_once_it_is_published(tmp_path, monkeypatch):
    session = DAYS[-1]
    _clock(monkeypatch, datetime(session.year, session.month, session.day, 21, 45, tzinfo=TAIPEI_TZ))
    provider = _Provider(_margin)
    service, store = _margin_service(tmp_path, provider)

    service.refresh_dates(session, session)

    assert provider.fetched == [session]
    assert session in store.available_dates()
