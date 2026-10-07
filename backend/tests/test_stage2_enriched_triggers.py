"""The Taiwan enriched refresh republishes every partition when history no longer matches.

Two triggers besides a new factor set: a historical partition written with another schema,
and a changed price-limit rule of a symbol with history outside the recent window.
00999A.TWSE is an ETF whose classification is unconfirmed (no limit board) until the security
master confirms it as a domestic equity ETF (10%); it closed at its would-be limit up, 110, on
the second session. 7777.TWSE is an ordinary stock listed on the seventh stored session.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.enriched_generation import EnrichedPublication, enriched_publication_incomplete
from app.indicators import pipeline
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

LIMIT_RULES_KEY = "limit_rules"

SESSIONS = [
    d for d in (date(2026, 8, 31) + timedelta(days=i) for i in range(12)) if d.weekday() < 5
]
STOCK, ETF, NEW = "2330.TWSE", "00999A.TWSE", "7777.TWSE"
PRICES = {
    STOCK: [1000.0 + i for i in range(len(SESSIONS))],
    ETF: [100.0] + [110.0] * (len(SESSIONS) - 1),
}


def _instrument(
    symbol: str, *, etf_confirmed: bool = False, listing_date: str = "2020-01-01"
) -> TaiwanInstrument:
    is_etf = symbol == ETF
    return TaiwanInstrument(
        symbol=symbol,
        code=symbol.split(".")[0],
        exchange="TWSE",
        name="測試標的",
        instrument_type="etf" if is_etf else "stock",
        listing_status="active",
        listing_date=listing_date,
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="ETF" if is_etf else "股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-09-17",
        etf_category="domestic_equity" if is_etf and etf_confirmed else None,
        classification_source="official_metadata" if is_etf and etf_confirmed else None,
        underlying_scope="domestic" if is_etf and etf_confirmed else None,
    )


class _FakeMaster:
    def __init__(self) -> None:
        self.instruments = {STOCK: _instrument(STOCK), ETF: _instrument(ETF)}

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return self.instruments.get(symbol)


@pytest.fixture
def master(monkeypatch) -> _FakeMaster:
    fake = _FakeMaster()
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: fake)
    pipeline.invalidate_taiwan_limit_profile_cache()
    yield fake
    pipeline.invalidate_taiwan_limit_profile_cache()


def _bars(closes: dict[str, list[float]], days: list[date]) -> pl.DataFrame:
    rows = [
        {
            "symbol": symbol,
            "date": d,
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1_000_000.0,
            "amount": c * 1_000_000.0,
            "quote_ts": None,
        }
        for symbol, series in closes.items()
        for d, c in zip(days, series, strict=True)
    ]
    return pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64})


@pytest.fixture
def service(tmp_path, master) -> TaiwanEnrichedRefreshService:
    daily = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    daily.write_batch(_bars(PRICES, SESSIONS))
    published = TaiwanEnrichedRefreshService(
        daily_store=daily,
        enriched_root=tmp_path / "kline_daily_enriched",
        share_capital=MagicMock(as_instruments=MagicMock(return_value=pl.DataFrame())),
        corporate_actions=TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions"),
    )
    published.refresh()
    _age(tmp_path)
    return published


def _part(tmp_path: Path, d: date) -> Path:
    return tmp_path / "kline_daily_enriched" / f"date={d.isoformat()}" / "part.parquet"


def _age(tmp_path: Path) -> None:
    old = time.time() - 3600
    for d in SESSIONS:
        os.utime(_part(tmp_path, d), (old, old))
        for bars in (tmp_path / "taiwan" / "daily" / f"date={d.isoformat()}").glob("*.parquet"):
            os.utime(bars, (old - 60, old - 60))


def _mtimes(tmp_path: Path) -> dict[date, int]:
    return {d: _part(tmp_path, d).stat().st_mtime_ns for d in SESSIONS}


def _rewritten(tmp_path: Path, before: dict[date, int]) -> list[date]:
    after = _mtimes(tmp_path)
    return [d for d in SESSIONS if after[d] != before[d]]


def _record(service: TaiwanEnrichedRefreshService) -> dict:
    return json.loads(service.factor_set_path.read_text(encoding="utf-8"))


def _etf_limit_up(tmp_path: Path, d: date) -> bool:
    return pl.read_parquet(_part(tmp_path, d)).filter(pl.col("symbol") == ETF)["signal_limit_up"][0]


def test_a_historical_partition_missing_a_column_republishes_every_partition(tmp_path, service):
    stale = _part(tmp_path, SESSIONS[1])
    pl.read_parquet(stale).drop("signal_limit_up").write_parquet(stale)
    _age(tmp_path)
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS
    assert "signal_limit_up" in pl.read_parquet_schema(stale)
    assert enriched_publication_incomplete(tmp_path) is False


def test_a_changed_limit_rule_for_one_symbol_republishes_every_partition(tmp_path, service, master):
    assert _etf_limit_up(tmp_path, SESSIONS[1]) is False
    published = _record(service).get(LIMIT_RULES_KEY)
    before = _mtimes(tmp_path)
    master.instruments[ETF] = _instrument(ETF, etf_confirmed=True)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS
    assert _etf_limit_up(tmp_path, SESSIONS[1]) is True
    assert _record(service)[LIMIT_RULES_KEY] != published


def test_nothing_changed_republishes_only_the_recent_partitions(tmp_path, service):
    record = _record(service)
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[-2:]
    assert _record(service) == record


def test_a_record_without_limit_rules_adopts_the_current_rules_without_a_rebuild(
    tmp_path, service, master
):
    current = _record(service)
    legacy = {k: v for k, v in current.items() if k != LIMIT_RULES_KEY}
    service.factor_set_path.write_text(json.dumps(legacy), encoding="utf-8")
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[-2:]
    assert _record(service) == {**legacy, LIMIT_RULES_KEY: current[LIMIT_RULES_KEY]}

    _age(tmp_path)
    before = _mtimes(tmp_path)
    master.instruments[ETF] = _instrument(ETF, etf_confirmed=True)
    service.refresh_recent(days=2)
    assert _rewritten(tmp_path, before) == SESSIONS


def test_a_failed_republish_keeps_the_previous_limit_rules(tmp_path, service, master, monkeypatch):
    published = _record(service).get(LIMIT_RULES_KEY)
    master.instruments[ETF] = _instrument(ETF, etf_confirmed=True)
    written = []
    original = EnrichedPublication.write_parquet

    def failing_write(self, df, out):
        if written:
            raise OSError("disk full")
        written.append(out)
        return original(self, df, out)

    monkeypatch.setattr(EnrichedPublication, "write_parquet", failing_write)
    with pytest.raises(OSError, match="disk full"):
        service.refresh_recent(days=2)

    assert _record(service)[LIMIT_RULES_KEY] == published
    assert enriched_publication_incomplete(tmp_path) is True

    monkeypatch.setattr(EnrichedPublication, "write_parquet", original)
    _age(tmp_path)
    before = _mtimes(tmp_path)
    service.refresh_recent(days=2)
    assert _rewritten(tmp_path, before) == SESSIONS
    assert _record(service)[LIMIT_RULES_KEY] != published
    assert enriched_publication_incomplete(tmp_path) is False


def _list_new_stock(service: TaiwanEnrichedRefreshService, master: _FakeMaster) -> None:
    listed = SESSIONS[6:]
    master.instruments[NEW] = _instrument(NEW, listing_date=listed[0].isoformat())
    service.daily_store.write_batch(_bars({NEW: [50.0] * len(listed)}, listed))


def test_a_new_listing_is_recorded_without_a_full_republish(tmp_path, service, master):
    _list_new_stock(service, master)
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[6:]
    assert NEW in _record(service)[LIMIT_RULES_KEY]
    assert enriched_publication_incomplete(tmp_path) is False


def test_a_new_listing_passing_its_fifth_session_needs_no_full_republish(tmp_path, service, master):
    _list_new_stock(service, master)
    service.refresh_recent(days=2)
    rules = _record(service)[LIMIT_RULES_KEY]
    _age(tmp_path)
    fifth = date(2026, 9, 14)
    service.daily_store.write_batch(_bars({STOCK: [1010.0], ETF: [110.0], NEW: [50.0]}, [fifth]))
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[-1:]
    assert _part(tmp_path, fifth).exists()
    assert rules[NEW].endswith("|open")
    assert _record(service)[LIMIT_RULES_KEY] == {**rules, NEW: f"0.1|stock|{SESSIONS[6]}|{fifth}"}


def test_a_changed_rule_of_a_symbol_inside_the_rewritten_window_is_recorded_without_a_full_republish(
    tmp_path, service, master
):
    _list_new_stock(service, master)
    service.refresh_recent(days=2)
    rules = _record(service)[LIMIT_RULES_KEY]
    _age(tmp_path)
    master.instruments[NEW] = _instrument(NEW, listing_date=SESSIONS[7].isoformat())
    service.daily_store.write_batch(_bars({NEW: [50.0] * 4}, SESSIONS[6:]))
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[6:]
    recorded = _record(service)[LIMIT_RULES_KEY]
    assert recorded[NEW] != rules[NEW]
    assert {k: v for k, v in recorded.items() if k != NEW} == {
        k: v for k, v in rules.items() if k != NEW
    }


def _share_capital(frame: pl.DataFrame) -> MagicMock:
    return MagicMock(as_instruments=MagicMock(return_value=frame))


def test_missing_share_capital_keeps_the_schema_and_republishes_only_the_recent_partitions(
    tmp_path, service
):
    service.share_capital = _share_capital(
        pl.DataFrame({"symbol": [STOCK, ETF], "float_shares": [1.0e9, 1.0e9]})
    )
    service.refresh()
    assert pl.read_parquet_schema(_part(tmp_path, SESSIONS[0]))["turnover_rate"] == pl.Float64
    _age(tmp_path)
    service.share_capital = _share_capital(pl.DataFrame())
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[-2:]
    newest = pl.read_parquet(_part(tmp_path, SESSIONS[-1]))
    assert newest.schema["turnover_rate"] == pl.Float64
    assert newest["turnover_rate"].null_count() == newest.height


def test_a_symbol_no_longer_stored_is_dropped_from_the_rules_without_a_rebuild(tmp_path, service):
    record = _record(service)
    record[LIMIT_RULES_KEY]["9999.TWSE"] = "0.1|stock|-|-"
    service.factor_set_path.write_text(json.dumps(record), encoding="utf-8")
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[-2:]
    assert "9999.TWSE" not in _record(service)[LIMIT_RULES_KEY]


def test_a_symbol_missing_from_the_master_keeps_its_published_rule(tmp_path, service, master):
    published = _record(service)[LIMIT_RULES_KEY][STOCK]
    del master.instruments[STOCK]
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[-2:]
    assert _record(service)[LIMIT_RULES_KEY][STOCK] == published
    assert (
        pl.read_parquet(_part(tmp_path, SESSIONS[0])).filter(pl.col("symbol") == STOCK).height == 1
    )


def test_a_window_ending_before_the_newest_day_republishes_everything_on_a_changed_rule(
    tmp_path, service, master
):
    master.instruments[ETF] = _instrument(ETF, etf_confirmed=True)
    before = _mtimes(tmp_path)

    service.refresh(start=SESSIONS[2], end=SESSIONS[5])

    assert _rewritten(tmp_path, before) == SESSIONS
    assert _etf_limit_up(tmp_path, SESSIONS[1]) is True


def test_a_schema_mismatch_outside_a_window_ending_early_republishes_everything(tmp_path, service):
    stale = _part(tmp_path, SESSIONS[0])
    pl.read_parquet(stale).drop("signal_limit_up").write_parquet(stale)
    _age(tmp_path)
    before = _mtimes(tmp_path)

    service.refresh(start=SESSIONS[2], end=SESSIONS[5])

    assert _rewritten(tmp_path, before) == SESSIONS
    assert "signal_limit_up" in pl.read_parquet_schema(stale)
    assert enriched_publication_incomplete(tmp_path) is False


def test_an_unreadable_historical_partition_is_rewritten_by_one_full_republish(tmp_path, service):
    _part(tmp_path, SESSIONS[1]).write_bytes(b"not a parquet file")
    _age(tmp_path)
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS
    assert pl.read_parquet(_part(tmp_path, SESSIONS[1])).height == len(PRICES)
    _age(tmp_path)
    before = _mtimes(tmp_path)
    service.refresh_recent(days=2)
    assert _rewritten(tmp_path, before) == SESSIONS[-2:]


def test_a_limit_rules_save_failure_is_logged_and_does_not_fail_the_refresh(
    tmp_path, service, master, monkeypatch, caplog
):
    record = _record(service)
    _list_new_stock(service, master)

    def failing_write(payload):
        raise OSError("record locked")

    monkeypatch.setattr(service, "_write_record", failing_write)
    with caplog.at_level("WARNING", logger="app.taiwan.enriched_refresh"):
        service.refresh_recent(days=2)

    assert _record(service) == record
    assert "limit rules not saved" in caplog.text


def test_a_moved_final_listing_window_of_a_symbol_with_history_republishes_everything(
    tmp_path, service, master
):
    _list_new_stock(service, master)
    service.daily_store.write_batch(_bars({NEW: [50.0]}, [SESSIONS[5]]))
    master.instruments[NEW] = _instrument(NEW, listing_date=SESSIONS[5].isoformat())
    service.refresh()
    current = _record(service)[LIMIT_RULES_KEY][NEW]
    assert current == f"0.1|stock|{SESSIONS[5]}|{SESSIONS[9]}"
    record = _record(service)
    record[LIMIT_RULES_KEY][NEW] = f"0.1|stock|{SESSIONS[5]}|{SESSIONS[8]}"
    service.factor_set_path.write_text(json.dumps(record), encoding="utf-8")
    _age(tmp_path)
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS
    assert _record(service)[LIMIT_RULES_KEY][NEW] == current


def test_a_listing_whose_rule_save_failed_counts_as_changed_once_it_has_older_rows(
    tmp_path, service, master, monkeypatch
):
    _list_new_stock(service, master)
    real_write = service._write_record

    def failing_write(payload):
        raise OSError("record locked")

    monkeypatch.setattr(service, "_write_record", failing_write)
    service.refresh_recent(days=2)
    assert NEW not in _record(service)[LIMIT_RULES_KEY]
    monkeypatch.setattr(service, "_write_record", real_write)
    _age(tmp_path)
    master.instruments[NEW] = _instrument(NEW, listing_date=SESSIONS[7].isoformat())
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS
    assert NEW in _record(service)[LIMIT_RULES_KEY]


def test_a_baseline_records_symbols_missing_from_the_master_as_without_a_limit(
    tmp_path, service, master
):
    legacy = {k: v for k, v in _record(service).items() if k != LIMIT_RULES_KEY}
    service.factor_set_path.write_text(json.dumps(legacy), encoding="utf-8")
    del master.instruments[STOCK]
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS[-2:]
    assert _record(service)[LIMIT_RULES_KEY][STOCK] == "none"


def test_a_malformed_published_rule_counts_as_changed(tmp_path, service):
    record = _record(service)
    current = record[LIMIT_RULES_KEY][ETF]
    record[LIMIT_RULES_KEY][ETF] = 5
    service.factor_set_path.write_text(json.dumps(record), encoding="utf-8")
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    assert _rewritten(tmp_path, before) == SESSIONS
    assert _record(service)[LIMIT_RULES_KEY][ETF] == current
