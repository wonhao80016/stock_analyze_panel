"""Listing age in trading sessions, for the basic filter's exclude_new_days."""

from __future__ import annotations

import logging
import os
import threading
from bisect import bisect_left
from collections.abc import Iterable, Mapping
from datetime import date
from types import SimpleNamespace
from typing import Any

import polars as pl

from app.volume_units import TAIWAN_SUFFIXES

logger = logging.getLogger(__name__)

_EXCHANGES = ("TWSE", "TPEX")

# Re-read only on a new (path, mtime, size) of the security master cache file.
_listing_dates_cache: tuple[tuple[str, int, int], dict[str, date]] | None = None
_listing_dates_lock = threading.Lock()


def exclude_new_days(basic_filter: Mapping[str, Any] | None) -> int:
    """Trading sessions a listing must have traded since its listing session; 0 disables the rule."""
    value = (basic_filter or {}).get("exclude_new_days")
    if value is None or value is False:
        return 0
    try:
        if isinstance(value, bool):
            raise TypeError
        sessions = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"exclude_new_days must be a number of trading sessions, got {value!r}"
        ) from exc
    return max(sessions, 0)


def eligible_from(symbols: Iterable[str], min_sessions: int) -> dict[str, date]:
    """First session on which each Taiwan symbol has traded ``min_sessions`` sessions since listing.

    The listing session is the security master's listing date, or the first stored bar of the
    code on either exchange when that is earlier, so a TPEx to TWSE transfer keeps its age.
    Symbols whose listing predates the stored calendar are absent: no session excludes them.
    ``date.max`` marks a listing still too young on every stored session, and a symbol known
    neither to the master nor to the stored bars.
    """
    if min_sessions <= 0:
        return {}
    taiwan = sorted({str(symbol) for symbol in symbols if str(symbol).endswith(TAIWAN_SUFFIXES)})
    if not taiwan:
        return {}
    from app.taiwan import daily_store

    sessions = daily_store.listing_sessions()
    calendar = sessions.trading_dates
    if not calendar:
        return {}
    listed = _listing_dates()
    eligible: dict[str, date] = {}
    for symbol in taiwan:
        start = _listing_session(symbol, listed.get(symbol), sessions.first_sessions, calendar[0])
        if start is None:
            continue
        index = bisect_left(calendar, start) + min_sessions
        eligible[symbol] = calendar[index] if index < len(calendar) else date.max
    return eligible


def _listing_session(
    symbol: str,
    listed: date | None,
    first_sessions: Mapping[str, date],
    calendar_start: date,
) -> date | None:
    code = symbol.rsplit(".", 1)[0]
    stored = [
        first_sessions[key]
        for key in (f"{code}.{exchange}" for exchange in _EXCHANGES)
        if key in first_sessions
    ]
    first_bar = min(stored) if stored else None
    if listed is not None and (first_bar is None or first_bar >= listed):
        return listed if listed >= calendar_start else None
    if first_bar is None:
        # Neither in the master nor stored yet, such as a listing trading for the first time today.
        return date.max
    # Bars stored from the calendar's first session on may continue an older history.
    if first_bar <= calendar_start:
        return None
    return first_bar


def _listing_dates() -> dict[str, date]:
    """Listing date per symbol from the security master cache file; never downloads the master."""
    global _listing_dates_cache
    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import ListingDateUnknownError, MarketProfileBridge

    try:
        path = get_security_master().cache_path
        stat = os.stat(path)
    except (AttributeError, OSError) as exc:
        logger.warning(
            "listing dates unavailable, new listings are placed by stored bars only: %s", exc
        )
        return {}
    signature = (str(path), stat.st_mtime_ns, stat.st_size)
    with _listing_dates_lock:
        cached = _listing_dates_cache
    if cached is not None and cached[0] == signature:
        return cached[1]
    try:
        frame = pl.read_parquet(path, columns=["symbol", "listing_date"])
    except Exception as exc:
        logger.warning("listing dates unreadable from %s: %s", path, exc)
        return {}
    dates: dict[str, date] = {}
    for symbol, raw in frame.iter_rows():
        try:
            dates[str(symbol)] = MarketProfileBridge.listing_date(
                SimpleNamespace(symbol=symbol, listing_date=raw)
            )
        except ListingDateUnknownError:
            continue
    with _listing_dates_lock:
        _listing_dates_cache = (signature, dates)
    return dates
