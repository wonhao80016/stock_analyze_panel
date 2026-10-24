"""Data status: margin balances are due only after their publication time, not at the 16:00 post-close cutoff."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

from app.taiwan import daily_update
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar

PREVIOUS = date(2026, 10, 20)
SESSION = date(2026, 10, 21)


def _service(margin_as_of: date) -> TaiwanDailyUpdateService:
    daily, inst, margin = MagicMock(), MagicMock(), MagicMock()
    daily.available_dates.return_value = [SESSION]
    inst.available_dates.return_value = [SESSION]
    margin.available_dates.return_value = [margin_as_of]
    return TaiwanDailyUpdateService(
        daily_store=daily, inst_store=inst, margin_store=margin, calendar=TaiwanTradingCalendar()
    )


def test_margin_of_the_previous_session_is_current_before_publication(monkeypatch):
    monkeypatch.setattr(daily_update, "taipei_now", lambda: datetime(2026, 10, 21, 17, 0, tzinfo=TAIPEI_TZ))

    status = _service(PREVIOUS).get_freshness(target_date=SESSION)

    assert status.margin_status == "current"
    assert status.margin_days_behind == 0
    assert status.is_fully_current is True


def test_margin_of_the_previous_session_is_stale_once_published(monkeypatch):
    monkeypatch.setattr(daily_update, "taipei_now", lambda: datetime(2026, 10, 21, 21, 45, tzinfo=TAIPEI_TZ))

    status = _service(PREVIOUS).get_freshness(target_date=SESSION)

    assert status.margin_status == "stale"
    assert status.margin_days_behind == 1
