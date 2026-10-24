"""Missing chip rows mean zero or unknown for that day, never the previous day (B190, B196, B197)."""
from __future__ import annotations

import io
import json
from datetime import date, datetime

import polars as pl
import pytest

from app.taiwan import detail_service
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment.institutional import (
    TaiwanInstitutionalProvider,
    TpexInstitutionalAdapter,
    TwseInstitutionalAdapter,
)
from app.taiwan.enrichment.models import MarginTrading, SourceMeta
from app.taiwan.symbol import parse_symbol

PREV, DAY = date(2026, 10, 21), date(2026, 10, 22)


def _t86_row(code: str, foreign_net: int) -> list[str]:
    buy = max(foreign_net, 0)
    sell = max(-foreign_net, 0)
    return [code, "name", f"{buy:,}", f"{sell:,}", f"{foreign_net:,}", "0", "0", "0",
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", f"{foreign_net:,}"]


def _t86(day: date, rows: list[list[str]]) -> dict:
    return {"stat": "OK", "date": day.strftime("%Y%m%d"), "data": rows}


def _tpex_row(code: str) -> list[str]:
    return [code, "name"] + ["0"] * 22


class TestAdapterDayFlag:
    def test_table_without_the_symbol_is_present(self):
        day = TwseInstitutionalAdapter().parse_day(
            _t86(DAY, [_t86_row("2330", 1000)]), DAY, "u", target_code="1410",
        )
        assert day.table_present is True
        assert day.flows == []

    def test_no_table_is_absent(self):
        day = TwseInstitutionalAdapter().parse_day({"stat": "no data"}, DAY, "u", target_code="1410")
        assert day.table_present is False
        assert day.flows == []

    def test_twse_response_for_another_date_is_not_stamped_with_the_request_date(self):
        payload = _t86(PREV, [_t86_row("2330", 1000)])

        assert TwseInstitutionalAdapter().parse_payload(payload, DAY, "u") == []
        assert TwseInstitutionalAdapter().parse_day(payload, DAY, "u").table_present is False

    def test_tpex_response_for_another_date_is_rejected(self):
        payload = {"stat": "ok", "date": "20261021", "tables": [{"date": "115/10/21", "data": [_tpex_row("8069")]}]}

        assert TpexInstitutionalAdapter().parse_payload(payload, DAY, "u") == []

    def test_tpex_response_for_the_requested_date_is_parsed(self):
        payload = {"stat": "ok", "date": "20261022", "tables": [{"date": "115/10/22", "data": [_tpex_row("8069")]}]}

        flows = TpexInstitutionalAdapter().parse_payload(payload, DAY, "u")

        assert [f.symbol for f in flows] == ["8069.TPEX"]


def _serve(monkeypatch, payloads: dict[str, dict]) -> list[str]:
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append(url)
        for key, payload in payloads.items():
            if key in url:
                return io.BytesIO(json.dumps(payload).encode("utf-8"))
        return io.BytesIO(json.dumps({"stat": "no data"}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def test_fetch_live_day_reports_zero_flows_for_a_symbol_absent_from_t86(monkeypatch):
    _serve(monkeypatch, {"date=20261022": _t86(DAY, [_t86_row("2330", 1000)])})

    flows = TaiwanInstitutionalProvider().fetch_live_day("TWSE", DAY, target_code="1410")

    assert len(flows) == 1
    flow = flows[0]
    assert flow.symbol == "1410.TWSE"
    assert flow.trade_date == DAY
    assert (flow.foreign_net, flow.investment_trust_net, flow.dealer_net, flow.computed_net) == (0, 0, 0, 0)
    assert flow.meta is not None and flow.meta.trade_date == DAY


def test_fetch_live_day_is_empty_without_a_t86_table(monkeypatch):
    _serve(monkeypatch, {})

    assert TaiwanInstitutionalProvider().fetch_live_day("TWSE", DAY, target_code="1410") == []


class _ChipStore:
    """Store double: partitions per date, rows for any symbols, honoring read_range(symbols, ...)."""

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


SESSIONS = [date(2026, 10, d) for d in (15, 16, 19, 20, 21, 22)]


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(detail_service, "taipei_now", lambda: datetime(2026, 10, 22, 22, 0))
    return TaiwanStockDetailService()


def test_live_institutional_shows_zero_today_instead_of_the_previous_day(svc, monkeypatch):
    calls = _serve(monkeypatch, {
        "date=20261022": _t86(DAY, [_t86_row("2330", 1000)]),
        "date=20261021": _t86(PREV, [_t86_row("1410", -2000)]),
    })
    store = _ChipStore({d: [_flow_row("2330.TWSE", 1)] for d in SESSIONS[:-1]}
                       | {PREV: [_flow_row("1410.TWSE", -2000)], DAY: [_flow_row("2330.TWSE", 1000)]})
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)
    monkeypatch.setattr(svc, "institutional_provider", TaiwanInstitutionalProvider())

    out = svc._aggregate_institutional(parse_symbol("1410.TWSE"))

    assert len(calls) == 1
    assert out.meta.trade_date == DAY.isoformat()
    assert (out.foreign_net, out.investment_trust_net, out.dealer_net, out.total_net) == (0, 0, 0, 0)
    assert out.foreign_net_5d == -2000


def test_store_fallback_treats_a_missing_row_on_the_latest_date_as_zero(svc, monkeypatch):
    store = _ChipStore({d: [_flow_row("1410.TWSE", 100 * (i + 1))] for i, d in enumerate(SESSIONS[:-1])}
                       | {DAY: [_flow_row("2330.TWSE", 1000)]})
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)

    out = svc._institutional_from_store(parse_symbol("1410.TWSE"))

    assert out is not None
    assert out.meta.trade_date == DAY.isoformat()
    assert (out.foreign_net, out.total_net) == (0, 0)
    assert out.foreign_net_5d == 200 + 300 + 400 + 500


def test_five_day_sum_counts_a_missing_end_row_as_zero(svc, monkeypatch):
    store = _ChipStore({d: [_flow_row("1410.TWSE", 10)] for d in SESSIONS[:-1]} | {DAY: [_flow_row("2330.TWSE", 1)]})
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: store)

    sums = svc._institutional_5d("1410.TWSE", DAY)

    assert sums == {"foreign_net": 40, "investment_trust_net": 0, "dealer_net": 0}


