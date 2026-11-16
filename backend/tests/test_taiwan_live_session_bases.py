"""Intraday rows and the stock page take the exchange's base on a session after an untraded one.

1435.TWSE did not trade on 2025-04-07; TWT84U(2025-04-08) prints base 33.65, limits 37.00 / 30.30,
and MIS quotes that base as the previous close. It closed at 31.50 on 2025-04-08 (open 31.00).
6000.TPEX is a TPEx copy of it whose base comes from the stored dailyQuotes(2025-04-07).
5205.TPEX goes limit up from its official base on the session after an untraded one and keeps going.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import kline
from app.api.taiwan import router as taiwan_router
from app.indicators.pipeline import compute_enriched, invalidate_taiwan_limit_profile_cache
from app.taiwan import detail_service, session_bases, universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enriched_refresh import compute_taiwan_enriched, limit_profile
from app.taiwan.live_enriched import TaiwanLiveEnriched
from app.taiwan.market_rules import ListingSessions
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.session_bases import TPEX, TWSE, TaiwanSessionBaseStore, session_base_schema
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

GAP = "1435.TWSE"
OTC_GAP = "6000.TPEX"
LIMIT_RUN = "5205.TPEX"
PLAIN = "2330.TWSE"
UNTRADED = date(2025, 4, 7)
TODAY = date(2025, 4, 8)
BASE, LIMIT_UP, LIMIT_DOWN = 33.65, 37.00, 30.30
LAST_TRADED_CLOSE = 37.35
LIMIT_FLAGS = ("signal_limit_up", "signal_limit_down", "signal_broken_limit_up", "signal_limit_down_recovery")
CLOSING_TS = int(datetime(2025, 4, 8, 13, 30, tzinfo=TAIPEI).timestamp() * 1000)
HOLIDAYS = {date(2025, 4, 3), date(2025, 4, 4)}


def _sessions(first: date, last: date) -> list[date]:
    days, day = [], first
    while day <= last:
        if day.weekday() < 5 and day not in HOLIDAYS:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = _sessions(date(2025, 2, 3), UNTRADED)
GAP_CLOSES = {date(2025, 4, 1): 41.45, date(2025, 4, 2): LAST_TRADED_CLOSE, UNTRADED: None}


def _bar(symbol: str, day: date, close: float | None) -> dict:
    return {
        "symbol": symbol, "date": day, "open": close, "high": close, "low": close, "close": close,
        "volume": 100_000.0 if close is not None else 0.0, "amount": None, "quote_ts": None,
    }


def _plain_close(k: int) -> float:
    return 900.0 + (k % 7)


BAR_SCHEMA = {
    "symbol": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
    "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64, "quote_ts": pl.Int64,
}


def _stored_bars(days: list[date]) -> pl.DataFrame:
    rows = []
    for k, day in enumerate(days):
        gap_close = GAP_CLOSES.get(day, 39.10)
        rows += [_bar(GAP, day, gap_close), _bar(OTC_GAP, day, gap_close), _bar(PLAIN, day, _plain_close(k))]
    return pl.DataFrame(rows, schema=BAR_SCHEMA)


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol, code=code, exchange=exchange, name=code, instrument_type="stock",
        listing_status="active", listing_date="2000-01-01", isin=None, industry=None, cfi_code=None,
        raw_category="股票", is_supported=True, source="TWSE_ISIN", updated_at="2025-04-01",
    )


def _base_row(symbol: str, source_date: date, bases: tuple[float, float, float]) -> dict:
    return {
        "symbol": symbol, "source_date": source_date, "ref_price": bases[0], "limit_up": bases[1],
        "limit_down": bases[2], "prev_close": None, "last_bid": None, "last_ask": None,
        "source": "test", "source_url": "https://example.invalid",
        "retrieved_at": datetime(2025, 4, 7, 16, 30, tzinfo=TAIPEI),
    }


def _write_base(
    root, source: str, symbol: str, source_date: date, bases: tuple[float, float, float] = (BASE, LIMIT_UP, LIMIT_DOWN)
) -> None:
    TaiwanSessionBaseStore(root / "taiwan" / "session_bases").write(
        source, source_date, pl.DataFrame([_base_row(symbol, source_date, bases)], schema=session_base_schema())
    )


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A data dir storing the three symbols through 2025-04-07, with their master and no events."""
    from app.config import settings

    taiwan = tmp_path / "taiwan"
    TaiwanDailyStore(data_dir=taiwan / "daily").write_batch(_stored_bars(SESSIONS))
    master_path = taiwan / "security_master.parquet"
    pl.DataFrame([_instrument(s).to_dict() for s in (GAP, OTC_GAP, PLAIN, LIMIT_RUN)]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    invalidate_taiwan_limit_profile_cache()
    yield tmp_path
    invalidate_taiwan_limit_profile_cache()


def _live_bars(prev_closes: dict[str, float | None]) -> pl.DataFrame:
    """Today's closing bars; the gap symbols open at 31.00 and close at 31.50."""
    rows = []
    for symbol, prev_close in prev_closes.items():
        close, low = (910.0, 905.0) if symbol == PLAIN else (31.50, 31.00)
        rows.append({
            "symbol": symbol, "date": TODAY, "open": low, "high": close, "low": low, "close": close,
            "volume": 657_100.0, "amount": None, "quote_ts": CLOSING_TS, "prev_close": prev_close,
        })
    return pl.DataFrame(rows, schema={
        "symbol": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64, "quote_ts": pl.Int64,
        "prev_close": pl.Float64,
    })


def _live_row(root, prev_closes: dict[str, float | None], symbol: str) -> dict:
    engine = TaiwanLiveEnriched(root)
    engine.prepare()
    rows = engine.rows(_live_bars(prev_closes))
    return rows.filter(pl.col("symbol") == symbol).row(0, named=True)


def _unscaled_ma5() -> float:
    traded = [39.10, 39.10, 41.45, LAST_TRADED_CLOSE, 31.50]
    return sum(traded) / len(traded)


def test_the_quoted_base_after_an_untraded_session_is_no_corporate_action(root):
    row = _live_row(root, {GAP: BASE, PLAIN: _plain_close(len(SESSIONS) - 1)}, GAP)

    assert row["ex_rights"] is False
    assert row["ma5"] == pytest.approx(_unscaled_ma5())
    assert (row["ref_prev_close"], row["ref_base_missing"]) == (BASE, False)
    assert row["prev_close"] == pytest.approx(BASE)
    assert row["change_pct"] == pytest.approx(-0.0639, abs=1e-4)
    assert row["signal_limit_down"] is False


def test_a_quoted_base_carries_the_limits_the_exchange_prints(root):
    """Without a stored TWT84U of today, 1435.TWSE's limits come from the quoted base 33.65."""
    row = _live_row(root, {GAP: BASE, PLAIN: _plain_close(len(SESSIONS) - 1)}, GAP)

    assert (row["ref_prev_close"], row["ref_limit_up"], row["ref_limit_down"]) == (BASE, LIMIT_UP, LIMIT_DOWN)
    assert row["signal_limit_down"] is False


def test_a_quoted_previous_close_below_a_traded_one_is_still_a_dividend(root):
    stored = _plain_close(len(SESSIONS) - 1)

    row = _live_row(root, {GAP: BASE, PLAIN: stored - 10.0}, PLAIN)

    assert row["ex_rights"] is True
    assert row["ref_prev_close"] == pytest.approx(stored - 10.0)
    assert row["ref_base_missing"] is False


def test_the_stored_tpex_base_comes_first_and_rows_read_no_file(root, monkeypatch):
    _write_base(root, TPEX, OTC_GAP, UNTRADED)
    engine = TaiwanLiveEnriched(root)
    engine.prepare()

    def no_read(*args, **kwargs):
        raise AssertionError("rows() read a session base file")

    monkeypatch.setattr(session_bases.TaiwanSessionBaseStore, "read", no_read)
    rows = engine.rows(_live_bars({OTC_GAP: BASE + 0.05, PLAIN: _plain_close(len(SESSIONS) - 1)}))
    row = rows.filter(pl.col("symbol") == OTC_GAP).row(0, named=True)

    assert (row["ref_prev_close"], row["ref_limit_up"], row["ref_limit_down"]) == (BASE, LIMIT_UP, LIMIT_DOWN)
    assert row["ref_base_missing"] is False
    assert row["ex_rights"] is False
    assert row["change_pct"] == pytest.approx(31.50 / BASE - 1)


def test_without_any_base_the_change_and_the_limit_flags_are_unknown(root):
    row = _live_row(root, {GAP: None, PLAIN: _plain_close(len(SESSIONS) - 1)}, GAP)

    assert row["ref_base_missing"] is True
    assert row["ref_prev_close"] is None
    assert (row["prev_close"], row["change_pct"]) == (None, None)
    assert all(row[flag] is None for flag in LIMIT_FLAGS)


def test_a_limit_run_from_an_official_base_counts_as_published(root):
    """Against the last close 10.00 the 9.90 of the session after the untraded one is no limit up."""
    closes = {SESSIONS[-4]: None, SESSIONS[-3]: 9.90, SESSIONS[-2]: 10.85, SESSIONS[-1]: 11.90}
    daily = TaiwanDailyStore(data_dir=root / "taiwan" / "daily")
    daily.write_batch(pl.DataFrame([_bar(LIMIT_RUN, d, closes.get(d, 10.00)) for d in SESSIONS], schema=BAR_SCHEMA))
    _write_base(root, TPEX, LIMIT_RUN, SESSIONS[-4], (9.00, 9.90, 8.10))
    live = _live_bars({LIMIT_RUN: 11.90}).with_columns([pl.lit(13.05).alias(c) for c in ("open", "high", "low", "close")])
    engine = TaiwanLiveEnriched(root)
    engine.prepare()
    row = engine.rows(live).row(0, named=True)

    calendar = (*SESSIONS, TODAY)
    published = compute_taiwan_enriched(
        pl.concat([daily.scan_range([LIMIT_RUN], None, None).select(list(BAR_SCHEMA)), live.select(list(BAR_SCHEMA))]),
        pl.DataFrame(schema=corporate_action_schema()),
        TODAY,
        calendar,
        limit_profile([LIMIT_RUN], ListingSessions(calendar, {LIMIT_RUN: SESSIONS[0]})),
        pl.DataFrame(),
        today=TODAY,
        session_bases=TaiwanSessionBaseStore(root / "taiwan" / "session_bases"),
    ).filter(pl.col("date") == TODAY).row(0, named=True)

    assert published["consecutive_limit_ups"] == 4
    assert row["consecutive_limit_ups"] == published["consecutive_limit_ups"]
    assert row["consecutive_limit_downs"] == published["consecutive_limit_downs"]


def _published_store(root, *, with_base: bool) -> TaiwanDailyStore:
    """The daily store of *root* with 2025-04-08 stored, and TWT84U(2025-04-08) when *with_base*."""
    daily = TaiwanDailyStore(data_dir=root / "taiwan" / "daily")
    today = _live_bars({GAP: BASE, OTC_GAP: BASE, PLAIN: None}).drop("prev_close")
    daily.write_batch(today)
    if with_base:
        _write_base(root, TWSE, GAP, TODAY)
    return daily


def _detail_rows(root, monkeypatch) -> dict[str, dict]:
    service = TaiwanStockDetailService(
        daily_store=TaiwanDailyStore(data_dir=root / "taiwan" / "daily"),
        hybrid_provider=MagicMock(),
        corporate_actions=TaiwanCorporateActionStore(data_dir=root / "taiwan" / "corporate_actions"),
    )
    monkeypatch.setattr(detail_service, "_detail_service", service)
    monkeypatch.setattr(detail_service, "taipei_now", lambda: datetime(2025, 4, 8, 18, 0, tzinfo=TAIPEI))
    monkeypatch.setattr(service, "_fetch_quote", lambda symbol: None)
    monkeypatch.setattr(service, "_aggregate_institutional", lambda sym: service._empty_institutional())
    monkeypatch.setattr(service, "_aggregate_margin", lambda sym: service._empty_margin())
    app = FastAPI()
    app.include_router(taiwan_router)
    response = TestClient(app).get(f"/api/taiwan/stocks/{GAP}")
    assert response.status_code == 200, response.text
    return {row["date"]: row for row in response.json()["daily_history"]["rows"]}


@pytest.mark.parametrize(("with_base", "expected"), [(True, BASE), (False, None)])
def test_the_stock_page_daily_rows_take_the_official_base(root, monkeypatch, with_base, expected):
    _published_store(root, with_base=with_base)

    rows = _detail_rows(root, monkeypatch)

    assert rows[TODAY.isoformat()]["prev_close"] == (pytest.approx(expected) if expected else None)
    assert rows["2025-04-02"]["prev_close"] == pytest.approx(41.45)


@pytest.mark.parametrize(("with_base", "expected"), [(True, 31.50 / BASE - 1), (False, None)])
def test_the_local_daily_k_line_takes_the_official_base(root, with_base, expected):
    daily = _published_store(root, with_base=with_base)

    enriched = compute_enriched(
        kline._with_reference_prices(GAP, daily.scan_range([GAP], None, None)), factors=pl.DataFrame()
    )
    row = enriched.filter(pl.col("date") == TODAY).row(0, named=True)

    assert row["change_pct"] == (pytest.approx(expected) if expected else None)
