"""The detail page must not download the official chip tables on every poll (B049, B064)."""
from __future__ import annotations

import io
import json
import threading
import urllib.error
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan import detail_service
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment.institutional import TaiwanInstitutionalProvider
from app.taiwan.enrichment.margin import TaiwanMarginProvider
from app.taiwan.symbol import parse_symbol

PREV, DAY = date(2026, 10, 22), date(2026, 10, 23)
EARLIER = [date(2026, 10, d) for d in (16, 19, 20, 21)]


def _roc(d: date) -> str:
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def _t86(d: date, foreign_net: int = 1000) -> dict:
    row = ["2330", "n", f"{foreign_net}", "0", f"{foreign_net}"] + ["0"] * 13 + [f"{foreign_net}"]
    return {"stat": "OK", "date": d.strftime("%Y%m%d"), "data": [row]}


def _tpex_institutional(d: date) -> dict:
    return {"stat": "ok", "date": d.strftime("%Y%m%d"), "tables": [{"date": _roc(d), "data": [["8069", "n"] + ["0"] * 22]}]}


def _mi_margn(d: date, balance_lots: int = 105) -> dict:
    row = ["2330", "n", "10", "5", "0", "100", f"{balance_lots}", "0", "1", "2", "0", "10", "11", "0", "0", ""]
    return {"stat": "OK", "date": d.strftime("%Y%m%d"), "tables": [{"data": [["x"]]}, {"date": _roc(d), "data": [row]}]}


PUBLISHED = {
    "T86": _t86,
    "insti/dailyTrade": _tpex_institutional,
    "MI_MARGN": _mi_margn,
}


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _Exchange:
    """urlopen double: tables up to *published_through*, the exchange's "no data" reply after it."""

    def __init__(
        self,
        published_through: date = PREV,
        error: Exception | None = None,
        delay: float = 0.0,
        garbled: tuple[date, ...] = (),
    ) -> None:
        self.published_through = published_through
        self.error = error
        self.delay = delay
        self.garbled = garbled
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, req, timeout=None):
        with self._lock:
            self.calls.append(req.full_url)
        if self.delay:
            threading.Event().wait(self.delay)
        if self.error is not None:
            raise self.error
        stamp = req.full_url.split("date=")[1].replace("/", "")
        day = datetime.strptime(stamp, "%Y%m%d").date()
        if day in self.garbled:
            return io.BytesIO(b"<html>busy</html>")
        builder = next(b for key, b in PUBLISHED.items() if key in req.full_url)
        payload = builder(day) if day <= self.published_through else {"stat": "很抱歉，沒有符合條件的資料!"}  # noqa: RUF001
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def requested(self, day: date) -> int:
        return sum(day.strftime("%Y%m%d") in url for url in self.calls)


class _ChipStore:
    def __init__(self, rows_by_date: dict[date, list[dict]] | None = None) -> None:
        self.rows_by_date = rows_by_date or {}

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
    return {"symbol": symbol, "margin_balance": balance, "margin_change": 5000,
            "short_balance": 11000, "short_change": 1000, "short_margin_ratio": 10.48}


@pytest.fixture
def clock(monkeypatch):
    c = _Clock(datetime(2026, 10, 23, 10, 0))
    monkeypatch.setattr(detail_service, "taipei_now", c)
    return c


@pytest.fixture
def stores(monkeypatch):
    inst, margin = _ChipStore(), _ChipStore()
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: inst)
    monkeypatch.setattr("app.taiwan.margin_store.TaiwanMarginStore", lambda *a, **k: margin)
    return inst, margin


def _service(monkeypatch, exchange: _Exchange) -> TaiwanStockDetailService:
    monkeypatch.setattr("urllib.request.urlopen", exchange)
    svc = TaiwanStockDetailService()
    svc.institutional_provider = TaiwanInstitutionalProvider()
    svc.margin_provider = TaiwanMarginProvider()
    return svc


TSMC = parse_symbol("2330.TWSE")


def test_polling_reuses_the_tables_fetched_when_the_page_opened(monkeypatch, clock, stores):
    exchange = _Exchange()
    svc = _service(monkeypatch, exchange)

    first_inst = svc._aggregate_institutional(TSMC)
    first_margin = svc._aggregate_margin(TSMC)
    opened = len(exchange.calls)
    for _ in range(6):
        clock.now += timedelta(seconds=10)
        inst = svc._aggregate_institutional(TSMC)
        margin = svc._aggregate_margin(TSMC)

    assert opened == 4, "today's unpublished table and the previous session's, per dataset"
    assert len(exchange.calls) == opened
    assert (inst.foreign_net, inst.meta.trade_date) == (first_inst.foreign_net, PREV.isoformat()) == (1000, PREV.isoformat())
    assert (margin.margin_balance, margin.meta.trade_date) == (first_margin.margin_balance, PREV.isoformat())


