"""Intraday minute bars folded from the realtime quote poll.

Taiwan has no first-party minute-bar feed, so bars are accumulated here from the
MIS snapshots the quote poll already fetches. Only the part of a session this
process was running for can be covered; missing stretches stay missing rather
than being interpolated, so a gap in the chart means "we were not watching"
rather than "no trading happened".

Partition layout mirrors the daily stores, stocks and ETFs together:
    data/kline_minute/date=YYYY-MM-DD/part.parquet
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from app.taiwan.realtime.calendar import (
    CONTINUOUS_TRADING_END,
    REGULAR_OPEN_END,
    REGULAR_OPEN_START,
    TAIPEI_TZ,
)

logger = logging.getLogger(__name__)

MINUTE_COLUMNS = [
    "symbol",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "unattributed_volume",
]

# Volume that accumulated over a longer unobserved stretch cannot be placed in one minute.
MAX_ATTRIBUTABLE_GAP = timedelta(seconds=60)

_DIR_BY_ASSET = {"stock": "kline_minute"}


@dataclass
class _OpenBar:
    minute: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    unattributed_volume: float | None = None


class MinuteBarRecorder:
    """Folds successive realtime snapshots into one-minute OHLCV bars."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.config import settings
            data_dir = Path(settings.data_dir)
        self._data_dir = Path(data_dir)
        self._lock = threading.Lock()
        # The close flush and a manual refresh can read-modify-write one partition at once.
        self._write_lock = threading.Lock()
        self._open: dict[str, _OpenBar] = {}
        # Cumulative day volume last seen per symbol. A minute's volume is the increase
        # since the previous snapshot, so the first snapshot after start-up credits none to
        # its minute: the part of its total that no stored bar holds is unattributed.
        self._cum_volume: dict[str, float] = {}
        self._last_seen: dict[str, datetime] = {}
        # Latest minute already written per symbol; that minute or an earlier one is never reopened.
        self._written_through: dict[str, datetime] = {}
        self._pending_unattributed: dict[str, float] = {}
        self._session_date: date | None = None

    def record(
        self,
        records: Iterable[dict],
        *,
        resolve_asset_type: Callable[[str], str] | None = None,
    ) -> int:
        """Fold one poll's quotes in, writing out any bars that just completed.

        Quotes are placed by their own exchange time, not the wall clock, so the closing-auction
        result stamped 13:30:00 becomes its own 13:30 bar carrying the auction volume.
        Returns the number of completed bars written.
        """
        rows = list(records)
        stored = self._stored_totals_for_new_symbols(rows, resolve_asset_type)
        completed: list[dict] = []
        with self._lock:
            for row in rows:
                bar = self._fold(row, stored)
                if bar is not None:
                    completed.append(bar)

        if not completed:
            return 0
        return self._write(completed, resolve_asset_type)

    def flush(self, *, resolve_asset_type: Callable[[str], str] | None = None) -> int:
        """Write out every bar still open.

        Called at the close, where the last bar of the day would otherwise never roll over.
        Not called on shutdown: a written bar is final, so a partial in-flight bar stays unwritten.
        """
        with self._lock:
            pending = [
                self._as_row(symbol, bar) for symbol, bar in self._open.items()
            ]
            for symbol, bar in self._open.items():
                self._written_through[symbol] = bar.minute
            self._open.clear()
        if not pending:
            return 0
        return self._write(pending, resolve_asset_type)

    # ── Folding ────────────────────────────────────────────────────

    def _stored_totals_for_new_symbols(
        self,
        rows: list[dict],
        resolve_asset_type: Callable[[str], str] | None,
    ) -> dict[str, tuple[float, datetime]]:
        """Per symbol not yet seen today: (volume its stored bars hold, its last stored minute)."""
        days: dict[tuple[str, date], set[str]] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").strip()
            quote_dt = _quote_datetime(row)
            if not symbol or quote_dt is None or _as_float(row.get("volume")) is None:
                continue
            if quote_dt.date() == self._session_date and symbol in self._cum_volume:
                continue
            asset = _asset_of(symbol, resolve_asset_type)
            if asset in _DIR_BY_ASSET:
                days.setdefault((asset, quote_dt.date()), set()).add(symbol)

        stored: dict[str, tuple[float, datetime]] = {}
        for (asset, day), symbols in days.items():
            target = self._partition_path(asset, day)
            if not target.exists():
                continue
            try:
                summary = (
                    _with_minute_columns(pl.read_parquet(target))
                    .filter(pl.col("symbol").is_in(sorted(symbols)))
                    .group_by("symbol")
                    .agg(
                        (
                            pl.col("volume").fill_null(0.0).sum()
                            + pl.col("unattributed_volume").fill_null(0.0).sum()
                        ).alias("total"),
                        pl.col("datetime").max().alias("last"),
                    )
                )
            except Exception as exc:
                logger.warning("minute bar partition unreadable (%s): %s", target, exc)
                continue
            for symbol, total, last in summary.iter_rows():
                stored[symbol] = (float(total), _as_taipei(last))
        return stored

    def _fold(self, row: dict, stored: dict[str, tuple[float, datetime]]) -> dict | None:
        """Update the open bar for one quote; return a row when a bar completes."""
        symbol = str(row.get("symbol") or "").strip()
        price = _as_float(row.get("close"))
        if not symbol or price is None or price <= 0:
            return None

        quote_dt = _quote_datetime(row)
        if quote_dt is None:
            return None
        if not (REGULAR_OPEN_START <= quote_dt.time() <= REGULAR_OPEN_END):
            return None
        if self._session_date is not None and quote_dt.date() < self._session_date:
            return None

        if self._session_date != quote_dt.date():
            self._session_date = quote_dt.date()
            self._open.clear()
            self._cum_volume.clear()
            self._last_seen.clear()
            self._written_through.clear()
            self._pending_unattributed.clear()

        minute = quote_dt.replace(second=0, microsecond=0)
        seen = _observed_datetime(row) or quote_dt
        last_seen = self._last_seen.get(symbol)
        self._last_seen[symbol] = seen

        cum = _as_float(row.get("volume"))
        delta = 0.0
        unattributed = 0.0
        if cum is not None:
            previous = self._cum_volume.get(symbol)
            if previous is None:
                stored_total, stored_through = stored.get(symbol, (0.0, None))
                unattributed = max(cum - stored_total, 0.0)
                if stored_through is not None:
                    self._written_through[symbol] = stored_through
            # A cumulative total that went backwards means a new session or a
            # provider reset; treat it as a fresh baseline instead of a negative bar.
            elif cum >= previous:
                delta = cum - previous
            self._cum_volume[symbol] = cum

        if (
            delta > 0
            and last_seen is not None
            and _trading_time_between(last_seen, seen) > MAX_ATTRIBUTABLE_GAP
        ):
            unattributed, delta = unattributed + delta, 0.0

        bar = self._open.get(symbol)
        written_through = self._written_through.get(symbol)
        if bar is None and written_through is not None and minute <= written_through:
            if delta or unattributed:
                self._pending_unattributed[symbol] = (
                    self._pending_unattributed.get(symbol, 0.0) + delta + unattributed
                )
            return None
        if bar is not None and bar.minute == minute:
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            bar.volume += delta
            bar.amount += price * delta
            if unattributed:
                bar.unattributed_volume = (bar.unattributed_volume or 0.0) + unattributed
            return None

        finished = None
        if bar is not None:
            finished = self._as_row(symbol, bar)
            self._written_through[symbol] = bar.minute
        unattributed += self._pending_unattributed.pop(symbol, 0.0)
        self._open[symbol] = _OpenBar(
            minute=minute,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=delta,
            amount=price * delta,
            unattributed_volume=unattributed or None,
        )
        return finished

    @staticmethod
    def _as_row(symbol: str, bar: _OpenBar) -> dict:
        return {
            "symbol": symbol,
            "datetime": bar.minute,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "amount": bar.amount,
            "unattributed_volume": bar.unattributed_volume,
        }

    # ── Persistence ────────────────────────────────────────────────

    def _write(
        self,
        rows: list[dict],
        resolve_asset_type: Callable[[str], str] | None,
    ) -> int:
        grouped: dict[tuple[str, date], list[dict]] = {}
        for row in rows:
            asset = _asset_of(row["symbol"], resolve_asset_type)
            if asset not in _DIR_BY_ASSET:
                continue
            grouped.setdefault((asset, row["datetime"].date()), []).append(row)

        written = 0
        with self._write_lock:
            for (asset, day), group in grouped.items():
                try:
                    written += self._write_partition(asset, day, pl.DataFrame(group))
                except Exception as exc:
                    logger.warning("minute bar write failed (%s %s): %s", asset, day, exc)
        return written

    def _partition_path(self, asset: str, day: date) -> Path:
        return self._data_dir / _DIR_BY_ASSET[asset] / f"date={day.isoformat()}" / "part.parquet"

    def _write_partition(self, asset: str, day: date, df: pl.DataFrame) -> int:
        target = self._partition_path(asset, day)
        partition_dir = target.parent
        partition_dir.mkdir(parents=True, exist_ok=True)

        # A new bar replaces the stored one for its minute unless it carries no volume at all.
        carries_volume = (pl.col("volume").fill_null(0.0) > 0) | (
            pl.col("unattributed_volume").fill_null(0.0) > 0
        )
        df = _with_minute_columns(df).with_columns(
            pl.when(carries_volume).then(2).otherwise(0).alias("_rank")
        )
        if target.exists():
            try:
                existing = _with_minute_columns(pl.read_parquet(target))
                df = pl.concat(
                    [existing.with_columns(pl.lit(1).alias("_rank")), df], how="vertical_relaxed"
                )
            except Exception as exc:
                logger.warning("minute bar partition unreadable, replacing (%s): %s", target, exc)

        df = (
            df.sort("_rank", maintain_order=True)
            .unique(subset=["symbol", "datetime"], keep="last", maintain_order=True)
            .drop("_rank")
            .sort(["symbol", "datetime"])
        )

        # Write to a sibling temp file then replace, so a reader never sees a
        # half-written partition. The suffix must not end in .parquet: readers glob
        # <root>/**/*.parquet, so a temp file named that way is picked up as a second
        # partition file and every row in it is counted twice.
        fd, tmp_path = tempfile.mkstemp(dir=partition_dir, suffix=".parquet.tmp")
        os.close(fd)
        try:
            df.write_parquet(tmp_path)
            os.replace(tmp_path, target)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
        return df.height


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # reject NaN