def _margin(code: str, day: date, margin_balance: int) -> MarginTrading:
    return MarginTrading(
        symbol=f"{code}.TWSE", trade_date=day, unit="shares", source_unit="lots", lot_multiplier=1000,
        margin_previous_balance=margin_balance, margin_buy=0, margin_sell=0, margin_cash_redemption=0,
        margin_balance=margin_balance, margin_change=0,
        short_previous_balance=0, short_sell=0, short_cover=0, short_stock_redemption=0,
        short_balance=0, short_change=0, short_margin_ratio=None,
        meta=SourceMeta(source="twse:mi_margn", source_url="u", fetched_at=datetime(2026, 10, 22, 21, 50),
                        trade_date=day, status="official"),
    )


class _MarginTables:
    def __init__(self, tables: dict[date, list[MarginTrading]]) -> None:
        self.tables = tables
        self.calls: list[date] = []

    def fetch_live_day(self, exchange, trade_date, target_code=None, timeout=10.0):
        self.calls.append(trade_date)
        rows = self.tables.get(trade_date, [])
        return [r for r in rows if target_code is None or r.symbol.split(".")[0] == target_code]


def test_live_margin_does_not_show_the_previous_day_when_today_lacks_the_symbol(svc, monkeypatch):
    provider = _MarginTables({DAY: [_margin("2330", DAY, 5000)], PREV: [_margin("1410", PREV, 9000)]})
    monkeypatch.setattr(svc, "margin_provider", provider)
    store = _ChipStore({PREV: [{"symbol": "1410.TWSE", "margin_balance": 9000, "margin_change": 0,
                                "short_balance": 0, "short_change": 0, "short_margin_ratio": None}]})
    monkeypatch.setattr("app.taiwan.margin_store.TaiwanMarginStore", lambda *a, **k: store)

    out = svc._aggregate_margin(parse_symbol("1410.TWSE"))

    assert provider.calls == [DAY]
    assert out.status == "unavailable"
    assert out.margin_balance is None
    assert out.meta.trade_date == DAY.isoformat()


def test_margin_store_fallback_does_not_carry_an_older_row_forward(svc, monkeypatch):
    row = {"margin_balance": 9000, "margin_change": 0, "short_balance": 0, "short_change": 0, "short_margin_ratio": None}
    store = _ChipStore({PREV: [{"symbol": "1410.TWSE", **row}], DAY: [{"symbol": "2330.TWSE", **row}]})
    monkeypatch.setattr("app.taiwan.margin_store.TaiwanMarginStore", lambda *a, **k: store)

    out = svc._margin_from_store(parse_symbol("1410.TWSE"))

    assert out is not None
    assert out.status == "unavailable"
    assert out.margin_balance is None
    assert out.meta.trade_date == DAY.isoformat()
