"""Display-only archive of official monthly revenue snapshots, one partition per revenue month.

The official lists carry only the latest month and no availability time, so these rows keep
``available_at`` unset and must not feed backtests, historical screens or any as-of query. A
later retrieval with different values overwrites the stored ones (recording ``revised_at``), so
a past month may read as its corrected figures rather than what was first published.

Layout: <taiwan data root>/monthly_revenue/year=YYYY/month=MM/part.parquet
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.universe.service import write_atomically

logger = logging.getLogger(__name__)

# Amounts in TWD; *_pct in percent (12.5 means 12.5%).
AMOUNT_COLUMNS = (
    "revenue",
    "previous_month_revenue",
    "previous_year_revenue",
    "cumulative_revenue",
    "previous_year_cumulative_revenue",
)
PERCENT_COLUMNS = ("mom_pct", "yoy_pct", "cumulative_yoy_pct")
# report_date is when the list was produced, not part of the figures; it alone is no revision.
COMPARED_COLUMNS = (*AMOUNT_COLUMNS, *PERCENT_COLUMNS)

SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "exchange": pl.Utf8,
    "year": pl.Int32,
    "month": pl.Int8,
    **{name: pl.Int64 for name in AMOUNT_COLUMNS},
    **{name: pl.Float64 for name in PERCENT_COLUMNS},
    "report_date": pl.Utf8,
    "source": pl.Utf8,
    "source_url": pl.Utf8,
    "first_retrieved_at": pl.Utf8,
    "last_retrieved_at": pl.Utf8,
    "revised_at": pl.Utf8,
    "revised_fields": pl.Utf8,
}

_write_lock = threading.Lock()
_read_cache: dict[str, tuple[tuple[int, int], pl.DataFrame]] = {}
_read_cache_lock = threading.Lock()


@dataclass
class UpsertResult:
    added: int = 0
    unchanged: int = 0
    revised: list[str] = field(default_factory=list)


def _changed_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    return [name for name in COMPARED_COLUMNS if old.get(name) != new.get(name)]


class TaiwanMonthlyRevenueStore:
    """Per-month parquet partitions keyed by (symbol, year, month)."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "monthly_revenue"
        self.root = Path(data_dir)

    def partition_path(self, year: int, month: int) -> Path:
        return self.root / f"year={year:04d}" / f"month={month:02d}" / "part.parquet"

    def upsert(self, rows: list[dict[str, Any]]) -> UpsertResult:
        """Merge *rows* into their month partitions.

        Each row must carry every SCHEMA column except first_retrieved_at, revised_at and
        revised_fields; its last_retrieved_at is the retrieval time. Equal values only advance
        last_retrieved_at; different values replace the stored ones (report_date included) and set
        revised_at. Each partition is replaced atomically; an unreadable partition or a failed
        replace raises and leaves that partition as it was.
        """
        result = UpsertResult()
        by_month: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            month = int(row["month"])
            if not 1 <= month <= 12:
                raise ValueError(f"invalid revenue month for {row.get('symbol')}: {month}")
            by_month[(int(row["year"]), month)].append(row)
        with _write_lock:
            for (year, month), incoming in sorted(by_month.items()):
                self._upsert_partition(year, month, incoming, result)
        return result

    def _upsert_partition(
        self, year: int, month: int, incoming: list[dict[str, Any]], result: UpsertResult
    ) -> None:
        path = self.partition_path(year, month)
        stored = {row["symbol"]: row for row in self._read(path).iter_rows(named=True)}
        changed = False
        for row in incoming:
            retrieved = row["last_retrieved_at"]
            previous = stored.get(row["symbol"])
            if previous is None:
                stored[row["symbol"]] = {
                    **{name: row.get(name) for name in SCHEMA},
                    "first_retrieved_at": retrieved,
                    "revised_at": None,
                    "revised_fields": None,
                }
                result.added += 1
                changed = True
                continue
            if previous["last_retrieved_at"] is not None and retrieved < previous["last_retrieved_at"]:
                result.unchanged += 1
                continue
            differences = _changed_fields(previous, row)
            if not differences:
                result.unchanged += 1
                if retrieved != previous["last_retrieved_at"]:
                    stored[row["symbol"]] = {**previous, "last_retrieved_at": retrieved}
                    changed = True
                continue
            logger.warning(
                "taiwan monthly revenue: %s %04d-%02d revised (%s)",
                row["symbol"], year, month,
                ", ".join(f"{name}: {previous.get(name)!r} -> {row.get(name)!r}" for name in differences),
            )
            stored[row["symbol"]] = {
                **{name: row.get(name) for name in SCHEMA},
                "first_retrieved_at": previous["first_retrieved_at"],
                "revised_at": retrieved,
                "revised_fields": ",".join(differences),
            }
            result.revised.append(row["symbol"])
            changed = True
        if not changed:
            return
        frame = pl.DataFrame(list(stored.values()), schema=SCHEMA).sort("symbol")
        write_atomically(path, frame.write_parquet)
        with _read_cache_lock:
            _read_cache.pop(str(path), None)

    def _read(self, path: Path) -> pl.DataFrame:
        """The partition at *path*, empty when absent; an unreadable file raises."""
        try:
            stat = path.stat()
        except FileNotFoundError:
            return pl.DataFrame(schema=SCHEMA)
        signature = (stat.st_mtime_ns, stat.st_size)
        key = str(path)
        with _read_cache_lock:
            cached = _read_cache.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        frame = pl.read_parquet(path)
        missing = [name for name in SCHEMA if name not in frame.columns]
        frame = frame.with_columns(pl.lit(None, dtype=SCHEMA[name]).alias(name) for name in missing)
        frame = frame.select(list(SCHEMA)).cast(SCHEMA)
        with _read_cache_lock:
            _read_cache[key] = (signature, frame)
        return frame

    def _partitions(self) -> list[Path]:
        return sorted(self.root.glob("year=*/month=*/part.parquet")) if self.root.exists() else []

    def read_all(self) -> pl.DataFrame:
        """Every stored row of every month; empty when nothing is stored, an unreadable partition raises."""
        frames = [self._read(path) for path in self._partitions()]
        return pl.concat(frames) if frames else pl.DataFrame(schema=SCHEMA)

    def signature(self) -> tuple[tuple[str, int, int], ...]:
        """(path, mtime_ns, size) of every stored partition; it changes whenever one is written."""
        return tuple((str(p), (s := p.stat()).st_mtime_ns, s.st_size) for p in self._partitions())

    def rows_for(self, symbol: str) -> list[dict[str, Any]]:
        """Every stored month of *symbol*, newest first."""
        rows: list[dict[str, Any]] = []
        if not self.root.exists():
            return rows
        for path in sorted(self.root.glob("year=*/month=*/part.parquet")):
            rows.extend(self._read(path).filter(pl.col("symbol") == symbol).iter_rows(named=True))
        return sorted(rows, key=lambda row: (row["year"], row["month"]), reverse=True)