def test_another_symbol_of_the_same_exchange_reuses_the_day_table(monkeypatch, clock, stores):
    exchange = _Exchange()
    svc = _service(monkeypatch, exchange)

    svc._aggregate_institutional(TSMC)
    calls = len(exchange.calls)
    other = svc._aggregate_institutional(parse_symbol("1410.TWSE"))

    assert len(exchange.calls) == calls
    assert other.meta.trade_date == PREV.isoformat()
    assert (other.foreign_net, other.total_net) == (0, 0), "a symbol absent from a present T86 has zero flows"


def test_a_stored_session_with_both_exchanges_is_read_without_network(monkeypatch, clock, stores):
    clock.now = datetime(2026, 10, 23, 22, 0)
    inst, margin = stores
    for d in [*EARLIER, PREV, DAY]:
        inst.rows_by_date[d] = [_flow_row("2330.TWSE", 700), _flow_row("8069.TPEX", 1)]
        margin.rows_by_date[d] = [_margin_row("2330.TWSE", 99000), _margin_row("8069.TPEX", 1)]
    exchange = _Exchange(published_through=DAY)
    svc = _service(monkeypatch, exchange)

    out_inst = svc._aggregate_institutional(TSMC)
    out_margin = svc._aggregate_margin(TSMC)

    assert exchange.calls == []
    assert (out_inst.status, out_inst.foreign_net, out_inst.meta.trade_date) == ("available", 700, DAY.isoformat())
    assert out_inst.foreign_net_5d == 700 * 5
    assert out_inst.meta.is_stale is False
    assert (out_margin.status, out_margin.margin_balance, out_margin.meta.trade_date) == ("available", 99000, DAY.isoformat())
    assert out_margin.short_margin_ratio == 10.48
    assert out_margin.meta.is_stale is False


def test_only_the_dates_missing_from_the_store_go_to_the_exchange(monkeypatch, clock, stores):
    inst, _ = stores
    inst.rows_by_date[PREV] = [_flow_row("2330.TWSE", 700), _flow_row("8069.TPEX", 1)]
    exchange = _Exchange()
    svc = _service(monkeypatch, exchange)

    out = svc._aggregate_institutional(TSMC)

    assert exchange.requested(DAY) == 1
    assert exchange.requested(PREV) == 0
    assert (out.foreign_net, out.meta.trade_date) == (700, PREV.isoformat())
    assert out.meta.is_stale is False, "an unpublished day does not make the stored session stale"


def test_a_stored_session_lacking_the_other_exchange_still_asks_the_exchange(monkeypatch, clock, stores):
    inst, _ = stores
    inst.rows_by_date[PREV] = [_flow_row("2330.TWSE", 700)]
    exchange = _Exchange()
    svc = _service(monkeypatch, exchange)

    out = svc._aggregate_institutional(TSMC)

    assert exchange.requested(PREV) == 1
    assert (out.foreign_net, out.meta.source) == (1000, "twse:t86")


def test_an_unpublished_table_is_asked_again_after_a_short_wait(monkeypatch, clock, stores):
    exchange = _Exchange()
    svc = _service(monkeypatch, exchange)

    svc._aggregate_institutional(TSMC)
    clock.now += timedelta(minutes=4)
    svc._aggregate_institutional(TSMC)
    assert exchange.requested(DAY) == 1

    clock.now += timedelta(minutes=2)
    exchange.published_through = DAY
    out = svc._aggregate_institutional(TSMC)

    assert exchange.requested(DAY) == 2
    assert exchange.requested(PREV) == 1, "the published previous session stays cached"
    assert out.meta.trade_date == DAY.isoformat()


def test_a_published_table_is_kept_until_the_taipei_day_ends(monkeypatch, clock, stores):
    exchange = _Exchange(published_through=DAY)
    svc = _service(monkeypatch, exchange)

    svc._aggregate_institutional(TSMC)
    clock.now = datetime(2026, 10, 23, 23, 59)
    svc._aggregate_institutional(TSMC)
    assert exchange.requested(DAY) == 1

    clock.now = datetime(2026, 10, 24, 0, 1)
    svc._aggregate_institutional(TSMC)
    assert exchange.requested(DAY) == 2


def test_a_failed_fetch_is_not_cached_and_backs_off(monkeypatch, clock, stores):
    exchange = _Exchange(error=TimeoutError("timed out"))
    svc = _service(monkeypatch, exchange)

    first = svc._aggregate_institutional(TSMC)
    opened = len(exchange.calls)
    clock.now += timedelta(seconds=10)
    svc._aggregate_institutional(TSMC)
    clock.now += timedelta(seconds=10)
    svc._aggregate_institutional(TSMC)

    assert first.status == "unavailable"
    assert opened == 1, "an unreachable exchange is not asked for the earlier sessions too"
    assert len(exchange.calls) == opened, "polls inside the backoff do not reach the exchange"

    exchange.error = None
    clock.now += timedelta(minutes=5)
    out = svc._aggregate_institutional(TSMC)

    assert len(exchange.calls) > opened
    assert (out.status, out.meta.trade_date) == ("available", PREV.isoformat())


