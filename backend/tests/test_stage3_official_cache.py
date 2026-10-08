"""STOCK_DAY_ALL / TPEx quotes download budget for the Taiwan realtime fallback.

Before today's close the official snapshot is the previous session, which the realtime
gate discards, so the hybrid provider must not download it then. After the close every
caller in the process shares one download per TTL, and a failure is retried only after
a short negative TTL.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import date, datetime

import httpx
import polars as pl
import pytest

from app.services.quote_service import QuoteService
from app.taiwan.providers import official_provider
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.realtime import calendar
from app.taiwan.realtime import service as realtime_service
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.realtime.service import TaiwanRealtimeService

pytestmark = pytest.mark.usefixtures("deny_network")

TRADING_DAY = date(2026, 10, 8)
HOLIDAY = date(2026, 10, 9)
SYMBOLS = ["2330.TWSE", "8069.TPEX"]
TTL = 600.0
FAILURE_TTL = 30.0

TWSE_ROWS = [
    {
        "Date": "1151007",
        "Code": "2330",
        "TradeVolume": "1,234",
        "TradeValue": "2,980,000",
        "OpeningPrice": "2400",
        "HighestPrice": "2445",
        "LowestPrice": "2390",
        "ClosingPrice": "2420",
    }
]
TPEX_ROWS = [
    {
        "Date": "1151007",
        "SecuritiesCompanyCode": "8069",
        "Open": "100",
        "High": "101",
        "Low": "99",
        "Close": "100.5",
        "TradingShares": "1000",
        "TransactionAmount": "100500",
    }
]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _OfficialEndpoints:
    """Counts GETs per official URL; optionally fails or blocks the first request."""

    def __init__(self, *, fail_first: bool = False, block: threading.Event | None = None) -> None:
        self.gets: Counter[str] = Counter()
        self.fail_first = fail_first
        self.block = block
        self.entered = threading.Event()
        self._lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        with self._lock:
            self.gets[url] += 1
            first = self.gets[url] == 1
        self.entered.set()
        if self.block is not None:
            self.block.wait(timeout=5)
        if self.fail_first and first:
            return httpx.Response(503)
        if url == official_provider.TWSE_QUOTE_URL:
            return httpx.Response(200, json=TWSE_ROWS)
        if url == official_provider.TPEX_QUOTE_URL:
            return httpx.Response(200, json=TPEX_ROWS)
        return httpx.Response(404)

    @property
    def total(self) -> int:
        return sum(self.gets.values())


class _TimeoutMis:
    def fetch_quotes(self, symbols):
        raise httpx.ReadTimeout("MIS timed out")


class _EmptyYahoo:
    def fetch_quotes(self, symbols):
        return {}


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(official_provider, "monotonic", fake, raising=False)
    monkeypatch.setattr(official_provider, "_quote_payloads", {}, raising=False)
    return fake


@pytest.fixture
def daily_calls(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_get_daily(self, symbols, *args, **kwargs):
        calls.append(list(symbols))
        return pl.DataFrame()

    monkeypatch.setattr(TaiwanHybridProvider, "get_daily", fake_get_daily)
    return calls


def _at(monkeypatch, hour: int, minute: int, day: date = TRADING_DAY) -> None:
    now = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TAIPEI_TZ)
    monkeypatch.setattr(calendar, "taipei_now", lambda: now)
    monkeypatch.setattr(realtime_service, "taipei_now", lambda: now)


def _mis_times_out(monkeypatch) -> None:
    svc = TaiwanRealtimeService(
        mis_provider=_TimeoutMis(),
        yahoo_provider=_EmptyYahoo(),
        trading_calendar=TaiwanTradingCalendar(known_trading_days={TRADING_DAY}),
    )
    monkeypatch.setattr(realtime_service, "get_realtime_service", lambda: svc)


def _provider(endpoints: _OfficialEndpoints) -> TaiwanHybridProvider:
    provider = TaiwanHybridProvider()
    provider.official.client = httpx.Client(transport=httpx.MockTransport(endpoints.handler))
    return provider


def test_preopen_polls_never_download_the_official_snapshot(monkeypatch, clock, daily_calls):
    _at(monkeypatch, 8, 45)
    _mis_times_out(monkeypatch)
    endpoints = _OfficialEndpoints()
    provider = _provider(endpoints)

    for _ in range(100):
        assert provider.get_realtime(symbols=SYMBOLS).is_empty()

    assert endpoints.total == 0
    assert daily_calls == []


@pytest.mark.parametrize(("hour", "minute"), [(9, 0), (11, 15), (13, 29), (13, 30)])
def test_mis_timeouts_during_the_session_never_download_the_official_snapshot(
    monkeypatch,
    clock,
    daily_calls,
    hour,
    minute,
):
    _at(monkeypatch, hour, minute)
    _mis_times_out(monkeypatch)
    endpoints = _OfficialEndpoints()
    provider = _provider(endpoints)

    for _ in range(20):
        assert provider.get_realtime(symbols=SYMBOLS).is_empty()

    assert endpoints.total == 0
    assert daily_calls == []


def test_post_close_downloads_each_snapshot_once_per_ttl(monkeypatch, clock, daily_calls):
    _at(monkeypatch, 14, 0)
    _mis_times_out(monkeypatch)
    endpoints = _OfficialEndpoints()
    providers = [_provider(endpoints) for _ in range(3)]

    first_fetch = clock.now
    for i in range(60):
        clock.now = first_fetch + i * (TTL - 1) / 59
        frame = providers[i % 3].get_realtime(symbols=SYMBOLS)
        assert sorted(frame["symbol"].to_list()) == sorted(SYMBOLS)

    assert endpoints.gets == {
        official_provider.TWSE_QUOTE_URL: 1,
        official_provider.TPEX_QUOTE_URL: 1,
    }

    clock.now = first_fetch + TTL + 1
    providers[0].get_realtime(symbols=SYMBOLS)
    assert endpoints.gets == {
        official_provider.TWSE_QUOTE_URL: 2,
        official_provider.TPEX_QUOTE_URL: 2,
    }
    assert daily_calls == []


def test_concurrent_post_close_callers_share_one_download(monkeypatch, clock, daily_calls):
    _at(monkeypatch, 14, 0)
    _mis_times_out(monkeypatch)
    release = threading.Event()
    endpoints = _OfficialEndpoints(block=release)
    providers = [_provider(endpoints) for _ in range(8)]
    frames: list[pl.DataFrame] = []
    errors: list[BaseException] = []

    def poll(provider: TaiwanHybridProvider) -> None:
        try:
            frames.append(provider.get_realtime(symbols=["2330.TWSE"]))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=poll, args=(p,)) for p in providers]
    for thread in threads:
        thread.start()
    assert endpoints.entered.wait(timeout=5)
    time.sleep(0.2)
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(frames) == 8
    assert all(frame["symbol"].to_list() == ["2330.TWSE"] for frame in frames)
    assert endpoints.gets == {official_provider.TWSE_QUOTE_URL: 1}


def test_failed_download_is_retried_only_after_the_negative_ttl(monkeypatch, clock, daily_calls):
    _at(monkeypatch, 14, 0)
    _mis_times_out(monkeypatch)
    endpoints = _OfficialEndpoints(fail_first=True)
    provider = _provider(endpoints)

    assert provider.get_realtime(symbols=["2330.TWSE"]).is_empty()
    assert endpoints.gets[official_provider.TWSE_QUOTE_URL] == 1

    clock.now += FAILURE_TTL - 1
    assert provider.get_realtime(symbols=["2330.TWSE"]).is_empty()
    assert endpoints.gets[official_provider.TWSE_QUOTE_URL] == 1

    clock.now += 2
    frame = provider.get_realtime(symbols=["2330.TWSE"])
    assert frame["symbol"].to_list() == ["2330.TWSE"]
    assert endpoints.gets[official_provider.TWSE_QUOTE_URL] == 2


def test_holiday_poll_loop_never_asks_the_provider(monkeypatch, clock):
    _at(monkeypatch, 10, 0, day=HOLIDAY)
    monkeypatch.setattr(
        calendar, "_DEFAULT_CALENDAR", TaiwanTradingCalendar(known_holidays={HOLIDAY})
    )
    endpoints = _OfficialEndpoints()
    provider = _provider(endpoints)
    asked: list[list[str]] = []
    monkeypatch.setattr(
        QuoteService,
        "_taiwan_realtime_records",
        staticmethod(
            lambda symbols: (
                asked.append(symbols) or provider.get_realtime(symbols=symbols).to_dicts()
            )
        ),
    )

    svc = QuoteService()
    fetches: list[bool] = []
    real_fetch = svc._fetch_quotes

    def recording_fetch(*, final: bool = False) -> bool:
        fetches.append(final)
        return real_fetch(final=final)

    svc._fetch_quotes = recording_fetch
    svc._running = True
    svc._enabled = True
    svc._interval = 0.5

    def stop_after_one_poll(_seconds: float) -> None:
        svc._running = False

    monkeypatch.setattr(time, "sleep", stop_after_one_poll)
    svc._poll_loop()

    assert QuoteService._market_phase() == "closed"
    assert fetches == []
    assert asked == []
    assert endpoints.total == 0
