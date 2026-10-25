"""Minute reads by date and unscheduled-closure probing."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.repository import DataStore, KlineRepository
from app.services import preferences
from app.services import quote_service as qs_module
from app.services.quote_service import QuoteService
from app.taiwan.realtime import calendar
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.minute_recorder import MinuteBarRecorder
from tests.test_stage3_closing_auction import run_one_poll
from tests.test_stage3_session_model import SESSION, TSMC, at, wall_clock

pytestmark = pytest.mark.usefixtures("deny_network")


def test_minute_bars_are_read_by_date(tmp_path):
    day = date(2026, 9, 17)
    quote_time = datetime(2026, 9, 17, 10, 0, 5, tzinfo=TAIPEI_TZ)
    recorder = MinuteBarRecorder(data_dir=tmp_path)
    recorder.record(
        [
            {
                "symbol": TSMC,
                "close": 50.0,
                "volume": 1_000.0,
                "quote_ts": int(quote_time.timestamp() * 1000),
            }
        ],
        resolve_asset_type=lambda symbol: "stock",
    )
    recorder.flush(resolve_asset_type=lambda symbol: "stock")

    repo = KlineRepository(DataStore(tmp_path))
    bars = repo.get_minute_by_dates([TSMC], [day, date(2026, 9, 18)])

    assert bars["symbol"].to_list() == [TSMC]
    assert bars["close"].to_list() == [50.0]


@pytest.fixture
def probing(monkeypatch):
    """A QuoteService on an unconfirmed weekday counting the quote rounds it fetches."""
    state = SimpleNamespace(trading_day=None, fetches=0)
    monkeypatch.setattr(calendar, "is_trading_day", lambda d: state.trading_day)
    monkeypatch.setattr(preferences, "get_taiwan_market_closed_date", lambda: None)
    monkeypatch.setattr(preferences, "get_realtime_data_provider", lambda: "taiwan")
    svc = QuoteService()

    def fetch(*, final=False):
        state.fetches += 1
        return True

    svc._fetch_quotes = fetch

    def poll(now: datetime) -> int:
        before = state.fetches
        with wall_clock(now):
            run_one_poll(svc)
        return state.fetches - before

    state.svc, state.poll = svc, poll
    return state


def test_no_quote_by_0905_on_an_unconfirmed_weekday_slows_polling_to_five_minutes(probing):
    assert probing.poll(at(9, 4, 50)) == 1
    assert probing.poll(at(9, 5, 0)) == 1
    assert probing.poll(at(9, 5, 6)) == 0
    assert probing.poll(at(9, 9, 59)) == 0
    assert probing.poll(at(9, 10, 0)) == 1

    with wall_clock(at(9, 10, 1)):
        probing.svc._track_closing_quotes([{"symbol": TSMC, "status": "realtime", "volume": 1.0}])
    assert probing.poll(at(9, 10, 6)) == 1
    assert probing.poll(at(9, 10, 12)) == 1


def test_a_confirmed_trading_day_keeps_the_regular_poll(probing):
    probing.trading_day = True
    assert probing.poll(at(9, 30, 0)) == 1
    assert probing.poll(at(9, 30, 6)) == 1


def test_status_badge_honours_the_manual_closure(monkeypatch):
    from app.api import intraday

    monkeypatch.setattr(preferences, "get_taiwan_market_closed_date", lambda: SESSION.isoformat())
    monkeypatch.setattr(intraday, "taipei_now", lambda: at(10, 0, 0))
    monkeypatch.setattr(qs_module, "cn_today", lambda: SESSION)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    monkeypatch.setattr(intraday, "_get_quote_service", lambda request: None)

    with wall_clock(at(10, 0, 0)):
        result = intraday.status(request)

    assert result["taiwan_market"]["status"] == "non_trading_day"
