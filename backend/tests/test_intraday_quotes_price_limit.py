"""GET /api/intraday/quotes — the price-limit fields behind the quote panel.

price_limit_pct is a decimal (0.1 is 10%); is_no_limit marks an instrument without limits; a
null percentage without is_no_limit means the rule is unknown, never a silent 10%.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture(autouse=True)
def _cached_security_master(monkeypatch) -> None:
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache(), "tests need the security master cache under DATA_DIR/taiwan"
    monkeypatch.setattr(universe, "_default_master", master)


def _quotes(symbol: str, prev_close: float) -> dict:
    quote = MagicMock()
    quote.symbol = symbol
    quote.prev_close = prev_close
    quote.to_dict.return_value = {"symbol": symbol, "name": symbol, "prev_close": prev_close}
    service = MagicMock()
    service.get_quotes.return_value = {symbol: quote}
    with patch("app.api.intraday.get_realtime_service", return_value=service):
        res = client.get("/api/intraday/quotes", params={"symbols": symbol})
    assert res.status_code == 200
    return res.json()["quotes"][0]


def _limit_fields(row: dict) -> tuple:
    return row["price_limit_pct"], row["is_no_limit"], row["limit_up"], row["limit_down"]


def test_an_ordinary_stock_gets_tick_aligned_limits():
    assert _limit_fields(_quotes("2454.TWSE", 5180.0)) == (0.1, False, 5695.0, 4665.0)


def test_a_foreign_component_etf_has_no_limit():
    assert _limit_fields(_quotes("00885.TWSE", 30.0)) == (None, True, None, None)


def test_an_unconfirmed_etf_is_unknown():
    assert _limit_fields(_quotes("00998A.TPEX", 20.0)) == (None, False, None, None)


def test_one_unknown_symbol_leaves_the_others_intact():
    prev_closes = {"2454.TWSE": 5180.0, "00998A.TPEX": 20.0, "00885.TWSE": 30.0, "0050.TWSE": 111.85}
    quotes = {}
    for symbol, prev_close in prev_closes.items():
        quote = MagicMock()
        quote.symbol = symbol
        quote.prev_close = prev_close
        quote.to_dict.return_value = {"symbol": symbol, "name": symbol, "prev_close": prev_close}
        quotes[symbol] = quote
    service = MagicMock()
    service.get_quotes.return_value = quotes
    with patch("app.api.intraday.get_realtime_service", return_value=service):
        res = client.get("/api/intraday/quotes", params={"symbols": ",".join(prev_closes)})

    assert res.status_code == 200
    rows = {row["symbol"]: _limit_fields(row) for row in res.json()["quotes"]}
    assert rows == {
        "2454.TWSE": (0.1, False, 5695.0, 4665.0),
        "00998A.TPEX": (None, False, None, None),
        "00885.TWSE": (None, True, None, None),
        "0050.TWSE": (0.1, False, 123.0, 100.7),
    }


def test_search_marks_an_unconfirmed_etf_as_unknown():
    res = client.get("/api/intraday/taiwan/search", params={"q": "00998A"})

    assert res.status_code == 200
    hit = next(item for item in res.json()["results"] if item["symbol"] == "00998A.TPEX")
    assert (hit["price_limit_pct"], hit["is_no_limit"]) == (None, False)


def test_a_new_listing_quoted_against_its_offer_price_has_no_limit(tmp_path, monkeypatch):
    """On its listing session 7799.TWSE quotes the offer price as the previous close; no limit applies."""
    from dataclasses import replace
    from datetime import date

    import polars as pl

    from app.config import settings
    from app.taiwan import universe
    from app.taiwan.daily_store import TaiwanDailyStore

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    TaiwanDailyStore(tmp_path / "taiwan" / "daily").write_batch(pl.DataFrame([
        {"symbol": "2330.TWSE", "date": "2026-09-09", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
         "volume": 1.0, "amount": 1.0, "quote_ts": None}
    ]))
    master = universe.get_security_master()
    listing = replace(master.get_instrument("2454.TWSE"), symbol="7799.TWSE", code="7799", listing_date="20260910")
    monkeypatch.setitem(master._instruments, listing.symbol, listing)

    quote = MagicMock()
    quote.symbol = listing.symbol
    quote.prev_close = 100.0
    quote.trade_date = date(2026, 9, 10)
    quote.to_dict.return_value = {"symbol": listing.symbol, "name": listing.symbol, "prev_close": 100.0}
    service = MagicMock()
    service.get_quotes.return_value = {listing.symbol: quote}
    with patch("app.api.intraday.get_realtime_service", return_value=service):
        res = client.get("/api/intraday/quotes", params={"symbols": listing.symbol})

    assert res.status_code == 200
    assert _limit_fields(res.json()["quotes"][0]) == (None, True, None, None)
