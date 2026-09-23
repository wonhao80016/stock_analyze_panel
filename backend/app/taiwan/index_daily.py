"""Daily OHLC history for the benchmark indices.

TWSE and TPEx both serve one calendar month per request, so history is
backfilled month by month into a parquet store that mirrors the stock daily
layout. Index series carry no volume or amount.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import date, timedelta

import httpx
import polars as pl

from app.taiwan.benchmarks import TAIEX, TPEX_INDEX, BenchmarkIndex, get_benchmark
from app.taiwan.daily_store import TAIWAN_DAILY_COLUMNS, TaiwanDailyStore
from app.taiwan.data_root import taiwan_data_root

logger = logging.getLogger(__name__)

TAIEX_MONTH_URL = "https://www.twse.com.tw/en/indicesReport/MI_5MINS_HIST"
TPEX_MONTH_URL = "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx"

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# TPEx silently returns the current month unless the date carries a day component.
_TPEX_DATE_FORMAT = "%Y/%m/%d"

_CURRENT_MONTH_TTL_SECONDS = 300.0
_FAILED_MONTH_RETRY_SECONDS = 600.0


def _today() -> date:
    return date.today()


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _to_float(raw: object) -> float | None:
    s = str(raw or "").strip().replace(",", "")
    if not s or s in {"--", "---"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_date(raw: object) -> date | None:
    s = str(raw or "").strip()
    parts = s.split("/")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(p) for p in parts)
    except ValueError:
        return None
    if year < 1911:  # ROC year, kept for robustness against upstream format drift
        year += 1911
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_starts(start: date, end: date) -> list[date]:
    months: list[date] = []
    cur = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last:
        months.append(cur)
        cur = _next_month(cur)
    return months


class TaiwanIndexDailyService:
    """Fetches, caches and serves benchmark index daily bars."""

    def __init__(
        self,
        store: TaiwanDailyStore | None = None,
        client: httpx.Client | None = None,
        calendar: TaiwanDailyStore | None = None,
    ) -> None:
        self.store = store or TaiwanDailyStore(taiwan_data_root() / "index_daily")
        self.client = client or httpx.Client(timeout=20.0, headers=_HEADERS)
        # Stock daily partitions mark the trading days a past index month must reach.
        self.calendar = calendar or TaiwanDailyStore()
        self._current_month_fetched_at: dict[str, float] = {}
        # Months before an index existed would otherwise be re-requested on every
        # chart load; a failed fetch is only held off for a cooldown instead.
        self._barren_months: set[tuple[str, int, int]] = set()
        self._failed_months: dict[tuple[str, int, int], float] = {}
        self._complete_months: set[tuple[str, int, int]] = set()
        # The detail page polls every few seconds, so a cold backfill would
        # otherwise be started concurrently and read a partition mid-write.
        self._symbol_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def _rows_to_frame(self, symbol: str, rows: list[list[object]]) -> pl.DataFrame:
        records = []
        for row in rows:
            if len(row) < 5:
                continue
            d = _to_date(row[0])
            close = _to_float(row[4])
            if d is None or close is None:
                continue
            records.append({
                "symbol": symbol,
                "date": d,
                "open": _to_float(row[1]),
                "high": _to_float(row[2]),
                "low": _to_float(row[3]),
                "close": close,
                "volume": None,
                "amount": None,
                "quote_ts": None,
            })
        if not records:
            return pl.DataFrame(schema={c: pl.Float64 for c in TAIWAN_DAILY_COLUMNS})
        return pl.DataFrame(records).select(TAIWAN_DAILY_COLUMNS)

    def _fetch_month(self, bench: BenchmarkIndex, month_start: date) -> pl.DataFrame | None:
        """The month's rows, empty when none are published, or None when the fetch failed."""
        try:
            if bench.symbol == TAIEX.symbol:
                payload = self.client.get(
                    TAIEX_MONTH_URL,
                    params={"response": "json", "date": month_start.strftime("%Y%m%d")},
                ).json()
                rows = payload.get("data") or []
            else:
                payload = self.client.get(
                    TPEX_MONTH_URL,
                    params={"response": "json", "date": month_start.strftime(_TPEX_DATE_FORMAT)},
                ).json()
                tables = payload.get("tables") or []
                rows = (tables[0].get("data") if tables else None) or []
        except Exception as exc:
            logger.warning("index month fetch failed %s %s: %s", bench.symbol, month_start, exc)
            return None

        frame = self._rows_to_frame(bench.symbol, rows)
        # A month outside the published range comes back empty rather than as an error.
        if frame.is_empty():
            return frame
        return frame.filter(
            (pl.col("date") >= month_start) & (pl.col("date") < _next_month(month_start))
        )

    def _last_trading_day(self, month_start: date) -> date | None:
        day = _next_month(month_start) - timedelta(days=1)
        while day >= month_start:
            if self.calendar.has_date(day):
                return day
            day -= timedelta(days=1)
        return None

    def _needs_fetch(self, symbol: str, month_start: date, last_cached: date | None) -> bool:
        key = (symbol, month_start.year, month_start.month)
        today = _today()
        if (month_start.year, month_start.month) == (today.year, today.month):
            last = self._current_month_fetched_at.get(symbol, 0.0)
            return (time.monotonic() - last) > _CURRENT_MONTH_TTL_SECONDS
        failed_at = self._failed_months.get(key)
        if failed_at is not None:
            return (time.monotonic() - failed_at) > _FAILED_MONTH_RETRY_SECONDS
        if key in self._barren_months or key in self._complete_months:
            return False
        if last_cached is None:
            return True
        # A month last fetched while it was still current stops short of its end.
        # Without calendar partitions for the month there is nothing to judge by, so
        # the cached rows are kept as they are.
        last_trading = self._last_trading_day(month_start)
        if last_trading is None or last_cached >= last_trading:
            self._complete_months.add(key)
            return False
        return True

    def ensure_range(self, symbol: str, start: date, end: date) -> None:
        """Backfill any month in the window that the store does not fully hold."""
        bench = get_benchmark(symbol)
        if bench is None:
            return
        today = _today()
        months = _month_starts(start, min(end, today))
        if not months:
            return
        cached = self.store.scan_range(
            [symbol], months[0], _next_month(months[-1]) - timedelta(days=1),
        )
        last_cached: dict[tuple[int, int], date] = {}
        for d in cached["date"].to_list() if not cached.is_empty() else []:
            ym = (d.year, d.month)
            if ym not in last_cached or d > last_cached[ym]:
                last_cached[ym] = d
        for month_start in months:
            ym = (month_start.year, month_start.month)
            if not self._needs_fetch(symbol, month_start, last_cached.get(ym)):
                continue
            key = (symbol, *ym)
            is_current = ym == (today.year, today.month)
            frame = self._fetch_month(bench, month_start)
            if frame is None:
                self._failed_months[key] = time.monotonic()
            elif frame.is_empty():
                self._failed_months.pop(key, None)
                if not is_current:
                    self._barren_months.add(key)
            else:
                self._failed_months.pop(key, None)
                self.store.write_batch(frame)
                if not is_current:
                    self._complete_months.add(key)
            if is_current:
                self._current_month_fetched_at[symbol] = time.monotonic()

    def read_range(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        with self._symbol_locks[symbol]:
            self.ensure_range(symbol, start, end)
            return self.store.scan_range([symbol], start, end)


_service: TaiwanIndexDailyService | None = None


def get_index_daily_service() -> TaiwanIndexDailyService:
    global _service
    if _service is None:
        _service = TaiwanIndexDailyService()
    return _service


__all__ = [
    "TAIEX",
    "TPEX_INDEX",
    "TaiwanIndexDailyService",
    "get_index_daily_service",
]
