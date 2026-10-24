from __future__ import annotations

import warnings
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.tickflow.repository import DataStore, KlineRepository

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOL = "2330.TWSE"
DAY = date(2026, 9, 17)


def _session_bars(*, unattributed: float | None, with_gap_column: bool = True) -> pl.DataFrame:
    start = datetime(DAY.year, DAY.month, DAY.day, 9, 0, tzinfo=TAIPEI_TZ)
    times = [start + timedelta(minutes=step) for step in range(265)]
    times.append(datetime(DAY.year, DAY.month, DAY.day, 13, 30, tzinfo=TAIPEI_TZ))
    count = len(times)
    frame = pl.DataFrame({
        "symbol": [SYMBOL] * count,
        "datetime": times,
        "open": [10.0] * count,
        "high": [10.0] * count,
        "low": [10.0] * count,
        "close": [10.0] * count,
        "volume": [100.0] * count,
        "amount": [1_000.0] * count,
    })
    if with_gap_column:
        # The recorder paused around 10:00 and placed the missed volume in no minute.
        gap = [None] * count
        gap[60] = unattributed
        frame = frame.with_columns(pl.Series("unattributed_volume", gap, dtype=pl.Float64))
    return frame


def _write(tmp_path, frame: pl.DataFrame, day: date = DAY) -> KlineRepository:
    partition = tmp_path / "kline_minute" / f"date={day.isoformat()}"
    partition.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(partition / "part.parquet")
    return KlineRepository(DataStore(tmp_path))


def _fill_rows(repo: KlineRepository) -> dict:
    return BacktestEngine._load_minute_for_fills(repo, [SYMBOL], {DAY.isoformat()}, "stock")


# B102: the repository hands the recorder's gap column to the minute-fill coverage check.

def test_a_recorded_pause_reaches_the_fill_check_and_drops_the_session(tmp_path) -> None:
    repo = _write(tmp_path, _session_bars(unattributed=5_000.0))

    bars = repo.get_minute_by_dates([SYMBOL], [DAY])

    assert bars["unattributed_volume"].drop_nulls().to_list() == [5_000.0]
    assert _fill_rows(repo) == {}


def test_an_old_partition_without_the_gap_column_still_reads_as_complete(tmp_path) -> None:
    repo = _write(tmp_path, _session_bars(unattributed=None, with_gap_column=False))

    bars = repo.get_minute_by_dates([SYMBOL], [DAY])

    assert bars["unattributed_volume"].null_count() == bars.height
    assert (SYMBOL, DAY.isoformat()) in _fill_rows(repo)


# B097: minute reads use the supported collect API and raise on unreadable partitions.

def _corrupt(tmp_path, day: date) -> None:
    partition = tmp_path / "kline_minute" / f"date={day.isoformat()}"
    partition.mkdir(parents=True)
    (partition / "part.parquet").write_bytes(b"not a parquet file")


def test_minute_reads_emit_no_streaming_deprecation(tmp_path) -> None:
    repo = _write(tmp_path, _session_bars(unattributed=None))

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        by_dates = repo.get_minute_by_dates([SYMBOL], [DAY])
        by_range = repo.get_minute_range([SYMBOL], DAY, DAY)

    assert by_dates.height == by_range.height == 266


@pytest.mark.parametrize("read", ["by_dates", "range"])
def test_an_unreadable_minute_partition_raises(tmp_path, read: str) -> None:
    repo = _write(tmp_path, _session_bars(unattributed=None))
    _corrupt(tmp_path, date(2026, 9, 18))

    with pytest.raises(pl.exceptions.PolarsError):
        if read == "by_dates":
            repo.get_minute_by_dates([SYMBOL], [date(2026, 9, 18)])
        else:
            repo.get_minute_range([SYMBOL], DAY, date(2026, 9, 18))


def test_missing_minute_data_is_still_an_empty_result(tmp_path) -> None:
    repo = KlineRepository(DataStore(tmp_path))

    assert repo.get_minute_by_dates([SYMBOL], [DAY]).is_empty()
    assert repo.get_minute_range([SYMBOL], DAY, DAY).is_empty()

    repo = _write(tmp_path, _session_bars(unattributed=None))
    assert repo.get_minute_range([SYMBOL], date(2026, 10, 1), date(2026, 10, 2)).is_empty()
    assert repo.get_minute_by_dates([SYMBOL], [date(2026, 10, 1)]).is_empty()
