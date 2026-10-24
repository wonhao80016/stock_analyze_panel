"""Exchange-aware missing chip rows, one-sided institutional writes, alert prices and margin date stamps."""
from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from app.config import settings
from app.services import alert_store
from app.taiwan import detail_service
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment.margin import TpexMarginAdapter, TwseMarginAdapter
from app.taiwan.enrichment.models import InstitutionalFlow
from app.taiwan.institutional_margin_refresh import TaiwanInstitutionalRefreshService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.symbol import parse_symbol

SESSIONS = [date(2026, 10, d) for d in (15, 16, 19, 20, 21, 22)]
PREV, DAY = SESSIONS[-2], SESSIONS[-1]


class _ChipStore:
    def __init__(self, rows_by_date: dict[date, list[dict]]) -> None:
        self.rows_by_date = rows_by_date

    def available_dates(self):
        return sorted(self.rows_by_date)

    def read_range(self, symbols, start, end):
        recs = [
            {"date": d, **row}
            for d, rows in self.rows_by_date.items() if start <= d <= end
            for row in rows if not symbols or row["symbol"] in symbols
        ]
        return pl.DataFrame(recs) if recs else pl.DataFrame()


def _flow_row(symbol: str, foreign_net: int) -> dict:
    return {"symbol": symbol, "foreign_net": foreign_net, "investment_trust_net": 0, "dealer_net": 0,
            "computed_net": foreign_net, "official_net": foreign_net}


def _margin_row(symbol: str, balance: int) -> dict:
    return {"symbol": symbol, "margin_balance": balance, "margin_change": 0, "short_balance": 0,
            "short_change": 0, "short_margin_ratio": None}


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(detail_service, "taipei_now", lambda: datetime(2026, 10, 22, 22, 0))
    return TaiwanStockDetailService()


def _twse_day_missing() -> dict[date, list[dict]]:
    rows = {d: [_flow_row("1410.TWSE", 100 * (i + 1)), _flow_row("8069.TPEX", 1)] for i, d in enumerate(SESSIONS[:-1])}
    rows[DAY] = [_flow_row("8069.TPEX", 1)]
    return rows


def test_store_fallback_skips_a_day_whose_exchange_table_is_missing(svc, monkeypatch):
    store = _ChipStore(_twse_day_missing())
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)

    out = svc._institutional_from_store(parse_symbol("1410.TWSE"))

    assert out is not None
    assert out.meta.trade_date == PREV.isoformat()
    assert out.foreign_net == 500


def test_five_day_sum_is_unknown_when_a_window_day_lacks_the_exchange(svc, monkeypatch):
    store = _ChipStore(_twse_day_missing())
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)

    sums = svc._institutional_5d("1410.TWSE", DAY)

    assert sums == {"foreign_net": None, "investment_trust_net": None, "dealer_net": None}


def test_margin_store_fallback_skips_a_day_whose_exchange_table_is_missing(svc, monkeypatch):
    store = _ChipStore({PREV: [_margin_row("1410.TWSE", 9000), _margin_row("8069.TPEX", 1)],
                        DAY: [_margin_row("8069.TPEX", 1)]})
    monkeypatch.setattr("app.taiwan.margin_store.TaiwanMarginStore", lambda *a, **k: store)

    out = svc._margin_from_store(parse_symbol("1410.TWSE"))

    assert out is not None
    assert out.status == "available"
    assert out.margin_balance == 9000
    assert out.meta.trade_date == PREV.isoformat()


class _Calendar:
    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5


def _flow(symbol: str, d: date) -> InstitutionalFlow:
    return InstitutionalFlow(
        symbol=symbol, trade_date=d, foreign_buy=1000, foreign_sell=500, foreign_net=500,
        investment_trust_buy=0, investment_trust_sell=0, investment_trust_net=0,
        dealer_buy=0, dealer_sell=0, dealer_net=0,
    )


class _Adapter:
    def __init__(self, symbol: str, empty_on: set[date]) -> None:
        self.symbol, self.empty_on = symbol, empty_on

    def build_url(self, d: date) -> str:
        return f"mock://{self.symbol}/{d}"

    def parse_payload(self, payload, d: date, url: str):
        return [] if d in self.empty_on else [_flow(self.symbol, d)]


class _Provider:
    def __init__(self, tpex_empty: set[date]) -> None:
        self.twse = _Adapter("2330.TWSE", set())
        self.tpex = _Adapter("8069.TPEX", tpex_empty)


def test_institutional_refresh_never_stores_a_new_day_with_one_exchange(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.taiwan.institutional_margin_refresh.INTER_REQUEST_DELAY_SEC", 0)
    stored = date(2026, 10, 14)
    store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    store.write_batch(pl.DataFrame([{"symbol": "2330.TWSE", "date": stored, "foreign_net": 1}]), partition_date=stored)
    service = TaiwanInstitutionalRefreshService(store=store, provider=_Provider({SESSIONS[1]}), calendar=_Calendar())
    service._fetch_json = lambda url: {}

    res = service.refresh_dates(SESSIONS[0], SESSIONS[3])

    assert store.available_dates() == [stored, SESSIONS[0]]
    assert [f["date"] for f in res["failed_dates"]] == [str(SESSIONS[1])]
    assert "TPEx" in res["failed_dates"][0]["error"]
    assert res["stopped_at"] == str(SESSIONS[1])


def test_alert_without_a_price_keeps_the_list(svc, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    now = int(time.time() * 1000)
    alert_store.append_many(tmp_path, [
        {"ts": now, "symbol": "1410.TWSE", "rule_id": "r1", "price": None, "message": "no price"},
        {"ts": now - 1000, "symbol": "1410.TWSE", "rule_id": "r2", "price": 24.3, "message": "priced"},
    ])

    alerts = svc._aggregate_recent_alerts("1410.TWSE")

    assert [(a.message, a.trigger_price) for a in alerts] == [("no price", None), ("priced", 24.3)]


def _twse_margin_row(code: str) -> list[str]:
    return [code, "name", "1", "2", "0", "10", "11", "", "0", "1", "0", "3", "4", "", "", ""]


def _tpex_margin_row(code: str) -> list[str]:
    return [code, "name", "10", "1", "2", "0", "11", "", "", "", "3", "1", "0", "0", "4"]


def test_twse_margin_for_another_date_is_rejected():
    payload = {"stat": "OK", "date": "20261021", "tables": [{"date": "115年10月21日", "data": [_twse_margin_row("2330")]}]}

    assert TwseMarginAdapter().parse_payload(payload, DAY, "u") == []


def test_twse_margin_for_the_requested_date_is_parsed():
    payload = {"stat": "OK", "date": "20261022", "tables": [{"data": [_twse_margin_row("2330")]}]}

    assert [m.symbol for m in TwseMarginAdapter().parse_payload(payload, DAY, "u")] == ["2330.TWSE"]


def test_tpex_margin_for_another_date_is_rejected():
    payload = {"stat": "ok", "date": "20261021", "tables": [{"date": "115/10/21", "data": [_tpex_margin_row("8069")]}]}

    assert TpexMarginAdapter().parse_payload(payload, DAY, "u") == []


def test_tpex_margin_for_the_requested_date_is_parsed():
    payload = {"stat": "ok", "date": "20261022", "tables": [{"date": "115/10/22", "data": [_tpex_margin_row("8069")]}]}

    assert [m.symbol for m in TpexMarginAdapter().parse_payload(payload, DAY, "u")] == ["8069.TPEX"]
