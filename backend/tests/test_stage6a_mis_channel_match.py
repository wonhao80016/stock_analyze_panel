"""MIS response items carry ch without the tse_/otc_ prefix; the market comes from ex or key."""
from __future__ import annotations

from app.taiwan.realtime.calendar import taipei_now
from app.taiwan.realtime.mis_provider import TwseMisRealtimeProvider


def _item(code: str, **extra) -> dict:
    now = taipei_now()
    return {
        "c": code,
        "n": code,
        "ch": f"{code}.tw",
        "z": "100.0",
        "y": "99.0",
        "d": now.strftime("%Y%m%d"),
        "t": "10:00:00",
        **extra,
    }


def test_market_prefix_comes_from_ex_when_codes_collide():
    quotes = TwseMisRealtimeProvider().fetch_quotes(
        ["2330.TWSE", "2330.TPEX"],
        mock_response_json={"msgArray": [_item("2330", ex="otc")]},
    )

    assert list(quotes) == ["2330.TPEX"]


def test_market_prefix_comes_from_key_without_ex():
    quotes = TwseMisRealtimeProvider().fetch_quotes(
        ["2330.TWSE", "2330.TPEX"],
        mock_response_json={"msgArray": [_item("2330", key="otc_2330.tw_20261024")]},
    )

    assert list(quotes) == ["2330.TPEX"]


def test_both_markets_of_one_code_map_to_their_own_symbol():
    quotes = TwseMisRealtimeProvider().fetch_quotes(
        ["2330.TWSE", "2330.TPEX"],
        mock_response_json={
            "msgArray": [_item("2330", ex="tse", z="10.0"), _item("2330", ex="otc", z="20.0")],
        },
    )

    assert quotes["2330.TWSE"].last_price == 10.0
    assert quotes["2330.TPEX"].last_price == 20.0


def test_code_only_fallback_needs_a_unique_code():
    provider = TwseMisRealtimeProvider()

    ambiguous = provider.fetch_quotes(
        ["2330.TWSE", "2330.TPEX"], mock_response_json={"msgArray": [_item("2330")]},
    )
    unique = provider.fetch_quotes(
        ["2330.TWSE", "8069.TPEX"], mock_response_json={"msgArray": [_item("2330")]},
    )

    assert ambiguous == {}
    assert list(unique) == ["2330.TWSE"]


def test_index_channel_matches():
    quotes = TwseMisRealtimeProvider().fetch_quotes(
        ["t00.TWSE", "o00.TPEX"],
        mock_response_json={"msgArray": [_item("t00", ex="tse"), _item("o00", ex="otc")]},
    )

    assert sorted(quotes) == ["o00.TPEX", "t00.TWSE"]


def test_item_naming_an_unrequested_market_is_dropped():
    quotes = TwseMisRealtimeProvider().fetch_quotes(
        ["2330.TPEX"], mock_response_json={"msgArray": [_item("2330", ex="tse")]},
    )

    assert quotes == {}
