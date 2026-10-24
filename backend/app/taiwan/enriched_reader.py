"""Read-only access to the published Taiwan enriched partitions.

The enriched table (kline_daily_enriched) is the single source of indicator
values; readers must not recompute them. A missing or unreadable partition
returns None so callers can report the indicators as unpublished instead of
falling back to a second formula.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

ENRICHED_DIR_NAME = "kline_daily_enriched"
_PARTITION_RE = re.compile(r"^date=(\d{4}-\d{2}-\d{2})$")


def _enriched_root() -> Path:
    from app.config import settings

    return Path(settings.data_dir) / ENRICHED_DIR_NAME


def _load_published(part: Path, wanted: list[str]) -> pl.DataFrame | None:
    """The partition frame, or None when it is absent, unreadable or carries no indicator values.

    A partition holding only the base bar columns (written by the live-quote merge)
    or whose requested columns are all null is not a published indicator table.
    """
    if not part.exists():
        return None
    try:
        frame = pl.read_parquet(part)
    except Exception as exc:
        logger.warning("enriched partition unreadable at %s: %s", part, exc)
        return None
    if "symbol" not in frame.columns or any(c not in frame.columns for c in wanted):
        return None
    if wanted and all(frame[c].null_count() == frame.height for c in wanted):
        return None
    return frame


def _select(
    frame: pl.DataFrame, wanted: list[str], symbols: list[str] | None, optional: tuple[str, ...] = ()
) -> pl.DataFrame:
    if symbols is not None:
        frame = frame.filter(pl.col("symbol").is_in(symbols))
    present = [c for c in optional if c in frame.columns and c not in wanted]
    return frame.select(["symbol", *wanted, *present])


def read_enriched_partition(
    as_of: date | str,
    columns: list[str],
    symbols: list[str] | None = None,
) -> pl.DataFrame | None:
    """One day's published rows with ``symbol`` plus the requested columns, or None if unpublished."""
    wanted = [c for c in columns if c != "symbol"]
    frame = _load_published(_enriched_root() / f"date={as_of}" / "part.parquet", wanted)
    return None if frame is None else _select(frame, wanted, symbols)


def read_latest_enriched(
    columns: list[str],
    symbols: list[str] | None = None,
    optional_columns: tuple[str, ...] = (),
) -> tuple[date, pl.DataFrame] | None:
    """Rows of the newest partition that really carries the requested indicators.

    ``optional_columns`` are added from that same partition when present; they never decide
    which partition is chosen.
    """
    root = _enriched_root()
    if not root.is_dir():
        return None
    wanted = [c for c in columns if c != "symbol"]
    dated: list[date] = []
    for child in root.iterdir():
        m = _PARTITION_RE.match(child.name)
        if m:
            dated.append(date.fromisoformat(m.group(1)))
    for d in sorted(dated, reverse=True):
        frame = _load_published(root / f"date={d.isoformat()}" / "part.parquet", wanted)
        if frame is not None:
            return d, _select(frame, wanted, symbols, optional_columns)
    return None
