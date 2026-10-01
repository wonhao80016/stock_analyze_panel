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
import tempfile
import threading
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.providers.base import (
    AmountUnit,
    PriceSemantics,
    SourceMetadata,
    VolumeUnit,
)
from app.taiwan.providers.normalizer import normalize_taiwan_daily
from app.taiwan.realtime.calendar import TAIPEI_TZ

logger = logging.getLogger(__name__)

TAIWAN_DAILY_COLUMNS: list[str] = [
    "symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"
]

# Stored bars stay raw; the enriched publish forward-adjusts them and keeps these prices as raw_*.
DEFAULT_TAIWAN_METADATA = SourceMetadata(
    source_name="taiwan_daily_store",
    volume_unit=VolumeUnit.SHARES,
    amount_unit=AmountUnit.TWD,
    price_semantics=PriceSemantics.RAW,
)

# How many of the newest stored days a daily run checks for repair, about four weeks.
REPAIR_WINDOW_PARTITIONS = 20


def is_provisional_mtime(d: date, mtime: float) -> bool:
    """Whether a partition for trading day *d* written at *mtime* may hold pre-final figures.

    The exchanges revise a day's volume, amount and institutional flows after the
    evening publication, so a partition last written on day *d* itself (Taipei time)
    is provisional until it is rewritten on a later day.
    """
    return datetime.fromtimestamp(mtime, TAIPEI_TZ).date() <= d


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

    def write_batch(self, df: pl.DataFrame, partition_date: date | None = None) -> int:
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
            total_written += self._write_partition(part_date_obj, group)

        logger.debug("write_batch total rows written: %d", total_written)
        return total_written

    def _write_partition(self, d: date, df: pl.DataFrame) -> int:
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
            if target.exists():
                try:
                    existing = pl.scan_parquet(target, missing_columns="insert", extra_columns="ignore").collect()
                    if not existing.is_empty():
                        df = pl.concat([existing, df], how="diagonal_relaxed")
                except Exception as exc:
                    logger.warning("Failed to read existing partition %s: %s", target, exc)

            # Final dedup + deterministic sort.
            before = df.height
            df = df.unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"])
            logger.debug("partition %s dedup: %d rows -> %d rows", d, before, df.height)

            # Atomic write: temp file -> replace.
            tmp_fd, tmp_path = tempfile.mkstemp(dir=str(partition_dir), suffix=".parquet")
            try:
                os.close(tmp_fd)
                df.write_parquet(tmp_path)
                Path(tmp_path).replace(target)
                logger.debug("Wrote %d rows to %s", df.height, target)
            except BaseException:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass
                raise

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

        Results are sorted by symbol ASC, date ASC.
        """
        df = self.read_all(symbols)
        if df.is_empty():
            return df
        return (
            df.with_columns(
                pl.col("date").max().over("symbol").alias("_max_date")
            )
            .filter(pl.col("date") == pl.col("_max_date"))
            .drop("_max_date")
            .sort(["symbol", "date"])
        )

    def read_latest(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """Deprecated: use :meth:`read_latest_per_symbol` instead."""
        return self.read_latest_per_symbol(symbols)

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
        """Return the stored dates among *dates* whose partition was last written on the day itself."""
        provisional: list[date] = []
        for d in dates:
            mtime = self.partition_mtime(d)
            if mtime is not None and is_provisional_mtime(d, mtime):
                provisional.append(d)
        return sorted(provisional)

    def latest_date(self) -> date | None:
        """Return the most recent partition date, or *None* if empty."""
        return self.available_dates()[-1] if self.available_dates() else None

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
