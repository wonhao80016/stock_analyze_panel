"""Price-limit lines of the Taiwan minute chart (GET /api/kline/minute).

The backend owns the limit prices: the ordinary rule snaps to the Taiwan tick ladder of the
instrument's class, a corporate-action session takes the exchange's published limits, and an
instrument without a limit or with an unconfirmed rule gets no prices and no rate at all.
"""
from __future__ import annotations

from datetime import date
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.api import kline
from app.taiwan.corporate_actions import TaiwanCorporateActionStore

EVENT = date(2026, 9, 8)
BEFORE = date(2026, 9, 7)


@pytest.fixture(autouse=True)
def _cached_security_master(monkeypatch) -> None:
    """Serve the shared master from the configured cache; a master missing its cache fetches the directory."""
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache(), "tests need the security master cache under DATA_DIR/taiwan"
    monkeypatch.setattr(universe, "_default_master", master)


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(kline, "cn_today", lambda: date(2026, 10, 6))
    monkeypatch.setattr(kline.kline_sync, "fetch_minute_single", lambda *a, **k: pl.DataFrame())
    return tmp_path


def _event(symbol: str, pre: float, ref: float, up: float | None, down: float | None) -> dict:
    return {
        "symbol": symbol, "date": EVENT, "kind": "exdiv", "pre_close": pre, "ref_price": ref,
        "limit_up": up, "limit_down": down, "open_ref": ref, "ex_factor": pre / ref,
        "official_label": None, "provider": "twse", "source": "twse:twt49u", "source_url": "u",
        "retrieved_at": None, "trade_date": EVENT, "status": "official",
    }


def _publish_events(*events: dict) -> None:
    schema = {
        "symbol": pl.String, "date": pl.Date, "kind": pl.String, "pre_close": pl.Float64,
        "ref_price": pl.Float64, "limit_up": pl.Float64, "limit_down": pl.Float64, "open_ref": pl.Float64,
        "ex_factor": pl.Float64, "official_label": pl.String, "provider": pl.String, "source": pl.String,
        "source_url": pl.String, "retrieved_at": pl.Datetime("us", "Asia/Taipei"), "trade_date": pl.Date,
        "status": pl.String,
    }
    TaiwanCorporateActionStore().upsert(pl.DataFrame(list(events), schema=schema))


def _request(symbol: str, asset_type: str, raw_closes: dict[date, float]):
    """A repo whose forward-adjusted close is half the raw close, so only raw_close gives the right base."""
    daily = pl.DataFrame({
        "symbol": [symbol] * len(raw_closes),
        "date": list(raw_closes),
        "raw_close": list(raw_closes.values()),
        "close": [value / 2 for value in raw_closes.values()],
    })

    def daily_asset(_asset_type, sym, start, end, columns=None):
        out = daily.filter((pl.col("symbol") == sym) & (pl.col("date") >= start) & (pl.col("date") <= end))
        return out.select([c for c in columns if c in out.columns]) if columns else out

    repo = MagicMock()
    repo.resolve_asset_type.return_value = asset_type
    repo.get_instruments.return_value = pl.DataFrame(schema={"symbol": pl.String, "name": pl.String})
    repo.get_instruments_asset.return_value = pl.DataFrame(schema={"symbol": pl.String, "name": pl.String})
    repo.get_daily_asset.side_effect = daily_asset
    repo.get_minute.return_value = pl.DataFrame()
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))


def _minute(symbol: str, asset_type: str, raw_closes: dict[date, float], trade_date: date) -> dict:
    return kline.get_minute(_request(symbol, asset_type, raw_closes), symbol=symbol, trade_date=trade_date)


def test_an_ordinary_session_snaps_the_limits_to_the_tick_ladder(isolated_data):
    """2454 at 5180: 5698 / 4662 on the old cent rounding, 5695 / 4665 on the 5-dollar tick."""
    got = _minute("2454.TWSE", "stock", {BEFORE: 5180.0}, EVENT)

    assert got["prev_close"] == 5180.0
    assert got["price_limit"] == {"rate": 0.1, "limit_up": 5695.0, "limit_down": 4665.0, "source": "model"}


def test_a_price_above_ten_thousand_keeps_its_limits(isolated_data):
    """5274 at 20278 must not be dropped by a price ceiling."""
    got = _minute("5274.TPEX", "stock", {BEFORE: 20278.0}, EVENT)

    assert got["price_limit"] == {"rate": 0.1, "limit_up": 22305.0, "limit_down": 18255.0, "source": "model"}


def test_an_ex_dividend_session_takes_the_official_limits(isolated_data):
    """2379 goes ex-dividend on 2026-09-08 (729, reference 704): 774 / 634, not 774.4 / 633.6."""
    _publish_events(_event("2379.TWSE", 729.0, 704.0, 774.0, 634.0))

    got = _minute("2379.TWSE", "stock", {BEFORE: 729.0}, EVENT)

    assert got["prev_close"] == 704.0
    assert got["price_limit"] == {"rate": 0.1, "limit_up": 774.0, "limit_down": 634.0, "source": "official"}


