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
from datetime import date

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
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return months


class TaiwanIndexDailyService:
    """Fetches, caches and serves benchmark index daily bars."""

    def __init__(
        self,
        store: TaiwanDailyStore | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.store = store or TaiwanDailyStore(taiwan_data_root() / "index_daily")
        self.client = client or httpx.Client(timeout=20.0, headers=_HEADERS)
        self._current_month_fetched_at: dict[str, float] = {}
        # Months before an index existed, and months whose fetch failed, would
        # otherwise be re-requested on every chart load.
        self._barren_months: set[tuple[str, int, int]] = set()
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

    def _fetch_month(self, bench: BenchmarkIndex, month_start: date) -> pl.DataFrame:
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
            return pl.DataFrame()

        frame = self._rows_to_frame(bench.symbol, rows)
        # A month outside the published range comes back empty rather than as an error.
        if frame.is_empty():
            return frame
        return frame.filter(
            (pl.col("date") >= month_start)
            & (pl.col("date") < (
                date(month_start.year + 1, 1, 1) if month_start.month == 12
                else date(month_start.year, month_start.month + 1, 1)
            ))
        )

    def _needs_fetch(self, symbol: str, month_start: date, cached_months: set[tuple[int, int]]) -> bool:
        today = date.today()
        if (month_start.year, month_start.month) == (today.year, today.month):
            last = self._current_month_fetched_at.get(symbol, 0.0)
            return (time.monotonic() - last) > _CURRENT_MONTH_TTL_SECONDS
        if (symbol, month_start.year, month_start.month) in self._barren_months:
            return False
        return (month_start.year, month_start.month) not in cached_months

    def ensure_range(self, symbol: str, start: date, end: date) -> None:
        """Backfill any month in the window that the store does not already hold."""
        bench = get_benchmark(symbol)
        if bench is None:
            return
        cached = self.store.scan_range([symbol], start, end)
        cached_months = {
            (d.year, d.month) for d in (cached["date"].to_list() if not cached.is_empty() else [])
        }
        today = date.today()
        for month_start in _month_starts(start, min(end, today)):
            if not self._needs_fetch(symbol, month_start, cached_months):
                continue
            frame = self._fetch_month(bench, month_start)
            if frame.is_empty():
                self._barren_months.add((symbol, month_start.year, month_start.month))
            else:
                self.store.write_batch(frame)
            if (month_start.year, month_start.month) == (today.year, today.month):
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
