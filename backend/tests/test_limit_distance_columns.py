"""Tests for the Taiwan price-limit distance columns (near_upper/lower_limit
harmonization: distance_to_upper_limit / distance_to_lower_limit) added to the
intraday enriched pipeline (app.indicators.pipeline.add_limit_distance_columns).
"""
from __future__ import annotations

import polars as pl
import pytest

from app.indicators import pipeline
from app.taiwan.market_rules import PriceLimitModel, TickSizeClass
from app.taiwan.universe.models import TaiwanInstrument


@pytest.fixture(autouse=True)
def _clear_limit_profile_cache():
    pipeline.invalidate_taiwan_limit_profile_cache()
    yield
    pipeline.invalidate_taiwan_limit_profile_cache()


def _instrument(
    symbol: str,
    *,
    instrument_type: str = "stock",
    etf_category: str | None = None,
    classification_source: str | None = None,
    underlying_scope: str | None = None,
    leverage_multiplier: float = 1.0,
) -> TaiwanInstrument:
    return TaiwanInstrument(
        symbol=symbol,
        code=symbol.split(".")[0],
        exchange="TWSE",
        name="測試標的",
        instrument_type=instrument_type,
        listing_status="active",
        listing_date="2020/01/01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票" if instrument_type == "stock" else "ETF",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-09-17",
        etf_category=etf_category,
        classification_source=classification_source,
        underlying_scope=underlying_scope,
        leverage_multiplier=leverage_multiplier,
    )


class _FakeSecurityMaster:
    def __init__(self, instruments: dict[str, TaiwanInstrument]):
        self._instruments = instruments

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return self._instruments.get(symbol)


def _patch_master(monkeypatch, instruments: dict[str, TaiwanInstrument]) -> None:
    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master",
        lambda: _FakeSecurityMaster(instruments),
    )


def test_ordinary_stock_distance_matches_calc_limits_for_pct(monkeypatch):
    symbol = "2330.TWSE"
    _patch_master(monkeypatch, {symbol: _instrument(symbol, instrument_type="stock")})
    prev_close, close = 100.0, 108.0
    df = pl.DataFrame({"symbol": [symbol], "close": [close], "prev_close": [prev_close]})

    out = pipeline.add_limit_distance_columns(df)

    limit_up, limit_down = PriceLimitModel.calc_limits_for_pct(prev_close, 0.10, TickSizeClass.ORDINARY_STOCK)
    expected_up = max(0.0, (limit_up - close) / limit_up)
    expected_down = max(0.0, (close - limit_down) / limit_down)
    assert out["distance_to_upper_limit"][0] == pytest.approx(expected_up)
    assert out["distance_to_lower_limit"][0] == pytest.approx(expected_down)


def test_leveraged_etf_20pct_distance_matches_calc_limits_for_pct(monkeypatch):
    symbol = "00631L.TWSE"
    inst = _instrument(
        symbol,
        instrument_type="etf",
        etf_category="leveraged",
        classification_source="official_metadata",
        underlying_scope="domestic",
        leverage_multiplier=2.0,
    )
    _patch_master(monkeypatch, {symbol: inst})
    prev_close, close = 100.0, 115.0
    df = pl.DataFrame({"symbol": [symbol], "close": [close], "prev_close": [prev_close]})

    out = pipeline.add_limit_distance_columns(df)

    limit_up, limit_down = PriceLimitModel.calc_limits_for_pct(prev_close, 0.20, TickSizeClass.ETF)
    expected_up = max(0.0, (limit_up - close) / limit_up)
    expected_down = max(0.0, (close - limit_down) / limit_down)
    assert out["distance_to_upper_limit"][0] == pytest.approx(expected_up)
    assert out["distance_to_lower_limit"][0] == pytest.approx(expected_down)


def test_no_limit_instrument_gives_null_distance(monkeypatch):
    bond_symbol = "00720B.TPEX"
    foreign_symbol = "00646.TWSE"
    instruments = {
        bond_symbol: _instrument(
            bond_symbol,
            instrument_type="etf",
            etf_category="bond",
            classification_source="cfi_code",
            underlying_scope="foreign",
        ),
        foreign_symbol: _instrument(
            foreign_symbol,
            instrument_type="etf",
            etf_category="foreign_equity",
            classification_source="official_metadata",
            underlying_scope="foreign",
        ),
    }
    _patch_master(monkeypatch, instruments)
    df = pl.DataFrame({
        "symbol": [bond_symbol, foreign_symbol],
        "close": [50.0, 30.0],
        "prev_close": [50.0, 30.0],
    })

    out = pipeline.add_limit_distance_columns(df)

    assert out["distance_to_upper_limit"].is_null().all()
    assert out["distance_to_lower_limit"].is_null().all()


def test_prev_close_null_gives_null_distance(monkeypatch):
    symbol = "2330.TWSE"
    _patch_master(monkeypatch, {symbol: _instrument(symbol)})
    df = pl.DataFrame(
        {"symbol": [symbol], "close": [100.0], "prev_close": [None]},
        schema={"symbol": pl.Utf8, "close": pl.Float64, "prev_close": pl.Float64},
    )

    out = pipeline.add_limit_distance_columns(df)

    assert out["distance_to_upper_limit"][0] is None
    assert out["distance_to_lower_limit"][0] is None


def test_unconfirmed_underlying_scope_does_not_crash_and_stays_null(monkeypatch):
    # underlying_scope="unknown" makes MarketProfileBridge.get_price_limit_pct
    # raise ValueError (models.py:146-150); the pipeline must swallow it, not crash.
    symbol = "00999.TWSE"
    inst = _instrument(
        symbol,
        instrument_type="etf",
        classification_source="official_metadata",
        underlying_scope="unknown",
    )
    _patch_master(monkeypatch, {symbol: inst})
    df = pl.DataFrame({"symbol": [symbol], "close": [50.0], "prev_close": [50.0]})

    out = pipeline.add_limit_distance_columns(df)

    assert out["distance_to_upper_limit"][0] is None
    assert out["distance_to_lower_limit"][0] is None


def test_symbol_not_in_security_master_gives_null(monkeypatch):
    # e.g. an A-share symbol running through the same shared pipeline.
    symbol = "600000.SH"
    _patch_master(monkeypatch, {})
    df = pl.DataFrame({"symbol": [symbol], "close": [10.0], "prev_close": [10.0]})

    out = pipeline.add_limit_distance_columns(df)

    assert out["distance_to_upper_limit"][0] is None
    assert out["distance_to_lower_limit"][0] is None


def test_limit_profile_cache_avoids_repeated_security_master_lookups(monkeypatch):
    symbol = "2330.TWSE"
    calls: list[str] = []

    class _CountingMaster:
        def get_instrument(self, sym: str) -> TaiwanInstrument | None:
            calls.append(sym)
            return _instrument(symbol)

    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: _CountingMaster())

    pipeline.taiwan_limit_profile([symbol])
    pipeline.taiwan_limit_profile([symbol])
    pipeline.taiwan_limit_profile([symbol])

    assert calls == [symbol]