def test_a_published_limit_wins_over_the_one_the_reference_price_would_give(isolated_data):
    """The 1400 reference alone gives 1540 / 1260; the exchange published 1500 / 1260."""
    _publish_events(_event("2454.TWSE", 1520.0, 1400.0, 1500.0, 1260.0))

    got = _minute("2454.TWSE", "stock", {BEFORE: 1520.0}, EVENT)

    assert got["price_limit"] == {"rate": 0.1, "limit_up": 1500.0, "limit_down": 1260.0, "source": "official"}


def test_an_event_without_published_limits_falls_back_to_the_reference_price(isolated_data):
    _publish_events(_event("2379.TWSE", 729.0, 704.0, None, None))

    got = _minute("2379.TWSE", "stock", {BEFORE: 729.0}, EVENT)

    assert got["price_limit"] == {"rate": 0.1, "limit_up": 774.0, "limit_down": 634.0, "source": "model"}


def test_an_etf_uses_the_etf_tick_ladder(isolated_data):
    """0050 at 111.85: 123.0 / 100.7 on the 0.05 ETF tick (the stock ladder gives 123.0 / 101.0)."""
    got = _minute("0050.TWSE", "etf", {BEFORE: 111.85}, EVENT)

    assert got["price_limit"] == {"rate": 0.1, "limit_up": 123.0, "limit_down": 100.7, "source": "model"}


@pytest.mark.parametrize("symbol", ["00885.TWSE", "00893.TWSE"])
def test_a_foreign_component_etf_has_no_limit(isolated_data, symbol):
    _publish_events(_event(symbol, 30.0, 29.5, 32.45, 26.55))

    got = _minute(symbol, "etf", {BEFORE: 30.0}, EVENT)

    assert got["price_limit"] == {"rate": None, "limit_up": None, "limit_down": None, "source": "no_limit"}


@pytest.mark.parametrize("symbol", ["00980T.TPEX", "9999.TWSE"])
def test_an_unconfirmed_rule_is_unknown_rather_than_ten_percent(isolated_data, symbol):
    """00980T has no confirmed underlying scope; 9999 is not in the security master."""
    got = _minute(symbol, "etf", {BEFORE: 20.0}, EVENT)

    assert got["price_limit"] == {"rate": None, "limit_up": None, "limit_down": None, "source": "unknown"}


def test_a_session_without_a_previous_close_keeps_only_the_rate(isolated_data):
    got = _minute("2454.TWSE", "stock", {}, EVENT)

    assert got["prev_close"] is None
    assert got["price_limit"] == {"rate": 0.1, "limit_up": None, "limit_down": None, "source": "rule"}


IPO_SESSIONS = (date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 15),
                date(2026, 9, 16), date(2026, 9, 17))


@pytest.fixture
def new_listing(isolated_data, monkeypatch):
    """7799.TWSE lists on 2026-09-10; its sessions 1-5 run through 09-16 on the stored calendar."""
    from dataclasses import replace

    from app.taiwan import universe
    from app.taiwan.daily_store import TaiwanDailyStore

    master = universe.get_security_master()
    template = master.get_instrument("2454.TWSE")
    listing = replace(template, symbol="7799.TWSE", code="7799", listing_date="20260910")
    monkeypatch.setitem(master._instruments, listing.symbol, listing)
    TaiwanDailyStore(isolated_data / "taiwan" / "daily").write_batch(pl.DataFrame([
        {"symbol": "2330.TWSE", "date": d.isoformat(), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
         "volume": 1.0, "amount": 1.0, "quote_ts": None}
        for d in IPO_SESSIONS
    ]))
    return lambda **changes: monkeypatch.setitem(master._instruments, listing.symbol, replace(listing, **changes))


def test_a_new_listing_has_no_limit_through_its_fifth_session(new_listing):
    for prev, session in pairwise(IPO_SESSIONS[1:6]):
        got = _minute("7799.TWSE", "stock", {prev: 100.0}, session)
        assert got["price_limit"] == {"rate": None, "limit_up": None, "limit_down": None, "source": "no_limit"}


def test_a_new_listing_takes_the_ordinary_limit_on_its_sixth_session(new_listing):
    got = _minute("7799.TWSE", "stock", {IPO_SESSIONS[5]: 220.0}, IPO_SESSIONS[6])

    assert got["price_limit"] == {"rate": 0.1, "limit_up": 242.0, "limit_down": 198.0, "source": "model"}


def test_a_stock_without_a_listing_date_has_an_unknown_rule(new_listing):
    new_listing(listing_date=None)

    got = _minute("7799.TWSE", "stock", {IPO_SESSIONS[5]: 220.0}, IPO_SESSIONS[6])

    assert got["price_limit"] == {"rate": None, "limit_up": None, "limit_down": None, "source": "unknown"}
