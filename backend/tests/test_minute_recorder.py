"""MIS 快照折算分鐘 K 測試。

台股沒有第一方分鐘 K 資料源, 分鐘棒由即時輪詢的快照累積而成。此處守住三件事:
  - OHLC 取自該分鐘內所有快照, 成交量取累計量的差分 (MIS 的 v 是當日累計)
  - 重啟後第一筆快照不得把重啟前的累計量算進當下這一分鐘
  - 非交易時段的快照不落盤
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.minute_recorder import MinuteBarRecorder


def _quote(hour: int, minute: int, second: int, price: float, cum_volume: float) -> dict:
    ts = datetime(2026, 9, 17, hour, minute, second, tzinfo=TAIPEI_TZ)
    return {
        "symbol": "3545.TWSE",
        "close": price,
        "volume": cum_volume,
        "quote_ts": int(ts.timestamp() * 1000),
        "status": "realtime",
    }


def _read(tmp_path: Path) -> list[dict]:
    target = tmp_path / "kline_minute" / "date=2026-09-17" / "part.parquet"
    if not target.exists():
        return []
    return pl.read_parquet(target).sort("datetime").to_dicts()


@pytest.fixture
def recorder(tmp_path: Path, monkeypatch) -> MinuteBarRecorder:
    monkeypatch.setattr(
        "app.taiwan.realtime.minute_recorder.get_market_status",
        lambda *a, **k: __import__(
            "app.taiwan.realtime.calendar", fromlist=["MarketStatus"],
        ).MarketStatus.OPEN,
    )
    return MinuteBarRecorder(data_dir=tmp_path)


def test_bar_takes_ohlc_from_every_snapshot_in_the_minute(recorder, tmp_path):
    for q in (
        _quote(10, 0, 0, 50.0, 1000),
        _quote(10, 0, 12, 50.3, 1400),
        _quote(10, 0, 30, 49.9, 1800),
        _quote(10, 0, 48, 50.2, 2000),
    ):
        recorder.record([q])
    recorder.flush()

    bars = _read(tmp_path)
    assert len(bars) == 1
    bar = bars[0]
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (50.0, 50.3, 49.9, 50.2)


def test_volume_is_the_difference_of_the_cumulative_total(recorder, tmp_path):
    """MIS 的 v 是當日累計量, 直接寫入會讓每分鐘都等於全日量。"""
    for q in (
        _quote(10, 0, 0, 50.0, 1000),
        _quote(10, 0, 30, 50.0, 1800),
        _quote(10, 0, 48, 50.0, 2000),
        _quote(10, 1, 6, 50.0, 2600),
    ):
        recorder.record([q])
    recorder.flush()

    bars = _read(tmp_path)
    assert len(bars) == 2
    # 第一根: 基準線建立於 1000, 之後累積到 2000 -> 1000 股
    assert bars[0]["volume"] == 1000.0
    # 第二根: 2000 -> 2600
    assert bars[1]["volume"] == 600.0


def test_first_snapshot_after_restart_contributes_no_volume(recorder, tmp_path):
    """重啟時已累計的成交量屬於沒看到的分鐘, 不能記在當下這一分鐘。"""
    recorder.record([_quote(10, 0, 0, 50.0, 500000)])
    recorder.flush()

    bars = _read(tmp_path)
    assert len(bars) == 1
    assert bars[0]["volume"] == 0.0


def test_amount_is_volume_weighted(recorder, tmp_path):
    """amount 逐筆累加 價格x增量, 因此 amount/volume 是該分鐘的加權均價。"""
    for q in (
        _quote(10, 0, 0, 50.0, 1000),
        _quote(10, 0, 20, 60.0, 2000),   # +1000 股 @ 60
        _quote(10, 0, 40, 40.0, 4000),   # +2000 股 @ 40
    ):
        recorder.record([q])
    recorder.flush()

    bar = _read(tmp_path)[0]
    assert bar["volume"] == 3000.0
    assert bar["amount"] == pytest.approx(60.0 * 1000 + 40.0 * 2000)
    assert bar["amount"] / bar["volume"] == pytest.approx(46.6667, abs=1e-3)


def test_snapshot_outside_the_session_is_dropped(recorder, tmp_path):
    recorder.record([_quote(8, 45, 0, 50.0, 1000)])   # 盤前試撮
    recorder.record([_quote(14, 0, 0, 50.0, 2000)])   # 盤後定價
    recorder.flush()

    assert _read(tmp_path) == []


def test_cumulative_reset_does_not_produce_negative_volume(recorder, tmp_path):
    """累計量倒退代表新的一場或資料源重置, 應重建基準而非寫出負量。"""
    recorder.record([_quote(10, 0, 0, 50.0, 5000)])
    recorder.record([_quote(10, 0, 30, 50.0, 100)])
    recorder.flush()

    bar = _read(tmp_path)[0]
    assert bar["volume"] == 0.0


def test_reread_merges_instead_of_replacing(recorder, tmp_path):
    """同一分區多次寫入須合併, 否則每輪都會覆蓋掉先前累積的分鐘棒。"""
    recorder.record([_quote(10, 0, 0, 50.0, 1000)])
    recorder.record([_quote(10, 1, 0, 51.0, 2000)])   # 完成 10:00 那根
    recorder.record([_quote(10, 2, 0, 52.0, 3000)])   # 完成 10:01 那根
    recorder.flush()

    bars = _read(tmp_path)
    assert [b["close"] for b in bars] == [50.0, 51.0, 52.0]


def test_temp_file_is_invisible_to_the_reader_glob(recorder, tmp_path, monkeypatch):
    """寫檔過程中的暫存檔不得符合讀取端的 *.parquet glob。

    讀取端是 kline_minute/**/*.parquet。暫存檔若也叫 .parquet, 寫入視窗內會被當成
    第二個分區檔一起讀走, 每一列讀兩次 —— 成交量憑空翻倍, 而且不會有任何錯誤。
    """
    seen: list[list[str]] = []
    real_replace = os.replace

    def _spy(src, dst):
        seen.append(sorted(p.name for p in Path(dst).parent.glob("*.parquet")))
        return real_replace(src, dst)

    monkeypatch.setattr("app.taiwan.realtime.minute_recorder.os.replace", _spy)

    recorder.record([_quote(10, 0, 0, 50.0, 1000)])
    recorder.record([_quote(10, 1, 0, 51.0, 2000)])
    recorder.flush()

    assert seen, "expected at least one partition write"
    for names in seen:
        assert names in ([], ["part.parquet"]), f"temp file matched the reader glob: {names}"


def test_close_flush_persists_the_last_bar_of_the_day(recorder, tmp_path):
    """收盤那一根不會再被跨分鐘事件寫出, 只能靠收盤定版時的 flush。"""
    recorder.record([_quote(13, 29, 0, 50.0, 1000)])
    recorder.record([_quote(13, 30, 0, 53.0, 2000)])
    assert [b["datetime"].hour for b in _read(tmp_path)] == [5]  # 13:29 台北 = 05:29 UTC

    recorder.flush()

    bars = _read(tmp_path)
    assert len(bars) == 2
    assert bars[-1]["close"] == 53.0
