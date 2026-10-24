"""Daily and institutional stores: latest row per symbol without reading every stored day, and retried replaces."""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.taiwan import daily_store as daily_store_module
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.institutional_store import TaiwanInstitutionalStore

FIRST = date(2026, 9, 1)
DAYS = [FIRST + timedelta(days=i) for i in range(10)]


def _daily_rows(day: date, symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": symbols,
        "date": [day] * len(symbols),
        "open": [10.0] * len(symbols),
        "high": [11.0] * len(symbols),
        "low": [9.0] * len(symbols),
        "close": [10.0 + day.day] * len(symbols),
        "volume": [1000.0] * len(symbols),
        "amount": [10_000.0] * len(symbols),
        "quote_ts": [0] * len(symbols),
    })


def _write_partitions(root: Path, frame_for) -> None:
    for day in DAYS:
        # 1101 stops trading after the first day, so its latest row is the oldest partition.
        symbols = ["2330.TWSE", "8069.TPEX"] + (["1101.TWSE"] if day == FIRST else [])
        part = root / f"date={day.isoformat()}" / "part.parquet"
        part.parent.mkdir(parents=True, exist_ok=True)
        frame_for(day, symbols).write_parquet(part)


class _FullReadSpy:
    """Records partitions read with every column; reads of named columns are left out."""

    def __init__(self, monkeypatch) -> None:
        self.paths: set[str] = set()
        real_scan, real_read = pl.scan_parquet, pl.read_parquet

        def scan(source, *args, **kwargs):
            self.paths.add(Path(str(source)).parent.name)
            return real_scan(source, *args, **kwargs)

        def read(source, *args, columns=None, **kwargs):
            if columns is None:
                self.paths.add(Path(str(source)).parent.name)
            return real_read(source, *args, columns=columns, **kwargs)

        monkeypatch.setattr(pl, "scan_parquet", scan)
        monkeypatch.setattr(pl, "read_parquet", read)


def _expected_latest() -> dict[str, date]:
    return {"1101.TWSE": FIRST, "2330.TWSE": DAYS[-1], "8069.TPEX": DAYS[-1]}


def test_daily_latest_per_symbol_reads_only_the_partitions_holding_a_latest_row(tmp_path, monkeypatch):
    _write_partitions(tmp_path, _daily_rows)
    store = TaiwanDailyStore(data_dir=tmp_path)
    spy = _FullReadSpy(monkeypatch)

    latest = store.read_latest_per_symbol()

    assert dict(zip(latest["symbol"], latest["date"], strict=True)) == _expected_latest()
    assert latest.columns == daily_store_module.TAIWAN_DAILY_COLUMNS
    assert spy.paths == {f"date={FIRST.isoformat()}", f"date={DAYS[-1].isoformat()}"}


def test_daily_latest_per_symbol_reuses_its_result_until_a_partition_changes(tmp_path, monkeypatch):
    _write_partitions(tmp_path, _daily_rows)
    store = TaiwanDailyStore(data_dir=tmp_path)
    store.read_latest_per_symbol()
    spy = _FullReadSpy(monkeypatch)

    latest = store.read_latest_per_symbol(["2330.TWSE"])
    assert latest["date"].to_list() == [DAYS[-1]]
    assert spy.paths == set()

    store.write_batch(_daily_rows(DAYS[-1], ["2330.TWSE"]).with_columns(pl.lit(99.0).alias("close")))
    latest = TaiwanDailyStore(data_dir=tmp_path).read_latest_per_symbol(["2330.TWSE"])
    assert latest["close"].to_list() == [99.0]


def test_daily_latest_per_symbol_skips_an_unreadable_partition(tmp_path):
    _write_partitions(tmp_path, _daily_rows)
    (tmp_path / f"date={DAYS[-1].isoformat()}" / "part.parquet").write_bytes(b"not parquet")
    store = TaiwanDailyStore(data_dir=tmp_path)

    latest = store.read_latest_per_symbol()

    assert dict(zip(latest["symbol"], latest["date"], strict=True)) == {
        "1101.TWSE": FIRST, "2330.TWSE": DAYS[-2], "8069.TPEX": DAYS[-2],
    }


def _institutional_rows(day: date, symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": symbols,
        "date": [day] * len(symbols),
        "trade_date": [day] * len(symbols),
        "foreign_net": [day.day] * len(symbols),
    })


def test_institutional_latest_per_symbol_reads_only_the_partitions_holding_a_latest_row(tmp_path, monkeypatch):
    store = TaiwanInstitutionalStore(data_dir=tmp_path)
    for day in DAYS:
        symbols = ["2330.TWSE", "8069.TPEX"] + (["1101.TWSE"] if day == FIRST else [])
        store.write_batch(_institutional_rows(day, symbols))
    spy = _FullReadSpy(monkeypatch)

    latest = store.read_latest_per_symbol()

    assert dict(zip(latest["symbol"], latest["date"], strict=True)) == _expected_latest()
    assert latest.filter(pl.col("symbol") == "2330.TWSE")["foreign_net"].to_list() == [DAYS[-1].day]
    assert spy.paths == {f"date={FIRST.isoformat()}", f"date={DAYS[-1].isoformat()}"}


def test_daily_partition_replace_is_retried_while_a_reader_holds_the_file(tmp_path, monkeypatch):
    store = TaiwanDailyStore(data_dir=tmp_path)
    store.write_batch(_daily_rows(FIRST, ["2330.TWSE"]))
    real_replace = os.replace
    attempts: list[str] = []

    def held_once(src, dst):
        attempts.append(str(dst))
        if len(attempts) == 1:
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", held_once)
    monkeypatch.setattr("app.taiwan.universe.service.time.sleep", lambda _s: None)

    store.write_batch(_daily_rows(FIRST, ["8069.TPEX"]))

    assert len(attempts) == 2
    stored = pl.read_parquet(tmp_path / f"date={FIRST.isoformat()}" / "part.parquet")
    assert sorted(stored["symbol"]) == ["2330.TWSE", "8069.TPEX"]
    assert [p.name for p in (tmp_path / f"date={FIRST.isoformat()}").iterdir()] == ["part.parquet"]


@pytest.mark.parametrize("symbols", [None, ["2330.TWSE", "1101.TWSE"]])
def test_daily_latest_per_symbol_matches_a_full_history_scan(tmp_path, symbols):
    _write_partitions(tmp_path, _daily_rows)
    store = TaiwanDailyStore(data_dir=tmp_path)
    history = store.read_all(symbols)
    expected = (
        history.filter(pl.col("date") == pl.col("date").max().over("symbol")).sort(["symbol", "date"])
    )

    assert store.read_latest_per_symbol(symbols).equals(expected)
