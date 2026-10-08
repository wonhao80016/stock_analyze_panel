"""Taiwan exchange holidays from the official TWSE market schedule, cached per year.

Lookups only read the local cache; the network is touched by ``refresh`` alone,
which the scheduler calls. A year without a published schedule stays unverified.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx

from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_today

logger = logging.getLogger(__name__)

TWSE_HOLIDAY_URL = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_FETCH_TIMEOUT_SECONDS = 15.0
# The exchange publishes the next year's schedule late in the year.
_NEXT_YEAR_FROM_MONTH = 11
# Listed dates are closures except first/last-session markers, whose names end with this suffix;
# settlement-only days carry the no-trading mark and are closures.
_TRADING_DAY_SUFFIX = "交易日"
_NO_TRADING_MARK = "無交易"
_DATE_FIELD = "日期"
_NAME_FIELD = "名稱"

Fetcher = Callable[[int], object]


@dataclass(frozen=True)
class HolidaySchedule:
    year: int
    holidays: frozenset[date]
    trading_days: frozenset[date]


def fetch_twse_schedule(year: int) -> object:
    """Return the raw TWSE schedule payload for *year*; raises on transport or HTTP errors."""
    response = httpx.get(
        TWSE_HOLIDAY_URL,
        params={"response": "json", "date": f"{year}0101"},
        headers=_HEADERS,
        timeout=_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _is_trading_entry(name: str) -> bool:
    name = name.strip()
    return name.endswith(_TRADING_DAY_SUFFIX) and _NO_TRADING_MARK not in name


def _parse(year: int, payload: object) -> HolidaySchedule | None:
    """None when *year* has no published closures; raises ValueError for an unrecognised payload."""
    if not isinstance(payload, dict) or payload.get("stat") != "ok":
        raise ValueError(
            f"unexpected TWSE schedule status: {payload.get('stat') if isinstance(payload, dict) else payload!r}"
        )
    fields = payload.get("fields")
    if not isinstance(fields, list) or _DATE_FIELD not in fields or _NAME_FIELD not in fields:
        raise ValueError(f"unexpected TWSE schedule fields: {fields!r}")
    date_col = fields.index(_DATE_FIELD)
    name_col = fields.index(_NAME_FIELD)
    holidays: set[date] = set()
    trading_days: set[date] = set()
    for row in payload.get("data") or []:
        if not isinstance(row, list | tuple) or len(row) <= max(date_col, name_col):
            continue
        try:
            day = date.fromisoformat(str(row[date_col]).strip())
        except ValueError:
            continue
        if day.year != year:
            continue
        (trading_days if _is_trading_entry(str(row[name_col])) else holidays).add(day)
    if not holidays:
        return None
    return HolidaySchedule(year, frozenset(holidays), frozenset(trading_days - holidays))


def parse_schedule(year: int, payload: object) -> HolidaySchedule | None:
    """Parse a TWSE schedule payload; None when *year* is unpublished or the payload is unrecognised."""
    try:
        return _parse(year, payload)
    except ValueError:
        return None


def _default_root() -> Path:
    from app.taiwan.data_root import taiwan_data_root

    return taiwan_data_root() / "holidays"


class TaiwanHolidayStore:
    """Per-year holiday cache at ``<root>/{year}.json``; thread-safe."""

    def __init__(
        self,
        root: Path | None = None,
        fetcher: Fetcher = fetch_twse_schedule,
        today: Callable[[], date] = taipei_today,
    ) -> None:
        self._root = Path(root) if root is not None else None
        self._fetcher = fetcher
        self._today = today
        self._lock = threading.Lock()
        self._schedules: dict[tuple[Path, int], HolidaySchedule | None] = {}
        self._attempted: dict[tuple[Path, int], date] = {}

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else _default_root()

    def path_for(self, year: int) -> Path:
        return self.root / f"{year}.json"

    def schedule(self, year: int) -> HolidaySchedule | None:
        """The cached schedule for *year*, or None when it is not cached (unverified)."""
        key = (self.root, year)
        try:
            return self._schedules[key]
        except KeyError:
            pass
        with self._lock:
            if key not in self._schedules:
                self._schedules[key] = self._read(self.path_for(year))
            return self._schedules[key]

    def is_holiday(self, day: date) -> bool | None:
        """True for a published closure, False for any other day of a cached year, None if the year is unverified."""
        schedule = self.schedule(day.year)
        if schedule is None:
            return None
        return day in schedule.holidays

    def refresh(self, year: int) -> HolidaySchedule | None:
        """Refetch *year* until one fetch per day succeeds or confirms it unpublished.

        A failed fetch or an unrecognised payload keeps the cached file and is retried
        on the next call. Never raises.
        """
        key = (self.root, year)
        today = self._today()
        with self._lock:
            if self._attempted.get(key) == today:
                return self._schedules.get(key)
            cached = self._read(self.path_for(year))
            self._schedules[key] = cached
        if cached is not None and self._fetched_on(year) == today:
            with self._lock:
                self._attempted[key] = today
            return cached
        try:
            schedule = _parse(year, self._fetcher(year))
        except Exception as exc:
            logger.warning(
                "TWSE holiday schedule fetch for %d failed; keeping the cache: %s", year, exc
            )
            return cached
        with self._lock:
            self._attempted[key] = today
        if schedule is None:
            logger.info(
                "TWSE holiday schedule for %d is not published; the year stays unverified", year
            )
            return cached
        try:
            self._write(year, schedule, today)
        except OSError as exc:
            logger.warning("TWSE holiday schedule cache write for %d failed: %s", year, exc)
        with self._lock:
            self._schedules[key] = schedule
        logger.info(
            "TWSE holiday schedule for %d cached: %d closures", year, len(schedule.holidays)
        )
        return schedule

    def refresh_upcoming_years(self) -> None:
        """Refresh the current year, and the next year once its schedule may be published."""
        today = self._today()
        self.refresh(today.year)
        if today.month >= _NEXT_YEAR_FROM_MONTH:
            self.refresh(today.year + 1)

    def _fetched_on(self, year: int) -> date | None:
        try:
            raw = json.loads(self.path_for(year).read_text(encoding="utf-8"))
            return date.fromisoformat(raw["fetched_on"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write(self, year: int, schedule: HolidaySchedule, today: date) -> None:
        from app.taiwan.universe.service import write_atomically

        body = {
            "year": year,
            "source": TWSE_HOLIDAY_URL,
            "fetched_on": today.isoformat(),
            "fetched_at": datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
            "holidays": sorted(d.isoformat() for d in schedule.holidays),
            "trading_days": sorted(d.isoformat() for d in schedule.trading_days),
        }
        text = json.dumps(body, ensure_ascii=False, indent=2)
        write_atomically(self.path_for(year), lambda tmp: tmp.write_text(text, encoding="utf-8"))

    @staticmethod
    def _read(path: Path) -> HolidaySchedule | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            year = int(raw["year"])
            holidays = frozenset(date.fromisoformat(d) for d in raw["holidays"])
            trading_days = frozenset(date.fromisoformat(d) for d in raw.get("trading_days") or [])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("TWSE holiday cache %s unreadable: %s", path, exc)
            return None
        if not holidays:
            return None
        return HolidaySchedule(year, holidays, trading_days)


_default_store: TaiwanHolidayStore | None = None
_default_store_lock = threading.Lock()


def get_holiday_store() -> TaiwanHolidayStore:
    global _default_store
    store = _default_store
    if store is None:
        with _default_store_lock:
            if _default_store is None:
                _default_store = TaiwanHolidayStore()
            store = _default_store
    return store


def refresh_upcoming_years(store: TaiwanHolidayStore | None = None) -> None:
    (store or get_holiday_store()).refresh_upcoming_years()
