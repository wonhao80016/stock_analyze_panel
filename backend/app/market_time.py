"""Wall-clock helpers pinned to UTC+8 (no daylight saving).

The server or container local timezone is unreliable (the python:slim image
defaults to UTC), so anything that stamps a trading date onto realtime rows must
say UTC+8 explicitly or Docker deployments write rows under the wrong date.

Trading session windows live in app.taiwan.realtime.calendar, not here: this
module only answers "what is the wall clock", never "is the market open".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def cn_now() -> datetime:
    """Current UTC+8 timestamp (timezone-aware)."""
    return datetime.now(CN_TZ)


def cn_today() -> date:
    """Current UTC+8 date."""
    return datetime.now(CN_TZ).date()
