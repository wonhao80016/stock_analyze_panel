"""分時資料拉取視窗與期望條數測試 (台股時段)。

回歸點: 分鐘 K 的拉取視窗與「本地資料是否完整」的期望條數原本寫死 A 股假設
(9:25 起、15:05 迄、全日 240 根含午休)。套在台股上會:
  - 漏掉 09:00-09:24 共 25 分鐘的開盤分鐘 K
  - 把 11:30-13:00 當成午休, 期望條數凍結在 120, 完整性判定永遠不觸發補拉
  - 09:00-09:30 期望條數算成 0, 開盤前 30 分鐘完全不補拉

另一個回歸點: 視窗必須帶時區。naive datetime 會被 .timestamp() 按伺服器本地時區
解讀, UTC 容器上整段偏移 8 小時, 分時補拉必然為空。

注意: tests/test_kline_sync_timezone.py 雖然也涵蓋視窗, 但它在到達斷言前就會
因為 provider 分流而失敗, 守不住這條回歸, 因此本檔直接測純函式。
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest

from app.api.kline import _expected_minute_rows, _past_close_with_margin
from app.market_time import CN_TZ
from app.services.kline_sync import intraday_window
from app.taiwan.realtime import calendar
from app.taiwan.realtime.calendar import TAIPEI_TZ


def _set_now(hour: int, minute: int, weekday: int = 0):
    day = 1 + ((weekday - datetime(2024, 1, 1).weekday()) % 7)
    fixed = datetime(2024, 1, day, hour, minute, tzinfo=TAIPEI_TZ)
    return patch.object(calendar, "taipei_now", lambda: fixed)


def test_window_covers_the_whole_taiwan_session():
    start, end = intraday_window(date(2026, 8, 14))
    assert (start.hour, start.minute) == (8, 55)
    assert (end.hour, end.minute) == (13, 35)
    for hour, minute in ((9, 0), (9, 1), (9, 30), (11, 30), (12, 0), (13, 0), (13, 30)):
        assert start <= datetime(2026, 8, 14, hour, minute, tzinfo=TAIPEI_TZ) <= end


def test_window_is_timezone_aware():
    """naive datetime 在 UTC 容器上會整段偏移 8 小時, 補拉必然為空。"""
    start, end = intraday_window(date(2026, 8, 14))
    assert start.tzinfo is not None
    assert end.tzinfo is not None
    assert start.utcoffset().total_seconds() == 8 * 3600


def test_expected_rows_for_a_past_date_is_the_full_session():
    with patch("app.api.kline.cn_today", lambda: date(2026, 8, 20)):
        assert _expected_minute_rows(date(2026, 8, 14)) == 270


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 0, 0),
        (9, 0, 0),
        (10, 0, 60),
        (11, 30, 150),
        (12, 30, 210),
        (13, 30, 270),
        (15, 0, 270),
    ],
)
def test_expected_rows_track_the_continuous_session(hour: int, minute: int, expected: int):
    """11:30-13:00 必須持續累加 —— A 股模型在此凍結於 120。"""
    today = date(2024, 1, 1)
    with _set_now(hour, minute), patch("app.api.kline.cn_today", lambda: today):
        assert _expected_minute_rows(today) == expected


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (9, 0, False),
        (13, 0, False),
        (13, 30, False),
        (13, 59, False),
        (14, 0, True),
        (15, 30, True),
    ],
)
def test_close_fallback_gate_follows_the_taiwan_close(hour: int, minute: int, expected: bool):
    """收盘+30 分缓冲后才回退到最近有资料的交易日。

    用 A 股的 15:30 会让平日的台股假日在 13:30-15:30 之间不回退, 分时图空白。
    """
    now = datetime(2026, 9, 17, hour, minute, tzinfo=CN_TZ)
    assert _past_close_with_margin(now) is expected
