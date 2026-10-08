"""Minute bars across recording gaps, the closing-auction bar, and old partitions without the gap column.

Volume that accumulated while nobody was polling cannot be placed in any one minute, so it is kept
apart in ``unattributed_volume`` instead of being credited to the first minute seen after the gap.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.api import kline as kline_api
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.minute_recorder import MinuteBarRecorder
from app.tickflow.repository import DataStore, KlineRepository
from tests.test_stage3_closing_auction import closing_result, harness, minute_bars  # noqa: F401
from tests.test_stage3_session_model import SESSION, at, tsmc_continuous_1324

pytestmark = pytest.mark.usefixtures("deny_network")

DAY = date(2026, 9, 17)
SYMBOL = "3545.TWSE"
OLD_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]


def _quote(
    clock: tuple[int, int, int],
    price: float,
    cum_volume: float,
    *,
    seen: tuple[int, int, int] | None = None,
) -> dict:
    quote_time = datetime(DAY.year, DAY.month, DAY.day, *clock, tzinfo=TAIPEI_TZ)
    row = {
        "symbol": SYMBOL,
        "close": price,
        "volume": cum_volume,
        "quote_ts": int(quote_time.timestamp() * 1000),
        "status": "realtime",
    }
    if seen is not None:
        row["retrieved_at"] = datetime(
            DAY.year, DAY.month, DAY.day, *seen, tzinfo=TAIPEI_TZ
        ).isoformat()
    return row


def _bars(root, day: date = DAY) -> list[dict]:
    target = root / "kline_minute" / f"date={day.isoformat()}" / "part.parquet"
    if not target.exists():
        return []
    return pl.read_parquet(target).sort("datetime").to_dicts()


def _by_minute(bars: list[dict]) -> dict[str, dict]:
    return {b["datetime"].astimezone(TAIPEI_TZ).strftime("%H:%M"): b for b in bars}


def test_a_45_minute_gap_does_not_spike_the_next_minute(tmp_path):
    recorder = MinuteBarRecorder(data_dir=tmp_path)
    for q in (
        _quote((10, 0, 0), 50.0, 1_000),
        _quote((10, 0, 30), 50.5, 1_500),
        _quote((10, 45, 10), 52.0, 50_000),
        _quote((10, 45, 40), 52.5, 50_600),
        _quote((10, 46, 5), 52.4, 51_000),
    ):
        recorder.record([q])
    recorder.flush()

    bars = _by_minute(_bars(tmp_path))
    assert bars["10:00"]["volume"] == 500.0
    assert bars["10:00"]["unattributed_volume"] == 1_000.0
    assert bars["10:45"]["volume"] == 600.0
    assert bars["10:45"]["amount"] == pytest.approx(52.5 * 600)
    assert bars["10:45"]["unattributed_volume"] == 48_500.0
    assert bars["10:46"]["volume"] == 400.0
    assert bars["10:46"]["unattributed_volume"] is None


def test_the_gap_is_measured_by_poll_time_not_by_the_last_trade_time(tmp_path):
    """An illiquid stock polled every few seconds keeps an old trade time; its next trade is not a gap."""
    recorder = MinuteBarRecorder(data_dir=tmp_path)
    recorder.record([_quote((10, 0, 0), 50.0, 1_000, seen=(10, 0, 2))])
    for second in range(7, 300, 5):
        recorder.record([_quote((10, 0, 0), 50.0, 1_000, seen=(10, second // 60, second % 60))])
    recorder.record([_quote((10, 5, 1), 51.0, 3_000, seen=(10, 5, 2))])
    recorder.flush()

    bars = _by_minute(_bars(tmp_path))
    assert bars["10:05"]["volume"] == 2_000.0
    assert bars["10:05"]["unattributed_volume"] is None


def test_a_poll_gap_is_detected_even_when_the_quote_times_are_close(tmp_path):
    recorder = MinuteBarRecorder(data_dir=tmp_path)
    recorder.record([_quote((10, 0, 0), 50.0, 1_000, seen=(10, 0, 2))])
    recorder.record([_quote((10, 0, 50), 50.0, 9_000, seen=(10, 3, 0))])
    recorder.flush()

    bar = _by_minute(_bars(tmp_path))["10:00"]
    assert bar["volume"] == 0.0
    assert bar["unattributed_volume"] == 1_000.0 + 8_000.0


def test_the_first_snapshot_after_a_restart_keeps_its_total_unattributed(tmp_path):
    recorder = MinuteBarRecorder(data_dir=tmp_path)
    recorder.record([_quote((11, 0, 0), 50.0, 500_000)])
    recorder.flush()

    bar = _bars(tmp_path)[0]
    assert bar["volume"] == 0.0
    assert bar["unattributed_volume"] == 500_000.0


def test_the_closing_auction_volume_lands_on_the_1330_bar_without_unattributed_volume(tmp_path):
    """Nothing trades between 13:25 and 13:30, so polling through the auction leaves no gap."""
    recorder = MinuteBarRecorder(data_dir=tmp_path)
    recorder.record([_quote((13, 24, 58), 50.0, 14_124_000, seen=(13, 24, 59))])
    recorder.record([_quote((13, 30, 0), 50.5, 20_154_000, seen=(13, 30, 5))])
    recorder.flush()

    bar = _by_minute(_bars(tmp_path))["13:30"]
    assert bar["volume"] == 6_030_000.0
    assert bar["unattributed_volume"] is None


def test_the_1330_bar_exists_through_the_live_poll(harness):  # noqa: F811
    harness.poll(at(13, 24, 59), tsmc_continuous_1324())
    harness.poll(at(13, 27, 0), tsmc_continuous_1324())
    harness.poll(at(13, 30, 5), closing_result())

    bars = _by_minute(minute_bars(harness.tmp_path))
    assert bars["13:30"]["volume"] == 6_030_000.0
    assert bars["13:30"]["unattributed_volume"] is None


def test_a_live_poll_gap_keeps_its_volume_apart(harness):  # noqa: F811
    def mis_at(clock: tuple[int, int, int], lots: int) -> dict:
        payload = tsmc_continuous_1324()
        quote_time = datetime(SESSION.year, SESSION.month, SESSION.day, *clock, tzinfo=TAIPEI_TZ)
        payload["msgArray"][0].update(
            {
                "t": quote_time.strftime("%H:%M:%S"),
                "tlong": str(int(quote_time.timestamp() * 1000)),
                "v": str(lots),
            }
        )
        return payload

    harness.poll(at(10, 0, 5), mis_at((10, 0, 3), 5_000))
    harness.poll(at(10, 45, 12), mis_at((10, 45, 10), 9_000))
    harness.poll(at(10, 46, 2), mis_at((10, 46, 0), 9_100))

    bars = _by_minute(minute_bars(harness.tmp_path))
    assert bars["10:45"]["volume"] == 0.0
    assert bars["10:45"]["unattributed_volume"] == 4_000_000.0


def _write_old_partition(root, folder: str, day: date) -> None:
    target = root / folder / f"date={day.isoformat()}" / "part.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": [SYMBOL],
            "datetime": [datetime(day.year, day.month, day.day, 10, 0, tzinfo=TAIPEI_TZ)],
            "open": [49.0],
            "high": [49.0],
            "low": [49.0],
            "close": [49.0],
            "volume": [700.0],
            "amount": [34_300.0],
        }
    ).write_parquet(target)


def _record_gap_day(root) -> None:
    recorder = MinuteBarRecorder(data_dir=root)
    recorder.record([_quote((10, 0, 0), 50.0, 1_000)])
    recorder.record([_quote((10, 30, 0), 50.0, 4_000)])
    recorder.flush()


def test_repository_reads_old_partitions_next_to_new_ones(tmp_path):
    previous = DAY - timedelta(days=1)
    _write_old_partition(tmp_path, "kline_minute", previous)
    _record_gap_day(tmp_path)
    repo = KlineRepository(DataStore(tmp_path))

    old = repo.get_minute(SYMBOL, previous)
    new = repo.get_minute(SYMBOL, DAY)
    assert old["volume"].to_list() == [700.0]
    assert old["unattributed_volume"].to_list() == [None]
    assert new["unattributed_volume"].to_list() == [1_000.0, 3_000.0]

    batch = repo.get_minute_batch([SYMBOL], previous)
    assert batch["volume"].to_list() == [700.0]

    ranged = repo.get_minute_range([SYMBOL], previous, DAY)
    assert ranged["volume"].to_list() == [700.0, 0.0, 0.0]

    parts = [
        str(tmp_path / "kline_minute" / f"date={day.isoformat()}" / "part.parquet")
        for day in (previous, DAY)
    ]
    assert repo._scan_minute(parts).collect()["unattributed_volume"].to_list() == [
        None,
        1_000.0,
        3_000.0,
    ]


def test_repository_reads_new_partitions_before_old_ones(tmp_path):
    _record_gap_day(tmp_path)
    later = DAY + timedelta(days=1)
    _write_old_partition(tmp_path, "kline_minute", later)
    repo = KlineRepository(DataStore(tmp_path))

    assert repo.get_minute(SYMBOL, later)["volume"].to_list() == [700.0]
    assert repo.get_minute(SYMBOL, DAY)["unattributed_volume"].to_list() == [1_000.0, 3_000.0]
    assert repo.get_minute_range([SYMBOL], DAY, later).height == 3


def test_recording_into_an_old_partition_keeps_its_rows(tmp_path):
    _write_old_partition(tmp_path, "kline_minute", DAY)
    recorder = MinuteBarRecorder(data_dir=tmp_path)
    recorder.record([_quote((10, 5, 0), 50.0, 1_000)])
    recorder.record([_quote((10, 6, 0), 50.0, 1_200)])
    recorder.flush()

    bars = _by_minute(_bars(tmp_path))
    assert bars["10:00"]["volume"] == 700.0
    assert bars["10:00"]["unattributed_volume"] is None
    assert bars["10:06"]["volume"] == 200.0


def _api_repo(minute: pl.DataFrame, day_volumes: dict[date, float]) -> MagicMock:
    daily = pl.DataFrame(
        {
            "date": list(day_volumes),
            "close": [50.0] * len(day_volumes),
            "raw_close": [50.0] * len(day_volumes),
            "volume": list(day_volumes.values()),
        }
    )

    def daily_asset(_asset_type, _symbol, start, end, columns=None):
        out = daily.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        return out.select([c for c in columns if c in out.columns]) if columns else out

    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.get_instruments.return_value = pl.DataFrame(
        schema={"symbol": pl.String, "name": pl.String}
    )
    repo.get_daily_asset.side_effect = daily_asset
    repo.get_minute.return_value = minute
    repo.get_minute_range.return_value = minute
    return repo


def _minute_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["2330.TWSE"] * 2,
            "datetime": [
                datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI_TZ),
                datetime(2026, 10, 8, 10, 0, tzinfo=TAIPEI_TZ),
            ],
            "open": [50.0, 50.0],
            "high": [50.0, 50.0],
            "low": [50.0, 50.0],
            "close": [50.0, 50.0],
            "volume": [100.0, 200.0],
            "amount": [5_000.0, 10_000.0],
            "unattributed_volume": [None, 300.0],
        }
    )


def test_minute_api_serves_the_unattributed_volume_for_coverage(monkeypatch):
    monkeypatch.setattr(kline_api, "_taiwan_reference_prices", lambda *a, **k: {})
    monkeypatch.setattr(kline_api.kline_sync, "fetch_minute_single", lambda *a, **k: pl.DataFrame())
    repo = _api_repo(
        _minute_frame().filter(pl.col("volume") == 200.0),
        {
            date(2026, 10, 7): 1_000.0,
            date(2026, 10, 8): 2_500.0,
        },
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))

    result = kline_api.get_minute(request, symbol="2330.TWSE", trade_date=date(2026, 10, 8))

    assert result["rows"][0]["unattributed_volume"] == 300.0


def test_minute_range_serves_each_session_unattributed_volume(monkeypatch):
    monkeypatch.setattr(kline_api, "_taiwan_reference_prices", lambda *a, **k: {})
    monkeypatch.setattr(kline_api, "cn_today", lambda: date(2026, 10, 8))
    repo = _api_repo(_minute_frame(), {date(2026, 10, 7): 1_000.0})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))

    result = kline_api.get_minute_range(request, "2330.TWSE", 2)

    assert [[r["unattributed_volume"] for r in s["rows"]] for s in result["sessions"]] == [
        [None],
        [300.0],
    ]
