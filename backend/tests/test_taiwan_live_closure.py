"""Intraday rows after a session the holiday schedule lists as open but the market did not trade.

The store holds every symbol through LAST. CLOSED, the weekday after it, is either an unscheduled
closure, when the quoted previous closes of TODAY are the stored closes of LAST, or a session the
store has not taken in yet, when they are the closes of CLOSED. GAP and OTC_GAP did not trade on
LAST, so their base is the official one MIS quotes; DIVIDEND goes ex-dividend on TODAY.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.indicators.pipeline import invalidate_taiwan_limit_profile_cache
from app.taiwan import universe
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.live_enriched import TaiwanLiveEnriched
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOLS = tuple(f"{1100 + i}.TWSE" for i in range(16)) + tuple(f"{6100 + i}.TPEX" for i in range(8))
GAP, OTC_GAP, DIVIDEND, PLAIN = SYMBOLS[0], SYMBOLS[16], SYMBOLS[1], SYMBOLS[2]
GAP_BASE = 33.65
LAST_TRADED_CLOSE = 37.35
DIVIDEND_REFERENCE = 40.0


def _weekdays(first: date, count: int) -> list[date]:
    days, day = [], first
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


*SESSIONS, CLOSED, TODAY = _weekdays(date(2025, 1, 6), 62)
LAST = SESSIONS[-1]
CLOSING_TS = int(datetime(TODAY.year, TODAY.month, TODAY.day, 13, 30, tzinfo=TAIPEI).timestamp() * 1000)
SCHEMA = {
    "symbol": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
    "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64, "quote_ts": pl.Int64,
}


def _close(symbol: str, k: int) -> float | None:
    """Close of *symbol* on session k (CLOSED is k = len(SESSIONS)); every session changes it."""
    if symbol in (GAP, OTC_GAP):
        return None if k == len(SESSIONS) - 1 else LAST_TRADED_CLOSE
    return 40.0 + SYMBOLS.index(symbol) + (0.5 if k % 2 else 0.0)


def _bar(symbol: str, day: date, close: float | None) -> dict:
    return {
        "symbol": symbol, "date": day, "open": close, "high": close, "low": close, "close": close,
        "volume": 100_000.0 if close is not None else 0.0, "amount": None, "quote_ts": None,
    }


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol, code=code, exchange=exchange, name=code, instrument_type="stock",
        listing_status="active", listing_date="2000-01-01", isin=None, industry=None, cfi_code=None,
        raw_category="股票", is_supported=True, source="TWSE_ISIN", updated_at="2025-01-01",
    )


@pytest.fixture
def root(tmp_path, monkeypatch):
    from app.config import settings

    taiwan = tmp_path / "taiwan"
    TaiwanDailyStore(data_dir=taiwan / "daily").write_batch(
        pl.DataFrame(
            [_bar(s, day, _close(s, k)) for k, day in enumerate(SESSIONS) for s in SYMBOLS], schema=SCHEMA
        )
    )
    master_path = taiwan / "security_master.parquet"
    pl.DataFrame([_instrument(s).to_dict() for s in SYMBOLS]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    invalidate_taiwan_limit_profile_cache()
    yield tmp_path
    invalidate_taiwan_limit_profile_cache()


def _quoted(symbols, previous: int) -> pl.DataFrame:
    """TODAY's closing bars quoting the close of session *previous* as the previous close."""
    rows = []
    for symbol in symbols:
        if symbol in (GAP, OTC_GAP):
            prev_close = GAP_BASE
        elif symbol == DIVIDEND:
            prev_close = DIVIDEND_REFERENCE
        else:
            prev_close = _close(symbol, previous)
        close = prev_close + 0.5
        rows.append({
            **_bar(symbol, TODAY, close), "low": close - 1.0, "quote_ts": CLOSING_TS, "prev_close": prev_close,
        })
    return pl.DataFrame(rows, schema={**SCHEMA, "prev_close": pl.Float64})


def _rows(root, bars: pl.DataFrame) -> dict[str, dict]:
    engine = TaiwanLiveEnriched(root)
    engine.prepare()
    return {row["symbol"]: row for row in engine.rows(bars).iter_rows(named=True)}


def _assert_bases_applied(rows: dict[str, dict], previous_close: float) -> None:
    for symbol in (GAP, OTC_GAP):
        assert (rows[symbol]["ref_prev_close"], rows[symbol]["ref_base_missing"]) == (GAP_BASE, False), symbol
        assert rows[symbol]["change_pct"] == pytest.approx(0.5 / GAP_BASE)
    assert rows[DIVIDEND]["ex_rights"] is True
    assert rows[DIVIDEND]["ref_prev_close"] == pytest.approx(DIVIDEND_REFERENCE)
    assert rows[PLAIN]["ex_rights"] is False
    assert rows[PLAIN]["change_pct"] == pytest.approx(0.5 / previous_close)


def _assert_bases_withheld(rows: dict[str, dict]) -> None:
    assert [r["ex_rights"] for r in rows.values()] == [False] * len(rows)
    assert [r["ref_prev_close"] for r in rows.values()] == [None] * len(rows)


def test_an_unscheduled_closure_before_today_still_applies_the_official_bases(root, caplog):
    with caplog.at_level(logging.WARNING, logger="app.taiwan.live_enriched"):
        rows = _rows(root, _quoted(SYMBOLS, previous=len(SESSIONS) - 1))

    _assert_bases_applied(rows, _close(PLAIN, len(SESSIONS) - 1))
    assert not [r for r in caplog.records if "behind" in r.getMessage()]


def test_a_store_lacking_a_traded_previous_session_still_withholds_them(root, caplog):
    with caplog.at_level(logging.WARNING, logger="app.taiwan.live_enriched"):
        rows = _rows(root, _quoted(SYMBOLS, previous=len(SESSIONS)))

    _assert_bases_withheld(rows)
    assert len([r for r in caplog.records if "behind" in r.getMessage()]) == 1


def test_a_few_quotes_at_the_stored_close_do_not_make_a_closure(root):
    few = SYMBOLS[:6]

    _assert_bases_withheld(_rows(root, _quoted(few, previous=len(SESSIONS) - 1)))


def test_a_store_holding_the_previous_session_applies_them_as_before(root):
    """CLOSED traded and is stored; GAP and OTC_GAP traded on it again, so they need no base."""
    closed = [
        _bar(s, CLOSED, LAST_TRADED_CLOSE if s in (GAP, OTC_GAP) else _close(s, len(SESSIONS)))
        for s in SYMBOLS
    ]
    TaiwanDailyStore(data_dir=root / "taiwan" / "daily").write_batch(pl.DataFrame(closed, schema=SCHEMA))
    resumed = pl.col("symbol").is_in([GAP, OTC_GAP])
    bars = _quoted(SYMBOLS, previous=len(SESSIONS)).with_columns(
        pl.when(resumed).then(LAST_TRADED_CLOSE).otherwise(pl.col("prev_close")).alias("prev_close")
    )

    rows = _rows(root, bars)

    assert rows[DIVIDEND]["ex_rights"] is True
    assert rows[DIVIDEND]["ref_prev_close"] == pytest.approx(DIVIDEND_REFERENCE)
    assert rows[PLAIN]["ex_rights"] is False
    assert rows[PLAIN]["change_pct"] == pytest.approx(0.5 / _close(PLAIN, len(SESSIONS)))
    assert (rows[GAP]["ref_prev_close"], rows[OTC_GAP]["ref_prev_close"]) == (None, None)
