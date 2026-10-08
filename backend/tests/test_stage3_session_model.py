"""Session model around the 13:25-13:30 closing auction, built from real MIS samples of 2026-10-08.

MIS marks call-auction (trial match) quotes with ts=1 and a trial price pz / volume ps while z is
"-"; the closing result arrives as a ts=0 quote stamped t=13:30:00.
"""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import date, datetime
from datetime import time as dt_time
from pathlib import Path

import polars as pl
import pytest

from app.services import quote_service as qs_module
from app.services.quote_service import QuoteService
from app.taiwan.realtime import calendar, mis_provider
from app.taiwan.realtime import service as service_module
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar, get_market_status
from app.taiwan.realtime.mis_provider import TwseMisRealtimeProvider
from app.taiwan.realtime.models import RealtimeStatus
from app.taiwan.realtime.service import TaiwanRealtimeService

pytestmark = pytest.mark.usefixtures("deny_network")

FIXTURES = Path(__file__).parent / "fixtures" / "mis_2026_10_08"
SESSION = date(2026, 10, 8)
TSMC = "2330.TWSE"


def mis_sample(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["body"]


def only(body: dict, code: str) -> dict:
    return {"msgArray": [m for m in body["msgArray"] if m["c"] == code]}


def tsmc_entry(name: str) -> dict:
    return copy.deepcopy(only(mis_sample(name), "2330")["msgArray"][0])


def tsmc_continuous_1324() -> dict:
    """2330 at its last continuous-trading match before the closing auction."""
    item = tsmc_entry("145659.json")
    item.update(
        {
            "t": "13:24:58",
            "tlong": "1791440698000",
            "z": "2550.0000",
            "tv": "12",
            "v": "14124",
            "pz": "-",
            "ps": "-",
            "ts": "0",
        }
    )
    for key in ("ov", "oz", "ot", "fv"):
        item.pop(key, None)
    return {"msgArray": [item]}


def tsmc_closing_trial(*, with_last_trade: bool = False) -> dict:
    """2330 during the closing auction, assumed to mirror the opening auction (ts=1, pz/ps)."""
    item = tsmc_entry("145659.json")
    item.update(
        {
            "t": "13:26:58",
            "tlong": "1791440818000",
            "z": "2550.0000" if with_last_trade else "-",
            "tv": "-",
            "v": "14124",
            "pz": "2545.0000",
            "ps": "5000",
            "ts": "1",
        }
    )
    return {"msgArray": [item]}


def at(hh: int, mm: int, ss: int = 0, day: date = SESSION) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=TAIPEI_TZ)


