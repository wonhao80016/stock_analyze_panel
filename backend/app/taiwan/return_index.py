"""Daily closes of the TWSE total return indices, archived from the MI_INDEX response.

MI_INDEX?type=ALL already carries the return index table next to the closing quotes, so this
adds no request. Days accumulate from the first archived response; past days are not
backfilled. Rows are a historical reference for display statistics and are not a backtest input.

Layout: <taiwan data root>/return_index/date=YYYY-MM-DD/part.parquet
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import polars as pl

from app.market_time import cn_now
from app.taiwan.universe.service import write_atomically

logger = logging.getLogger(__name__)

TAIEX_TOTAL_RETURN = "發行量加權股價報酬指數"
SOURCE = "twse_mi_index"
TAIPEI_TZ = "Asia/Taipei"

_TABLE_TITLE_MARKERS = ("報酬指數", "臺灣證券交易所")
_NAME_FIELDS = ("報酬指數", "指數")
_CLOSE_FIELD = "收盤指數"
_MISSING_VALUES = {"", "-", "--", "---", "N/A"}

SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date,
    "index_name": pl.Utf8,
    "close": pl.Float64,
    "source": pl.Utf8,
    "source_url": pl.Utf8,
    "retrieved_at": pl.Datetime("us", TAIPEI_TZ),
}

_write_lock = threading.Lock()


class ReturnIndexTableMissingError(LookupError):
    """The response has no TWSE return index table."""


class ReturnIndexSchemaError(ValueError):
    """The return index table no longer has the expected columns or TAIEX row."""


class ArchiveOutcome(StrEnum):
    ADDED = "added"
    UNCHANGED = "unchanged"
    REVISED = "revised"


def _close_value(raw: object) -> float:
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if text in _MISSING_VALUES:
        raise ReturnIndexSchemaError(f"close value is missing: {raw!r}")
    try:
        return float(text)
    except ValueError as exc:
        raise ReturnIndexSchemaError(f"close value is not a number: {raw!r}") from exc


def _is_return_index_table(table: dict[str, Any]) -> bool:
    title = str(table.get("title") or "").replace("台", "臺")
    return all(marker in title for marker in _TABLE_TITLE_MARKERS)


def parse_return_index_table(payload: dict[str, Any]) -> dict[str, float]:
    """Index name -> close from the TWSE return index table of an MI_INDEX payload.

    Raises ReturnIndexTableMissingError without such a table, and ReturnIndexSchemaError when the
    name or close column cannot be found by name, or the TAIEX total return row is absent or
    has no numeric close. Other rows without a numeric close are skipped.
    """
    tables = [t for t in payload.get("tables") or [] if _is_return_index_table(t)]
    if not tables:
        raise ReturnIndexTableMissingError("no 報酬指數(臺灣證券交易所) table in the response")
    if len(tables) > 1:
        raise ReturnIndexSchemaError(f"{len(tables)} return index tables match")
    table = tables[0]
    fields = [str(f).strip() for f in table.get("fields") or []]
    name_idx = next((fields.index(f) for f in _NAME_FIELDS if f in fields), None)
    if name_idx is None or _CLOSE_FIELD not in fields:
        raise ReturnIndexSchemaError(f"unexpected return index fields: {fields}")
    close_idx = fields.index(_CLOSE_FIELD)

    closes: dict[str, float] = {}
    for row in table.get("data") or []:
        if len(row) <= max(name_idx, close_idx):
            continue
        name = str(row[name_idx]).strip()
        if not name:
            continue
        try:
            closes[name] = _close_value(row[close_idx])
        except ReturnIndexSchemaError:
            if name == TAIEX_TOTAL_RETURN:
                raise
            logger.info("return index %s has no close: %r", name, row[close_idx])
    if TAIEX_TOTAL_RETURN not in closes:
        raise ReturnIndexSchemaError(f"{TAIEX_TOTAL_RETURN} row is missing")
    return closes


class TaiwanReturnIndexStore:
    """One parquet partition per trading date."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "return_index"
        self.root = Path(data_dir)

    def partition_path(self, d: date) -> Path:
        return self.root / f"date={d.isoformat()}" / "part.parquet"

    def write_day(
        self, d: date, closes: dict[str, float], source_url: str, retrieved_at: datetime
    ) -> ArchiveOutcome:
        """Store *closes* for *d*.

        Identical closes leave the partition untouched; different ones replace it and log a
        warning. An unreadable partition or a failed replace raises and leaves it as it was.
        """
        if not closes:
            raise ValueError(f"no return index closes for {d}")
        path = self.partition_path(d)
        with _write_lock:
            previous = self._read(path)
            stored = dict(zip(previous["index_name"].to_list(), previous["close"].to_list(), strict=True))
            if stored == closes:
                return ArchiveOutcome.UNCHANGED
            if stored:
                changes = ", ".join(
                    f"{name}: {stored.get(name)!r} -> {closes.get(name)!r}"
                    for name in sorted(set(stored) | set(closes))
                    if stored.get(name) != closes.get(name)
                )
                logger.warning("taiwan return index %s revised (%s)", d, changes)
            frame = pl.DataFrame(
                [
                    {
                        "date": d,
                        "index_name": name,
                        "close": close,
                        "source": SOURCE,
                        "source_url": source_url,
                        "retrieved_at": retrieved_at,
                    }
                    for name, close in sorted(closes.items())
                ],
                schema=SCHEMA,
            )
            write_atomically(path, frame.write_parquet)
        return ArchiveOutcome.REVISED if stored else ArchiveOutcome.ADDED

    def _read(self, path: Path) -> pl.DataFrame:
        """The partition at *path*, empty when absent; an unreadable file raises."""
        if not path.exists():
            return pl.DataFrame(schema=SCHEMA)
        return pl.read_parquet(path).select(list(SCHEMA)).cast(SCHEMA)

    def read_range(self, start: date, end: date, index_name: str | None = None) -> pl.DataFrame:
        """Rows dated within [start, end], oldest first; *index_name* keeps that index only."""
        frames: list[pl.DataFrame] = []
        if self.root.exists():
            for part in self.root.glob("date=*/part.parquet"):
                try:
                    d = date.fromisoformat(part.parent.name.split("=", 1)[1])
                except ValueError:
                    continue
                if start <= d <= end:
                    frames.append(self._read(part))
        if not frames:
            return pl.DataFrame(schema=SCHEMA)
        frame = pl.concat(frames)
        if index_name is not None:
            frame = frame.filter(pl.col("index_name") == index_name)
        return frame.sort(["date", "index_name"])


