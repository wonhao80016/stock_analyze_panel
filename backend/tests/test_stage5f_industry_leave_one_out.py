"""The industry 5-session baseline a stock is compared with excludes the stock itself.

RELATIVE_STRENGTH_OUTLIER compares a stock's 5-session return with its industry's equal-weight
return; with the stock inside that mean, a small industry's leader pulls its own baseline up.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan import enriched_refresh
from app.taiwan.industry_intelligence import IndustryMetrics, TaiwanIndustryIntelligenceService
from app.taiwan.market_rules import ListingSessions

TARGET = date(2026, 8, 28)


@pytest.fixture
def industry():
    dates = [TARGET - timedelta(days=i) for i in range(30, -1, -1)]
    base_5d = dates[-6]
    closes = {"A.TWSE": 130.0, "B.TWSE": 100.0, "C.TWSE": 102.0, "D.TWSE": 105.0}
    daily = MagicMock()
    daily.available_dates.return_value = dates

    def read_range(_symbols, start, _end):
        if start == TARGET:
            return pl.DataFrame({
                "symbol": list(closes),
                "close": list(closes.values()),
                "amount": [1000.0] * 4,
                "volume": [10.0] * 4,
            })
        if start == base_5d:
            # D has no 5-session base, so it is outside the industry mean.
            return pl.DataFrame({"symbol": ["A.TWSE", "B.TWSE", "C.TWSE"], "close": [100.0] * 3})
        return pl.DataFrame()

    daily.read_range.side_effect = read_range
    master = MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": list(closes),
        "name": list(closes),
        "exchange": ["TWSE"] * 4,
        "instrument_type": ["stock"] * 4,
        "listing_status": ["active"] * 4,
        "industry": ["半導體業"] * 4,
    })
    snap = TaiwanIndustryIntelligenceService(daily_store=daily, security_master=master).get_snapshot(TARGET)
    (metrics,) = snap.industries
    return metrics


def test_published_average_still_includes_every_member(industry):
    assert industry.average_return_5d == pytest.approx((0.30 + 0.0 + 0.02) / 3, abs=1e-6)


def test_baseline_for_a_member_excludes_it(industry):
    assert industry.average_return_5d_excluding("A.TWSE") == pytest.approx((0.0 + 0.02) / 2)
    assert industry.average_return_5d_excluding("B.TWSE") == pytest.approx((0.30 + 0.02) / 2)


def test_baseline_for_a_stock_outside_the_mean_is_the_full_mean(industry):
    assert industry.average_return_5d_excluding("D.TWSE") == pytest.approx((0.30 + 0.0 + 0.02) / 3)


def test_a_lone_member_has_no_baseline():
    metrics = IndustryMetrics(industry="X", average_return_5d=0.05)
    assert metrics.average_return_5d_excluding("A.TWSE") is None
    single = IndustryMetrics.model_construct(industry="X")
    assert single.average_return_5d_excluding("A.TWSE") is None


def test_member_returns_stay_out_of_the_api_payload(industry):
    assert "return_5d_by_symbol" not in industry.model_dump()
    assert "_return_5d_by_symbol" not in industry.model_dump_json()


def test_industry_breadth_uses_the_market_traded_basis(monkeypatch):
    """Advance/decline/flat count traded rows only, as the market totals do, so industries add up."""
    dates = [TARGET - timedelta(days=i) for i in range(30, -1, -1)]
    prev = dates[-2]
    # symbol -> (close today, volume today, close yesterday)
    rows = {
        "UP.TWSE": (11.0, 10.0, 10.0),
        "FLAT.TWSE": (10.0, 10.0, 10.0),
        "NOTRADE.TWSE": (10.0, 0.0, 10.0),
        "NOCLOSE.TWSE": (None, 10.0, 10.0),
        "NEW.TWSE": (10.0, 10.0, None),
    }
    daily = MagicMock()
    daily.available_dates.return_value = dates
    calendar = ListingSessions(tuple(dates), {symbol: dates[0] for symbol in rows})
    monkeypatch.setattr(enriched_refresh, "listing_sessions", lambda _store=None: calendar)

    def read_range(_symbols, start, _end):
        if start == TARGET:
            return pl.DataFrame({
                "symbol": list(rows),
                "open": [v[0] for v in rows.values()],
                "high": [v[0] for v in rows.values()],
                "low": [v[0] for v in rows.values()],
                "close": [v[0] for v in rows.values()],
                "amount": [1.0] * len(rows),
                "volume": [v[1] for v in rows.values()],
            }, schema_overrides={c: pl.Float64 for c in ("open", "high", "low", "close")})
        if _end == prev:
            picked = {s: v[2] for s, v in rows.items() if v[2] is not None}
            closes = list(picked.values())
            return pl.DataFrame({
                "symbol": list(picked), "date": [prev] * len(picked),
                "open": closes, "high": closes, "low": closes, "close": closes,
                "volume": [10.0] * len(picked), "amount": [1.0] * len(picked),
            })
        return pl.DataFrame()

    daily.read_range.side_effect = read_range
    master = MagicMock()
    master.to_dataframe.return_value = pl.DataFrame({
        "symbol": list(rows),
        "name": list(rows),
        "exchange": ["TWSE"] * len(rows),
        "instrument_type": ["stock"] * len(rows),
        "listing_status": ["active"] * len(rows),
        "industry": ["半導體業"] * len(rows),
    })
    (m,) = TaiwanIndustryIntelligenceService(daily_store=daily, security_master=master).get_snapshot(TARGET).industries

    assert m.traded_symbol_count == 3
    assert (m.advance_count, m.decline_count, m.flat_count) == (1, 0, 1)
    assert m.uncompared_count == 1
    assert m.untraded_count == 2
    assert m.advance_count + m.decline_count + m.flat_count + m.uncompared_count + m.untraded_count == 5