def test_a_failure_of_one_exchange_does_not_hold_back_the_other(monkeypatch, clock, stores):
    exchange = _Exchange(error=TimeoutError("timed out"))
    svc = _service(monkeypatch, exchange)

    svc._aggregate_institutional(TSMC)
    exchange.error = None
    out = svc._aggregate_institutional(parse_symbol("8069.TPEX"))

    assert out.status == "available"
    assert out.meta.source == "tpex:daily_trade"


def test_concurrent_requests_share_one_fetch_per_table(monkeypatch, clock, stores):
    exchange = _Exchange(delay=0.2)
    svc = _service(monkeypatch, exchange)
    start = threading.Barrier(6)
    results: list = []

    def open_page():
        start.wait()
        results.append(svc._aggregate_institutional(TSMC))

    threads = [threading.Thread(target=open_page) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert exchange.requested(DAY) == 1
    assert exchange.requested(PREV) == 1
    assert [r.foreign_net for r in results] == [1000] * 6


def test_the_table_cache_is_bounded(clock):
    cache = detail_service._LiveTableCache()

    for offset in range(200):
        cache.get(("institutional", "TWSE", DAY - timedelta(days=offset)), lambda: ["row"])

    assert len(cache) <= detail_service._LIVE_TABLE_LIMIT


def test_a_stored_session_shown_because_a_newer_day_failed_is_stale(monkeypatch, clock, stores):
    inst, margin = stores
    inst.rows_by_date[PREV] = [_flow_row("2330.TWSE", 700), _flow_row("8069.TPEX", 1)]
    margin.rows_by_date[PREV] = [_margin_row("2330.TWSE", 99000), _margin_row("8069.TPEX", 1)]
    exchange = _Exchange(error=TimeoutError("timed out"))
    svc = _service(monkeypatch, exchange)

    for _ in range(2):
        out_inst = svc._aggregate_institutional(TSMC)
        out_margin = svc._aggregate_margin(TSMC)
        assert (out_inst.foreign_net, out_inst.meta.trade_date) == (700, PREV.isoformat())
        assert (out_inst.meta.status, out_inst.meta.is_stale) == ("stale", True)
        assert (out_margin.margin_balance, out_margin.meta.trade_date) == (99000, PREV.isoformat())
        assert (out_margin.meta.status, out_margin.meta.is_stale) == ("stale", True)
        clock.now += timedelta(seconds=10)


def test_a_garbled_reply_for_today_still_shows_the_previous_session_as_stale(monkeypatch, clock, stores):
    exchange = _Exchange(garbled=(DAY,))
    svc = _service(monkeypatch, exchange)

    out = svc._aggregate_institutional(TSMC)
    margin = svc._aggregate_margin(TSMC)

    assert (out.status, out.foreign_net, out.meta.trade_date) == ("available", 1000, PREV.isoformat())
    assert (out.meta.source, out.meta.status, out.meta.is_stale) == ("twse:t86", "stale", True)
    assert (margin.status, margin.meta.trade_date, margin.meta.is_stale) == ("available", PREV.isoformat(), True)

    clock.now += timedelta(seconds=10)
    svc._aggregate_institutional(TSMC)
    assert exchange.requested(DAY) == 2, "today's table backs off, one request per dataset so far"


@pytest.mark.parametrize("error", [
    TimeoutError("timed out"),
    urllib.error.URLError(ConnectionRefusedError("refused")),
    urllib.error.HTTPError("https://www.twse.com.tw", 503, "Service Unavailable", None, None),
], ids=["timeout", "connection", "http-5xx"])
@pytest.mark.parametrize("symbol", ["2330.TWSE", "8069.TPEX"])
def test_an_unreachable_exchange_costs_one_wait_per_dataset_and_falls_back_to_the_store(
    monkeypatch, clock, stores, error, symbol,
):
    inst, margin = stores
    older = EARLIER[0]
    inst.rows_by_date[older] = [_flow_row("2330.TWSE", 700), _flow_row("8069.TPEX", 7)]
    margin.rows_by_date[older] = [_margin_row("2330.TWSE", 99000), _margin_row("8069.TPEX", 9)]
    exchange = _Exchange(error=error)
    svc = _service(monkeypatch, exchange)
    sym = parse_symbol(symbol)

    out_inst = svc._aggregate_institutional(sym)
    out_margin = svc._aggregate_margin(sym)

    assert len(exchange.calls) == 2, "one wait for the institutional table and one for the margin table"
    assert (out_inst.meta.source, out_inst.meta.trade_date, out_inst.meta.is_stale) == (
        "taiwan_institutional_store", older.isoformat(), True)
    assert (out_margin.meta.source, out_margin.meta.trade_date, out_margin.meta.is_stale) == (
        "taiwan_margin_store", older.isoformat(), True)

    for _ in range(6):
        clock.now += timedelta(seconds=10)
        svc._aggregate_institutional(sym)
        svc._aggregate_margin(sym)
    assert len(exchange.calls) <= 4, "the exchange-wide backoff holds the polls back"