def archive_mi_index_return_index(
    payload: dict[str, Any],
    target_date: date,
    source_url: str,
    store: TaiwanReturnIndexStore,
    retrieved_at: datetime | None = None,
) -> ArchiveOutcome | None:
    """Archive the return index table of an MI_INDEX *payload* requested for *target_date*.

    Returns None, after logging, when the payload is not stamped with *target_date* or has
    no usable table. Store failures raise.
    """
    stamped = str(payload.get("date") or "").strip()
    if stamped != target_date.strftime("%Y%m%d"):
        logger.warning("return index not archived: MI_INDEX for %s is stamped %r", target_date, stamped)
        return None
    try:
        closes = parse_return_index_table(payload)
    except ReturnIndexTableMissingError as exc:
        logger.warning("return index not archived for %s: %s", target_date, exc)
        return None
    except ReturnIndexSchemaError as exc:
        logger.error("return index not archived for %s: %s", target_date, exc)
        return None
    return store.write_day(target_date, closes, source_url, retrieved_at or cn_now())


def read_return_index_series(
    start: date,
    end: date,
    index_name: str = TAIEX_TOTAL_RETURN,
    store: TaiwanReturnIndexStore | None = None,
) -> pl.DataFrame:
    """Archived daily closes of *index_name* within [start, end] as (date, close), oldest first.

    Only archived days appear; a missing day is absent rather than filled.
    """
    frame = (store or TaiwanReturnIndexStore()).read_range(start, end, index_name=index_name)
    return frame.select("date", "close")
