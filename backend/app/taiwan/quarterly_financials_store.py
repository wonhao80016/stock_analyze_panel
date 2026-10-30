"""Display-only archive of official quarterly statement snapshots, one partition per fiscal quarter.

The official lists carry only the latest quarter and no availability time, so these rows keep
``available_at`` unset and must not feed backtests, historical screens or any as-of query. A
later retrieval with different values overwrites the stored ones (recording ``revised_at``), so
a past quarter may read as its corrected figures rather than what was first published.

Layout: <taiwan data root>/quarterly_financials/year=YYYY/quarter=Q/part.parquet
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

AMOUNT_COLUMNS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "parent_net_income",
    "assets",
    "liabilities",
    "equity",
    "parent_equity",
)
# Income amounts (TWD) and EPS (TWD per share) are year-to-date; balance amounts are quarter-end TWD.
VALUE_COLUMNS = (*AMOUNT_COLUMNS[:5], "cumulative_eps", *AMOUNT_COLUMNS[5:])
COMPARED_COLUMNS = ("accounting_category", "period_end", *VALUE_COLUMNS)

SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "exchange": pl.Utf8,
    "accounting_category": pl.Utf8,
    "fiscal_year": pl.Int32,
    "fiscal_quarter": pl.Int8,
    "period_end": pl.Utf8,
    **{name: pl.Int64 for name in AMOUNT_COLUMNS[:5]},
    "cumulative_eps": pl.Float64,
    **{name: pl.Int64 for name in AMOUNT_COLUMNS[5:]},
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


class TaiwanQuarterlyFinancialStore:
    """Per-quarter parquet partitions keyed by (symbol, fiscal_year, fiscal_quarter)."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "quarterly_financials"
        self.root = Path(data_dir)

    def partition_path(self, fiscal_year: int, fiscal_quarter: int) -> Path:
        return self.root / f"year={fiscal_year:04d}" / f"quarter={fiscal_quarter}" / "part.parquet"

    def upsert(self, rows: list[dict[str, Any]]) -> UpsertResult:
        """Merge *rows* into their quarter partitions.

        Each row must carry every SCHEMA column except first_retrieved_at, revised_at and
        revised_fields; its last_retrieved_at is the retrieval time. Equal values only advance
        last_retrieved_at; different values replace the stored ones and set revised_at. Each
        partition is replaced atomically; an unreadable partition or a failed replace raises and
        leaves that partition as it was.
        """
        result = UpsertResult()
        by_quarter: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            quarter = int(row["fiscal_quarter"])
            if not 1 <= quarter <= 4:
                raise ValueError(f"invalid fiscal quarter for {row.get('symbol')}: {quarter}")
            by_quarter[(int(row["fiscal_year"]), quarter)].append(row)
        with _write_lock:
            for (year, quarter), incoming in sorted(by_quarter.items()):
                self._upsert_partition(year, quarter, incoming, result)
        return result

    def _upsert_partition(
        self, year: int, quarter: int, incoming: list[dict[str, Any]], result: UpsertResult
    ) -> None:
        path = self.partition_path(year, quarter)
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
                "taiwan quarterly financials: %s %dQ%d revised (%s)",
                row["symbol"], year, quarter,
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
        return sorted(self.root.glob("year=*/quarter=*/part.parquet")) if self.root.exists() else []

    def read_all(self) -> pl.DataFrame:
        """Every stored row of every quarter; empty when nothing is stored, an unreadable partition raises."""
        frames = [self._read(path) for path in self._partitions()]
        return pl.concat(frames) if frames else pl.DataFrame(schema=SCHEMA)

    def signature(self) -> tuple[tuple[str, int, int], ...]:
        """(path, mtime_ns, size) of every stored partition; it changes whenever one is written."""
        return tuple((str(p), (s := p.stat()).st_mtime_ns, s.st_size) for p in self._partitions())

    def rows_for(self, symbol: str) -> list[dict[str, Any]]:
        """Every stored quarter of *symbol*, oldest first."""
        rows: list[dict[str, Any]] = []
        if not self.root.exists():
            return rows
        for path in sorted(self.root.glob("year=*/quarter=*/part.parquet")):
            rows.extend(self._read(path).filter(pl.col("symbol") == symbol).iter_rows(named=True))
        return sorted(rows, key=lambda row: (row["fiscal_year"], row["fiscal_quarter"]))
