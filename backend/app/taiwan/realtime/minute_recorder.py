"""Intraday minute bars folded from the realtime quote poll.

Taiwan has no first-party minute-bar feed, so bars are accumulated here from the
MIS snapshots the quote poll already fetches. Only the part of a session this
process was running for can be covered; missing stretches stay missing rather
than being interpolated, so a gap in the chart means "we were not watching"
rather than "no trading happened".

Partition layout mirrors the daily stores:
    data/kline_minute/date=YYYY-MM-DD/part.parquet
    data/kline_etf_minute/date=YYYY-MM-DD/part.parquet
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

from app.taiwan.realtime.calendar import (
    REGULAR_OPEN_END,
    REGULAR_OPEN_START,
    TAIPEI_TZ,
    MarketStatus,
    get_market_status,
)

logger = logging.getLogger(__name__)

MINUTE_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]

_DIR_BY_ASSET = {"etf": "kline_etf_minute", "stock": "kline_minute"}


@dataclass
class _OpenBar:
    minute: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


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
        # Cumulative day volume last seen per symbol. A minute's volume is the
        # increase since the previous snapshot, so the first snapshot after start-up
        # contributes none: its cumulative total belongs to minutes we never saw.
        self._cum_volume: dict[str, float] = {}
        self._session_date: date | None = None

    def record(
        self,
        records: Iterable[dict],
        *,
        resolve_asset_type: Callable[[str], str] | None = None,
    ) -> int:
        """Fold one poll's quotes in, writing out any bars that just completed.

        Returns the number of completed bars written.
        """
        if get_market_status() != MarketStatus.OPEN:
            return 0

        completed: list[dict] = []
        with self._lock:
            for row in records:
                bar = self._fold(row)
                if bar is not None:
                    completed.append(bar)

        if not completed:
            return 0
        return self._write(completed, resolve_asset_type)

    def flush(self, *, resolve_asset_type: Callable[[str], str] | None = None) -> int:
        """Write out every bar still open.

        Called at the close, where the last bar of the day would otherwise never
        roll over. Not called on shutdown: the in-flight bar is not persisted there
        on purpose, because a restart resets the cumulative-volume baseline and the
        replacement bar for that same minute would overwrite it with a zero volume.
        """
        with self._lock:
            pending = [
                self._as_row(symbol, bar) for symbol, bar in self._open.items()
            ]
            self._open.clear()
        if not pending:
            return 0
        return self._write(pending, resolve_asset_type)

    # ── Folding ────────────────────────────────────────────────────

    def _fold(self, row: dict) -> dict | None:
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

        if self._session_date != quote_dt.date():
            self._session_date = quote_dt.date()
            self._open.clear()
            self._cum_volume.clear()

        minute = quote_dt.replace(second=0, microsecond=0)

        cum = _as_float(row.get("volume"))
        delta = 0.0
        if cum is not None:
            previous = self._cum_volume.get(symbol)
            # A cumulative total that went backwards means a new session or a
            # provider reset; treat it as a fresh baseline instead of a negative bar.
            if previous is not None and cum >= previous:
                delta = cum - previous
            self._cum_volume[symbol] = cum

        bar = self._open.get(symbol)
        if bar is not None and bar.minute == minute:
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            bar.volume += delta
            bar.amount += price * delta
            return None

        finished = self._as_row(symbol, bar) if bar is not None else None
        self._open[symbol] = _OpenBar(
            minute=minute,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=delta,
            amount=price * delta,
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
        }

    # ── Persistence ────────────────────────────────────────────────

    def _write(
        self,
        rows: list[dict],
        resolve_asset_type: Callable[[str], str] | None,
    ) -> int:
        grouped: dict[tuple[str, date], list[dict]] = {}
        for row in rows:
            asset = "stock"
            if resolve_asset_type is not None:
                try:
                    asset = resolve_asset_type(row["symbol"])
                except Exception:
                    asset = "stock"
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

    def _write_partition(self, asset: str, day: date, df: pl.DataFrame) -> int:
        partition_dir = self._data_dir / _DIR_BY_ASSET[asset] / f"date={day.isoformat()}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        target = partition_dir / "part.parquet"

        df = df.select(MINUTE_COLUMNS)
        if target.exists():
            try:
                existing = pl.read_parquet(target)
                df = pl.concat([existing.select(MINUTE_COLUMNS), df], how="vertical_relaxed")
            except Exception as exc:
                logger.warning("minute bar partition unreadable, replacing (%s): %s", target, exc)

        df = df.unique(subset=["symbol", "datetime"], keep="last").sort(["symbol", "datetime"])

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
