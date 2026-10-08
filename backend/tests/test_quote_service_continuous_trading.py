"""quote_service 連續交易時段測試 (台股時段回歸)。

回歸點: _is_continuous_trading 曾寫死 A 股時段 (9:30-11:30 / 13:00-15:00)。
台股 09:00-13:30 連續交易無午休, 舊窗口會讓 09:00-09:30 與 11:30-13:00 共 120 分鐘
的盤中時段被判為非交易時段 —— 監控規則停止評估。

修正: 委派 app.taiwan.realtime.calendar.get_market_status(), 由日曆模組單一定義台股時段。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from app.services.quote_service import QuoteService
from app.taiwan.realtime import calendar
from app.taiwan.realtime.calendar import TAIPEI_TZ


def _taipei_now_at(hour: int, minute: int, weekday: int = 0):
    """構造一個台北時間固定時刻的 taipei_now 替身。weekday: 0=週一 ... 6=週日。"""
    # 2024-01-01 是週一, 據此定位目標 weekday 的日期
    day = 1 + ((weekday - datetime(2024, 1, 1).weekday()) % 7)
    fixed = datetime(2024, 1, day, hour, minute, tzinfo=TAIPEI_TZ)

    def _fn():
        return fixed

    return _fn


def _set_now(hour: int, minute: int, weekday: int = 0):
    """patch 日曆模組的 taipei_now 到固定時刻。"""
    return patch.object(calendar, "taipei_now", _taipei_now_at(hour, minute, weekday))


# ── 台股連續交易時段 (應返回 True) ───────────────────────────────────
def test_open_0900_is_continuous():
    """09:00 開盤第一刻。A 股窗口在此為 False, 台股必須為 True。"""
    with _set_now(9, 0, weekday=0):
        assert QuoteService._is_continuous_trading() is True


def test_mid_morning_1000_is_continuous():
    with _set_now(10, 0, weekday=0):
        assert QuoteService._is_continuous_trading() is True


def test_no_lunch_break_1200_is_continuous():
    """12:00 台股無午休, 仍連續交易 (最關鍵回歸點: A 股窗口在此為 False)。"""
    with _set_now(12, 0, weekday=0):
        assert QuoteService._is_continuous_trading() is True


def test_no_lunch_break_1130_is_continuous():
    """11:30 A 股早盤收盤, 台股照常交易。"""
    with _set_now(11, 30, weekday=0):
        assert QuoteService._is_continuous_trading() is True


def test_close_1324_is_continuous():
    """13:24 is the last minute before the closing auction and still continuous trading."""
    with _set_now(13, 24, weekday=0):
        assert QuoteService._is_continuous_trading() is True


def test_closing_auction_1325_to_1330_not_continuous():
    """The 13:25-13:30 closing auction only carries trial prices, so alerts pause."""
    for minute in (25, 27, 30):
        with _set_now(13, minute, weekday=0):
            assert QuoteService._is_continuous_trading() is False


# ── 盤前試撮與盤後定價必須排除 (應返回 False) ────────────────────────
def test_trial_matching_0850_not_continuous():
    """08:50 盤前試撮, ask1/bid1 為試算價非成交價。"""
    with _set_now(8, 50, weekday=0):
        assert QuoteService._is_continuous_trading() is False


def test_just_before_open_0859_not_continuous():
    with _set_now(8, 59, weekday=0):
        assert QuoteService._is_continuous_trading() is False


def test_just_after_close_1331_not_continuous():
    """13:31 已收盤進入盤後定價交易。"""
    with _set_now(13, 31, weekday=0):
        assert QuoteService._is_continuous_trading() is False


def test_post_close_1400_not_continuous():
    with _set_now(14, 0, weekday=0):
        assert QuoteService._is_continuous_trading() is False


def test_early_morning_0700_not_continuous():
    with _set_now(7, 0, weekday=0):
        assert QuoteService._is_continuous_trading() is False


def test_evening_1600_not_continuous():
    with _set_now(16, 0, weekday=0):
        assert QuoteService._is_continuous_trading() is False


# ── 週末全天不交易 ────────────────────────────────────────────────────
def test_weekend_saturday_not_trading():
    """10:30 週六, 落在連續交易時間區間內, 但週末不交易。"""
    with _set_now(10, 30, weekday=5):
        assert QuoteService._is_continuous_trading() is False


def test_weekend_sunday_not_trading():
    """12:00 週日, 落在連續交易時間區間內, 但週末不交易。"""
    with _set_now(12, 0, weekday=6):
        assert QuoteService._is_continuous_trading() is False
