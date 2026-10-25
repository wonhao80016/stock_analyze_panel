"""Taiwan Daily Persistence Layer (Phase 6B-Data Foundation).

Provides atomic per-date parquet writes, incremental refresh, dedup, and
batch reads for Taiwan daily OHLCV data.  No live internet access required
at read time; all provider calls are isolated in TaiwanDailyRefreshService.

Partition layout:
    data/taiwan/daily/date=YYYY-MM-DD/part.parquet
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any

import polars as pl

from app.config import settings
from app.taiwan.market_rules import ListingSessions
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.universe.service import write_atomically

logger = logging.getLogger(__name__)

# Stored bars stay raw; the enriched publish forward-adjusts them and keeps these prices as raw_*.
TAIWAN_DAILY_COLUMNS: list[str] = [
    "symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"
]

# How many of the newest stored days a daily run checks for repair, about four weeks.
REPAIR_WINDOW_PARTITIONS = 20


def is_provisional_mtime(d: date, mtime: float, final_from: dt_time | None = None) -> bool:
    """Whether a partition for trading day *d* written at *mtime* may hold pre-final figures.

    A partition last written on day *d* itself (Taipei time) is provisional until it is
    rewritten on a later day, or, when *final_from* is given, until it is rewritten on
    day *d* at or after that Taipei time.
    """
    written = datetime.fromtimestamp(mtime, TAIPEI_TZ)
    if final_from is not None and written.date() == d and written.time() >= final_from:
        return False
    return written.date() <= d


def daily_final_cutoff() -> dt_time:
    """Taipei time from which a daily partition written on its trading day is final."""
    return settings.taiwan_daily_final_cutoff


class TaiwanDailyStore:
    """Minimal Taiwan daily parquet persistence.

    Layout:  <settings.data_dir>/taiwan/daily/date=YYYY-MM-DD/part.parquet
    Partition column:  date
    Canonical symbol:  2330.TWSE  (never Yahoo suffix)
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        # Phase 8B-5.0.6: 省略 data_dir 时锚定到 settings.data_dir(与 app 其它数据
        # 同一个 project-root/exe-adjacent 根), 不再是 cwd-dependent 的裸相对路径。
        # 显式传入 data_dir(所有既有 deterministic test 都这样做)时行为不变。
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "daily"
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._partition_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def _lock_for(self, d: date) -> threading.Lock:
        """Return a per-partition lock keyed by date ISO string."""
        key = d.isoformat()
        with self._locks_guard:
            return self._partition_locks[key]

    # ── Write ──────────────────────────────────────────────────────

    def write_batch(
        self, df: pl.DataFrame, partition_date: date | None = None, *, skip_existing: bool = False
    ) -> int:
        """Atomically append rows to the daily parquet dataset, grouped by date.

        Each distinct date in *df* is written to its own partition directory:
            date=YYYY-MM-DD/part.parquet

        If a partition already exists, incoming rows for that date are merged
        with the existing rows, deduplicated by (symbol, date), and sorted
        deterministically (symbol ASC, date ASC).

        Args:
            df: Polars DataFrame whose columns are a superset of
                TAIWAN_DAILY_COLUMNS.
            partition_date:  Ignored.  Kept for API compatibility.
            skip_existing: Keep stored rows and drop incoming rows whose (symbol, date)
                is already stored, checked under the partition lock. An unreadable
                partition then raises instead of being replaced, and a provisional
                partition stays provisional.

        Returns:
            Number of rows written (after dedup).
        """
        del partition_date
        if df.is_empty():
            logger.debug("write_batch called with empty DataFrame; nothing to write.")
            return 0

        df = df.with_columns(pl.col("date").cast(pl.Date, strict=False))
        df = df.select(TAIWAN_DAILY_COLUMNS)

        # Deduplicate incoming batch by (symbol, date), keep last.
        before = df.height
        df = df.unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"])
        total_written = 0
        logger.debug("write_batch dedup: %d rows -> %d rows", before, df.height)

        # Group by date and write each partition independently.
        for part_date in sorted(df.select(pl.col("date")).unique().sort("date").to_pandas()["date"].tolist()):
            part_date_obj = part_date.to_pydatetime().date() if hasattr(part_date, "to_pydatetime") else part_date
            group = df.filter(pl.col("date") == part_date_obj)
            total_written += self._write_partition(part_date_obj, group, skip_existing=skip_existing)

        logger.debug("write_batch total rows written: %d", total_written)
        return total_written

    def _write_partition(self, d: date, df: pl.DataFrame, *, skip_existing: bool = False) -> int:
        """Write *df* to the partition directory for date *d*, merging if exists.

        The entire read-merge-dedup-replace sequence is protected by a
        per-partition lock so that concurrent writers targeting the same
        date are serialized (different dates can still write in parallel).
        """
        with self._lock_for(d):
            partition_dir = self._data_dir / f"date={d.isoformat()}"
            partition_dir.mkdir(parents=True, exist_ok=True)
            target = partition_dir / "part.parquet"

            # Merge with existing partition if present.
            new_rows = df.height
            keep_provisional = False
            final_from = daily_final_cutoff()
            if target.exists():
                keep_provisional = skip_existing and is_provisional_mtime(
                    d, target.stat().st_mtime, final_from
                )
                try:
                    existing = pl.scan_parquet(target, missing_columns="insert", extra_columns="ignore").collect()
                    if skip_existing:
                        df = df.join(existing.select("symbol", "date"), on=["symbol", "date"], how="anti")
                        new_rows = df.height
                    if not existing.is_empty():
                        df = pl.concat([existing, df], how="diagonal_relaxed")
                except Exception as exc:
                    if skip_existing:
                        raise
                    logger.warning("Failed to read existing partition %s: %s", target, exc)
                if new_rows == 0:
                    return df.height

            # Final dedup + deterministic sort.
            before = df.height
            df = df.unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"])
            logger.debug("partition %s dedup: %d rows -> %d rows", d, before, df.height)

            write_atomically(target, df.write_parquet)
            if keep_provisional and not is_provisional_mtime(
                d, target.stat().st_mtime, final_from
            ):
                # Rows already stored are still pre-final figures, so the day stays provisional;
                # the second before the cutoff stays newer than an enriched publish made before it.
                last_second = (
                    datetime.combine(d, final_from, TAIPEI_TZ) - timedelta(seconds=1)
                ).timestamp()
                os.utime(target, (last_second, last_second))
            invalidate_listing_sessions(self._data_dir)
            invalidate_latest_rows(self._data_dir)
            logger.debug("Wrote %d rows to %s", df.height, target)

            return df.height

    # ── Read ───────────────────────────────────────────────────────

    def read_all(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """Return all persisted rows, optionally filtered by *symbols*.

        Results are sorted by symbol ASC, date ASC.
        """
        return self.read_range(symbols, None, None)

    def read_latest_date_rows(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """Return rows from the latest available trading date.

        This reads only the partition with the maximum date in storage.
        Useful for "today's snapshot" queries.
        """
        latest = self.latest_date()
        if latest is None:
            return pl.DataFrame(schema=_schema())
        return self.read_range(symbols, latest, latest)

    def read_latest_per_symbol(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """Return the latest available row for each symbol.

        For each distinct symbol in storage (optionally filtered by *symbols*),
        returns the row with the maximum date.  This is the primary API for
        the Taiwan Screener, which needs one current quote per instrument.

        Results are sorted by symbol ASC, date ASC. Unreadable partitions are skipped, as in read_range.
        """

        def read_symbols(path: Path) -> list[str] | None:
            try:
                frame = pl.read_parquet(path, columns=["symbol", "date"])
            except Exception as exc:
                logger.warning("read_latest_per_symbol skipped %s: %s", path, exc)
                return None
            return frame.filter(pl.col("date").is_not_null())["symbol"].to_list()

        def read_rows(path: Path, wanted: list[str]) -> pl.DataFrame | None:
            try:
                return (
                    pl.scan_parquet(path, missing_columns="insert", extra_columns="ignore")
                    .filter(pl.col("date").is_not_null() & pl.col("symbol").is_in(wanted))
                    .collect()
                )
            except Exception as exc:
                logger.warning("read_latest_per_symbol skipped %s: %s", path, exc)
                return None

        def combine(frames: list[pl.DataFrame]) -> pl.DataFrame:
            if not frames:
                return pl.DataFrame(schema=_schema())
            return pl.concat(frames, how="diagonal_relaxed").select(TAIWAN_DAILY_COLUMNS).sort(["symbol", "date"])

        latest = newest_rows_per_symbol(self._data_dir, read_symbols, read_rows, combine)
        if symbols is not None:
            latest = latest.filter(pl.col("symbol").is_in(symbols))
        return latest

    def read_dates(self, dates: list[date], symbols: list[str] | None = None) -> pl.DataFrame:
        """Rows of the partitions for *dates* only, sorted by symbol and date.

        Absent and unreadable partitions are skipped, as in read_range.
        """
        frames: list[pl.DataFrame] = []
        for d in sorted(set(dates)):
            path = self._data_dir / f"date={d.isoformat()}" / "part.parquet"
            if not path.exists():
                continue
            try:
                lf = pl.scan_parquet(path, missing_columns="insert", extra_columns="ignore")
                lf = lf.filter(pl.col("date").is_not_null())
                if symbols is not None:
                    lf = lf.filter(pl.col("symbol").is_in(symbols))
                frame = lf.collect()
            except Exception as exc:
                logger.warning("read_dates skipped %s: %s", path, exc)
                continue
            if not frame.is_empty():
                frames.append(frame)
        if not frames:
            return pl.DataFrame(schema=_schema())
        return pl.concat(frames, how="diagonal_relaxed").select(TAIWAN_DAILY_COLUMNS).sort(["symbol", "date"])

    def symbol_suffix_counts(self, d: date, suffixes: list[str]) -> dict[str, int]:
        """Rows of the partition for *d* whose symbol ends with each of *suffixes*.

        Only the symbol column is read. Zeros when the partition is absent; an unreadable one raises.
        """
        path = self._data_dir / f"date={d.isoformat()}" / "part.parquet"
        if not path.exists():
            return dict.fromkeys(suffixes, 0)
        symbol = pl.col("symbol").cast(pl.String)
        counts = (
            pl.scan_parquet(path)
            .select([symbol.str.ends_with(s).sum().alias(s) for s in suffixes])
            .collect()
            .row(0, named=True)
        )
        return {s: int(counts[s] or 0) for s in suffixes}

    def read_range(
        self,
        symbols: list[str] | None,
        start: date | None,
        end: date | None,
    ) -> pl.DataFrame:
        """Return all rows for *symbols* between *start* and *end* (inclusive)."""
        files = sorted(self._data_dir.glob("date=*/part.parquet"))
        if not files:
            return pl.DataFrame(schema=_schema())

        frames: list[pl.DataFrame] = []
        for f in files:
            part = f.parent.name
            try:
                d = date.fromisoformat(part.split("=", 1)[1])
            except (ValueError, IndexError):
                continue
            if (start is not None and d < start) or (end is not None and d > end):
                continue
            try:
                lf = pl.scan_parquet(f, missing_columns="insert", extra_columns="ignore")
                lf = lf.filter(pl.col("date").is_not_null())
                df = lf.collect()
                if df.is_empty():
                    continue
                if symbols is not None:
                    df = df.filter(pl.col("symbol").is_in(symbols))
                if not df.is_empty():
                    frames.append(df)
            except Exception as exc:
                logger.warning("read_range skipped %s: %s", f, exc)

        if not frames:
            return pl.DataFrame(schema=_schema())
        return pl.concat(frames, how="diagonal_relaxed").select(TAIWAN_DAILY_COLUMNS)

    def scan_range(
        self,
        symbols: list[str] | None,
        start: date | None,
        end: date | None,
    ) -> pl.DataFrame:
        """Same rows as :meth:`read_range`, but pushes the filters into the parquet scan.

        read_range collects every partition in the window before filtering, which is
        costly over multi-year ranges. Falls back to read_range if the scan fails.
        """
        pattern = str(self._data_dir / "date=*" / "part.parquet")
        try:
            lf = pl.scan_parquet(pattern, missing_columns="insert", extra_columns="ignore")
            if symbols is not None:
                lf = lf.filter(pl.col("symbol").is_in(symbols))
            if start is not None:
                lf = lf.filter(pl.col("date") >= start)
            if end is not None:
                lf = lf.filter(pl.col("date") <= end)
            return lf.select(TAIWAN_DAILY_COLUMNS).sort(["symbol", "date"]).collect()
        except Exception as exc:
            logger.warning("scan_range fell back to read_range: %s", exc)
            return self.read_range(symbols, start, end)

    def available_dates(self) -> list[date]:
        """Return all partition dates present in storage."""
        dates: list[date] = []
        for f in self._data_dir.glob("date=*/part.parquet"):
            try:
                d = date.fromisoformat(f.parent.name.split("=", 1)[1])
                dates.append(d)
            except (ValueError, IndexError):
                continue
        return sorted(dates)

    def has_date(self, d: date) -> bool:
        """Return *True* if a partition exists for *d*."""
        return (self._data_dir / f"date={d.isoformat()}" / "part.parquet").exists()

    def incomplete_dates(self, dates: list[date]) -> list[date]:
        """Return the stored dates among *dates* whose partition lacks TWSE or TPEx rows.

        Both exchanges trade on the same days, so a partition holding only one of
        them is half a snapshot. An unreadable partition is reported as well, so it
        is refetched rather than trusted.
        """
        incomplete: list[date] = []
        for d in dates:
            path = self._data_dir / f"date={d.isoformat()}" / "part.parquet"
            if not path.exists():
                continue
            try:
                symbols = pl.read_parquet(path, columns=["symbol"]).get_column("symbol")
            except Exception as exc:
                logger.warning("incomplete_dates could not read %s: %s", path, exc)
                incomplete.append(d)
                continue
            if not (symbols.str.ends_with(".TWSE").any() and symbols.str.ends_with(".TPEX").any()):
                incomplete.append(d)
        return sorted(incomplete)

    def partition_mtime(self, d: date) -> float | None:
        """Return when the partition for *d* was last written, or None if absent."""
        try:
            return (self._data_dir / f"date={d.isoformat()}" / "part.parquet").stat().st_mtime
        except OSError:
            return None

    def provisional_dates(self, dates: list[date]) -> list[date]:
        """Return the stored dates among *dates* whose partition is provisional (is_provisional_mtime)."""
        provisional: list[date] = []
        final_from = daily_final_cutoff()
        for d in dates:
            mtime = self.partition_mtime(d)
            if mtime is not None and is_provisional_mtime(d, mtime, final_from):
                provisional.append(d)
        return sorted(provisional)

    def repair_dates(self, target: date) -> list[date]:
        """Return the recent stored dates up to *target* that hold one exchange only or are provisional."""
        recent = [d for d in self.available_dates()[-REPAIR_WINDOW_PARTITIONS:] if d <= target]
        return sorted(set(self.incomplete_dates(recent)) | set(self.provisional_dates(recent)))

    def latest_date(self) -> date | None:
        """Return the most recent partition date, or *None* if empty."""
        return self.available_dates()[-1] if self.available_dates() else None

    def first_sessions(self) -> dict[str, date]:
        """Return each stored symbol's earliest stored date."""
        pattern = str(self._data_dir / "date=*" / "part.parquet")
        if not any(self._data_dir.glob("date=*/part.parquet")):
            return {}
        frame = (
            pl.scan_parquet(pattern, missing_columns="insert", extra_columns="ignore")
            .select("symbol", "date")
            .group_by("symbol")
            .agg(pl.col("date").min())
            .collect()
        )
        return dict(zip(frame["symbol"].to_list(), frame["date"].to_list(), strict=True))

    # ── Symbol / date helpers ──────────────────────────────────────

    def has_symbol_date(self, symbol: str, d: date) -> bool:
        """Return *True* if *symbol* has a row for *d*."""
        partition_path = self._data_dir / f"date={d.isoformat()}" / "part.parquet"
        if not partition_path.exists():
            return False
        try:
            df = pl.scan_parquet(partition_path, missing_columns="insert", extra_columns="ignore")
            df = df.filter(pl.col("symbol") == symbol)
            return not df.collect().is_empty()
        except Exception:
            return False

    def known_symbols(self) -> list[str]:
        """Return all distinct symbols present in storage."""
        frames: list[pl.DataFrame] = []
        for f in self._data_dir.glob("date=*/part.parquet"):
            try:
                lf = pl.scan_parquet(f, missing_columns="insert", extra_columns="ignore")
                df = lf.collect().select(["symbol"]).unique()
                if not df.is_empty():
                    frames.append(df)
            except Exception:
                continue
        if not frames:
            return []
        return sorted(pl.concat(frames, how="diagonal_relaxed")["symbol"].unique().to_list())


# A write through TaiwanDailyStore drops its directory's entry at once; a write from another
# process is picked up when the entry expires.
_LISTING_SESSIONS_TTL_SECONDS = 60.0
_listing_sessions_cache: dict[str, tuple[float, ListingSessions]] = {}


def _listing_sessions_key(data_dir: Path) -> str:
    return os.path.normcase(os.path.abspath(data_dir))


def invalidate_listing_sessions(data_dir: Path) -> None:
    """Drop the cached sessions of *data_dir*, so the next lookup sees its current partitions."""
    _listing_sessions_cache.pop(_listing_sessions_key(data_dir), None)


def listing_sessions(store: TaiwanDailyStore | None = None) -> ListingSessions:
    """The stored trading sessions and first session per symbol, for placing initial-listing windows.

    Without *store* the default daily directory is read, and never created when absent.
    """
    if store is None:
        from app.taiwan.data_root import taiwan_data_root

        data_dir = taiwan_data_root() / "daily"
    else:
        data_dir = store._data_dir
    key = _listing_sessions_key(data_dir)
    now = time.monotonic()
    cached = _listing_sessions_cache.get(key)
    if cached is not None and now - cached[0] < _LISTING_SESSIONS_TTL_SECONDS:
        return cached[1]
    if store is None:
        if not data_dir.is_dir():
            return ListingSessions(())
        store = TaiwanDailyStore(data_dir)
    sessions = ListingSessions(tuple(store.available_dates()), store.first_sessions())
    _listing_sessions_cache[key] = (now, sessions)
    return sessions


# Keyed by directory; an entry holds for as long as no partition is added, removed or rewritten.
_latest_rows_cache: dict[str, tuple[tuple[tuple[str, int, int], ...], pl.DataFrame]] = {}
_latest_rows_lock = threading.Lock()


def newest_rows_per_symbol(
    data_dir: Path,
    read_symbols: Callable[[Path], list[str] | None],
    read_rows: Callable[[Path, list[str]], pl.DataFrame | None],
    combine: Callable[[list[pl.DataFrame]], pl.DataFrame],
) -> pl.DataFrame:
    """Every symbol's rows from the newest ``date=*/part.parquet`` partition under *data_dir* that holds it.

    Partitions are visited newest first and read in full only for symbols not yet seen, and the
    *combine*d result is reused until a partition's name, mtime or size changes. A reader returning
    None skips that partition.
    """
    files: list[Path] = []
    for path in sorted(data_dir.glob("date=*/part.parquet"), reverse=True):
        try:
            date.fromisoformat(path.parent.name.split("=", 1)[1])
        except (ValueError, IndexError):
            continue
        files.append(path)
    signature: list[tuple[str, int, int]] = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((path.parent.name, stat.st_mtime_ns, stat.st_size))
    key = _listing_sessions_key(data_dir)
    with _latest_rows_lock:
        cached = _latest_rows_cache.get(key)
    if cached is not None and cached[0] == tuple(signature):
        return cached[1]

    seen: set[str] = set()
    frames: list[pl.DataFrame] = []
    complete = True
    for path in files:
        present = read_symbols(path)
        if present is None:
            complete = False
            continue
        new = set(present) - seen
        if not new:
            continue
        rows = read_rows(path, sorted(new))
        if rows is None:
            complete = False
            continue
        if rows.is_empty():
            continue
        frames.append(rows)
        seen |= new
    result = combine(frames)
    # A skipped partition may only be locked or mid-write; its unchanged signature must not pin the gap.
    if complete:
        with _latest_rows_lock:
            _latest_rows_cache[key] = (tuple(signature), result)
    return result


def invalidate_latest_rows(data_dir: Path) -> None:
    """Drop the cached newest_rows_per_symbol result of *data_dir* after a partition write."""
    with _latest_rows_lock:
        _latest_rows_cache.pop(_listing_sessions_key(data_dir), None)


# ── Internal helpers ──────────────────────────────────────────────

def _schema() -> dict[str, Any]:
    return {
        "symbol": pl.Utf8,
        "date": pl.Date,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
        "amount": pl.Float64,
        "quote_ts": pl.Int64,
    }
