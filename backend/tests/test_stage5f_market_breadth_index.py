"""Market breadth basis and benchmark index status of the market intelligence snapshot.

Breadth counts only rows that traded (close present and volume > 0) as advance, decline or
flat; untraded rows and rows without a previous close are counted apart, so the five counts
add up to the snapshot rows. Index status comes from the local index_daily store.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService

TARGET = date(2026, 8, 28)
PREV = date(2026, 8, 27)
OLDER = date(2026, 8, 26)

# symbol -> (close today, volume today, close yesterday or None)
ROWS = {
    "UP.TWSE": (11.0, 1000.0, 10.0),
    "DOWN.TWSE": (19.5, 1000.0, 20.0),
    "FLAT.TWSE": (30.0, 1000.0, 30.0),
    "NOTRADE.TWSE": (50.0, 0.0, 50.0),
    "NOCLOSE.TPEX": (None, 500.0, 40.0),
    "NEW.TPEX": (42.0, 1000.0, None),
}


def _daily(rows: dict, d: date, today: bool) -> pl.DataFrame:
    picked = rows
    closes = [v[0] if today else v[2] for v in picked.values()]
    return pl.DataFrame(
        {
            "symbol": list(picked),
            "date": [d] * len(picked),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [v[1] if today else 1000.0 for v in picked.values()],
            "amount": [(v[0] or 0.0) * v[1] if today else 1.0 for v in picked.values()],
            "quote_ts": [None] * len(picked),
        },
        schema_overrides={"close": pl.Float64, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64},
    )


def _service(index_store=None) -> TaiwanMarketIntelligenceService:
    current = _daily(ROWS, TARGET, True)
    prior = _daily({s: v for s, v in ROWS.items() if v[2] is not None}, PREV, False)
    daily = MagicMock()
    daily.available_dates.return_value = [PREV, TARGET]
    daily.provisional_dates.return_value = []
    daily.incomplete_dates.return_value = []
    daily.read_range.side_effect = lambda _s, start, end: (
        current if start == TARGET and end == TARGET
        else prior if start == PREV and end == PREV
        else pl.concat([prior, current])
    )
    universe = pl.DataFrame({
        "symbol": list(ROWS),
        "exchange": [s.rsplit(".", 1)[1] for s in ROWS],
        "instrument_type": ["stock"] * len(ROWS),
        "listing_status": ["active"] * len(ROWS),
    })
    master = MagicMock()
    master.to_dataframe.return_value = universe
    master.get_instrument.return_value = None
    empty = MagicMock()
    empty.available_dates.return_value = []
    empty.read_range.return_value = pl.DataFrame()
    market_inst = MagicMock()
    market_inst.available_dates.return_value = []
    market_inst.read_day.return_value = pl.DataFrame(
        schema={"exchange": pl.String, "category": pl.String, "net_amount": pl.Int64, "has_discrepancy": pl.Boolean}
    )
    corporate_actions = MagicMock()
    corporate_actions.read.return_value = pl.DataFrame()
    return TaiwanMarketIntelligenceService(
        daily_store=daily,
        inst_store=empty,
        margin_store=empty,
        security_master=master,
        market_inst_store=market_inst,
        corporate_actions=corporate_actions,
        index_store=index_store,
    )


@pytest.fixture
def empty_index_store(tmp_path):
    return TaiwanDailyStore(tmp_path / "index_daily")


def test_breadth_counts_only_traded_rows(empty_index_store):
    totals = _service(empty_index_store).get_snapshot(TARGET).market_totals

    assert totals.snapshot_row_count == 6
    assert totals.traded_count == 4
    assert (totals.advance_count, totals.decline_count, totals.flat_count) == (1, 1, 1)
    assert totals.uncompared_count == 1
    assert totals.untraded_count == 2
    assert (
        totals.advance_count + totals.decline_count + totals.flat_count
        + totals.uncompared_count + totals.untraded_count
    ) == totals.snapshot_row_count


def test_exchange_slices_follow_the_same_basis(empty_index_store):
    snap = _service(empty_index_store).get_snapshot(TARGET)
    tpex = snap.by_exchange.tpex
    assert (tpex.traded_count, tpex.uncompared_count, tpex.untraded_count, tpex.flat_count) == (1, 1, 1, 0)


def test_change_distribution_has_its_own_flat_band(empty_index_store):
    bands = {b.label: b.count for b in _service(empty_index_store).get_snapshot(TARGET).change_distribution}

    assert bands["平盤"] == 1
    assert bands["0~1%"] == 0
    assert bands[">5%"] == 1
    assert bands["-3~-1%"] == 1
    assert sum(bands.values()) == 3


def _index_rows(symbol: str, closes: dict[date, float]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol] * len(closes),
        "date": list(closes),
        "open": list(closes.values()),
        "high": list(closes.values()),
        "low": list(closes.values()),
        "close": list(closes.values()),
        "volume": [None] * len(closes),
        "amount": [None] * len(closes),
        "quote_ts": [None] * len(closes),
    }, schema_overrides={"volume": pl.Float64, "amount": pl.Float64, "quote_ts": pl.String})


def test_index_status_is_read_from_the_local_store(empty_index_store):
    store = empty_index_store
    store.write_batch(_index_rows("t00.TWSE", {OLDER: 21000.0, PREV: 22000.0, TARGET: 22220.0}))
    store.write_batch(_index_rows("o00.TPEX", {OLDER: 250.0, PREV: 255.0}))
    store.write_batch(_index_rows("t00.TWSE", {date(2026, 8, 31): 99999.0}))

    snap = _service(store).get_snapshot(TARGET)
    taiex, tpex = snap.indexes.taiex, snap.indexes.tpex_index

    assert taiex.status == "current"
    assert taiex.trade_date == str(TARGET)
    assert taiex.close == 22220.0
    assert taiex.change == pytest.approx(220.0)
    assert taiex.change_pct == pytest.approx(0.01)

    assert tpex.status == "stale"
    assert tpex.trade_date == str(PREV)
    assert tpex.close == 255.0
    assert tpex.change_pct == pytest.approx(0.02)

    assert snap.data_quality.indexes.status == "stale"
    assert snap.data_quality.indexes.as_of == str(PREV)
    assert snap.data_quality.indexes.source == "taiwan_index_daily_store"


def test_missing_index_rows_stay_unavailable_without_a_date(empty_index_store):
    snap = _service(empty_index_store).get_snapshot(TARGET)

    assert snap.indexes.taiex.status == "unavailable"
    assert snap.indexes.taiex.trade_date is None
    assert snap.indexes.taiex.close is None
    assert snap.data_quality.indexes.status == "unavailable"
