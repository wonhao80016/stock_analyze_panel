"""The one-day move of every Taiwan summary starts from the base the enriched publish uses.

2303.TWSE and 2317.TWSE do not trade on 2026-09-07. TWT84U(2026-09-08) prints 2303.TWSE's
base 47.00 with limits 51.70 / 42.30, not its last close of 49.50, and it closes on the limit
down. 2317.TWSE has no official row, so its base is unknown. 2330.TWSE trades every session.
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.taiwan.abnormal_diagnostics import TaiwanAbnormalDiagnosticsService
from app.taiwan.corporate_actions import corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore, listing_sessions
from app.taiwan.enriched_refresh import compute_taiwan_enriched, limit_profile
from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_institutional_store import TaiwanMarketInstitutionalStore
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService
from app.taiwan.research_context import TaiwanStockResearchContextService
from app.taiwan.session_bases import TWSE, TaiwanSessionBaseStore, session_base_schema
from app.taiwan.universe import get_security_master

pytestmark = pytest.mark.usefixtures("deny_network")

SESSIONS = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8)]
GAP = date(2026, 9, 8)
PLAIN = date(2026, 9, 4)
CLOSES = {
    "2330.TWSE": [600.0, 605.0, 610.0, 615.0, 606.0, 612.0],
    "2303.TWSE": [51.0, 50.5, 50.0, 49.5, None, 42.3],
    "2317.TWSE": [100.0, 101.0, 102.0, 100.0, None, 95.0],
}
OFFICIAL = {"symbol": "2303.TWSE", "source_date": GAP, "ref_price": 47.0, "limit_up": 51.7, "limit_down": 42.3}


@pytest.fixture(autouse=True)
def _cached_security_master(monkeypatch) -> None:
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache(), "tests need the security master cache under DATA_DIR/taiwan"
    monkeypatch.setattr(universe, "_default_master", master)


@pytest.fixture
def daily(tmp_path, monkeypatch) -> TaiwanDailyStore:
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    rows = []
    for symbol, closes in CLOSES.items():
        for d, close in zip(SESSIONS, closes, strict=True):
            price = {"open": close, "high": close, "low": close, "close": close}
            rows.append({"symbol": symbol, "date": d, **price, "volume": 0.0 if close is None else 1_000.0,
                         "amount": 0.0 if close is None else close * 1_000.0, "quote_ts": None})
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    store.write_batch(pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64, "open": pl.Float64,
                                                           "high": pl.Float64, "low": pl.Float64,
                                                           "close": pl.Float64}))
    bases = TaiwanSessionBaseStore()
    bases.write(TWSE, GAP, pl.DataFrame([OFFICIAL]).with_columns(pl.col("source_date").cast(pl.Date)).select(
        [pl.col(c) if c in OFFICIAL else pl.lit(None, dtype=t).alias(c) for c, t in session_base_schema().items()]
    ))
    return store


def _published(daily: TaiwanDailyStore, d: date) -> dict[str, dict]:
    raw = daily.read_all()
    sessions = listing_sessions()
    enriched = compute_taiwan_enriched(
        raw,
        pl.DataFrame(schema=corporate_action_schema()),
        SESSIONS[-1],
        trading_days=sessions.trading_dates,
        limits=limit_profile(sorted(CLOSES), sessions),
        instruments=pl.DataFrame(),
        today=SESSIONS[-1],
        session_bases=TaiwanSessionBaseStore(),
    )
    return {r["symbol"]: r for r in enriched.filter(pl.col("date") == d).iter_rows(named=True)}


def _stores(daily: TaiwanDailyStore) -> dict:
    tmp = daily._data_dir.parent
    return {
        "daily_store": daily,
        "inst_store": TaiwanInstitutionalStore(data_dir=tmp / "institutional"),
        "margin_store": TaiwanMarginStore(data_dir=tmp / "margin"),
        "security_master": get_security_master(),
    }


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def test_market_breadth_counts_a_gap_session_from_the_published_base(daily):
    tmp = daily._data_dir.parent
    svc = TaiwanMarketIntelligenceService(
        **_stores(daily), market_inst_store=TaiwanMarketInstitutionalStore(data_dir=tmp / "market_institutional")
    )

    gap, plain = svc.get_snapshot(GAP).market_totals, svc.get_snapshot(PLAIN).market_totals

    published = _published(daily, GAP)
    assert published["2330.TWSE"]["change_pct"] > 0 > published["2303.TWSE"]["change_pct"]
    assert (published["2303.TWSE"]["prev_close"], published["2303.TWSE"]["signal_limit_down"]) == (47.0, True)
    assert (published["2317.TWSE"]["ref_base_missing"], published["2317.TWSE"]["change_pct"]) == (True, None)
    assert (gap.advance_count, gap.decline_count, gap.uncompared_count) == (1, 1, 1)
    assert gap.lower_limit_count == 1
    assert (plain.advance_count, plain.decline_count, plain.uncompared_count) == (1, 2, 0)


def test_industry_one_day_returns_match_the_published_change(daily):
    svc = TaiwanIndustryIntelligenceService(**_stores(daily))

    for d in (GAP, PLAIN):
        movers = {m.symbol: m.change_pct for ind in svc.get_snapshot(d).industries for m in ind.top_turnover}
        published = _published(daily, d)
        assert {s: movers[s] for s in CLOSES} == {s: _rounded(published[s]["change_pct"]) for s in CLOSES}

    assert movers["2303.TWSE"] == round(49.5 / 50.0 - 1, 6)
    assert _published(daily, GAP)["2303.TWSE"]["change_pct"] == pytest.approx(42.3 / 47.0 - 1)


def test_diagnostics_measure_a_gap_session_from_the_published_base(daily):
    svc = TaiwanAbnormalDiagnosticsService(**_stores(daily))

    for d in (GAP, PLAIN):
        items = {i.symbol: i for i in svc.get_diagnostics(d, include_all=True).items}
        published = _published(daily, d)
        for symbol in CLOSES:
            assert items[symbol].previous_close == published[symbol]["prev_close"]
            assert items[symbol].change_pct == _rounded(published[symbol]["change_pct"])

    assert items["2303.TWSE"].previous_close == 50.0


def test_research_context_measures_a_gap_session_from_the_published_base(daily):
    svc = TaiwanStockResearchContextService(**_stores(daily))

    for d in (GAP, PLAIN):
        published = _published(daily, d)
        for symbol in CLOSES:
            price = svc.get_research_context(symbol.split(".")[0], d).price_context
            assert price.previous_close == published[symbol]["prev_close"]
            assert price.change_pct == _rounded(published[symbol]["change_pct"])

    rules = svc.get_research_context("2303", GAP).market_rules_context
    assert (rules.limit_up, rules.limit_down) == (51.7, 42.3)
    assert svc.get_research_context("2303", PLAIN).price_context.previous_close == 50.0
