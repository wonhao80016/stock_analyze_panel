"""Yahoo fallback quotes take the open from the chart bar, never from the day high."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.yahoo_provider import YahooRealtimeProvider

pytestmark = pytest.mark.usefixtures("deny_network")

QUOTE_TIME = int(datetime(2026, 10, 8, 10, 15, tzinfo=TAIPEI_TZ).timestamp())
BAR_TIME = int(datetime(2026, 10, 8, 9, 0, tzinfo=TAIPEI_TZ).timestamp())
PREVIOUS_BAR_TIME = int(datetime(2026, 10, 7, 9, 0, tzinfo=TAIPEI_TZ).timestamp())


def _payload(indicators=None, timestamps=None):
    result = {
        "meta": {
            "regularMarketPrice": 1015.0,
            "chartPreviousClose": 1000.0,
            "regularMarketVolume": 12_345_000,
            "regularMarketTime": QUOTE_TIME,
            "regularMarketDayHigh": 1020.0,
            "regularMarketDayLow": 995.0,
            "shortName": "TSMC",
        }
    }
    if indicators is not None:
        result["indicators"] = indicators
    if timestamps is not None:
        result["timestamp"] = timestamps
    return {"chart": {"result": [result]}}


def _fetch(payload):
    return YahooRealtimeProvider().fetch_single_quote("2330.TWSE", mock_response_json=payload)


def test_open_comes_from_the_bar_open():
    q = _fetch(_payload({"quote": [{"open": [1002.0], "high": [1020.0]}]}, [BAR_TIME]))

    assert q.open == 1002.0
    assert q.high == 1020.0


def test_open_uses_the_last_non_null_bar():
    q = _fetch(
        _payload(
            {"quote": [{"open": [998.0, 1002.0, None]}]}, [PREVIOUS_BAR_TIME, BAR_TIME, BAR_TIME]
        )
    )

    assert q.open == 1002.0


@pytest.mark.parametrize(
    "indicators",
    [
        None,
        {},
        {"quote": []},
        {"quote": [{}]},
        {"quote": [{"open": []}]},
        {"quote": [{"open": [None]}]},
    ],
)
def test_missing_bar_open_is_none_not_the_day_high(indicators):
    q = _fetch(_payload(indicators, [BAR_TIME]))

    assert q.open is None
    assert q.high == 1020.0


def test_previous_session_bar_open_is_not_today_open():
    q = _fetch(_payload({"quote": [{"open": [998.0]}]}, [PREVIOUS_BAR_TIME]))

    assert q.open is None


@pytest.mark.parametrize("timestamps", [None, [], [BAR_TIME]])
def test_open_without_a_timestamp_for_every_bar_is_none(timestamps):
    q = _fetch(_payload({"quote": [{"open": [998.0, 1002.0]}]}, timestamps))

    assert q.open is None
