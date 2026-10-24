"""The screener reads only the partitions it needs: each exchange's newest daily partition among
the last five, the institutional 5-session window and the margin chip date.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore, listing_sessions
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from app.taiwan.universe import get_security_master

pytestmark = pytest.mark.usefixtures("deny_network")

DAYS: list[date] = []
_d = date(2026, 9, 1)
while len(DAYS) < 12:
    if _d.weekday() < 5:
        DAYS.append(_d)
    _d += timedelta(days=1)

TWSE = ("2330.TWSE", "2317.TWSE")
LAGGING_TPEX = "8069.TPEX"
STALE_TPEX = "6488.TPEX"


def _bar(symbol: str, day: date, close: float) -> dict:
    return {
        "symbol": symbol, "date": day, "open": close, "high": close, "low": close, "close": close,
        "volume": 1000.0, "amount": close * 1000.0, "quote_ts": None,
    }


def _chip_rows(symbols, days) -> tuple[list[dict], list[dict]]:
    inst, margin = [], []
    for d in days:
        for s in symbols:
            inst.append({
                "symbol": s, "date": d, "trade_date": d, "foreign_net": 10, "investment_trust_net": 1,
                "dealer_net": 1, "status": "available", "source": "test",
            })
            margin.append({
                "symbol": s, "date": d, "trade_date": d, "margin_balance": 100, "margin_change": 5,
                "short_balance": 50, "short_margin_ratio": 5.0, "status": "available", "source": "test",
            })
    return inst, margin


@pytest.fixture
def world(tmp_path, monkeypatch):
    """TWSE trades every day; LAGGING_TPEX lacks the newest day; STALE_TPEX stopped three days back."""
    from app.config import settings

    get_security_master()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    daily = TaiwanDailyStore(data_dir=tmp_path / "daily")
    rows = [_bar(s, d, 100.0 + i) for i, d in enumerate(DAYS) for s in TWSE]
    rows += [_bar(LAGGING_TPEX, d, 50.0 + i) for i, d in enumerate(DAYS[:-1])]
    rows += [_bar(STALE_TPEX, d, 70.0 + i) for i, d in enumerate(DAYS[:-3])]
    daily.write_batch(pl.DataFrame(rows))
    inst = TaiwanInstitutionalStore(data_dir=tmp_path / "inst")
    margin = TaiwanMarginStore(data_dir=tmp_path / "margin")
    inst_rows, margin_rows = _chip_rows((*TWSE, LAGGING_TPEX), DAYS[-8:])
    inst.write_batch(pl.DataFrame(inst_rows))
    margin.write_batch(pl.DataFrame(margin_rows))
    service = TaiwanScreenerService(daily_store=daily, institutional_store=inst, margin_store=margin)
    return tmp_path, service


@pytest.fixture
def opened(monkeypatch):
    """Partition dates opened per store directory, by any polars parquet reader."""
    counts: dict[str, Counter] = {}
    real_scan, real_read = pl.scan_parquet, pl.read_parquet

    def track(source) -> None:
        path = Path(str(source))
        if not path.parent.name.startswith("date=") or "*" in path.parent.name:
            return
        counts.setdefault(path.parent.parent.name, Counter())[path.parent.name.split("=", 1)[1]] += 1

    def scan(source, *args, **kwargs):
        track(source)
        return real_scan(source, *args, **kwargs)

    def read(source, *args, **kwargs):
        track(source)
        return real_read(source, *args, **kwargs)

    monkeypatch.setattr(pl, "scan_parquet", scan)
    monkeypatch.setattr(pl, "read_parquet", read)
    return counts


def _iso(days) -> set[str]:
    return {d.isoformat() for d in days}


def _row_reads(monkeypatch, store: TaiwanDailyStore) -> list:
    """Dates whose full rows read_dates loads; symbol-column probes are not included."""
    days: list = []
    real = store.read_dates

    def spy(dates, symbols=None):
        days.extend(dates)
        return real(dates, symbols)

    monkeypatch.setattr(store, "read_dates", spy)
    return days


def _daily_only_service(tmp_path, monkeypatch, rows: list[dict]) -> TaiwanScreenerService:
    from app.config import settings

    get_security_master()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    daily = TaiwanDailyStore(data_dir=tmp_path / "daily")
    daily.write_batch(pl.DataFrame(rows))
    return TaiwanScreenerService(
        daily_store=daily,
        institutional_store=TaiwanInstitutionalStore(data_dir=tmp_path / "inst"),
        margin_store=TaiwanMarginStore(data_dir=tmp_path / "margin"),
    )


TPEX_STOCKS = ("6488.TPEX", "5274.TPEX", LAGGING_TPEX)


def _daily_flags(res) -> list[str]:
    return [s for s in res.degraded_sections if s.startswith("daily_")]


def test_a_run_with_both_exchanges_on_the_newest_day_reads_that_partition_only(tmp_path, monkeypatch, opened):
    rows = [_bar(s, d, 100.0) for d in DAYS for s in (*TWSE, *TPEX_STOCKS)]
    service = _daily_only_service(tmp_path, monkeypatch, rows)
    listing_sessions(service.daily_store)
    reads = _row_reads(monkeypatch, service.daily_store)
    opened.clear()

    res = service.run(TaiwanScreenerRequest())

    assert _iso(reads) == _iso(DAYS[-1:])
    assert set(opened["daily"]) == _iso(DAYS[-6:])
    assert _daily_flags(res) == []
    assert {i.quote_date for i in res.items} == {DAYS[-1].isoformat()}


def test_an_exchange_absent_from_the_two_newest_days_comes_from_its_latest_partition(tmp_path, monkeypatch, opened):
    rows = [_bar(s, d, 100.0) for d in DAYS for s in TWSE]
    rows += [_bar(s, d, 50.0 + i) for i, d in enumerate(DAYS[:-2]) for s in TPEX_STOCKS]
    service = _daily_only_service(tmp_path, monkeypatch, rows)
    listing_sessions(service.daily_store)
    reads = _row_reads(monkeypatch, service.daily_store)
    opened.clear()

    res = service.run(TaiwanScreenerRequest())

    tpex = {i.symbol: i for i in res.items if i.exchange == "TPEX"}
    assert set(tpex) == set(TPEX_STOCKS)
    assert {i.quote_date for i in tpex.values()} == {DAYS[-3].isoformat()}
    assert tpex[LAGGING_TPEX].close == pytest.approx(50.0 + len(DAYS) - 3)
    assert _daily_flags(res) == ["daily_stale_tpex"]
    assert res.data_dates.daily_as_of == DAYS[-1].isoformat()
    assert _iso(reads) == _iso([DAYS[-1], DAYS[-3]])
    assert set(opened["daily"]) == _iso(DAYS[-8:])


def test_an_exchange_absent_from_the_last_five_partitions_is_flagged_missing(tmp_path, monkeypatch, opened):
    rows = [_bar(s, d, 100.0) for d in DAYS for s in TWSE]
    rows += [_bar(s, d, 50.0) for d in DAYS[:-5] for s in TPEX_STOCKS]
    service = _daily_only_service(tmp_path, monkeypatch, rows)
    listing_sessions(service.daily_store)
    opened.clear()

    res = service.run(TaiwanScreenerRequest())

    assert {i.exchange for i in res.items} == {"TWSE"}
    assert _daily_flags(res) == ["daily_missing_tpex"]
    assert set(opened["daily"]) == _iso(DAYS[-10:])


TWSE_STOCKS = (*TWSE, "2303.TWSE", "2454.TWSE")


def _full_and_sparse_rows(sparse: tuple[str, ...]) -> list[dict]:
    """Every stock trades on DAYS[:-1]; on DAYS[-1] the *sparse* exchanges hold one row only."""
    rows = [_bar(s, d, 100.0) for d in DAYS[:-1] for s in (*TWSE_STOCKS, *TPEX_STOCKS)]
    for ex, stocks in (("TWSE", TWSE_STOCKS), ("TPEX", TPEX_STOCKS)):
        rows += [_bar(s, DAYS[-1], 200.0) for s in (stocks[:1] if ex in sparse else stocks)]
    return rows


def test_a_sparse_newest_partition_falls_back_for_both_exchanges(tmp_path, monkeypatch):
    service = _daily_only_service(tmp_path, monkeypatch, _full_and_sparse_rows(("TWSE", "TPEX")))

    res = service.run(TaiwanScreenerRequest())

    assert {i.symbol for i in res.items} == {*TWSE_STOCKS, *TPEX_STOCKS}
    assert {i.quote_date for i in res.items} == {DAYS[-2].isoformat()}
    assert _daily_flags(res) == ["daily_stale_twse", "daily_stale_tpex"]
    assert res.data_dates.daily_as_of == DAYS[-2].isoformat()


def test_a_sparse_exchange_falls_back_while_the_full_one_stays_current(tmp_path, monkeypatch):
    service = _daily_only_service(tmp_path, monkeypatch, _full_and_sparse_rows(("TWSE",)))

    res = service.run(TaiwanScreenerRequest())
    dates = {i.symbol: i.quote_date for i in res.items}

    assert set(dates) == {*TWSE_STOCKS, *TPEX_STOCKS}
    assert {dates[s] for s in TWSE_STOCKS} == {DAYS[-2].isoformat()}
    assert {dates[s] for s in TPEX_STOCKS} == {DAYS[-1].isoformat()}
    assert _daily_flags(res) == ["daily_stale_twse"]


def test_half_of_the_earlier_rows_still_counts_as_current(tmp_path, monkeypatch):
    rows = [_bar(s, d, 100.0) for d in DAYS[:-1] for s in TWSE_STOCKS]
    rows += [_bar(s, DAYS[-1], 200.0) for s in TWSE_STOCKS[:2]]
    service = _daily_only_service(tmp_path, monkeypatch, rows)

    res = service.run(TaiwanScreenerRequest(exchange="TWSE"))

    assert _daily_flags(res) == []
    assert {i.symbol for i in res.items} == set(TWSE_STOCKS[:2])


def test_symbols_without_a_row_are_counted(tmp_path, monkeypatch):
    service = _daily_only_service(tmp_path, monkeypatch, _full_and_sparse_rows(()))
    universe = get_security_master().to_dataframe(supported_only=True).filter(
        (pl.col("exchange") == "TPEX") & pl.col("instrument_type").is_in(["stock", "etf"])
        & (pl.col("listing_status") == "active")
    )["symbol"].to_list()
    assert set(universe) > set(TPEX_STOCKS)

    res = service.run(TaiwanScreenerRequest(exchange="TPEX"))

    assert res.total == len(TPEX_STOCKS)
    assert res.data_dates.daily_unlisted_count == len(set(universe) - set(TPEX_STOCKS))


def test_an_exchange_outside_the_request_is_not_flagged(tmp_path, monkeypatch):
    rows = [_bar(s, d, 100.0) for d in DAYS for s in TWSE]
    service = _daily_only_service(tmp_path, monkeypatch, rows)

    res = service.run(TaiwanScreenerRequest(exchange="TWSE"))

    assert _daily_flags(res) == []
    assert service.run(TaiwanScreenerRequest(exchange="TPEX")).degraded_sections == ["daily_missing_tpex"]


def test_a_run_reads_the_rows_of_the_newest_daily_partition_and_the_one_before(world, monkeypatch, opened):
    _, service = world
    listing_sessions(service.daily_store)
    reads = _row_reads(monkeypatch, service.daily_store)
    opened.clear()

    service.run(TaiwanScreenerRequest())

    assert _iso(reads) == _iso(DAYS[-2:])
    assert set(opened["daily"]) == _iso(DAYS[-7:])


def test_the_chip_reads_stay_inside_the_five_session_window(world, opened):
    _, service = world
    listing_sessions(service.daily_store)
    opened.clear()

    res = service.run(TaiwanScreenerRequest())

    assert set(opened.get("inst", {})) == _iso(DAYS[-5:])
    assert set(opened.get("margin", {})) == _iso(DAYS[-1:])
    assert res.data_dates.institutional_as_of == DAYS[-1].isoformat()


def test_an_exchange_missing_from_the_newest_day_takes_the_day_before(world):
    _, service = world

    res = service.run(TaiwanScreenerRequest())
    items = {i.symbol: i for i in res.items}

    assert items["2330.TWSE"].quote_date == DAYS[-1].isoformat()
    assert items[LAGGING_TPEX].quote_date == DAYS[-2].isoformat()
    assert items[LAGGING_TPEX].close == pytest.approx(50.0 + len(DAYS) - 2)
    assert items[LAGGING_TPEX].foreign_net == 10
    assert _daily_flags(res) == ["daily_stale_tpex"]


def test_a_symbol_absent_from_its_exchange_partition_is_not_listed(world):
    _, service = world

    res = service.run(TaiwanScreenerRequest())

    assert STALE_TPEX not in {i.symbol for i in res.items}
    assert res.data_dates.daily_as_of == DAYS[-1].isoformat()


def test_an_unreadable_newest_partition_falls_back_to_the_day_before(world):
    _, service = world
    listing_sessions(service.daily_store)
    (service.daily_store._data_dir / f"date={DAYS[-1].isoformat()}" / "part.parquet").write_bytes(b"broken")

    res = service.run(TaiwanScreenerRequest())

    assert res.data_dates.daily_as_of == DAYS[-2].isoformat()
    assert {i.quote_date for i in res.items} == {DAYS[-2].isoformat()}
    assert _daily_flags(res) == ["daily_stale_twse", "daily_stale_tpex"]


def test_read_dates_reads_only_the_given_partitions(world, opened):
    _, service = world
    store = service.daily_store

    frame = store.read_dates([DAYS[3], DAYS[5], date(2020, 1, 1)], ["2330.TWSE", LAGGING_TPEX])

    assert set(opened["daily"]) == _iso([DAYS[3], DAYS[5]])
    assert frame.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"]
    assert frame.select("symbol", "date").rows() == [
        ("2330.TWSE", DAYS[3]), ("2330.TWSE", DAYS[5]), (LAGGING_TPEX, DAYS[3]), (LAGGING_TPEX, DAYS[5]),
    ]
    assert store.read_dates([]).is_empty()


def test_symbol_suffix_counts_reads_one_partition_symbol_column(world, opened):
    _, service = world
    store = service.daily_store
    suffixes = [".TWSE", ".TPEX"]

    assert store.symbol_suffix_counts(DAYS[-1], suffixes) == {".TWSE": 2, ".TPEX": 0}
    assert store.symbol_suffix_counts(date(2020, 1, 1), suffixes) == {".TWSE": 0, ".TPEX": 0}
    assert set(opened["daily"]) == _iso(DAYS[-1:])
