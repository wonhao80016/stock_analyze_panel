"""quote_service 行情輪詢階段測試 (台股時段)。

回歸點: _market_phase 曾寫死 A 股階段機 (preopen 9:15 / morning / morning_final /
pre_afternoon / afternoon / close_final 15:00)。套用在台股上會造成:
  - 09:00-09:15 盤中不拉行情 (階段為 closed)
  - 11:30-13:00 落入 morning_final, 拉一次成功後即停止, 行情凍結在 11:30
  - 13:30-15:00 已收盤卻仍判為 afternoon 持續輪詢

修正: 階段界線改由 app.taiwan.realtime.calendar 的時段常數決定, 台股連續交易
09:00-13:30 無午休, 因此只剩收盤一個 final 邊界。
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest

from app.services.quote_service import QuoteService
from app.taiwan.realtime import calendar
from app.taiwan.realtime.calendar import TAIPEI_TZ


def _taipei_now_at(hour: int, minute: int, weekday: int = 0):
    """構造一個台北時間固定時刻的 taipei_now 替身。weekday: 0=週一 ... 6=週日。"""
    day = 1 + ((weekday - datetime(2024, 1, 1).weekday()) % 7)
    fixed = datetime(2024, 1, day, hour, minute, tzinfo=TAIPEI_TZ)

    def _fn():
        return fixed

    return _fn


def _set_now(hour: int, minute: int, weekday: int = 0):
    return patch.object(calendar, "taipei_now", _taipei_now_at(hour, minute, weekday))


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (7, 0, "closed"),
        (8, 29, "closed"),
        (8, 30, "preopen"),
        (8, 59, "preopen"),
        (9, 0, "regular"),
        (11, 30, "regular"),
        (12, 0, "regular"),
        (13, 30, "regular"),
        (13, 31, "close_final"),
        (15, 0, "close_final"),
        (23, 30, "close_final"),
    ],
)
def test_weekday_phase_boundaries(hour: int, minute: int, expected: str):
    with _set_now(hour, minute, weekday=0):
        assert QuoteService._market_phase() == expected


@pytest.mark.parametrize("weekday", [5, 6])
def test_weekend_is_always_closed(weekday: int):
    for hour in (9, 12, 14):
        with _set_now(hour, 0, weekday=weekday):
            assert QuoteService._market_phase() == "closed"


def test_lunch_hours_keep_polling():
    """11:30-13:00 是台股盤中, 必須持續輪詢而非進入 final 定版。"""
    svc = QuoteService()
    for hour, minute in ((11, 30), (12, 0), (12, 55), (13, 0)):
        with _set_now(hour, minute, weekday=0):
            phase = QuoteService._market_phase()
            assert phase == "regular"
            assert svc._should_poll_for_phase(phase) is True


def test_open_first_fifteen_minutes_polls():
    """09:00-09:15 是台股盤中, A 股階段機在此不拉行情。"""
    svc = QuoteService()
    with _set_now(9, 5, weekday=0):
        phase = QuoteService._market_phase()
        assert phase == "regular"
        assert svc._should_poll_for_phase(phase) is True


def test_close_final_stops_after_successful_sync():
    """收盤定版成功後停止輪詢, 不再對收盤後的靜止行情持續拉取。"""
    svc = QuoteService()
    with _set_now(14, 0, weekday=0), \
            patch.object(calendar, "taipei_today", lambda: date(2024, 1, 1)):
        phase = QuoteService._market_phase()
        assert phase == "close_final"
        assert svc._should_poll_for_phase(phase) is True

        key = svc._final_sync_key(phase)
        assert key == (date(2024, 1, 1), "close")
        svc._final_sync_done.add(key)
        assert svc._should_poll_for_phase(phase) is False


def test_no_morning_final_key():
    """台股無午休定版, 只有收盤一個 final 邊界。"""
    svc = QuoteService()
    assert svc._final_sync_key("regular") is None
    assert svc._final_sync_key("preopen") is None
    assert svc._final_sync_key("closed") is None
