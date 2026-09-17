"""Taiwan Market Calendar and Trading Session Utilities.

Governs operating hours for Taiwan Stock Exchange (TWSE) and Taipei Exchange (TPEx)
under Asia/Taipei timezone (UTC+8, no daylight saving time).

Operating Schedule:
  - 08:30 - 09:00: PRE_OPEN (Pre-market trial matching)
  - 09:00 - 13:30: OPEN (Continuous regular session, 270 minutes, no lunch break)
  - 13:30 - 14:30: POST_CLOSE (Post-market odd-lot & fixed-price trading)
  - All other weekday times: CLOSED
  - Weekends & Public Holidays: NON_TRADING_DAY
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone

from app.taiwan.realtime.models import MarketStatus

TAIPEI_TZ = timezone(timedelta(hours=8))

PRE_OPEN_START = dt_time(8, 30)
REGULAR_OPEN_START = dt_time(9, 0)
REGULAR_OPEN_END = dt_time(13, 30)
POST_CLOSE_END = dt_time(14, 30)


def _minutes_of_day(t: dt_time) -> int:
    return t.hour * 60 + t.minute


SESSION_TOTAL_MINUTES = float(_minutes_of_day(REGULAR_OPEN_END) - _minutes_of_day(REGULAR_OPEN_START))


def taipei_now() -> datetime:
    """Current timestamp in Asia/Taipei timezone."""
    return datetime.now(TAIPEI_TZ)


def taipei_today() -> date:
    """Current date in Asia/Taipei timezone."""
    return datetime.now(TAIPEI_TZ).date()


class TaiwanTradingCalendar:
    """Trading calendar model for Taiwan Stock Exchange and Taipei Exchange.

    Distinguishes strictly between confirmed trading days, confirmed non-trading days
    (weekends, statutory holidays, extraordinary typhoon closures), and unverified scheduled sessions.
    """

    def __init__(
        self,
        known_holidays: set[date] | None = None,
        known_trading_days: set[date] | None = None,
    ) -> None:
        self.known_holidays = set(known_holidays) if known_holidays else set()
        self.known_trading_days = set(known_trading_days) if known_trading_days else set()

    def add_holiday(self, d: date) -> None:
        self.known_holidays.add(d)

    def add_trading_day(self, d: date) -> None:
        self.known_trading_days.add(d)

    def is_trading_day(self, d: date) -> bool | None:
        """Evaluate if date is a trading day.

        Returns:
            True: Confirmed trading day.
            False: Confirmed non-trading day (weekend, statutory holiday, typhoon closure).
            None: Unverified (weekday with unknown statutory/extraordinary closure status).
        """
        if d.weekday() >= 5:  # Saturday or Sunday
            return False
        if d in self.known_holidays:
            return False
        if d in self.known_trading_days:
            return True
        return None  # Unverified status

    def get_market_status(
        self,
        dt: datetime | None = None,
        require_verified_trading_day: bool = False,
    ) -> MarketStatus:
        """Determine session status for a given datetime.

        If require_verified_trading_day is True and the weekday has not been confirmed via
        first-party calendar or daily data, returns SCHEDULED_OPEN_UNVERIFIED during trading hours
        instead of falsely claiming guaranteed regular session.
        """
        now = dt if dt is not None else taipei_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=TAIPEI_TZ)
        else:
            now = now.astimezone(TAIPEI_TZ)

        d = now.date()
        trading_day_state = self.is_trading_day(d)

        # 1. Confirmed non-trading day (weekends, statutory holidays, typhoon closures)
        if trading_day_state is False:
            return MarketStatus.NON_TRADING_DAY

        # 2. Time-of-day session division
        t = now.time()
        if t < PRE_OPEN_START:
            return MarketStatus.CLOSED
        elif t > POST_CLOSE_END:
            return MarketStatus.CLOSED

        # 3. Extraordinary closure / Unverified weekday during scheduled trading hours
        if trading_day_state is None and require_verified_trading_day:
            return MarketStatus.SCHEDULED_OPEN_UNVERIFIED

        if t < REGULAR_OPEN_START:
            return MarketStatus.PRE_OPEN
        elif t <= REGULAR_OPEN_END:
            return MarketStatus.OPEN
        else:
            return MarketStatus.POST_CLOSE


def session_minutes_elapsed(dt: datetime | None = None) -> float:
    """Minutes traded so far in the regular session, clamped to [0, SESSION_TOTAL_MINUTES].

    The session is continuous, so elapsed time is a plain difference from the open.
    """
    now = dt if dt is not None else taipei_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI_TZ)
    else:
        now = now.astimezone(TAIPEI_TZ)

    t = now.time()
    if t < REGULAR_OPEN_START:
        return 0.0
    if t >= REGULAR_OPEN_END:
        return SESSION_TOTAL_MINUTES
    return float(_minutes_of_day(t) - _minutes_of_day(REGULAR_OPEN_START)) + now.second / 60.0


def session_progress(dt: datetime | None = None) -> float:
    """Fraction of the regular session elapsed, in [0.0, 1.0]."""
    return session_minutes_elapsed(dt) / SESSION_TOTAL_MINUTES


def session_progress_from_ts(ts_ms: int | float | None) -> float:
    """Session progress derived from a quote timestamp in milliseconds.

    Quote timestamps beat server time, which carries network and rate-limit delay.
    An absent or unparsable timestamp yields a full session so that callers scaling
    partial volume never divide by a bogus fraction.
    """
    if not ts_ms:
        return 1.0
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=TAIPEI_TZ)
    except (ValueError, TypeError, OSError):
        return 1.0
    return session_progress(dt)


_DEFAULT_CALENDAR = TaiwanTradingCalendar()


def get_market_status(
    dt: datetime | None = None,
    holidays: set[date] | None = None,
    require_verified_trading_day: bool = False,
) -> MarketStatus:
    """Determine Taiwan market operating status for a given datetime.

    Delegates to TaiwanTradingCalendar with optional ad-hoc holiday fixtures.
    """
    if holidays:
        cal = TaiwanTradingCalendar(known_holidays=holidays)
        return cal.get_market_status(dt, require_verified_trading_day=require_verified_trading_day)
    return _DEFAULT_CALENDAR.get_market_status(dt, require_verified_trading_day=require_verified_trading_day)