def _asset_of(symbol: str, resolve_asset_type: Callable[[str], str] | None) -> str:
    if resolve_asset_type is None:
        return "stock"
    try:
        return resolve_asset_type(symbol)
    except Exception:
        return "stock"


def _as_taipei(value: datetime) -> datetime:
    return value.astimezone(TAIPEI_TZ) if value.tzinfo else value.replace(tzinfo=TAIPEI_TZ)


def _with_minute_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Partitions written before the gap column existed are read with it as null."""
    if "unattributed_volume" not in df.columns:
        df = df.with_columns(pl.lit(None).alias("unattributed_volume"))
    return df.with_columns(pl.col("unattributed_volume").cast(pl.Float64)).select(MINUTE_COLUMNS)


def _trading_time_between(start: datetime, end: datetime) -> timedelta:
    """Time between two polls, less the closing call auction, during which nothing executes."""
    auction_start = end.replace(
        hour=CONTINUOUS_TRADING_END.hour,
        minute=CONTINUOUS_TRADING_END.minute,
        second=0,
        microsecond=0,
    )
    auction_end = end.replace(
        hour=REGULAR_OPEN_END.hour, minute=REGULAR_OPEN_END.minute, second=0, microsecond=0
    )
    overlap = min(end, auction_end) - max(start, auction_start)
    return (end - start) - max(overlap, timedelta(0))


def _observed_datetime(row: dict) -> datetime | None:
    """When the poll fetched the quote; an untraded stock's quote time lags it."""
    value = row.get("retrieved_at")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.astimezone(TAIPEI_TZ) if value.tzinfo else value.replace(tzinfo=TAIPEI_TZ)


def _quote_datetime(row: dict) -> datetime | None:
    """Taipei-local quote time, preferring the exchange timestamp over local time."""
    ts_ms = row.get("quote_ts")
    if ts_ms:
        try:
            return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=TAIPEI_TZ)
        except (OverflowError, OSError, ValueError, TypeError):
            pass
    stamp = row.get("timestamp")
    if isinstance(stamp, datetime):
        return stamp.astimezone(TAIPEI_TZ) if stamp.tzinfo else stamp.replace(tzinfo=TAIPEI_TZ)
    return None


_RECORDER: MinuteBarRecorder | None = None
_RECORDER_LOCK = threading.Lock()


def get_minute_recorder() -> MinuteBarRecorder:
    global _RECORDER
    with _RECORDER_LOCK:
        if _RECORDER is None:
            _RECORDER = MinuteBarRecorder()
        return _RECORDER


__all__ = ["MINUTE_COLUMNS", "MinuteBarRecorder", "get_minute_recorder"]