@contextmanager
def wall_clock(now: datetime):
    """Pin every Taipei clock the realtime path reads to *now*."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(calendar, "taipei_now", lambda: now)
        mp.setattr(calendar, "taipei_today", lambda: now.date())
        mp.setattr(mis_provider, "taipei_now", lambda: now)
        mp.setattr(service_module, "taipei_now", lambda: now)
        mp.setattr(qs_module, "cn_today", lambda: now.date())
        yield


class SampleMis(TwseMisRealtimeProvider):
    def __init__(self, payload: dict) -> None:
        super().__init__()
        self.payload = payload

    def fetch_quotes(self, symbols, mock_response_json=None):
        return super().fetch_quotes(symbols, mock_response_json=self.payload)


class NoYahoo:
    def fetch_quotes(self, symbols):
        return {}


def realtime_service(payload: dict) -> TaiwanRealtimeService:
    return TaiwanRealtimeService(
        cache_ttl_seconds=0.0,
        trading_calendar=TaiwanTradingCalendar(known_trading_days={SESSION}),
        mis_provider=SampleMis(payload),
        yahoo_provider=NoYahoo(),
    )


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        (dt_time(8, 59, 59), "pre_open"),
        (dt_time(9, 0, 0), "open"),
        (dt_time(13, 24, 59), "open"),
        (dt_time(13, 25, 0), "closing_auction"),
        (dt_time(13, 27, 0), "closing_auction"),
        (dt_time(13, 30, 0), "closing_auction"),
        (dt_time(13, 30, 1), "post_close"),
        (dt_time(14, 30, 0), "post_close"),
    ],
)
def test_market_status_boundaries(clock: dt_time, expected: str):
    now = datetime.combine(SESSION, clock, tzinfo=TAIPEI_TZ)
    assert get_market_status(now).value == expected


def test_closing_auction_is_not_continuous_trading():
    with wall_clock(at(13, 24, 59)):
        assert QuoteService._is_continuous_trading() is True
    with wall_clock(at(13, 27)):
        assert QuoteService._is_continuous_trading() is False


def test_official_fallback_stays_blocked_through_the_closing_auction():
    from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider

    with wall_clock(at(13, 27)):
        assert TaiwanHybridProvider._official_fallback_allowed() is False
    with wall_clock(at(13, 30, 5)):
        assert TaiwanHybridProvider._official_fallback_allowed() is True


@pytest.mark.parametrize(
    ("sample", "wall"),
    [
        ("084501.json", at(8, 45, 1)),
        ("090011.json", at(9, 0, 11)),
    ],
)
def test_opening_trial_quotes_never_become_trades(sample: str, wall: datetime):
    """ts=1 carries a trial price pz with z="-"; pz must never be published as a trade."""
    with wall_clock(wall):
        quotes = TwseMisRealtimeProvider().fetch_quotes(
            [TSMC], mock_response_json=only(mis_sample(sample), "2330")
        )
    quote = quotes.get(TSMC)
    assert quote is None or (quote.last_price is None and not quote.source_meta.is_realtime)


def test_closing_trial_quote_with_a_last_trade_is_not_live():
    """If the closing auction keeps z, the quote is still a trial snapshot, not a new trade."""
    with wall_clock(at(13, 27)):
        quotes = TwseMisRealtimeProvider().fetch_quotes(
            [TSMC],
            mock_response_json=tsmc_closing_trial(with_last_trade=True),
        )
    quote = quotes[TSMC]
    assert quote.last_price == 2550.0
    assert quote.source_meta.status == RealtimeStatus.TRIAL_MATCH.value
    assert quote.source_meta.is_realtime is False


def test_trial_quotes_never_reach_the_live_frame():
    from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider

    for payload in (tsmc_closing_trial(), tsmc_closing_trial(with_last_trade=True)):
        with wall_clock(at(13, 27)), pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                service_module, "get_realtime_service", lambda p=payload: realtime_service(p)
            )
            assert TaiwanHybridProvider()._live_quote_frame([TSMC]).is_empty()


def test_last_continuous_quote_is_not_stale_during_the_closing_auction():
    """MIS stops updating t at ~13:25 for the auction; at 13:27 that quote is still current."""
    with wall_clock(at(13, 27)):
        quote = realtime_service(tsmc_continuous_1324()).get_quotes([TSMC])[TSMC]
    assert quote.source_meta.is_stale is False
    assert quote.source_meta.is_realtime is True
    assert quote.source_meta.status == RealtimeStatus.REALTIME.value


def test_continuous_quote_still_goes_stale_during_open():
    """The auction exemption must not switch off staleness in continuous trading."""
    with wall_clock(at(13, 24, 59)):
        payload = tsmc_continuous_1324()
        payload["msgArray"][0].update({"t": "13:22:00", "tlong": "1791440520000"})
        quote = realtime_service(payload).get_quotes([TSMC])[TSMC]
    assert quote.source_meta.is_stale is True


def make_monitor_service() -> tuple[QuoteService, list[int]]:
    svc = QuoteService()
    calls: list[int] = []

    def counting_enriched_today():
        calls.append(1)
        return pl.DataFrame(), None

    svc.get_enriched_today = counting_enriched_today
    return svc, calls


def test_monitors_pause_during_the_closing_auction():
    svc, calls = make_monitor_service()
    with wall_clock(at(13, 27)):
        svc._track_closing_quotes([{"symbol": TSMC, "status": "realtime", "volume": 1000.0}])
        svc._quote_source_realtime = True
        svc._evaluate_monitors(pl.DataFrame(), None)
    assert calls == []


def test_monitors_evaluate_once_on_the_closing_result():
    svc, calls = make_monitor_service()
    with wall_clock(at(13, 24, 50)):
        svc._track_closing_quotes([{"symbol": TSMC, "status": "realtime", "volume": 1000.0}])
    with wall_clock(at(13, 30, 5)):
        records = [{"symbol": TSMC, "status": RealtimeStatus.CLOSE_FINAL.value}]
        svc._track_closing_quotes(records)
        svc._quote_source_realtime = QuoteService._records_realtime_status(records)
        svc._evaluate_monitors(pl.DataFrame(), None)
        svc._evaluate_monitors(pl.DataFrame(), None)
    assert calls == [1]


def test_monitors_wait_for_every_live_symbol_before_the_closing_evaluation():
    svc, calls = make_monitor_service()
    with wall_clock(at(13, 24, 50)):
        svc._track_closing_quotes(
            [
                {"symbol": TSMC, "status": "realtime", "volume": 1000.0},
                {"symbol": "2317.TWSE", "status": "realtime", "volume": 1000.0},
            ]
        )
    with wall_clock(at(13, 30, 5)):
        records = [{"symbol": TSMC, "status": RealtimeStatus.CLOSE_FINAL.value}]
        svc._track_closing_quotes(records)
        svc._quote_source_realtime = QuoteService._records_realtime_status(records)
        svc._evaluate_monitors(pl.DataFrame(), None)
    assert calls == []
