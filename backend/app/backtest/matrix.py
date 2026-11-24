"""Matrix structures, builders, NumPy features, and matrix-strategy contract."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads

from app.backtest.minute_trigger import build_minute_exit_reference
from app.backtest.numba_runtime import run_numba_parallel
from app.indicators.pipeline import (
    CROSS_TOLERANCE,
    _reference_prev_close,
    ema_values,
    kd_values,
    macd_values,
    rsi_values,
    wilder_values,
)
from app.price_limits import write_numpy_price_limit_matrix
from app.strategy.listing_age import eligible_from, exclude_new_days
from app.strategy.scoring import AMOUNT_ESTIMATED, SCORING_DIRECTION_LOW
from app.strategy.scoring import recursive_warmup_bars as recursive_warmup_bars
from app.taiwan.share_capital_store import MIN_SESSION_COVERAGE as MIN_SESSION_COVERAGE
from app.taiwan.share_capital_store import session_coverage as _symbols_session_coverage
from app.volume_units import TAIWAN_SUFFIXES

try:
    from numba import njit, prange
except ImportError:
    def njit(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def decorator(func):
            return func

        return decorator

    prange = range

# Basic-filter bounds on a price or a market value; app.strategy.engine.RAW_PRICE_BOUND_KEYS.
_RAW_PRICE_BOUND_KEYS = (
    "price_min", "price_max",
    "market_cap_min", "market_cap_max",
    "float_cap_min", "float_cap_max",
)
_MATRIX_CACHE_VERSION = 3
_DIRECT_MATRIX_LOADER_VERSION = 13
_MATRIX_AXIS_INDEX_VERSION = 1
# Newest axis index files kept next to the matrix disk cache.
_MATRIX_AXIS_INDEX_KEEP = 16
_PUBLISHED_LIMIT_SIGNALS = (
    ("signal_limit_up", "limit_up_locked"),
    ("signal_limit_down", "limit_down_locked"),
)
# True on a Taiwan row whose official limit base is unknown; partitions written before it lack it.
BASE_MISSING_COLUMN = "ref_base_missing"
# Enriched columns that move a bar's previous-close base onto the official reference price.
_REFERENCE_BASE_FIELDS = ("ref_prev_close", BASE_MISSING_COLUMN)
LIMIT_RULE_NOT_IN_SECURITY_MASTER = "security_master_missing"
LIMIT_RULE_UNCONFIRMED = "price_limit_unknown"
LIMIT_RULE_LISTING_DATE_UNKNOWN = "listing_date_unknown"
_ARROW_BATCH_SIZE = 131_072
_SCORE_ASSET_CHUNK_SIZE = 256
_ROLLING_MATERIALIZED_WINDOW_BUDGET_BYTES = 32 * 1024 * 1024
_MATRIX_DISK_CACHE_DEFAULT_MAX_BYTES = 512 * 1024 * 1024

logger = logging.getLogger(__name__)


class MatrixPrewarmCancelledError(RuntimeError):
    """A matrix cache prewarm was cancelled during application shutdown."""


def _raise_if_matrix_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise MatrixPrewarmCancelledError("matrix cache prewarm cancelled")


_MATRIX_DISK_CACHE_LOCK = threading.RLock()
_MATRIX_DISK_CACHE_LEASES: dict[str, int] = {}
_MATRIX_DISK_CACHE_PENDING_DELETE: set[str] = set()
_ACTIVE_VALID_BAR_INDEX: ContextVar[Any] = ContextVar(
    "active_valid_bar_index",
    default=None,
)


@contextmanager
def _activate_valid_bar_index(index: ValidBarIndex) -> Iterator[None]:
    token = _ACTIVE_VALID_BAR_INDEX.set(index)
    try:
        yield
    finally:
        _ACTIVE_VALID_BAR_INDEX.reset(token)


@dataclass(frozen=True)
class MatrixCacheProfile:
    """Shared disk-cache boundary for one matrix-native asset universe."""

    field_columns: frozenset[str]
    warmup_bars: int
    forward_bars: int
    max_disk_bytes: int = _MATRIX_DISK_CACHE_DEFAULT_MAX_BYTES
    generation: str = "default"


@dataclass(frozen=True)
class ValidBarIndex:
    """Asset-major CSR index of effective market bars."""

    shape: tuple[int, int]
    offsets: np.ndarray
    rows: np.ndarray

    @property
    def nbytes(self) -> int:
        return int(self.offsets.nbytes + self.rows.nbytes)


def _build_valid_bar_index(valid_mask: np.ndarray) -> ValidBarIndex:
    valid = np.asarray(valid_mask, dtype=bool)
    if valid.ndim != 2:
        raise ValueError("valid bar index requires a 2D mask")
    counts = np.count_nonzero(valid, axis=0).astype(np.int64, copy=False)
    offsets = np.empty(valid.shape[1] + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(counts, out=offsets[1:])
    rows = np.empty(int(offsets[-1]), dtype=np.int32)
    for asset_id in range(valid.shape[1]):
        start = int(offsets[asset_id])
        stop = int(offsets[asset_id + 1])
        rows[start:stop] = np.flatnonzero(valid[:, asset_id]).astype(
            np.int32,
            copy=False,
        )
    offsets.flags.writeable = False
    rows.flags.writeable = False
    return ValidBarIndex(shape=valid.shape, offsets=offsets, rows=rows)


@dataclass(frozen=True)
class MarketDataMatrix:
    """Compact base market data shared by matrix-native strategies and matchers."""

    timestamps: np.ndarray
    timestamp_labels: tuple[str, ...]
    session_ids: np.ndarray
    symbols: tuple[str, ...]
    names: tuple[str, ...]

    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    tradable: np.ndarray
    limit_up_locked: np.ndarray
    limit_down_locked: np.ndarray
    fields: Mapping[str, np.ndarray]
    volume_unit: str = "shares"
    cache_status: str = "memory"

    cache_path: str | None = None
    cache_lease: Any | None = field(default=None, compare=False, repr=False)
    vector_fields: frozenset[str] = field(default_factory=frozenset)
    cache_timing_ms: Mapping[str, float] = field(default_factory=dict)
    _valid_bars: ValidBarIndex | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    @property
    def shape(self) -> tuple[int, int]:
        return self.open.shape

    @property
    def nbytes(self) -> int:
        arrays = [
            self.timestamps,
            self.session_ids,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.tradable,
            self.limit_up_locked,
            self.limit_down_locked,
            *self.fields.values(),
        ]
        index_bytes = self._valid_bars.nbytes if self._valid_bars is not None else 0
        return int(sum(array.nbytes for array in arrays) + index_bytes)

    @property
    def valid_bars(self) -> ValidBarIndex:
        index = self._valid_bars
        if index is None:
            index = _build_valid_bar_index(np.isfinite(self.close))
            object.__setattr__(self, "_valid_bars", index)
        return index

    def field(self, name: str) -> np.ndarray:
        if name == "open":
            return self.open
        if name == "high":
            return self.high
        if name == "low":
            return self.low
        if name == "close":
            return self.close
        if name == "volume":
            return self.volume
        try:
            return self.fields[name]
        except KeyError as exc:
            raise ValueError(f"MarketDataMatrix missing field: {name}") from exc


@dataclass(frozen=True)
class SignalMatrix:
    """Strategy output before execution delays are applied."""

    entry: np.ndarray
    exit: np.ndarray
    score: np.ndarray
    entry_signal_code: np.ndarray
    exit_signal_code: np.ndarray
    entry_signal_ids: tuple[str, ...] = ()
    exit_signal_ids: tuple[str, ...] = ()

    @property
    def shape(self) -> tuple[int, int]:
        return self.entry.shape

    @property
    def nbytes(self) -> int:
        return int(sum(
            value.nbytes
            for value in self.__dict__.values()
            if isinstance(value, np.ndarray)
        ))


@dataclass(frozen=True)
class MarketMatrix:
    """Execution matrix consumed by the Python matcher and future Numba kernel."""

    timestamps: np.ndarray
    timestamp_labels: tuple[str, ...]
    session_ids: np.ndarray
    symbols: tuple[str, ...]
    names: tuple[str, ...]

    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    score: np.ndarray
    entry: np.ndarray
    exit: np.ndarray
    tradable: np.ndarray
    limit_up_locked: np.ndarray
    limit_down_locked: np.ndarray
    entry_reference_price: np.ndarray
    exit_reference_price: np.ndarray
    # Traded price = adjusted price * raw_scale of the same bar; 1 outside Taiwan.
    raw_scale: np.ndarray

    entry_signal_time: np.ndarray
    exit_signal_time: np.ndarray
    entry_signal_code: np.ndarray
    exit_signal_code: np.ndarray
    entry_signal_ids: tuple[str, ...]
    exit_signal_ids: tuple[str, ...]
    # Minute-replay sell line armed by the prior session's state, NaN when no exit signal can fire.
    minute_exit_line: np.ndarray | None = None
    minute_exit_line_code: np.ndarray | None = None

    @property
    def shape(self) -> tuple[int, int]:
        return self.open.shape

    @property
    def nbytes(self) -> int:
        return int(sum(
            value.nbytes
            for value in self.__dict__.values()
            if isinstance(value, np.ndarray)
        ))


def build_market_data_matrix(
    panel: pl.DataFrame,
    *,
    field_columns: set[str] | frozenset[str] | None = None,
    live_quotes: bool = False,
) -> MarketDataMatrix:
    """Encode a long base panel into immutable ``time x asset`` arrays.

    live_quotes marks a panel of live quote rows, whose close is the traded price, so a requested
    raw_close missing from it is taken from close; any other panel without it raises.
    """
    if panel.is_empty():
        raise ValueError("cannot build MarketDataMatrix from an empty panel")

    timestamp_col, unique_timestamps, symbol_values, time_id, asset_id = _encode_axes(panel)
    shape = (len(unique_timestamps), len(symbol_values))

    def float_matrix(
        column: str,
        default: float = np.nan,
        null_fill: float | None = None,
    ) -> np.ndarray:
        return _float_matrix(panel, column, shape, time_id, asset_id, default, null_fill)

    open_ = float_matrix("open")
    high = float_matrix("high")
    low = float_matrix("low")
    close = float_matrix("close")
    volume = float_matrix("volume", 0.0 if "volume" in panel.columns else 1.0, 0.0)
    limit_up_locked = _bool_matrix(panel, "signal_limit_up", shape, time_id, asset_id)
    limit_down_locked = _bool_matrix(panel, "signal_limit_down", shape, time_id, asset_id)
    base_missing = _bool_matrix(panel, BASE_MISSING_COLUMN, shape, time_id, asset_id)
    limit_up_locked |= base_missing
    limit_down_locked |= base_missing
    tradable = _tradable_matrix(open_, high, low, close, volume)

    core_columns = {
        timestamp_col,
        "symbol",
        "name",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "signal_limit_up",
        "signal_limit_down",
        BASE_MISSING_COLUMN,
    }
    # Absolute price bounds and vwap_bias read raw_close, so it is kept whenever the panel has it.
    wanted_fields = set(field_columns or ()) - core_columns
    if "raw_close" in panel.columns:
        wanted_fields.add("raw_close")
    fields: dict[str, np.ndarray] = {}
    for column in sorted(wanted_fields):
        if column == "price_limit_pct":
            continue
        if column in panel.columns and (
            panel[column].dtype.is_numeric()
            or (column == AMOUNT_ESTIMATED and panel[column].dtype == pl.Boolean)
        ):
            fields[column] = float_matrix(column)
        elif column == "raw_close":
            if not live_quotes:
                raise ValueError("market data lacks raw_close, which the requested fields need")
            fields[column] = np.array(close, copy=True)

    names = [""] * len(symbol_values)
    if "name" in panel.columns:
        row_names = panel["name"].fill_null("").cast(pl.Utf8).to_numpy()
        for row, aid in enumerate(asset_id):
            if not names[int(aid)] and row_names[row]:
                names[int(aid)] = str(row_names[row])

    if "price_limit_pct" in wanted_fields:
        trading_dates = unique_timestamps.cast(pl.Date).to_list()
        fields["price_limit_pct"] = write_numpy_price_limit_matrix(
            np.empty(shape, dtype=np.float32),
            trading_dates,
            symbol_values,
            valid=np.isfinite(close),
        )

    timestamp_labels = tuple(str(value)[:19] for value in unique_timestamps.to_numpy())
    timestamps = _timestamp_int64(unique_timestamps)
    session_dates = unique_timestamps.cast(pl.Date).to_numpy()
    session_values = np.unique(session_dates)
    session_ids = np.searchsorted(session_values, session_dates).astype(np.int32)

    arrays = (
        timestamps,
        session_ids,
        open_,
        high,
        low,
        close,
        volume,
        tradable,
        limit_up_locked,
        limit_down_locked,
        *fields.values(),
    )
    _make_read_only(*arrays)

    sym_tuple = tuple(str(value) for value in symbol_values)

    return MarketDataMatrix(
        timestamps=timestamps,
        timestamp_labels=timestamp_labels,
        session_ids=session_ids,
        symbols=sym_tuple,
        names=tuple(names),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tradable=tradable,
        limit_up_locked=limit_up_locked,
        limit_down_locked=limit_down_locked,
        fields=MappingProxyType(fields),
    )



def load_market_data_matrix_from_parquet(
    parquet_root: Path,
    start: date,
    end: date,
    *,
    field_columns: set[str] | frozenset[str],
    symbols: list[str] | None = None,
    instruments: pl.DataFrame | None = None,
    batch_size: int = _ARROW_BATCH_SIZE,
    cache_root: Path | None = None,
    coverage_start: date | None = None,
    coverage_end: date | None = None,
    cache_field_columns: set[str] | frozenset[str] | None = None,
    cache_max_bytes: int = _MATRIX_DISK_CACHE_DEFAULT_MAX_BYTES,
    profile_generation: str = "default",
    source_generation: str | None = None,
    cancel_event: threading.Event | None = None,
) -> MarketDataMatrix:
    """Load a daily market matrix, reusing a covering read-only mmap when possible."""
    _raise_if_matrix_cancelled(cancel_event)
    if start > end:
        raise ValueError("matrix parquet range start must not exceed end")
    root = Path(parquet_root)
    if not root.exists():
        raise ValueError(f"matrix parquet root does not exist: {root}")
    available_start, available_end = _partition_date_bounds(root)
    if available_start is None or available_end is None:
        raise ValueError("本地指标数据为空，请先在数据页面同步日K并完成指标计算")
    effective_start = max(start, available_start)
    effective_end = min(end, available_end)
    if effective_start > effective_end:
        raise ValueError("matrix parquet range contains no market data")
    requested_fields = _normalize_matrix_cache_fields(field_columns)
    requested_coverage_start = coverage_start or start
    requested_coverage_end = coverage_end or end
    if requested_coverage_start > start or requested_coverage_end < end:
        raise ValueError("matrix cache coverage must include the requested range")
    build_start = max(requested_coverage_start, available_start)
    build_end = min(requested_coverage_end, available_end)
    build_fields = frozenset(
        requested_fields
        | _normalize_matrix_cache_fields(cache_field_columns or field_columns)
    )
    normalized_symbols = _normalize_symbol_request(symbols)
    instrument_fingerprint = _instrument_fingerprint(instruments).hex()

    partitioning = pads.partitioning(
        pa.schema([("date", pa.date32())]),
        flavor="hive",
    )
    dataset = pads.dataset(
        str(root),
        format="parquet",
        partitioning=partitioning,
    )
    _validate_matrix_dataset_schema(dataset)
    if "raw_close" in dataset.schema.names:
        requested_fields = frozenset(requested_fields | {"raw_close"})
        build_fields = frozenset(build_fields | {"raw_close"})
    reference_fields = {name for name in _REFERENCE_BASE_FIELDS if name in dataset.schema.names}
    requested_fields = frozenset(requested_fields | reference_fields)
    build_fields = frozenset(build_fields | reference_fields)

    if cache_root is None:
        return _build_market_data_matrix_from_dataset(
            dataset,
            root,
            effective_start,
            effective_end,
            requested_fields,
            normalized_symbols,
            instruments,
            batch_size=batch_size,
            cache_status="disabled",
            cancel_event=cancel_event,
        )

    cache_dir = Path(cache_root)
    cache_dir.mkdir(parents=True, exist_ok=True)
    requested_partitions = (
        {}
        if source_generation
        else _partition_fingerprints(
            root,
            effective_start,
            effective_end,
            include_predecessor=True,
        )
    )
    covering = _find_covering_matrix_cache(
        cache_dir,
        root,
        effective_start,
        effective_end,
        requested_fields,
        normalized_symbols,
        requested_partitions,
        instrument_fingerprint,
        source_generation,
    )
    if covering is not None:
        path, status = covering
        try:
            market = _load_market_data_matrix_cache(path, cache_status=status)
            return _slice_and_project_market_data_matrix(
                market,
                effective_start,
                effective_end,
                requested_fields,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("invalid matrix disk cache %s: %s", path, exc)
            shutil.rmtree(path, ignore_errors=True)

    build_partitions = _partition_fingerprints(root, build_start, build_end)
    if not build_partitions:
        raise ValueError("matrix parquet range contains no market data")
    cache_path = _matrix_disk_cache_path(
        cache_dir,
        root,
        build_start,
        build_end,
        build_fields,
        normalized_symbols,
        build_partitions,
        instrument_fingerprint,
        profile_generation,
        source_generation,
    )
    if (cache_path / "manifest.json").exists():
        market = _load_market_data_matrix_cache(cache_path, cache_status="exact")
        return _slice_and_project_market_data_matrix(
            market,
            effective_start,
            effective_end,
            requested_fields,
        )

    _build_market_data_matrix_cache_from_dataset(
        dataset,
        root,
        cache_path,
        build_start,
        build_end,
        build_fields,
        normalized_symbols,
        instruments,
        build_partitions,
        instrument_fingerprint,
        profile_generation,
        source_generation,
        batch_size=batch_size,
        axis_cache_root=cache_dir,
        cancel_event=cancel_event,
    )
    _raise_if_matrix_cancelled(cancel_event)
    _prune_matrix_disk_cache(
        cache_dir,
        keep=cache_path,
        max_bytes=int(cache_max_bytes),
        current_source_generation=source_generation,
    )
    market = _load_market_data_matrix_cache(cache_path, cache_status="built")
    return _slice_and_project_market_data_matrix(
        market,
        effective_start,
        effective_end,
        requested_fields,
    )


def _validate_matrix_dataset_schema(dataset: pads.Dataset) -> None:
    available = set(dataset.schema.names)
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - available
    if missing:
        raise ValueError(f"matrix parquet missing columns: {sorted(missing)}")


def _normalize_matrix_cache_fields(
    field_columns: set[str] | frozenset[str],
) -> frozenset[str]:
    ignored = {
        "symbol",
        "date",
        "name",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "signal_limit_up",
        "signal_limit_down",
    }
    return frozenset(str(name) for name in field_columns if str(name) not in ignored)


def _normalize_symbol_request(symbols: list[str] | None) -> tuple[str, ...] | None:
    if symbols is None:
        return None
    return tuple(sorted({str(symbol) for symbol in symbols}))


def _matrix_filter_expression(
    start: date,
    end: date,
    symbols: tuple[str, ...] | None,
):
    expression = (pads.field("date") >= pa.scalar(start)) & (
        pads.field("date") <= pa.scalar(end)
    )
    if symbols is not None:
        expression &= pads.field("symbol").isin(list(symbols))
    return expression


def _resolve_matrix_storage_fields(
    dataset: pads.Dataset,
    wanted_fields: frozenset[str],
    instruments: pl.DataFrame | None,
) -> tuple[list[str], list[str], list[str]]:
    available = set(dataset.schema.names)
    parquet_fields = sorted(
        name
        for name in wanted_fields
        if name != "price_limit_pct"
        and name in available
        and (
            _arrow_numeric(dataset.schema.field(name).type)
            or name == BASE_MISSING_COLUMN
        )
    )
    instrument_columns = set(instruments.columns) if instruments is not None else set()
    matrix_fields = set(parquet_fields)
    vector_fields = {
        name
        for name in ("total_shares", "float_shares")
        if name in wanted_fields
        and name in instrument_columns
        and name not in parquet_fields
    }
    if "raw_close" in wanted_fields:
        matrix_fields.add("raw_close")
    if "turnover_rate" in wanted_fields:
        matrix_fields.add("turnover_rate")
    if "price_limit_pct" in wanted_fields:
        matrix_fields.add("price_limit_pct")
    resolved = matrix_fields | vector_fields
    unresolved = wanted_fields - resolved
    if unresolved:
        raise ValueError(f"matrix parquet fields unavailable: {sorted(unresolved)}")
    return parquet_fields, sorted(matrix_fields), sorted(vector_fields)


def _build_market_data_matrix_from_dataset(
    dataset: pads.Dataset,
    root: Path,
    start: date,
    end: date,
    wanted_fields: frozenset[str],
    symbols: tuple[str, ...] | None,
    instruments: pl.DataFrame | None,
    *,
    batch_size: int,
    cache_status: str,
    cancel_event: threading.Event | None = None,
) -> MarketDataMatrix:
    _raise_if_matrix_cancelled(cancel_event)
    filter_expr = _matrix_filter_expression(start, end, symbols)
    actual_dates, actual_symbols = _collect_parquet_axes(
        dataset,
        filter_expr,
        batch_size=batch_size,
        cancel_event=cancel_event,
    )
    if not actual_dates or not actual_symbols:
        raise ValueError("matrix parquet range contains no market data")
    parquet_fields, matrix_fields, vector_fields = _resolve_matrix_storage_fields(
        dataset,
        wanted_fields,
        instruments,
    )
    published_assets = _published_limit_assets(dataset, actual_symbols)
    shape = (len(actual_dates), len(actual_symbols))
    arrays = {
        "open": np.full(shape, np.nan, dtype=np.float32),
        "high": np.full(shape, np.nan, dtype=np.float32),
        "low": np.full(shape, np.nan, dtype=np.float32),
        "close": np.full(shape, np.nan, dtype=np.float32),
        "volume": np.zeros(shape, dtype=np.float32),
        "limit_up_locked": np.zeros(shape, dtype=np.uint8),
        "limit_down_locked": np.zeros(shape, dtype=np.uint8),
    }
    fields = {
        name: np.full(shape, np.nan, dtype=np.float32)
        for name in matrix_fields
    }
    seen = np.zeros(shape, dtype=bool)
    _scan_matrix_values(
        dataset,
        filter_expr,
        actual_dates,
        actual_symbols,
        arrays,
        fields,
        parquet_fields,
        seen,
        batch_size=batch_size,
        cancel_event=cancel_event,
        published_limit_assets=published_assets,
    )
    _raise_if_matrix_cancelled(cancel_event)
    names = _populate_matrix_derived_arrays(
        actual_symbols,
        arrays,
        fields,
        wanted_fields,
        instruments,
        seen,
        parquet_fields=parquet_fields,
        vector_fields=vector_fields,
    )
    if "price_limit_pct" in fields:
        write_numpy_price_limit_matrix(
            fields["price_limit_pct"],
            actual_dates,
            actual_symbols,
            valid=seen,
        )
    for name in vector_fields:
        fields[name] = np.where(seen, fields[name], np.nan).astype(np.float32, copy=False)
    tradable = _tradable_matrix(
        arrays["open"],
        arrays["high"],
        arrays["low"],
        arrays["close"],
        arrays["volume"],
    )
    timestamps, session_ids = _matrix_time_axes(
        actual_dates,
        _partition_dates(root, start, end),
    )
    _make_read_only(
        timestamps,
        session_ids,
        *arrays.values(),
        tradable,
        *fields.values(),
    )
    return MarketDataMatrix(
        timestamps=timestamps,
        timestamp_labels=tuple(value.isoformat() for value in actual_dates),
        session_ids=session_ids,
        symbols=tuple(actual_symbols),
        names=tuple(names),
        open=arrays["open"],
        high=arrays["high"],
        low=arrays["low"],
        close=arrays["close"],
        volume=arrays["volume"],
        tradable=tradable,
        limit_up_locked=arrays["limit_up_locked"],
        limit_down_locked=arrays["limit_down_locked"],
        fields=MappingProxyType(fields),
        cache_status=cache_status,
    )


def _build_market_data_matrix_cache_from_dataset(
    dataset: pads.Dataset,
    root: Path,
    cache_path: Path,
    start: date,
    end: date,
    wanted_fields: frozenset[str],
    symbols: tuple[str, ...] | None,
    instruments: pl.DataFrame | None,
    source_partitions: Mapping[str, str],
    instrument_fingerprint: str,
    profile_generation: str,
    source_generation: str | None,
    *,
    batch_size: int,
    axis_cache_root: Path,
    cancel_event: threading.Event | None = None,
) -> None:
    _raise_if_matrix_cancelled(cancel_event)
    build_started = time.perf_counter()
    timing_ms: dict[str, float] = {}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.parent / f".{cache_path.name}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    mapped: list[np.memmap] = []
    try:
        filter_expr = _matrix_filter_expression(start, end, symbols)
        stage_started = time.perf_counter()
        actual_dates, actual_symbols = _load_or_build_matrix_axes(
            dataset,
            root,
            start,
            end,
            symbols,
            source_partitions,
            filter_expr,
            batch_size=batch_size,
            cache_root=axis_cache_root,
            cancel_event=cancel_event,
        )
        _raise_if_matrix_cancelled(cancel_event)
        if not actual_dates or not actual_symbols:
            raise ValueError("matrix parquet range contains no market data")
        timing_ms["axes"] = round((time.perf_counter() - stage_started) * 1000, 1)
        stage_started = time.perf_counter()
        parquet_fields, matrix_fields, vector_fields = _resolve_matrix_storage_fields(
            dataset,
            wanted_fields,
            instruments,
        )
        published_assets = _published_limit_assets(dataset, actual_symbols)
        shape = (len(actual_dates), len(actual_symbols))
        array_specs, field_specs, total_bytes = _matrix_binary_layout(
            shape,
            matrix_fields,
            vector_fields,
        )
        data_path = temporary / "matrix.bin"
        with data_path.open("wb") as stream:
            stream.truncate(total_bytes)

        arrays = {
            name: _open_matrix_memmap(data_path, spec, mapped)
            for name, spec in array_specs.items()
        }
        fields = {
            name: _open_matrix_memmap(data_path, spec, mapped)
            for name, spec in field_specs.items()
        }
        timestamps, session_ids = _matrix_time_axes(
            actual_dates,
            sorted(date.fromisoformat(value) for value in source_partitions),
        )
        arrays["timestamps"][:] = timestamps
        arrays["session_ids"][:] = session_ids
        timing_ms["layout"] = round((time.perf_counter() - stage_started) * 1000, 1)
        stage_started = time.perf_counter()
        seen = np.zeros(shape, dtype=bool)
        _scan_matrix_values(
            dataset,
            filter_expr,
            actual_dates,
            actual_symbols,
            arrays,
            fields,
            parquet_fields,
            seen,
            batch_size=batch_size,
            cancel_event=cancel_event,
            published_limit_assets=published_assets,
        )
        _raise_if_matrix_cancelled(cancel_event)
        if not seen.any():
            raise ValueError("matrix parquet range contains no requested market data")
        timing_ms["scan"] = round((time.perf_counter() - stage_started) * 1000, 1)
        stage_started = time.perf_counter()
        _mask_unseen_staging_core(arrays, fields, parquet_fields, seen)
        names = _populate_matrix_derived_arrays(
            actual_symbols,
            arrays,
            fields,
            wanted_fields,
            instruments,
            seen,
            parquet_fields=parquet_fields,
            vector_fields=vector_fields,
        )
        _mask_unseen_staging_fields(fields, seen)
        _raise_if_matrix_cancelled(cancel_event)
        if "price_limit_pct" in fields:
            write_numpy_price_limit_matrix(
                fields["price_limit_pct"],
                actual_dates,
                actual_symbols,
                valid=seen,
            )
        _write_tradable_matrix(
            arrays["tradable"],
            arrays["open"],
            arrays["high"],
            arrays["low"],
            arrays["close"],
            arrays["volume"],
        )
        timing_ms["derived"] = round((time.perf_counter() - stage_started) * 1000, 1)
        _raise_if_matrix_cancelled(cancel_event)
        stage_started = time.perf_counter()
        for values in mapped:
            values.flush()
        _close_matrix_memmaps(mapped)
        mapped.clear()
        timing_ms["flush_close"] = round((time.perf_counter() - stage_started) * 1000, 1)
        timing_ms["total_before_publish"] = round(
            (time.perf_counter() - build_started) * 1000,
            1,
        )

        manifest = {
            "version": _DIRECT_MATRIX_LOADER_VERSION,
            "storage": "matrix.bin",
            "parquet_root": str(root.resolve()),
            "coverage_start": start.isoformat(),
            "coverage_end": end.isoformat(),
            "cache_field_columns": sorted(wanted_fields),
            "symbols_request": None if symbols is None else list(symbols),
            "source_partitions": dict(source_partitions),
            "instrument_fingerprint": instrument_fingerprint,
            "profile_generation": str(profile_generation),
            "source_generation": source_generation,
            "build_timing_ms": timing_ms,
            "timestamp_labels": [value.isoformat() for value in actual_dates],
            "symbols": list(actual_symbols),
            "names": list(names),
            "volume_unit": "shares",
            "arrays": array_specs,
            "fields": field_specs,
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        _raise_if_matrix_cancelled(cancel_event)
        try:
            os.replace(temporary, cache_path)
        except OSError:
            if (cache_path / "manifest.json").exists():
                shutil.rmtree(temporary, ignore_errors=True)
            else:
                raise
    except BaseException:
        _close_matrix_memmaps(mapped)
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _matrix_binary_layout(
    shape: tuple[int, int],
    matrix_fields: list[str],
    vector_fields: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
    time_count = shape[0]
    arrays = [
        ("timestamps", np.dtype(np.int64), (time_count,)),
        ("session_ids", np.dtype(np.int32), (time_count,)),
        ("open", np.dtype(np.float32), shape),
        ("high", np.dtype(np.float32), shape),
        ("low", np.dtype(np.float32), shape),
        ("close", np.dtype(np.float32), shape),
        ("volume", np.dtype(np.float32), shape),
        ("tradable", np.dtype(np.uint8), shape),
        ("limit_up_locked", np.dtype(np.uint8), shape),
        ("limit_down_locked", np.dtype(np.uint8), shape),
    ]
    offset = 0

    def add_spec(dtype: np.dtype, value_shape: tuple[int, ...]) -> dict[str, Any]:
        nonlocal offset
        offset += (-offset) % 64
        spec = {
            "offset": offset,
            "dtype": dtype.str,
            "shape": list(value_shape),
        }
        offset += int(np.prod(value_shape, dtype=np.int64)) * dtype.itemsize
        return spec

    array_specs = {name: add_spec(dtype, value_shape) for name, dtype, value_shape in arrays}
    field_specs = {
        name: add_spec(np.dtype(np.float32), shape)
        for name in matrix_fields
    }
    field_specs.update({
        name: add_spec(np.dtype(np.float32), (shape[1],))
        for name in vector_fields
    })
    return array_specs, field_specs, offset


def _open_matrix_memmap(
    path: Path,
    spec: Mapping[str, Any],
    mapped: list[np.memmap],
) -> np.memmap:
    values = np.memmap(
        path,
        dtype=np.dtype(str(spec["dtype"])),
        mode="r+",
        offset=int(spec["offset"]),
        shape=tuple(int(value) for value in spec["shape"]),
        order="C",
    )
    mapped.append(values)
    return values


def _mask_unseen_staging_core(
    arrays: Mapping[str, np.ndarray],
    fields: Mapping[str, np.ndarray],
    parquet_fields: list[str],
    seen: np.ndarray,
) -> None:
    rows_per_chunk = max(1, (32 * 1024 * 1024) // max(1, seen.shape[1]))
    targets = [
        arrays["open"],
        arrays["high"],
        arrays["low"],
        arrays["close"],
        *(fields[name] for name in parquet_fields),
    ]
    for start in range(0, seen.shape[0], rows_per_chunk):
        stop = min(seen.shape[0], start + rows_per_chunk)
        missing = ~seen[start:stop]
        for target in targets:
            target[start:stop][missing] = np.nan


def _mask_unseen_staging_fields(
    fields: Mapping[str, np.ndarray],
    seen: np.ndarray,
) -> None:
    rows_per_chunk = max(1, (32 * 1024 * 1024) // max(1, seen.shape[1]))
    for start in range(0, seen.shape[0], rows_per_chunk):
        stop = min(seen.shape[0], start + rows_per_chunk)
        missing = ~seen[start:stop]
        for target in fields.values():
            if target.ndim == 2:
                target[start:stop][missing] = np.nan


def _close_matrix_memmaps(mapped: list[np.memmap]) -> None:
    for values in reversed(mapped):
        try:
            values.flush()
        except (OSError, ValueError):
            pass
        mmap_obj = getattr(values, "_mmap", None)
        if mmap_obj is not None:
            try:
                mmap_obj.close()
            except (OSError, ValueError):
                pass


def _scan_matrix_values(
    dataset: pads.Dataset,
    filter_expr,
    actual_dates: list[date],
    actual_symbols: list[str],
    arrays: Mapping[str, np.ndarray],
    fields: Mapping[str, np.ndarray],
    parquet_fields: list[str],
    seen: np.ndarray,
    *,
    batch_size: int,
    cancel_event: threading.Event | None = None,
    published_limit_assets: np.ndarray | None = None,
) -> None:
    date_to_id = {value: index for index, value in enumerate(actual_dates)}
    symbol_to_id = {value: index for index, value in enumerate(actual_symbols)}
    read_published_limits = (
        published_limit_assets is not None and bool(published_limit_assets.any())
    )
    if read_published_limits and BASE_MISSING_COLUMN not in dataset.schema.names:
        # The dataset takes its schema from one partition, which may predate the column.
        dataset = dataset.replace_schema(
            dataset.schema.append(pa.field(BASE_MISSING_COLUMN, pa.bool_()))
        )
    scan_columns = list(dict.fromkeys([
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        *parquet_fields,
        *(
            [*(column for column, _target in _PUBLISHED_LIMIT_SIGNALS), BASE_MISSING_COLUMN]
            if read_published_limits
            else []
        ),
    ]))
    scanner = dataset.scanner(
        columns=scan_columns,
        filter=filter_expr,
        batch_size=int(batch_size),
        use_threads=True,
    )
    flat_seen = seen.ravel()
    asset_count = len(actual_symbols)
    scan_targets = {
        "open": arrays["open"],
        "high": arrays["high"],
        "low": arrays["low"],
        "close": arrays["close"],
        "volume": arrays["volume"],
        **{name: fields[name] for name in parquet_fields},
    }
    for batch in scanner.to_batches():
        _raise_if_matrix_cancelled(cancel_event)
        time_ids = _arrow_axis_ids(_batch_column(batch, "date"), date_to_id)
        asset_ids = _arrow_axis_ids(_batch_column(batch, "symbol"), symbol_to_id)
        flat_ids = time_ids.astype(np.int64) * asset_count + asset_ids
        if np.unique(flat_ids).size != flat_ids.size or flat_seen[flat_ids].any():
            raise ValueError("MarketDataMatrix requires unique date/symbol rows")
        flat_seen[flat_ids] = True
        for name, target in scan_targets.items():
            values = _arrow_float_values(
                _batch_column(batch, name),
                null_fill=0.0 if name == "volume" else np.nan,
            )
            target[time_ids, asset_ids] = values
        if read_published_limits:
            published = published_limit_assets[asset_ids]
            base_missing = _arrow_true_mask(_batch_column(batch, BASE_MISSING_COLUMN))
            for column, target_name in _PUBLISHED_LIMIT_SIGNALS:
                flags = _published_limit_flags(
                    _batch_column(batch, column), published, column, base_missing
                )
                arrays[target_name][time_ids[published], asset_ids[published]] = flags


def _taiwan_asset_mask(symbols: Sequence[str]) -> np.ndarray:
    return np.fromiter(
        (str(symbol).endswith(TAIWAN_SUFFIXES) for symbol in symbols),
        dtype=bool,
        count=len(symbols),
    )


def _published_limit_assets(dataset: pads.Dataset, symbols: Sequence[str]) -> np.ndarray:
    """Mask of assets whose locks are the limit signals enriched published.

    Taiwan limits depend on the tick ladder, per-instrument rates and official
    corporate-action reference prices, which only the enriched publisher applies.
    """
    published = _taiwan_asset_mask(symbols)
    if published.any():
        missing = [
            column
            for column, _target in _PUBLISHED_LIMIT_SIGNALS
            if column not in dataset.schema.names
        ]
        if missing:
            raise ValueError(
                f"Taiwan matrix parquet missing published limit signals {missing}; "
                "republish the enriched data"
            )
    return published


def _arrow_true_mask(values: pa.Array) -> np.ndarray:
    flags = pc.fill_null(pc.cast(values, pa.bool_()), False)
    return np.asarray(flags.to_numpy(zero_copy_only=False), dtype=bool)


def _published_limit_flags(
    values: pa.Array, published: np.ndarray, column: str, base_missing: np.ndarray,
) -> np.ndarray:
    """Locks of the *published* rows; a row whose limit base is unknown is locked both ways."""
    if values.null_count:
        nulls = np.asarray(values.is_null().to_numpy(zero_copy_only=False), dtype=bool)
        if (nulls & published & ~base_missing).any():
            raise ValueError(
                f"Taiwan matrix parquet has rows without {column}; republish the enriched data"
            )
    return (_arrow_true_mask(values) | base_missing)[published].astype(np.uint8)


def _populate_matrix_derived_arrays(
    actual_symbols: list[str],
    arrays: Mapping[str, np.ndarray],
    fields: dict[str, np.ndarray],
    wanted_fields: frozenset[str],
    instruments: pl.DataFrame | None,
    seen: np.ndarray,
    *,
    parquet_fields: list[str],
    vector_fields: list[str],
) -> list[str]:
    names, instrument_fields = _instrument_axis_values(
        actual_symbols,
        set(wanted_fields),
        instruments,
        time_count=seen.shape[0],
    )
    for name, values in instrument_fields.items():
        if name in parquet_fields:
            continue
        if name not in fields:
            fields[name] = values
            continue
        if name in vector_fields:
            fields[name][:] = values[0]
        else:
            np.copyto(fields[name], values, where=seen)
    if "raw_close" in wanted_fields and "raw_close" not in parquet_fields:
        raise ValueError("the matrix requires the published raw_close column")
    # Only the publish prices turnover; a current share count must not reach past bars.
    if "turnover_rate" in wanted_fields and "turnover_rate" not in parquet_fields:
        fields["turnover_rate"][:] = np.nan
    return names


def _write_tradable_matrix(
    target: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> None:
    """_tradable_matrix written in row chunks, so a memmap build keeps bounded memory."""
    rows_per_chunk = max(1, (32 * 1024 * 1024) // max(1, open_.shape[1] * 4))
    for start in range(0, open_.shape[0], rows_per_chunk):
        stop = min(open_.shape[0], start + rows_per_chunk)
        target[start:stop] = _tradable_matrix(
            open_[start:stop], high[start:stop], low[start:stop], close[start:stop], volume[start:stop],
        )


def _matrix_time_axes(
    actual_dates: list[date],
    session_dates: list[date],
) -> tuple[np.ndarray, np.ndarray]:
    """Session ids index the market's partition dates, so a few-symbol load keeps its gaps."""
    epoch = date(1970, 1, 1)
    timestamps = np.asarray(
        [(value - epoch).days * 86_400_000 for value in actual_dates],
        dtype=np.int64,
    )
    sessions = np.asarray([value.toordinal() for value in session_dates], dtype=np.int64)
    days = np.asarray([value.toordinal() for value in actual_dates], dtype=np.int64)
    return timestamps, np.searchsorted(sessions, days).astype(np.int32)


def _partition_dates(root: Path, start: date, end: date) -> list[date]:
    dates: list[date] = []
    for partition in root.glob("date=*"):
        try:
            partition_date = date.fromisoformat(partition.name.removeprefix("date="))
        except ValueError:
            continue
        if start <= partition_date <= end and next(partition.rglob("*.parquet"), None) is not None:
            dates.append(partition_date)
    return sorted(dates)


def _matrix_disk_cache_path(
    cache_root: Path,
    parquet_root: Path,
    start: date,
    end: date,
    field_columns: frozenset[str],
    symbols: tuple[str, ...] | None,
    source_partitions: Mapping[str, str],
    instrument_fingerprint: str,
    profile_generation: str,
    source_generation: str | None,
) -> Path:
    payload = {
        "version": _DIRECT_MATRIX_LOADER_VERSION,
        "parquet_root": str(parquet_root.resolve()),
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
        "fields": sorted(field_columns),
        "symbols": symbols,
        "source_partitions": dict(source_partitions),
        "instrument_fingerprint": instrument_fingerprint,
        "profile_generation": str(profile_generation),
        "source_generation": source_generation,
    }
    digest = hashlib.blake2b(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        digest_size=20,
    )
    return cache_root / f"v{_DIRECT_MATRIX_LOADER_VERSION}-{digest.hexdigest()}"


def _partition_fingerprints(
    root: Path,
    start: date,
    end: date,
    *,
    include_predecessor: bool = False,
) -> dict[str, str]:
    selected: list[tuple[date, Path]] = []
    predecessor: tuple[date, Path] | None = None
    for partition in root.glob("date=*"):
        try:
            partition_date = date.fromisoformat(partition.name.removeprefix("date="))
        except ValueError:
            continue
        if partition_date < start:
            if predecessor is None or partition_date > predecessor[0]:
                predecessor = (partition_date, partition)
            continue
        if partition_date <= end:
            selected.append((partition_date, partition))
    if include_predecessor and predecessor is not None:
        selected.append(predecessor)
    result: dict[str, str] = {}
    for partition_date, partition in sorted(selected):
        digest = hashlib.blake2b(digest_size=20)
        files = sorted(partition.rglob("*.parquet"))
        if not files:
            continue
        for path in files:
            stat = path.stat()
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(int(stat.st_size).to_bytes(8, "little", signed=False))
            digest.update(int(stat.st_mtime_ns).to_bytes(8, "little", signed=False))
        result[partition_date.isoformat()] = digest.hexdigest()
    return result


def _partition_date_bounds(root: Path) -> tuple[date | None, date | None]:
    earliest: date | None = None
    latest: date | None = None
    for partition in root.glob("date=*"):
        try:
            value = date.fromisoformat(partition.name.removeprefix("date="))
        except ValueError:
            continue
        if earliest is None or value < earliest:
            earliest = value
        if latest is None or value > latest:
            latest = value
    return earliest, latest


def _instrument_fingerprint(instruments: pl.DataFrame | None) -> bytes:
    if instruments is None or instruments.is_empty() or "symbol" not in instruments.columns:
        return b"no-instruments"
    columns = [
        name
        for name in ("symbol", "name", "total_shares", "float_shares", "limit_up", "limit_down")
        if name in instruments.columns
    ]
    payload = instruments.select(columns).sort("symbol").to_dicts()
    return hashlib.blake2b(
        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        digest_size=20,
    ).digest()


def _find_covering_matrix_cache(
    root: Path,
    parquet_root: Path,
    start: date,
    end: date,
    requested_fields: frozenset[str],
    symbols: tuple[str, ...] | None,
    source_partitions: Mapping[str, str],
    instrument_fingerprint: str,
    source_generation: str | None,
) -> tuple[Path, str] | None:
    matches: list[tuple[int, int, Path, str]] = []
    for path in root.glob(f"v{_DIRECT_MATRIX_LOADER_VERSION}-*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if int(manifest.get("version", -1)) != _DIRECT_MATRIX_LOADER_VERSION:
                continue
            if manifest.get("parquet_root") != str(parquet_root.resolve()):
                continue
            if manifest.get("symbols_request") != (None if symbols is None else list(symbols)):
                continue
            if manifest.get("instrument_fingerprint") != instrument_fingerprint:
                continue
            if source_generation is not None:
                if manifest.get("source_generation") != source_generation:
                    continue
            cached_start = date.fromisoformat(str(manifest["coverage_start"]))
            cached_end = date.fromisoformat(str(manifest["coverage_end"]))
            if cached_start > start or cached_end < end:
                continue
            cached_fields = frozenset(str(name) for name in manifest["cache_field_columns"])
            if not requested_fields.issubset(cached_fields):
                continue
            cached_partitions = manifest.get("source_partitions", {})
            if source_generation is None:
                relevant_partitions = {
                    key: value
                    for key, value in source_partitions.items()
                    if date.fromisoformat(key) >= cached_start
                }
                if any(
                    cached_partitions.get(key) != value
                    for key, value in relevant_partitions.items()
                ):
                    continue
            storage = path / str(manifest.get("storage", "matrix.bin"))
            size = storage.stat().st_size
            exact = (
                cached_start == start
                and cached_end == end
                and cached_fields == requested_fields
            )
            matches.append((size, -path.stat().st_mtime_ns, path, "exact" if exact else "covering"))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    if not matches:
        return None
    _size, _mtime, path, status = min(matches)
    return path, status


class _MatrixDiskCacheLease:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        with _MATRIX_DISK_CACHE_LOCK:
            _MATRIX_DISK_CACHE_LEASES[self.path] = (
                _MATRIX_DISK_CACHE_LEASES.get(self.path, 0) + 1
            )

    def __del__(self) -> None:
        path = self.path
        should_delete = False
        with _MATRIX_DISK_CACHE_LOCK:
            remaining = _MATRIX_DISK_CACHE_LEASES.get(path, 0) - 1
            if remaining > 0:
                _MATRIX_DISK_CACHE_LEASES[path] = remaining
            else:
                _MATRIX_DISK_CACHE_LEASES.pop(path, None)
                should_delete = path in _MATRIX_DISK_CACHE_PENDING_DELETE
        if should_delete:
            _try_delete_matrix_cache_path(Path(path))


def _try_delete_matrix_cache_path(path: Path) -> bool:
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.debug("matrix disk cache prune skipped %s: %s", path, exc)
        with _MATRIX_DISK_CACHE_LOCK:
            _MATRIX_DISK_CACHE_PENDING_DELETE.add(str(path))
        return False
    with _MATRIX_DISK_CACHE_LOCK:
        _MATRIX_DISK_CACHE_PENDING_DELETE.discard(str(path))
    return True


def _load_market_data_matrix_cache(
    path: Path,
    *,
    cache_status: str,
) -> MarketDataMatrix:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if int(manifest["version"]) != _DIRECT_MATRIX_LOADER_VERSION:
        raise ValueError("matrix disk cache version mismatch")
    storage_path = path / str(manifest["storage"])

    def load_array(spec: Mapping[str, Any]) -> np.ndarray:
        values = np.memmap(
            storage_path,
            dtype=np.dtype(str(spec["dtype"])),
            mode="r",
            offset=int(spec["offset"]),
            shape=tuple(int(value) for value in spec["shape"]),
            order="C",
        )
        values.flags.writeable = False
        return values

    arrays = {name: load_array(spec) for name, spec in manifest["arrays"].items()}
    stored_fields = {name: load_array(spec) for name, spec in manifest["fields"].items()}
    shape = arrays["open"].shape
    if any(
        values.shape != shape
        for name, values in arrays.items()
        if name not in {"timestamps", "session_ids"}
    ):
        raise ValueError("matrix disk cache contains inconsistent array shapes")
    if any(
        values.shape not in {shape, (shape[1],)}
        for values in stored_fields.values()
    ):
        raise ValueError("matrix disk cache contains inconsistent field shapes")
    vector_field_names = frozenset(
        name
        for name, values in stored_fields.items()
        if values.shape == (shape[1],)
    )
    fields = {
        name: (
            values
            if values.shape == shape
            else np.broadcast_to(values.reshape(1, -1), shape)
        )
        for name, values in stored_fields.items()
    }
    _make_read_only(*fields.values())
    os.utime(path, None)
    return MarketDataMatrix(
        timestamps=arrays["timestamps"],
        timestamp_labels=tuple(str(value) for value in manifest["timestamp_labels"]),
        session_ids=arrays["session_ids"],
        symbols=tuple(str(value) for value in manifest["symbols"]),
        names=tuple(str(value) for value in manifest["names"]),
        open=arrays["open"],
        high=arrays["high"],
        low=arrays["low"],
        close=arrays["close"],
        volume=arrays["volume"],
        tradable=arrays["tradable"],
        limit_up_locked=arrays["limit_up_locked"],
        limit_down_locked=arrays["limit_down_locked"],
        fields=MappingProxyType(fields),
        volume_unit=str(manifest["volume_unit"]),
        cache_status=cache_status,
        cache_path=str(path),
        cache_lease=_MatrixDiskCacheLease(path),
        vector_fields=vector_field_names,
        cache_timing_ms=MappingProxyType({
            str(name): float(value)
            for name, value in manifest.get("build_timing_ms", {}).items()
        }),
    )


def _slice_and_project_market_data_matrix(
    market: MarketDataMatrix,
    start: date,
    end: date,
    requested_fields: frozenset[str],
) -> MarketDataMatrix:
    labels = market.timestamp_labels
    start_label = start.isoformat()
    end_label = end.isoformat()
    start_id = 0
    while start_id < len(labels) and labels[start_id] < start_label:
        start_id += 1
    stop_id = start_id
    while stop_id < len(labels) and labels[stop_id] <= end_label:
        stop_id += 1
    if start_id >= stop_id:
        raise ValueError("matrix parquet range contains no market data")
    sliced = slice_market_data_matrix(market, start_id, stop_id)
    missing = requested_fields - set(sliced.fields)
    if missing:
        raise ValueError(f"matrix disk cache missing requested fields: {sorted(missing)}")
    projected_values: dict[str, np.ndarray] = {}
    for name in sorted(requested_fields):
        values = sliced.fields[name]
        if name in sliced.vector_fields:
            values = np.where(np.isfinite(sliced.close), values, np.nan).astype(
                np.float32,
                copy=False,
            )
            values.flags.writeable = False
        projected_values[name] = values
    projected = MappingProxyType(projected_values)
    return MarketDataMatrix(
        timestamps=sliced.timestamps,
        timestamp_labels=sliced.timestamp_labels,
        session_ids=sliced.session_ids,
        symbols=sliced.symbols,
        names=sliced.names,
        open=sliced.open,
        high=sliced.high,
        low=sliced.low,
        close=sliced.close,
        volume=sliced.volume,
        tradable=sliced.tradable,
        limit_up_locked=sliced.limit_up_locked,
        limit_down_locked=sliced.limit_down_locked,
        fields=projected,
        volume_unit=sliced.volume_unit,
        cache_status=sliced.cache_status,
        cache_path=sliced.cache_path,
        cache_lease=sliced.cache_lease,
        vector_fields=frozenset(),
        cache_timing_ms=sliced.cache_timing_ms,
    )


def _prune_matrix_disk_cache(
    root: Path,
    *,
    keep: Path,
    max_bytes: int,
    current_source_generation: str | None = None,
) -> None:
    if max_bytes <= 0:
        raise ValueError("matrix disk cache max_bytes must be positive")
    with _MATRIX_DISK_CACHE_LOCK:
        pending = [Path(value) for value in _MATRIX_DISK_CACHE_PENDING_DELETE]
    for path in pending:
        with _MATRIX_DISK_CACHE_LOCK:
            leased = _MATRIX_DISK_CACHE_LEASES.get(str(path), 0) > 0
        if not leased:
            _try_delete_matrix_cache_path(path)

    _prune_matrix_axis_indexes(root)
    entries: list[tuple[Path, int, int]] = []
    for path in root.glob("v*-*"):
        if not path.is_dir():
            continue
        if path != keep and not path.name.startswith(f"v{_DIRECT_MATRIX_LOADER_VERSION}-"):
            with _MATRIX_DISK_CACHE_LOCK:
                leased = _MATRIX_DISK_CACHE_LEASES.get(str(path), 0) > 0
                if leased:
                    _MATRIX_DISK_CACHE_PENDING_DELETE.add(str(path))
            if not leased and _try_delete_matrix_cache_path(path):
                continue
        try:
            size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
            entries.append((path, size, path.stat().st_mtime_ns))
        except OSError:
            continue
    if current_source_generation is not None:
        try:
            keep_manifest = json.loads(
                (keep / "manifest.json").read_text(encoding="utf-8")
            )
            keep_parquet_root = keep_manifest.get("parquet_root")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            keep_parquet_root = None
        for path, _size, _mtime in list(entries):
            if path == keep:
                continue
            try:
                manifest = json.loads(
                    (path / "manifest.json").read_text(encoding="utf-8")
                )
                same_universe = (
                    keep_parquet_root is not None
                    and manifest.get("parquet_root") == keep_parquet_root
                    and manifest.get("symbols_request") is None
                )
                old_generation = manifest.get("source_generation") != current_source_generation
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                same_universe = False
                old_generation = False
            if not (same_universe and old_generation):
                continue
            with _MATRIX_DISK_CACHE_LOCK:
                leased = _MATRIX_DISK_CACHE_LEASES.get(str(path), 0) > 0
                if leased:
                    _MATRIX_DISK_CACHE_PENDING_DELETE.add(str(path))
            if not leased:
                _try_delete_matrix_cache_path(path)
        entries = [entry for entry in entries if entry[0] == keep or entry[0].exists()]
    total = sum(size for _path, size, _mtime in entries)
    for path, size, _mtime in sorted(entries, key=lambda item: item[2]):
        if total <= max_bytes:
            break
        if path == keep:
            continue
        with _MATRIX_DISK_CACHE_LOCK:
            leased = _MATRIX_DISK_CACHE_LEASES.get(str(path), 0) > 0
            if leased:
                _MATRIX_DISK_CACHE_PENDING_DELETE.add(str(path))
        if leased:
            continue
        if _try_delete_matrix_cache_path(path):
            total -= size


def _prune_matrix_axis_indexes(root: Path) -> None:
    """Delete axis index files of other versions and all but the newest current ones."""
    current: list[tuple[int, Path]] = []
    for path in root.glob(".axes-*.json"):
        try:
            if path.name.startswith(f".axes-v{_MATRIX_AXIS_INDEX_VERSION}-"):
                current.append((path.stat().st_mtime_ns, path))
            else:
                path.unlink()
        except OSError as exc:
            logger.debug("matrix axis index prune skipped %s: %s", path, exc)
    for _mtime, path in sorted(current, reverse=True)[_MATRIX_AXIS_INDEX_KEEP:]:
        try:
            path.unlink()
        except OSError as exc:
            logger.debug("matrix axis index prune skipped %s: %s", path, exc)


def _matrix_axis_cache_path(
    cache_root: Path,
    parquet_root: Path,
    start: date,
    end: date,
    symbols: tuple[str, ...] | None,
) -> Path:
    payload = json.dumps(
        {
            "root": str(parquet_root.resolve()),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "symbols": symbols,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
    return cache_root / f".axes-v{_MATRIX_AXIS_INDEX_VERSION}-{digest}.json"


def _load_or_build_matrix_axes(
    dataset: pads.Dataset,
    parquet_root: Path,
    start: date,
    end: date,
    symbols: tuple[str, ...] | None,
    source_partitions: Mapping[str, str],
    filter_expr,
    *,
    batch_size: int,
    cache_root: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[list[date], list[str]]:
    _raise_if_matrix_cancelled(cancel_event)
    path = _matrix_axis_cache_path(cache_root, parquet_root, start, end, symbols)
    previous: dict[str, Any] | None = None
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if (
                int(previous.get("version", -1)) == _MATRIX_AXIS_INDEX_VERSION
                and previous.get("source_partitions") == dict(source_partitions)
            ):
                os.utime(path, None)
                return (
                    [date.fromisoformat(value) for value in previous["dates"]],
                    [str(value) for value in previous["symbols"]],
                )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            previous = None

    if previous is not None:
        previous_partitions = previous.get("source_partitions", {})
        changed_labels = {
            value
            for value, fingerprint in source_partitions.items()
            if previous_partitions.get(value) != fingerprint
        }
        removed_labels = set(previous_partitions) - set(source_partitions)
        rewritten_labels = {
            value for value in changed_labels if value in previous_partitions
        }
        if removed_labels or rewritten_labels:
            actual_dates, actual_symbols = _collect_parquet_axes(
                dataset,
                filter_expr,
                batch_size=batch_size,
                cancel_event=cancel_event,
            )
            changed_labels = set()
            retained_dates = {value.isoformat() for value in actual_dates}
        else:
            retained_dates = {
                str(value)
                for value in previous.get("dates", [])
                if str(value) in source_partitions and str(value) not in changed_labels
            }
            actual_symbols = sorted({str(value) for value in previous.get("symbols", [])})
        if changed_labels:
            changed_dates = [date.fromisoformat(value) for value in sorted(changed_labels)]
            changed_filter = _matrix_filter_expression(
                min(changed_dates),
                max(changed_dates),
                symbols,
            ) & pads.field("date").isin(changed_dates)
            scanner = dataset.scanner(
                columns=["date", "symbol"],
                filter=changed_filter,
                batch_size=int(batch_size),
                use_threads=True,
            )
            symbols_set = set(actual_symbols)
            for batch in scanner.to_batches():
                _raise_if_matrix_cancelled(cancel_event)
                retained_dates.update(
                    value.isoformat()
                    for value in pc.unique(_batch_column(batch, "date")).to_pylist()
                )
                symbols_set.update(
                    str(value)
                    for value in pc.unique(_batch_column(batch, "symbol")).to_pylist()
                )
            actual_symbols = sorted(symbols_set)
        actual_dates = [date.fromisoformat(value) for value in sorted(retained_dates)]
    else:
        actual_dates, actual_symbols = _collect_parquet_axes(
            dataset,
            filter_expr,
            batch_size=batch_size,
            cancel_event=cancel_event,
        )

    _raise_if_matrix_cancelled(cancel_event)
    payload = {
        "version": _MATRIX_AXIS_INDEX_VERSION,
        "source_partitions": dict(source_partitions),
        "dates": [value.isoformat() for value in actual_dates],
        "symbols": list(actual_symbols),
    }
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return actual_dates, actual_symbols


def _collect_parquet_axes(
    dataset: pads.Dataset,
    filter_expr,
    *,
    batch_size: int,
    cancel_event: threading.Event | None = None,
) -> tuple[list[date], list[str]]:
    dates: set[date] = set()
    symbols: set[str] = set()
    scanner = dataset.scanner(
        columns=["date", "symbol"],
        filter=filter_expr,
        batch_size=int(batch_size),
        use_threads=True,
    )
    for batch in scanner.to_batches():
        _raise_if_matrix_cancelled(cancel_event)
        dates.update(pc.unique(_batch_column(batch, "date")).to_pylist())
        symbols.update(
            str(value)
            for value in pc.unique(_batch_column(batch, "symbol")).to_pylist()
        )
    return sorted(dates), sorted(symbols)


def _arrow_axis_ids(values: pa.Array, mapping: Mapping[Any, int]) -> np.ndarray:
    encoded = pc.dictionary_encode(values)
    dictionary_ids = np.asarray(
        [mapping[value] for value in encoded.dictionary.to_pylist()],
        dtype=np.int32,
    )
    indices = encoded.indices.to_numpy(zero_copy_only=False)
    return dictionary_ids[np.asarray(indices, dtype=np.int32)]


def _batch_column(batch: pa.RecordBatch, name: str) -> pa.Array:
    return batch.column(batch.schema.get_field_index(name))


def _arrow_float_values(values: pa.Array, *, null_fill: float) -> np.ndarray:
    casted = pc.cast(values, pa.float32(), safe=False)
    if casted.null_count:
        casted = pc.fill_null(casted, pa.scalar(null_fill, type=pa.float32()))
    result = np.array(
        casted.to_numpy(zero_copy_only=False),
        dtype=np.float32,
        copy=True,
    )
    replacement = np.float32(null_fill)
    result[~np.isfinite(result)] = replacement
    return result


def _arrow_numeric(value_type: pa.DataType) -> bool:
    return bool(
        pa.types.is_integer(value_type)
        or pa.types.is_floating(value_type)
        or pa.types.is_decimal(value_type)
    )


def _instrument_axis_values(
    symbols: list[str],
    wanted_fields: set[str],
    instruments: pl.DataFrame | None,
    *,
    time_count: int,
) -> tuple[list[str], dict[str, np.ndarray]]:
    names = [""] * len(symbols)
    fields: dict[str, np.ndarray] = {}
    if instruments is None or instruments.is_empty() or "symbol" not in instruments.columns:
        return names, fields

    by_symbol = {
        str(row["symbol"]): row
        for row in instruments.unique(subset=["symbol"]).iter_rows(named=True)
    }
    numeric_fields = [
        name
        for name in sorted(wanted_fields)
        if name in instruments.columns and instruments[name].dtype.is_numeric()
    ]
    vectors = {
        name: np.full(len(symbols), np.nan, dtype=np.float32)
        for name in numeric_fields
    }
    for asset_id, symbol in enumerate(symbols):
        row = by_symbol.get(symbol)
        if row is None:
            continue
        names[asset_id] = str(row.get("name") or "")
        for name, target in vectors.items():
            value = row.get(name)
            if value is not None:
                target[asset_id] = np.float32(value)

    shape = (1, len(symbols))
    fields.update({
        name: np.broadcast_to(values.reshape(shape), (time_count, len(symbols)))
        for name, values in vectors.items()
    })
    return names, fields


def make_signal_matrix(
    shape: tuple[int, int],
    *,
    entry: np.ndarray | None = None,
    exit: np.ndarray | None = None,
    score: np.ndarray | None = None,
    entry_signal_code: np.ndarray | None = None,
    exit_signal_code: np.ndarray | None = None,
    entry_signal_ids: tuple[str, ...] = (),
    exit_signal_ids: tuple[str, ...] = (),
) -> SignalMatrix:
    """Create a compact read-only signal matrix with canonical dtypes."""
    entry_array = _coerce_array(entry, shape, np.uint8, 0)
    exit_array = _coerce_array(exit, shape, np.uint8, 0)
    score_array = _coerce_array(score, shape, np.float32, 0.0)
    entry_codes = _coerce_array(entry_signal_code, shape, np.int16, -1)
    exit_codes = _coerce_array(exit_signal_code, shape, np.int16, -1)
    return _finalize_signal_matrix(
        entry_array,
        exit_array,
        score_array,
        entry_codes,
        exit_codes,
        entry_signal_ids=entry_signal_ids,
        exit_signal_ids=exit_signal_ids,
    )


def _finalize_signal_matrix(
    entry: np.ndarray,
    exit_: np.ndarray,
    score: np.ndarray,
    entry_signal_code: np.ndarray,
    exit_signal_code: np.ndarray,
    *,
    entry_signal_ids: tuple[str, ...] = (),
    exit_signal_ids: tuple[str, ...] = (),
) -> SignalMatrix:
    shape = entry.shape
    _make_read_only(entry, exit_, score, entry_signal_code, exit_signal_code)
    result = SignalMatrix(
        entry=entry,
        exit=exit_,
        score=score,
        entry_signal_code=entry_signal_code,
        exit_signal_code=exit_signal_code,
        entry_signal_ids=tuple(entry_signal_ids),
        exit_signal_ids=tuple(exit_signal_ids),
    )
    validate_signal_matrix(result, shape)
    return result


def validate_signal_matrix(signals: SignalMatrix, shape: tuple[int, int]) -> None:
    """Fail explicitly when a matrix strategy violates the shared output contract."""
    specs = {
        "entry": (signals.entry, np.dtype(np.uint8)),
        "exit": (signals.exit, np.dtype(np.uint8)),
        "score": (signals.score, np.dtype(np.float32)),
        "entry_signal_code": (signals.entry_signal_code, np.dtype(np.int16)),
        "exit_signal_code": (signals.exit_signal_code, np.dtype(np.int16)),
    }
    for name, (array, dtype) in specs.items():
        if not isinstance(array, np.ndarray):
            raise TypeError(f"SignalMatrix.{name} must be a numpy array")
        if array.shape != shape:
            raise ValueError(
                f"SignalMatrix.{name} shape {array.shape} does not match market {shape}"
            )
        if array.dtype != dtype:
            raise TypeError(f"SignalMatrix.{name} must use {dtype}, got {array.dtype}")
        if array.flags.writeable:
            raise ValueError(f"SignalMatrix.{name} must be read-only")
    # NaN marks a score that cannot be computed; infinities are never valid scores.
    if np.isinf(signals.score).any():
        raise ValueError("SignalMatrix.score must not contain infinite values")


def _raw_scale_matrix(market: MarketDataMatrix) -> np.ndarray:
    """raw_close / close per bar for Taiwan assets; the bar's OHLC share one adjustment factor."""
    scale = np.ones(market.shape, dtype=np.float32)
    taiwan = _taiwan_asset_mask(market.symbols)
    if not taiwan.any():
        return scale
    raw_close = market.fields.get("raw_close")
    if raw_close is None:
        raise ValueError("Taiwan execution requires raw_close to recover traded prices")
    close = market.close[:, taiwan]
    raw = raw_close[:, taiwan]
    priced = np.isfinite(close) & (close > 0)
    if np.any(priced & ~(np.isfinite(raw) & (raw > 0))):
        raise ValueError("Taiwan execution requires raw_close on every priced bar")
    taiwan_scale = np.full(close.shape, np.nan, dtype=np.float32)
    np.divide(raw, close, out=taiwan_scale, where=priced)
    scale[:, taiwan] = taiwan_scale
    return scale


def build_market_matrix_from_signals(
    market: MarketDataMatrix,
    signals: SignalMatrix,
    *,
    entry_delay_bars: int = 0,
    exit_delay_bars: int = 0,
    entry_reference_price: np.ndarray | None = None,
    exit_reference_price: np.ndarray | None = None,
    minute_exit_trigger: bool = False,
) -> MarketMatrix:
    """Combine base data and strategy signals into the matcher input matrix."""
    if entry_delay_bars not in (0, 1) or exit_delay_bars not in (0, 1):
        raise ValueError("phase-two MarketMatrix supports only zero or one bar delay")
    validate_signal_matrix(signals, market.shape)

    present = _present_matrix(market.open, market.high, market.low, market.close, market.volume)
    entry, entry_signal_time, entry_signal_code = _delay_signal_matrix(
        signals.entry,
        signals.entry_signal_code,
        present,
        entry_delay_bars,
    )
    exit_, exit_signal_time, exit_signal_code = _delay_signal_matrix(
        signals.exit,
        signals.exit_signal_code,
        present,
        exit_delay_bars,
    )

    base_reference = np.full(market.shape, np.nan, dtype=np.float32)
    for column in ("ma5", "ma10", "ma20"):
        values = market.fields.get(column)
        if values is None:
            continue
        use = ~np.isfinite(base_reference) & np.isfinite(values) & (values > 0)
        base_reference[use] = values[use]

    def _resolve_reference(reference: np.ndarray | None, delay_bars: int) -> np.ndarray:
        if reference is not None:
            if reference.shape != market.shape:
                raise ValueError("reference price shape does not match MarketDataMatrix")
            return np.array(reference, dtype=np.float32, copy=True)
        resolved = np.array(base_reference, copy=True)
        if delay_bars == 1:
            resolved[1:] = base_reference[:-1]
            resolved[0] = np.nan
        return resolved

    resolved_entry_reference = _resolve_reference(entry_reference_price, entry_delay_bars)
    resolved_exit_reference = _resolve_reference(exit_reference_price, exit_delay_bars)
    raw_scale = _raw_scale_matrix(market)

    minute_exit_line = minute_exit_line_code = None
    if minute_exit_trigger:
        trigger_reference = build_minute_exit_reference(
            market.close,
            market.fields,
            signals.exit_signal_code,
            signals.exit_signal_ids,
        )
        trigger_mask = signals.exit != 0
        if exit_reference_price is None:
            # The trigger line is the only reference known intraday; other exits keep the daily fill.
            resolved_exit_reference[:] = np.nan
        resolved_exit_reference[trigger_mask] = trigger_reference[trigger_mask]
        minute_exit_line, minute_exit_line_code = _armed_minute_exit_lines(
            market, signals.exit_signal_ids,
        )

    _make_read_only(
        entry,
        exit_,
        resolved_entry_reference,
        resolved_exit_reference,
        raw_scale,
        entry_signal_time,
        exit_signal_time,
        entry_signal_code,
        exit_signal_code,
    )

    return MarketMatrix(
        timestamps=market.timestamps,
        timestamp_labels=market.timestamp_labels,
        session_ids=market.session_ids,
        symbols=market.symbols,
        names=market.names,
        open=market.open,
        high=market.high,
        low=market.low,
        close=market.close,
        volume=market.volume,
        score=signals.score,
        entry=entry,
        exit=exit_,
        tradable=market.tradable,
        limit_up_locked=market.limit_up_locked,
        limit_down_locked=market.limit_down_locked,
        entry_reference_price=resolved_entry_reference,
        exit_reference_price=resolved_exit_reference,
        raw_scale=raw_scale,
        entry_signal_time=entry_signal_time,
        exit_signal_time=exit_signal_time,
        entry_signal_code=entry_signal_code,
        exit_signal_code=exit_signal_code,
        entry_signal_ids=signals.entry_signal_ids,
        exit_signal_ids=signals.exit_signal_ids,
        minute_exit_line=minute_exit_line,
        minute_exit_line_code=minute_exit_line_code,
    )


# Each replayable crossing signal fires only while the left series was last strictly above the
# right one before this bar, as cross_below requires.
_MINUTE_EXIT_ARMING = {
    "signal_ma5_breakdown": ("close", "ma5"),
    "signal_ma10_breakdown": ("close", "ma10"),
    "signal_ma20_breakdown": ("close", "ma20"),
    "signal_ma_dead_5_20": ("ma5", "ma20"),
}


def _armed_minute_exit_lines(
    market: MarketDataMatrix,
    exit_signal_ids: tuple[str, ...],
) -> tuple[np.ndarray, np.ndarray]:
    """Highest sell line among the exit signals armed at each bar's open, and its signal code.

    The arming state uses only the previous bar, so choosing a replay day never reads the close.
    """
    shape = market.shape
    line = np.full(shape, np.nan, dtype=np.float32)
    codes = np.full(shape, -1, dtype=np.int16)
    present = np.isfinite(market.close)

    def _series(name: str) -> np.ndarray | None:
        return market.close if name == "close" else market.fields.get(name)

    for code, signal_id in enumerate(exit_signal_ids):
        arming = _MINUTE_EXIT_ARMING.get(signal_id)
        if arming is None:
            continue
        left, right = (_series(name) for name in arming)
        if left is None or right is None:
            continue
        side = cross_side(
            np.where(present, left, np.nan), np.where(present, right, np.nan), market.close
        )
        armed = _previous_side(side) == 1
        candidate = build_minute_exit_reference(
            market.close,
            market.fields,
            np.full(shape, code, dtype=np.int16),
            exit_signal_ids,
        )
        with np.errstate(invalid="ignore"):
            use = armed & np.isfinite(candidate) & ~(line >= candidate)
        line[use] = candidate[use]
        codes[use] = code
    _make_read_only(line, codes)
    return line, codes


def build_market_matrix(
    panel: pl.DataFrame,
    entries: pl.Series | None,
    exits: pl.Series | None,
    *,
    entry_delay_bars: int = 0,
    exit_delay_bars: int = 0,
    entry_signal_ids: list[str] | None = None,
    exit_signal_ids: list[str] | None = None,
    minute_exit_trigger: bool = False,
) -> MarketMatrix:
    """Backward-compatible long-panel boundary used by legacy/Polars strategies."""
    if panel.is_empty():
        raise ValueError("cannot build MarketMatrix from an empty panel")
    market = build_market_data_matrix(
        panel,
        field_columns={"score", "ma5", "ma10", "ma20"}
        | ({"raw_close"} if "raw_close" in panel.columns else set()),
    )
    _, _, _, time_id, asset_id = _encode_axes(panel)
    shape = market.shape

    raw_entry = _scatter_bool_series(entries, len(panel), shape, time_id, asset_id)
    raw_exit = _scatter_bool_series(exits, len(panel), shape, time_id, asset_id)
    entry_codes, normalized_entry_ids = _signal_code_matrix(
        panel,
        entry_signal_ids,
        shape,
        time_id,
        asset_id,
    )
    exit_codes, normalized_exit_ids = _signal_code_matrix(
        panel,
        exit_signal_ids,
        shape,
        time_id,
        asset_id,
    )
    signals = make_signal_matrix(
        shape,
        entry=raw_entry,
        exit=raw_exit,
        score=market.fields.get("score"),
        entry_signal_code=entry_codes,
        exit_signal_code=exit_codes,
        entry_signal_ids=normalized_entry_ids,
        exit_signal_ids=normalized_exit_ids,
    )
    return build_market_matrix_from_signals(
        market,
        signals,
        entry_delay_bars=entry_delay_bars,
        exit_delay_bars=exit_delay_bars,
        minute_exit_trigger=minute_exit_trigger,
    )


def slice_market_data_matrix(market: MarketDataMatrix, start: int, stop: int) -> MarketDataMatrix:
    """Return a read-only time slice without copying the underlying market arrays."""
    fields = {name: values[start:stop] for name, values in market.fields.items()}
    result = MarketDataMatrix(
        timestamps=market.timestamps[start:stop],
        timestamp_labels=market.timestamp_labels[start:stop],
        session_ids=market.session_ids[start:stop],
        symbols=market.symbols,
        names=market.names,
        open=market.open[start:stop],
        high=market.high[start:stop],
        low=market.low[start:stop],
        close=market.close[start:stop],
        volume=market.volume[start:stop],
        tradable=market.tradable[start:stop],
        limit_up_locked=market.limit_up_locked[start:stop],
        limit_down_locked=market.limit_down_locked[start:stop],
        fields=MappingProxyType(fields),
        volume_unit=market.volume_unit,
        cache_status=market.cache_status,
        cache_path=market.cache_path,
        cache_lease=market.cache_lease,
        vector_fields=market.vector_fields,
        cache_timing_ms=market.cache_timing_ms,
    )
    _make_read_only(
        result.timestamps,
        result.session_ids,
        result.open,
        result.high,
        result.low,
        result.close,
        result.volume,
        result.tradable,
        result.limit_up_locked,
        result.limit_down_locked,
        *fields.values(),
    )
    return result


def slice_signal_matrix(signals: SignalMatrix, start: int, stop: int) -> SignalMatrix:
    return _finalize_signal_matrix(
        signals.entry[start:stop],
        signals.exit[start:stop],
        signals.score[start:stop],
        signals.entry_signal_code[start:stop],
        signals.exit_signal_code[start:stop],
        entry_signal_ids=signals.entry_signal_ids,
        exit_signal_ids=signals.exit_signal_ids,
    )


class RealtimeMarketDataMatrix:
    """Mutable staging buffer for one live asset universe.

    Historical rows are built once. Repeated snapshots for the current bar only
    overwrite the last row; a later timestamp of the same session appends one row,
    and another session must rebuild the buffer. Strategies only receive read-only
    views through :meth:`snapshot`.
    """

    def __init__(
        self,
        panel: pl.DataFrame,
        *,
        field_columns: set[str] | frozenset[str],
        build_count: int = 1,
    ) -> None:
        self.field_columns = frozenset(field_columns) | (
            {"raw_close"} if "raw_close" in panel.columns else frozenset()
        )
        self.market = _writable_market_copy(
            build_market_data_matrix(panel, field_columns=self.field_columns)
        )
        self.generation = 1
        self.build_count = int(build_count)
        self.update_count = 0

    def update(self, latest_panel: pl.DataFrame) -> None:
        if latest_panel.is_empty():
            raise ValueError("cannot update live matrix from an empty panel")
        timestamp_col = "datetime" if "datetime" in latest_panel.columns else "date"
        if timestamp_col not in latest_panel.columns:
            raise ValueError("live matrix update requires date or datetime")
        if latest_panel[timestamp_col].n_unique() != 1:
            raise ValueError("live matrix update must contain exactly one timestamp")

        latest = build_market_data_matrix(
            latest_panel,
            field_columns=self.field_columns,
            live_quotes=True,
        )
        target_ids = _live_target_asset_ids(self.market.symbols, latest.symbols)
        latest_timestamp = int(latest.timestamps[0])
        current_timestamp = int(self.market.timestamps[-1])
        if latest_timestamp < current_timestamp:
            raise ValueError("live matrix update timestamp is older than current snapshot")

        if latest.timestamp_labels[0][:10] != self.market.timestamp_labels[-1][:10]:
            # History rows are final and adjusted as of the session they were built for.
            raise ValueError("live matrix update starts another session; rebuild required")
        if latest_timestamp == current_timestamp:
            _overwrite_latest_market_row(self.market, latest, target_ids)
        else:
            self.market = _append_market_row(self.market, latest, target_ids)
        self.generation += 1
        self.update_count += 1

    def snapshot(self) -> MarketDataMatrix:
        return _readonly_market_view(self.market)


def _live_target_asset_ids(
    symbols: tuple[str, ...],
    latest_symbols: tuple[str, ...],
) -> np.ndarray:
    symbol_array = np.asarray(symbols)
    latest_array = np.asarray(latest_symbols)
    target_ids = np.searchsorted(symbol_array, latest_array)
    valid = target_ids < len(symbol_array)
    if not valid.all() or not np.array_equal(symbol_array[target_ids], latest_array):
        raise ValueError("live matrix symbol axis changed; rebuild required")
    return target_ids.astype(np.int32, copy=False)


def _market_array_fields() -> tuple[str, ...]:
    return (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "tradable",
        "limit_up_locked",
        "limit_down_locked",
    )


def _writable_market_copy(market: MarketDataMatrix) -> MarketDataMatrix:
    fields = {name: np.array(values, copy=True) for name, values in market.fields.items()}
    return MarketDataMatrix(
        timestamps=np.array(market.timestamps, copy=True),
        timestamp_labels=market.timestamp_labels,
        session_ids=np.array(market.session_ids, copy=True),
        symbols=market.symbols,
        names=market.names,
        open=np.array(market.open, copy=True),
        high=np.array(market.high, copy=True),
        low=np.array(market.low, copy=True),
        close=np.array(market.close, copy=True),
        volume=np.array(market.volume, copy=True),
        tradable=np.array(market.tradable, copy=True),
        limit_up_locked=np.array(market.limit_up_locked, copy=True),
        limit_down_locked=np.array(market.limit_down_locked, copy=True),
        fields=MappingProxyType(fields),
        volume_unit=market.volume_unit,
    )


def _readonly_view(values: np.ndarray) -> np.ndarray:
    view = values.view()
    view.flags.writeable = False
    return view


def _readonly_market_view(market: MarketDataMatrix) -> MarketDataMatrix:
    fields = {name: _readonly_view(values) for name, values in market.fields.items()}
    return MarketDataMatrix(
        timestamps=_readonly_view(market.timestamps),
        timestamp_labels=market.timestamp_labels,
        session_ids=_readonly_view(market.session_ids),
        symbols=market.symbols,
        names=market.names,
        open=_readonly_view(market.open),
        high=_readonly_view(market.high),
        low=_readonly_view(market.low),
        close=_readonly_view(market.close),
        volume=_readonly_view(market.volume),
        tradable=_readonly_view(market.tradable),
        limit_up_locked=_readonly_view(market.limit_up_locked),
        limit_down_locked=_readonly_view(market.limit_down_locked),
        fields=MappingProxyType(fields),
        volume_unit=market.volume_unit,
    )


def _overwrite_latest_market_row(
    market: MarketDataMatrix,
    latest: MarketDataMatrix,
    target_ids: np.ndarray,
) -> None:
    for name in _market_array_fields():
        getattr(market, name)[-1, target_ids] = getattr(latest, name)[0]
    for name, values in market.fields.items():
        latest_values = latest.fields.get(name)
        if latest_values is None:
            raise ValueError(f"live matrix update missing field: {name}")
        values[-1, target_ids] = latest_values[0]
    object.__setattr__(market, "_valid_bars", None)


def _append_market_row(
    market: MarketDataMatrix,
    latest: MarketDataMatrix,
    target_ids: np.ndarray,
) -> MarketDataMatrix:
    asset_count = len(market.symbols)

    def aligned(values: np.ndarray, fill: float | int) -> np.ndarray:
        row = np.full(asset_count, fill, dtype=values.dtype)
        row[target_ids] = values[0]
        return row

    arrays: dict[str, np.ndarray] = {}
    for name in _market_array_fields():
        source = getattr(latest, name)
        fill = np.nan if np.issubdtype(source.dtype, np.floating) else 0
        arrays[name] = np.concatenate(
            [getattr(market, name), aligned(source, fill)[None, :]],
            axis=0,
        )

    fields: dict[str, np.ndarray] = {}
    for name, old_values in market.fields.items():
        latest_values = latest.fields.get(name)
        if latest_values is None:
            raise ValueError(f"live matrix update missing field: {name}")
        fields[name] = np.concatenate(
            [old_values, aligned(latest_values, np.nan)[None, :]],
            axis=0,
        )

    latest_label = latest.timestamp_labels[0]
    same_session = latest_label[:10] == market.timestamp_labels[-1][:10]
    next_session = int(market.session_ids[-1]) if same_session else int(market.session_ids[-1]) + 1
    return MarketDataMatrix(
        timestamps=np.concatenate([market.timestamps, latest.timestamps[:1]]),
        timestamp_labels=(*market.timestamp_labels, latest_label),
        session_ids=np.concatenate([
            market.session_ids,
            np.array([next_session], dtype=np.int32),
        ]),
        symbols=market.symbols,
        names=market.names,
        fields=MappingProxyType(fields),
        volume_unit=market.volume_unit,
        **arrays,
    )


def _encode_axes(
    panel: pl.DataFrame,
) -> tuple[str, pl.Series, np.ndarray, np.ndarray, np.ndarray]:
    timestamp_col = "datetime" if "datetime" in panel.columns else "date"
    required = {timestamp_col, "symbol", "open", "high", "low", "close"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"MarketDataMatrix missing columns: {sorted(missing)}")

    timestamp_series = panel[timestamp_col]
    unique_timestamps = timestamp_series.unique().sort()
    symbol_series = panel["symbol"].cast(pl.Utf8)
    unique_symbols = symbol_series.unique().sort()
    row_timestamps = timestamp_series.to_numpy()
    timestamp_values = unique_timestamps.to_numpy()
    row_symbols = symbol_series.to_numpy()
    symbol_values = unique_symbols.to_numpy()
    time_id = np.searchsorted(timestamp_values, row_timestamps).astype(np.int32)
    asset_id = np.searchsorted(symbol_values, row_symbols).astype(np.int32)
    keys = time_id.astype(np.int64) * len(symbol_values) + asset_id
    if np.unique(keys).size != len(panel):
        raise ValueError("MarketDataMatrix requires unique timestamp/symbol rows")
    return timestamp_col, unique_timestamps, symbol_values, time_id, asset_id


def _float_matrix(
    panel: pl.DataFrame,
    column: str,
    shape: tuple[int, int],
    time_id: np.ndarray,
    asset_id: np.ndarray,
    default: float = np.nan,
    null_fill: float | None = None,
) -> np.ndarray:
    out = np.full(shape, default, dtype=np.float32)
    if column not in panel.columns:
        return out
    values = np.array(
        panel[column].cast(pl.Float32, strict=False).to_numpy(),
        dtype=np.float32,
        copy=True,
    )
    values[~np.isfinite(values)] = np.nan if null_fill is None else null_fill
    out[time_id, asset_id] = values
    return out


def _timestamp_int64(series: pl.Series) -> np.ndarray:
    if series.dtype == pl.Date:
        return series.cast(pl.Datetime("ms")).cast(pl.Int64).to_numpy()
    if isinstance(series.dtype, pl.Datetime):
        return series.cast(pl.Datetime("ms")).cast(pl.Int64).to_numpy()
    return series.cast(pl.Int64, strict=False).to_numpy()


def _bool_matrix(
    panel: pl.DataFrame,
    column: str,
    shape: tuple[int, int],
    time_id: np.ndarray,
    asset_id: np.ndarray,
) -> np.ndarray:
    out = np.zeros(shape, dtype=np.uint8)
    if column in panel.columns:
        out[time_id, asset_id] = panel[column].fill_null(False).cast(pl.UInt8).to_numpy()
    return out


def _tradable_matrix(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> np.ndarray:
    present = _present_matrix(open_, high, low, close, volume)
    valid_ohlc = present & ((open_ > 0) | (high > 0) | (low > 0) | (close > 0))
    max_price = np.array(open_, dtype=np.float32, copy=True)
    min_price = np.array(open_, dtype=np.float32, copy=True)
    for values in (high, low, close):
        np.fmax(max_price, values, out=max_price)
        np.fmin(min_price, values, out=min_price)
    spread = max_price - min_price
    tolerance = np.maximum(np.abs(close) * np.float32(1e-4), np.float32(0.01))
    suspended_zero_volume = ((volume <= 0) | np.isnan(volume)) & (spread <= tolerance)
    return (valid_ohlc & ~suspended_zero_volume).astype(np.uint8)


def _present_matrix(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> np.ndarray:
    del volume
    return (
        np.isfinite(open_)
        | np.isfinite(high)
        | np.isfinite(low)
        | np.isfinite(close)
    )


def _normalize_signal(signal: str) -> str:
    return signal if signal.startswith(("signal_", "csg_")) else f"signal_{signal}"


def _signal_code_matrix(
    panel: pl.DataFrame,
    signal_ids: list[str] | None,
    shape: tuple[int, int],
    time_id: np.ndarray,
    asset_id: np.ndarray,
) -> tuple[np.ndarray, tuple[str, ...]]:
    normalized = tuple(_normalize_signal(signal) for signal in (signal_ids or []))
    row_codes = np.full(len(panel), -1, dtype=np.int16)
    for code, column in enumerate(normalized):
        if column not in panel.columns:
            continue
        mask = panel[column].fill_null(False).cast(pl.Boolean).to_numpy()
        row_codes[(row_codes < 0) & mask] = code
    codes = np.full(shape, -1, dtype=np.int16)
    codes[time_id, asset_id] = row_codes
    return codes, normalized


def _scatter_bool_series(
    series: pl.Series | None,
    length: int,
    shape: tuple[int, int],
    time_id: np.ndarray,
    asset_id: np.ndarray,
) -> np.ndarray:
    out = np.zeros(shape, dtype=np.uint8)
    if series is None or len(series) != length:
        return out
    out[time_id, asset_id] = series.fill_null(False).cast(pl.UInt8).to_numpy()
    return out


def _delay_signal_matrix(
    raw: np.ndarray,
    codes: np.ndarray,
    present: np.ndarray,
    delay_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    shape = raw.shape
    output = np.zeros(shape, dtype=np.uint8)
    signal_time = np.full(shape, -1, dtype=np.int32)
    signal_code = np.full(shape, -1, dtype=np.int16)
    for asset_id in range(shape[1]):
        rows = np.flatnonzero(present[:, asset_id])
        if len(rows) <= delay_bars:
            continue
        source_rows = rows[: len(rows) - delay_bars] if delay_bars else rows
        target_rows = rows[delay_bars:] if delay_bars else rows
        active = raw[source_rows, asset_id] != 0
        if not active.any():
            continue
        sources = source_rows[active]
        targets = target_rows[active]
        output[targets, asset_id] = 1
        signal_time[targets, asset_id] = sources.astype(np.int32)
        signal_code[targets, asset_id] = codes[sources, asset_id]
    return output, signal_time, signal_code


def _coerce_array(
    value: np.ndarray | None,
    shape: tuple[int, int],
    dtype: np.dtype | type,
    fill: int | float,
) -> np.ndarray:
    if value is None:
        return np.full(shape, fill, dtype=dtype)
    array = np.asarray(value, dtype=dtype)
    if array.shape != shape:
        raise ValueError(f"matrix shape {array.shape} does not match expected {shape}")
    return np.array(array, dtype=dtype, copy=True)


def _make_read_only(*arrays: np.ndarray) -> None:
    for array in arrays:
        array.flags.writeable = False


# ============================================================================
# Shared NumPy feature primitives
# ============================================================================


def shift(values: np.ndarray, periods: int = 1) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    out = np.full(source.shape, np.nan, dtype=np.float32)
    if periods == 0:
        out[:] = source
    elif periods > 0 and periods < source.shape[0]:
        out[periods:] = source[:-periods]
    elif periods < 0 and -periods < source.shape[0]:
        out[:periods] = source[-periods:]
    return out


_FLOAT32_CROSS_TOLERANCE = 2.0**-22


def cross_side(left: np.ndarray, right: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """app.indicators.pipeline.cross_side on ``time x asset`` arrays; NaN where either is missing.

    The difference is taken in float64. When either input is float32, as the matrix's own moving
    averages are, the tolerance is at least 2^-22 * max(|left|, |right|), two float32 ulps.
    """
    left_values = np.asarray(left, dtype=np.float64)
    right_values = np.asarray(right, dtype=np.float64)
    diff = left_values - right_values
    tolerance = np.fmax(CROSS_TOLERANCE, CROSS_TOLERANCE * np.abs(np.asarray(scale, dtype=np.float64)))
    if np.asarray(left).dtype == np.float32 or np.asarray(right).dtype == np.float32:
        rounding = _FLOAT32_CROSS_TOLERANCE * np.fmax(np.abs(left_values), np.abs(right_values))
        tolerance = np.fmax(tolerance, rounding)
    with np.errstate(invalid="ignore"):
        side = np.where(diff >= tolerance, 1.0, np.where(diff <= -tolerance, -1.0, 0.0))
    side[~np.isfinite(diff)] = np.nan
    return side


def _previous_side(side: np.ndarray) -> np.ndarray:
    """The last nonzero side above each row of its column, 0 where none; NaN rows are skipped."""
    rows = np.arange(side.shape[0])[:, None]
    known = np.where(np.isfinite(side) & (side != 0), rows, -1)
    last = np.maximum.accumulate(known, axis=0)
    previous = np.vstack([np.full((1, side.shape[1]), -1), last[:-1]])
    values = np.take_along_axis(side, np.maximum(previous, 0), axis=0)
    return np.where(previous >= 0, values, 0.0)


def cross_above(left: np.ndarray, right: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """app.indicators.pipeline.cross_above: a missing bar, like an absent published row, is skipped."""
    side = cross_side(left, right, scale)
    return (side == 1) & (_previous_side(side) == -1)


def cross_below(left: np.ndarray, right: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """The mirror of cross_above."""
    side = cross_side(left, right, scale)
    return (side == -1) & (_previous_side(side) == 1)


def _resolve_valid_bar_index(
    source: np.ndarray,
    valid: np.ndarray,
    bar_index: ValidBarIndex | None,
) -> ValidBarIndex:
    if bar_index is not None:
        if bar_index.shape != source.shape:
            raise ValueError("valid bar index shape does not match values")
        return bar_index
    active = _ACTIVE_VALID_BAR_INDEX.get()
    if isinstance(active, ValidBarIndex) and active.shape == source.shape:
        return active
    return _build_valid_bar_index(valid)


@njit(cache=True, nogil=True, parallel=True)
def _valid_shift_kernel(
    source: np.ndarray,
    valid: np.ndarray,
    offsets: np.ndarray,
    rows: np.ndarray,
    periods: int,
) -> np.ndarray:
    out = np.full(source.shape, np.nan, dtype=np.float32)
    distance = abs(periods)
    for asset_id in prange(source.shape[1]):
        start = int(offsets[asset_id])
        stop = int(offsets[asset_id + 1])
        ring = np.empty(distance, dtype=np.int32)
        seen = 0
        if periods > 0:
            for position in range(start, stop):
                row = int(rows[position])
                if not valid[row, asset_id] or not np.isfinite(source[row, asset_id]):
                    continue
                slot = seen % distance
                if seen >= distance:
                    out[row, asset_id] = source[int(ring[slot]), asset_id]
                ring[slot] = row
                seen += 1
        else:
            for position in range(stop - 1, start - 1, -1):
                row = int(rows[position])
                if not valid[row, asset_id] or not np.isfinite(source[row, asset_id]):
                    continue
                slot = seen % distance
                if seen >= distance:
                    out[row, asset_id] = source[int(ring[slot]), asset_id]
                ring[slot] = row
                seen += 1
    return out


def valid_shift(
    values: np.ndarray,
    periods: int = 1,
    valid_mask: np.ndarray | None = None,
    *,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    """Shift by effective observations, skipping missing market bars.

    The matrix keeps a shared time axis, so a suspended asset can have NaN
    rows between two valid bars.  Standard per-symbol indicators must treat
    those rows as absent rather than as observations.
    """
    source = np.asarray(values, dtype=np.float32)
    valid = (
        np.isfinite(source)
        if valid_mask is None
        else np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    )
    if valid.shape != source.shape:
        raise ValueError("valid_shift mask shape does not match values")
    if periods == 0:
        out = np.full(source.shape, np.nan, dtype=np.float32)
        out[valid] = source[valid]
        return out
    index = _resolve_valid_bar_index(source, valid, bar_index)

    return run_numba_parallel(
        lambda: _valid_shift_kernel(
            source,
            valid,
            index.offsets,
            index.rows,
            int(periods),
        )
    )


def rolling_min(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    return _rolling_reduce(source, window, np.min)


def rolling_max(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    return _rolling_reduce(source, window, np.max)


def rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    return _rolling_reduce(source, window, np.mean)


def rolling_sum(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    return _rolling_reduce(source, window, np.sum)


def rolling_std(values: np.ndarray, window: int, *, ddof: int = 0) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    return _rolling_reduce(
        source,
        window,
        lambda view, axis: np.std(view, axis=axis, ddof=int(ddof)),
        materialized_window_budget_bytes=_ROLLING_MATERIALIZED_WINDOW_BUDGET_BYTES,
    )


_VALID_REDUCE_MIN = 0
_VALID_REDUCE_MAX = 1
_VALID_REDUCE_MEAN = 2
_VALID_REDUCE_STD = 3
_VALID_REDUCE_SUM = 4


@njit(cache=True, nogil=True, parallel=True)
def _valid_rolling_kernel(
    source: np.ndarray,
    valid: np.ndarray,
    offsets: np.ndarray,
    rows: np.ndarray,
    window: int,
    operation: int,
    ddof: int,
) -> np.ndarray:
    out = np.full(source.shape, np.nan, dtype=np.float32)
    window_value = float(window)
    denominator = float(window - ddof)
    for asset_id in prange(source.shape[1]):
        start = int(offsets[asset_id])
        stop = int(offsets[asset_id + 1])
        ring = np.empty(window, dtype=np.float32)
        seen = 0
        for position in range(start, stop):
            row = int(rows[position])
            value = source[row, asset_id]
            if not valid[row, asset_id] or not np.isfinite(value):
                continue
            ring[seen % window] = value
            seen += 1
            if seen < window:
                continue
            first = seen - window
            if operation == _VALID_REDUCE_MIN:
                result = ring[first % window]
                for offset in range(1, window):
                    candidate = ring[(first + offset) % window]
                    if candidate < result:
                        result = candidate
                out[row, asset_id] = result
            elif operation == _VALID_REDUCE_MAX:
                result = ring[first % window]
                for offset in range(1, window):
                    candidate = ring[(first + offset) % window]
                    if candidate > result:
                        result = candidate
                out[row, asset_id] = result
            else:
                total = 0.0
                for offset in range(window):
                    total += float(ring[(first + offset) % window])
                mean = total / window_value
                if operation == _VALID_REDUCE_MEAN:
                    out[row, asset_id] = mean
                elif operation == _VALID_REDUCE_SUM:
                    out[row, asset_id] = total
                else:
                    squared = 0.0
                    for offset in range(window):
                        delta = float(ring[(first + offset) % window]) - mean
                        squared += delta * delta
                    out[row, asset_id] = np.sqrt(squared / denominator)
    return out


def _valid_rolling_reduce(
    values: np.ndarray,
    valid_mask: np.ndarray,
    window: int,
    operation: int,
    *,
    ddof: int = 0,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool)
    if source.ndim != 2 or valid.shape != source.shape:
        raise ValueError("valid rolling inputs must be matching 2D arrays")
    if window <= 0:
        raise ValueError("valid rolling window must be positive")
    if ddof < 0 or ddof >= window:
        raise ValueError("valid rolling ddof must be in [0, window)")
    index = _resolve_valid_bar_index(source, valid, bar_index)
    return run_numba_parallel(
        lambda: _valid_rolling_kernel(
            source,
            valid,
            index.offsets,
            index.rows,
            int(window),
            int(operation),
            int(ddof),
        )
    )


def valid_rolling_min(
    values: np.ndarray,
    valid_mask: np.ndarray,
    window: int,
    *,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    index = _resolve_valid_bar_index(source, valid, bar_index)
    return _valid_rolling_reduce(
        source,
        valid,
        window,
        _VALID_REDUCE_MIN,
        bar_index=index,
    )


def valid_rolling_max(
    values: np.ndarray,
    valid_mask: np.ndarray,
    window: int,
    *,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    index = _resolve_valid_bar_index(source, valid, bar_index)
    return _valid_rolling_reduce(
        source,
        valid,
        window,
        _VALID_REDUCE_MAX,
        bar_index=index,
    )


def valid_rolling_mean(
    values: np.ndarray,
    valid_mask: np.ndarray,
    window: int,
    *,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    index = _resolve_valid_bar_index(source, valid, bar_index)
    return _valid_rolling_reduce(
        source,
        valid,
        window,
        _VALID_REDUCE_MEAN,
        bar_index=index,
    )


def valid_rolling_std(
    values: np.ndarray,
    valid_mask: np.ndarray,
    window: int,
    *,
    ddof: int = 0,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    index = _resolve_valid_bar_index(source, valid, bar_index)
    return _valid_rolling_reduce(
        source,
        valid,
        window,
        _VALID_REDUCE_STD,
        ddof=int(ddof),
        bar_index=index,
    )


def valid_rolling_sum(
    values: np.ndarray,
    valid_mask: np.ndarray,
    window: int,
    *,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    index = _resolve_valid_bar_index(source, valid, bar_index)
    return _valid_rolling_reduce(
        source,
        valid,
        window,
        _VALID_REDUCE_SUM,
        bar_index=index,
    )


def rolling_quantile(values: np.ndarray, window: int, quantile: float) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    q = float(quantile)
    if not 0.0 <= q <= 1.0:
        raise ValueError("rolling quantile must be in [0, 1]")
    return _rolling_reduce(
        source,
        window,
        lambda view, axis: np.quantile(view, q, axis=axis),
        materialized_window_budget_bytes=_ROLLING_MATERIALIZED_WINDOW_BUDGET_BYTES,
    )


@njit(cache=True, nogil=True, parallel=True)
def _valid_rolling_quantile_kernel(
    source: np.ndarray,
    valid: np.ndarray,
    offsets: np.ndarray,
    rows: np.ndarray,
    window: int,
    lower: int,
    upper: int,
    gamma: float,
) -> np.ndarray:
    out = np.full(source.shape, np.nan, dtype=np.float32)
    for asset_id in prange(source.shape[1]):
        start = int(offsets[asset_id])
        stop = int(offsets[asset_id + 1])
        ring = np.empty(window, dtype=np.float32)
        ordered = np.empty(window, dtype=np.float32)
        seen = 0
        for position in range(start, stop):
            row = int(rows[position])
            value = source[row, asset_id]
            if not valid[row, asset_id] or not np.isfinite(value):
                continue
            slot = seen % window
            count = seen
            if seen >= window:
                count = window - 1
                evicted = ring[slot]
                index = 0
                while ordered[index] != evicted:
                    index += 1
                for moved in range(index, count):
                    ordered[moved] = ordered[moved + 1]
            index = count
            while index > 0 and ordered[index - 1] > value:
                ordered[index] = ordered[index - 1]
                index -= 1
            ordered[index] = value
            ring[slot] = value
            seen += 1
            if seen < window:
                continue
            below = np.float64(ordered[lower])
            above = np.float64(ordered[upper])
            difference = above - below
            if gamma >= 0.5:
                out[row, asset_id] = above - difference * (1.0 - gamma)
            else:
                out[row, asset_id] = below + difference * gamma
    return out


def valid_rolling_quantile(
    values: np.ndarray,
    valid_mask: np.ndarray,
    window: int,
    quantile: float,
    *,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    """``np.quantile`` (linear method) over each asset's last ``window`` valid bars."""
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    if source.ndim != 2 or valid.shape != source.shape:
        raise ValueError("valid rolling inputs must be matching 2D arrays")
    if window <= 0:
        raise ValueError("valid rolling window must be positive")
    q = float(quantile)
    if not 0.0 <= q <= 1.0:
        raise ValueError("rolling quantile must be in [0, 1]")
    size = int(window)
    # numpy's linear method: index (n - 1) * q and its two-sided lerp, evaluated in float64.
    virtual = (size - 1) * q
    lower = min(max(int(np.floor(virtual)), 0), size - 1)
    upper = min(lower + 1, size - 1)
    gamma = virtual - lower
    index = _resolve_valid_bar_index(source, valid, bar_index)
    return run_numba_parallel(
        lambda: _valid_rolling_quantile_kernel(
            source,
            valid,
            index.offsets,
            index.rows,
            size,
            lower,
            upper,
            gamma,
        )
    )


def ewm_adjust_false(
    values: np.ndarray,
    *,
    span: int | None = None,
    alpha: float | None = None,
) -> np.ndarray:
    """Pandas-compatible EWM mean for ``adjust=False, ignore_na=False``."""
    if alpha is None:
        if span is None or span <= 0:
            raise ValueError("span must be positive when alpha is omitted")
        alpha = 2.0 / (float(span) + 1.0)
    if not 0.0 < float(alpha) <= 1.0:
        raise ValueError("alpha must be in (0, 1]")

    source = np.asarray(values, dtype=np.float32)
    alpha_value = float(alpha)

    def _compute() -> np.ndarray:
        out = np.full(source.shape, np.nan, dtype=np.float32)
        weighted = np.zeros(source.shape[1], dtype=np.float64)
        old_weight = np.ones(source.shape[1], dtype=np.float64)
        initialized = np.zeros(source.shape[1], dtype=bool)
        decay = 1.0 - alpha_value

        for time_id in range(source.shape[0]):
            row = source[time_id].astype(np.float64, copy=False)
            finite = np.isfinite(row)
            continuing = finite & initialized
            starting = finite & ~initialized

            old_weight[initialized] *= decay
            if continuing.any():
                denominator = old_weight[continuing] + alpha_value
                weighted[continuing] = (
                    old_weight[continuing] * weighted[continuing]
                    + alpha_value * row[continuing]
                ) / denominator
                old_weight[continuing] = 1.0
            if starting.any():
                weighted[starting] = row[starting]
                old_weight[starting] = 1.0
                initialized[starting] = True
            out[time_id, initialized] = weighted[initialized].astype(np.float32)
        return out

    return _compute()


@njit(cache=True, nogil=True, parallel=True)
def _valid_ewm_kernel(
    source: np.ndarray,
    valid: np.ndarray,
    offsets: np.ndarray,
    rows: np.ndarray,
    alpha: float,
) -> np.ndarray:
    out = np.full(source.shape, np.nan, dtype=np.float32)
    decay = 1.0 - alpha
    for asset_id in prange(source.shape[1]):
        start = int(offsets[asset_id])
        stop = int(offsets[asset_id + 1])
        initialized = False
        state = 0.0
        for position in range(start, stop):
            row = int(rows[position])
            value = source[row, asset_id]
            if not valid[row, asset_id] or not np.isfinite(value):
                continue
            if initialized:
                state = decay * state + alpha * float(value)
            else:
                state = float(value)
                initialized = True
            out[row, asset_id] = np.float32(state)
    return out


def valid_ewm_adjust_false(
    values: np.ndarray,
    valid_mask: np.ndarray,
    *,
    span: int | None = None,
    alpha: float | None = None,
    bar_index: ValidBarIndex | None = None,
) -> np.ndarray:
    """Pandas-compatible EWM that advances only on effective observations."""
    if alpha is None:
        if span is None or span <= 0:
            raise ValueError("span must be positive when alpha is omitted")
        alpha = 2.0 / (float(span) + 1.0)
    if not 0.0 < float(alpha) <= 1.0:
        raise ValueError("alpha must be in (0, 1]")
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    if valid.shape != source.shape:
        raise ValueError("valid_ewm mask shape does not match values")
    alpha_value = float(alpha)
    index = _resolve_valid_bar_index(source, valid, bar_index)

    return run_numba_parallel(
        lambda: _valid_ewm_kernel(
            source,
            valid,
            index.offsets,
            index.rows,
            alpha_value,
        )
    )


def safe_divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = np.full(np.broadcast_shapes(numerator.shape, denominator.shape), np.nan, dtype=np.float32)
    np.divide(
        numerator,
        denominator,
        out=out,
        where=np.isfinite(denominator) & (denominator != 0),
    )
    return out


def _rolling_reduce(
    values: np.ndarray,
    window: int,
    reducer: Callable[..., np.ndarray],
    *,
    asset_chunk_size: int = 256,
    materialized_window_budget_bytes: int | None = None,
) -> np.ndarray:
    if window <= 0:
        raise ValueError("rolling window must be positive")
    source = np.asarray(values, dtype=np.float32)
    if source.ndim != 2:
        raise ValueError("matrix rolling features require a 2D array")
    out = np.full(source.shape, np.nan, dtype=np.float32)
    if source.shape[0] < window:
        return out

    if materialized_window_budget_bytes is not None:
        output_rows = source.shape[0] - window + 1
        logical_bytes_per_asset = output_rows * window * source.itemsize
        if logical_bytes_per_asset > 0:
            budget_chunk_size = max(
                1,
                int(materialized_window_budget_bytes) // logical_bytes_per_asset,
            )
            asset_chunk_size = min(asset_chunk_size, budget_chunk_size)

    for start in range(0, source.shape[1], asset_chunk_size):
        stop = min(start + asset_chunk_size, source.shape[1])
        view = np.lib.stride_tricks.sliding_window_view(
            source[:, start:stop],
            window_shape=window,
            axis=0,
        )
        out[window - 1 :, start:stop] = reducer(view, axis=-1).astype(np.float32, copy=False)
    return out


@njit(cache=True, nogil=True, parallel=True)
def knn_direction_votes(
    raw_features: np.ndarray,
    close: np.ndarray,
    session_ids: np.ndarray,
    bar_offsets: np.ndarray,
    bar_rows: np.ndarray,
    candidate_offsets: np.ndarray,
    take: int,
    horizon: int,
    norm_window: int,
    own_span: int,
    session_span: int,
) -> np.ndarray:
    """Lorentzian-distance k-NN vote per valid bar of each asset; NaN where a bar is not classified.

    Bars are an asset's valid bars (a ValidBarIndex's offsets and rows). Each feature of
    raw_features[time, asset, feature] is min-max scaled over the last norm_window bars. Bar p sums
    the labels sign(close[j + horizon] - close[j]) of the take nearest j = p - candidate_offsets
    (ascending, each >= horizon) under sum(log1p(|a - b|)); equal distances keep the more recent j.
    p is classified only with own_span earlier bars, the oldest at most session_span sessions back,
    and take usable candidates; a bar whose scaled features are not all finite is never used.
    """
    prediction = np.full(close.shape, np.nan, dtype=np.float32)
    feature_count = raw_features.shape[2]
    first_read = own_span - candidate_offsets[-1]
    for asset in prange(close.shape[1]):
        start = bar_offsets[asset]
        count = bar_offsets[asset + 1] - start
        if count <= own_span:
            continue
        # Min-max position over the last norm_window own bars, for the bars a classification reads.
        scaled = np.full((count - first_read, feature_count), np.nan, dtype=np.float32)
        for position in range(first_read, count):
            for feature in range(feature_count):
                low = np.float32(np.inf)
                high = np.float32(-np.inf)
                complete = True
                for earlier in range(position - norm_window + 1, position + 1):
                    value = raw_features[bar_rows[start + earlier], asset, feature]
                    if not np.isfinite(value):
                        complete = False
                        break
                    low = min(low, value)
                    high = max(high, value)
                if complete and high > low:
                    current = raw_features[bar_rows[start + position], asset, feature]
                    scaled[position - first_read, feature] = (current - low) / (high - low)

        best_distance = np.empty(take, dtype=np.float64)
        best_position = np.empty(take, dtype=np.int64)
        for position in range(own_span, count):
            row = bar_rows[start + position]
            oldest_row = bar_rows[start + position - own_span]
            if session_ids[row] - session_ids[oldest_row] > session_span:
                continue
            query = position - first_read
            usable = True
            for feature in range(feature_count):
                if not np.isfinite(scaled[query, feature]):
                    usable = False
                    break
            if not usable:
                continue
            found = 0
            for offset in candidate_offsets:
                candidate = position - offset
                distance = 0.0
                for feature in range(feature_count):
                    value = scaled[candidate - first_read, feature]
                    if not np.isfinite(value):
                        distance = np.inf
                        break
                    distance += np.log1p(abs(float(scaled[query, feature]) - float(value)))
                if not np.isfinite(distance):
                    continue
                if found < take:
                    slot = found
                    found += 1
                elif distance < best_distance[take - 1]:
                    slot = take - 1
                else:
                    continue
                # Only strictly nearer neighbours move ahead, so ties keep the more recent one.
                while slot > 0 and best_distance[slot - 1] > distance:
                    best_distance[slot] = best_distance[slot - 1]
                    best_position[slot] = best_position[slot - 1]
                    slot -= 1
                best_distance[slot] = distance
                best_position[slot] = candidate
            if found < take:
                continue
            votes = 0.0
            for slot in range(take):
                then = close[bar_rows[start + best_position[slot]], asset]
                later = close[bar_rows[start + best_position[slot] + horizon], asset]
                if later > then:
                    votes += 1.0
                elif later < then:
                    votes -= 1.0
            prediction[row, asset] = votes
    return prediction


# ============================================================================
# Matrix-native strategy protocol and framework-owned pipeline
# ============================================================================


@runtime_checkable
class MatrixStrategy(Protocol):
    """Optional hook ``limit_entries(entry, score, params)``: called with a copy of the entries left
    after the framework's basic filter and asset mask and the strategy's own score; returns a mask
    of the market's shape selecting those to keep, such as a per-bar top-N cut. It cannot add entries.
    """

    def required_fields(self) -> frozenset[str]: ...

    def required_warmup_bars(self, params: dict) -> int: ...

    def compute_signals(
        self,
        market: MarketDataMatrix,
        params: dict,
    ) -> SignalMatrix: ...


@dataclass(frozen=True)
class MatrixPipelineConfig:
    basic_filter: dict
    scoring: dict[str, float]
    order_by: str | None
    descending: bool
    scoring_directions: dict[str, str] = field(default_factory=dict)
    asset_mask: np.ndarray | None = None


class MatrixStrategyPipeline:
    """Apply framework-owned filtering and scoring to a matrix strategy output."""

    def run(
        self,
        strategy: MatrixStrategy,
        market: MarketDataMatrix,
        params: dict,
        config: MatrixPipelineConfig,
        timing_ms: dict[str, float] | None = None,
    ) -> SignalMatrix:
        with _activate_valid_bar_index(market.valid_bars):
            return self._run_with_valid_bars(
                strategy,
                market,
                params,
                config,
                timing_ms,
            )

    def _run_with_valid_bars(
        self,
        strategy: MatrixStrategy,
        market: MarketDataMatrix,
        params: dict,
        config: MatrixPipelineConfig,
        timing_ms: dict[str, float] | None,
    ) -> SignalMatrix:
        strategy_started = time.perf_counter()
        signals = strategy.compute_signals(market, params)
        validate_signal_matrix(signals, market.shape)
        if timing_ms is not None:
            timing_ms["strategy_signals"] = round(
                (time.perf_counter() - strategy_started) * 1000,
                1,
            )

        filter_started = time.perf_counter()
        basic_mask = build_pipeline_filter_mask(market, config)
        entry = signals.entry.astype(bool) & basic_mask
        limit_entries = getattr(strategy, "limit_entries", None)
        if callable(limit_entries):
            kept = np.asarray(limit_entries(entry.copy(), signals.score, params), dtype=bool)
            if kept.shape != market.shape:
                raise ValueError(
                    f"matrix strategy limit_entries must return a mask of shape {market.shape}, "
                    f"got {kept.shape}"
                )
            entry &= kept
        entry = entry.astype(np.uint8)
        score = build_matrix_score(
            market,
            entry.astype(bool),
            config.scoring,
            config.order_by,
            config.descending,
            fallback=signals.score,
            directions=config.scoring_directions,
        )
        entry_codes = np.where(entry != 0, signals.entry_signal_code, -1).astype(np.int16)
        exit_codes = np.where(signals.exit != 0, signals.exit_signal_code, -1).astype(np.int16)
        if timing_ms is not None:
            timing_ms["filter_score"] = round(
                (time.perf_counter() - filter_started) * 1000,
                1,
            )
        return make_signal_matrix(
            market.shape,
            entry=entry,
            exit=signals.exit,
            score=score,
            entry_signal_code=entry_codes,
            exit_signal_code=exit_codes,
            entry_signal_ids=signals.entry_signal_ids,
            exit_signal_ids=signals.exit_signal_ids,
        )


def build_pipeline_filter_mask(
    market: MarketDataMatrix,
    config: MatrixPipelineConfig,
) -> np.ndarray:
    basic_mask = build_basic_filter_mask(market, config.basic_filter)
    if config.asset_mask is None:
        return basic_mask

    asset_mask = np.asarray(config.asset_mask, dtype=bool)
    if asset_mask.shape != (market.shape[1],):
        raise ValueError("matrix strategy asset mask length does not match market")
    return basic_mask & asset_mask[None, :]


def build_basic_filter_mask(market: MarketDataMatrix, config: dict) -> np.ndarray:
    if not config or not config.get("enabled", True):
        return np.ones(market.shape, dtype=bool)

    mask = np.ones(market.shape, dtype=bool)
    # Absolute bounds compare the price traded that day, so they need raw_close.
    close = market.fields.get("raw_close")
    raw_bounds = sorted(key for key in _RAW_PRICE_BOUND_KEYS if config.get(key) is not None)
    if raw_bounds and close is None:
        raise ValueError(f"basic filter bounds {raw_bounds} need field raw_close, which the market data lacks")
    if config.get("price_min") is not None:
        mask &= close >= float(config["price_min"])
    if config.get("price_max") is not None:
        mask &= close <= float(config["price_max"])

    _apply_bound(mask, market, config, "market_cap", "total_shares", scale=close)
    _apply_bound(mask, market, config, "float_cap", "float_shares", scale=close)
    _apply_bound(mask, market, config, "amount", "amount")
    _apply_bound(mask, market, config, "turnover", "turnover_rate")

    if config.get("exclude_st"):
        asset_mask = np.array(
            [
                not any(token in name.upper() for token in ("ST", "*ST", "退"))
                for name in market.names
            ],
            dtype=bool,
        )
        mask &= asset_mask[None, :]

    boards = config.get("boards")
    if isinstance(boards, list) and boards:
        board_mask = np.zeros(len(market.symbols), dtype=bool)
        for asset_id, symbol in enumerate(market.symbols):
            board_mask[asset_id] = _symbol_in_boards(symbol, boards)
        mask &= board_mask[None, :]

    eligible = eligible_from(market.symbols, exclude_new_days(config))
    if eligible:
        days = np.array([label[:10] for label in market.timestamp_labels], dtype="datetime64[D]")
        for asset_id, symbol in enumerate(market.symbols):
            first = eligible.get(symbol)
            if first is not None:
                mask[:, asset_id] &= days >= np.datetime64(first, "D")
    return mask


def scores_by_order_field(scoring: Mapping[str, float], order_by: str | None) -> bool:
    """Whether build_matrix_score scores by the order_by field, signed so that higher ranks first."""
    weights = [float(weight) for weight in scoring.values() if float(weight) != 0.0]
    return not (weights and sum(weights) > 0) and bool(order_by) and order_by != "score"


def build_matrix_score(
    market: MarketDataMatrix,
    universe: np.ndarray,
    scoring: dict[str, float],
    order_by: str | None,
    descending: bool,
    *,
    fallback: np.ndarray,
    directions: Mapping[str, str] | None = None,
) -> np.ndarray:
    """Score candidates in ``universe``; a candidate missing any scoring input scores NaN, others 0."""
    weights = {name: float(weight) for name, weight in scoring.items() if float(weight) != 0.0}
    total_weight = sum(weights.values())
    if weights and total_weight > 0:
        score = np.zeros(market.shape, dtype=np.float32)
        all_finite = universe.copy()
        row_count, asset_count = market.shape
        chunk_size = min(_SCORE_ASSET_CHUNK_SIZE, asset_count)
        finite_scratch = np.empty((row_count, chunk_size), dtype=bool)
        work_mask = np.empty((row_count, chunk_size), dtype=bool)
        value_scratch = np.empty((row_count, chunk_size), dtype=np.float32)
        for name, weight in weights.items():
            values = matrix_feature(market, name)
            row_min = np.full(row_count, np.inf, dtype=np.float32)
            row_max = np.full(row_count, -np.inf, dtype=np.float32)
            for start in range(0, asset_count, chunk_size):
                stop = min(start + chunk_size, asset_count)
                width = stop - start
                finite = finite_scratch[:, :width]
                values_chunk = values[:, start:stop]
                np.isfinite(values_chunk, out=finite)
                all_finite[:, start:stop] &= finite
                finite &= universe[:, start:stop]
                np.minimum(
                    row_min,
                    np.min(values_chunk, axis=1, where=finite, initial=np.inf),
                    out=row_min,
                )
                np.maximum(
                    row_max,
                    np.max(values_chunk, axis=1, where=finite, initial=-np.inf),
                    out=row_max,
                )
            row_range = row_max - row_min
            varying_rows = np.isfinite(row_range) & (row_range > 0)
            normalized_weight = np.float32(weight / total_weight)
            for start in range(0, asset_count, chunk_size):
                stop = min(start + chunk_size, asset_count)
                width = stop - start
                finite = finite_scratch[:, :width]
                mask = work_mask[:, :width]
                scratch = value_scratch[:, :width]
                values_chunk = values[:, start:stop]
                np.isfinite(values_chunk, out=finite)
                finite &= universe[:, start:stop]
                scratch.fill(0.0)
                np.logical_and(finite, varying_rows[:, None], out=mask)
                np.subtract(values_chunk, row_min[:, None], out=scratch, where=mask)
                np.divide(scratch, row_range[:, None], out=scratch, where=mask)
                np.logical_and(finite, ~varying_rows[:, None], out=mask)
                scratch[mask] = np.float32(0.5)
                if (directions or {}).get(name) == SCORING_DIRECTION_LOW:
                    scratch[finite] = np.float32(1.0) - scratch[finite]
                scratch *= normalized_weight
                score[:, start:stop] += scratch
        score *= np.float32(100.0)
        score[~universe] = 0.0
        score[universe & ~all_finite] = np.nan
        return score

    if scores_by_order_field(scoring, order_by):
        values = matrix_feature(market, order_by)
        result = np.zeros(market.shape, dtype=np.float32)
        direction = np.float32(1.0 if descending else -1.0)
        for start in range(0, market.shape[1], _SCORE_ASSET_CHUNK_SIZE):
            stop = min(start + _SCORE_ASSET_CHUNK_SIZE, market.shape[1])
            values_chunk = values[:, start:stop]
            candidates = universe[:, start:stop]
            finite = np.isfinite(values_chunk)
            np.multiply(
                values_chunk,
                direction,
                out=result[:, start:stop],
                where=candidates & finite,
            )
            result[:, start:stop][candidates & ~finite] = np.nan
        return result
    result = np.zeros(market.shape, dtype=np.float32)
    np.copyto(result, fallback, where=universe)
    return result


_MATRIX_COMPUTED_FEATURES = frozenset({
    "prev_close", "change_pct", "change_amount", "amplitude",
    "boll_upper", "boll_lower", "boll_position", "boll_width",
    "high_60d", "low_60d", "annual_vol_20d",
    "macd_dif", "macd_dea", "macd_hist",
    "macd_dif_pct", "macd_dea_pct", "macd_hist_pct",
    "kdj_k", "kdj_d", "kdj_j", "atr_14", "atr_pct",
    "vol_ma5", "vol_ma10", "vol_ratio_5d", "vol_ratio_10d", "vol_trend_5_10",
    "turnover_ratio_5d", "log_amount", "amount_ratio_5d",
    "gap_return", "intraday_return", "close_position",
    "distance_to_high_60d", "distance_from_low_60d",
    "max_ret_20d", "ret_skew_20d", "up_days_20d",
    "amihud_20d", "turnover_z_60d", "vol_price_corr_20d",
    "vwap_bias", "vol_trend_5_60",
    "limit_up_count_20d", "limit_up_count_60d",
})


def matrix_feature(market: MarketDataMatrix, name: str) -> np.ndarray:
    if name in {"open", "high", "low", "close", "volume"} or name in market.fields:
        return market.field(name)
    supported = (
        name in _MATRIX_COMPUTED_FEATURES
        or (name.startswith("ma") and name.endswith("_bias") and name[2:-5].isdigit())
        or (name.startswith("ema") and name.endswith("_bias") and name[3:-5].isdigit())
        or (name.startswith("ma") and name[2:].isdigit())
        or (name.startswith("ema") and name[3:].isdigit())
        or (name.startswith("rsi_") and name[4:].isdigit())
        or (name.startswith("momentum_") and name.endswith("d"))
    )
    if not supported:
        raise ValueError(f"unsupported matrix feature: {name}")
    with _activate_valid_bar_index(market.valid_bars):
        return _compute_matrix_feature(market, name)


def session_coverage(
    market: MarketDataMatrix,
    name: str,
    rows: slice = slice(None),
) -> np.ndarray:
    """app.taiwan.share_capital_store.session_coverage of feature *name* on each session in *rows*.

    Strategies reach the rule here because their imports are limited to this module.
    """
    return _symbols_session_coverage(
        name, market.symbols, market.close[rows], matrix_feature(market, name)[rows]
    )


def _traded_amount(market: MarketDataMatrix) -> np.ndarray:
    """``amount`` with live estimates as NaN; only liquidity filters may use an estimate."""
    amount = market.field("amount")
    estimated = market.fields.get(AMOUNT_ESTIMATED)
    if estimated is None:
        return amount
    return np.where(estimated > 0.5, np.float32(np.nan), amount).astype(np.float32, copy=False)


def _previous_bar_values(values: np.ndarray, present: np.ndarray) -> np.ndarray:
    """Value on each asset's previous present bar, NaN where that bar's value is missing."""
    time_count, asset_count = values.shape
    rows = np.repeat(np.arange(time_count, dtype=np.float32)[:, None], asset_count, axis=1)
    previous_row = valid_shift(rows, 1, present)
    out = np.full(values.shape, np.nan, dtype=np.float32)
    has_previous = np.isfinite(previous_row)
    out[has_previous] = values[previous_row[has_previous].astype(np.int64), np.nonzero(has_previous)[1]]
    return out


def _window_has_missing(values: np.ndarray, present: np.ndarray, window: int) -> np.ndarray:
    """True where the last *window* present bars hold a missing value, as Polars rolling nulls do."""
    missing = (present & ~np.isfinite(values)).astype(np.float32)
    share = valid_rolling_mean(missing, present, window)
    return np.isfinite(share) & (share > 0)


def _reference_previous_close(market: MarketDataMatrix) -> np.ndarray:
    """Previous close of each bar on the base app.indicators.pipeline._reference_prev_close sets."""
    close_valid = np.isfinite(market.close)
    previous = valid_shift(market.close, 1, close_valid)
    reference = market.fields.get("ref_prev_close")
    base_missing = market.fields.get(BASE_MISSING_COLUMN)
    flagged = np.zeros(market.shape, dtype=bool)
    if reference is not None:
        flagged |= np.isfinite(reference)
    if base_missing is not None:
        flagged |= base_missing > 0.5
    flagged &= close_valid
    if not flagged.any():
        return previous
    rows, assets = np.nonzero(flagged)
    columns = {"close": market.close[rows, assets], "_previous": previous[rows, assets]}
    raw_close = market.fields.get("raw_close")
    if reference is not None and raw_close is not None:
        columns["ref_prev_close"] = reference[rows, assets]
        columns["raw_close"] = raw_close[rows, assets]
    if base_missing is not None:
        columns[BASE_MISSING_COLUMN] = base_missing[rows, assets] > 0.5
    frame = pl.DataFrame(columns).fill_nan(None)
    base = frame.select(
        _reference_prev_close(frame, pl.col("_previous")).cast(pl.Float32)
    ).to_series().to_numpy()
    out = np.array(previous, dtype=np.float32, copy=True)
    out[rows, assets] = base
    return out


def _close_to_close_return(market: MarketDataMatrix) -> np.ndarray:
    """Daily return the scoring factors use (app.strategy.scoring._daily_change_expr)."""
    return _valid_return_over_bars(market.close, np.isfinite(market.close), 1)


def _compute_matrix_feature(market: MarketDataMatrix, name: str) -> np.ndarray:
    close_valid = np.isfinite(market.close)
    if name == "prev_close":
        return _reference_previous_close(market)
    if name == "change_pct":
        return _return_from(market.close, _reference_previous_close(market))
    if name == "change_amount":
        previous = _reference_previous_close(market)
        out = np.full(market.shape, np.nan, dtype=np.float32)
        np.subtract(market.close, previous, out=out, where=np.isfinite(previous))
        return out
    if name == "amplitude":
        previous = _reference_previous_close(market)
        out = np.full(market.shape, np.nan, dtype=np.float32)
        np.divide(
            market.high - market.low,
            previous,
            out=out,
            where=np.isfinite(previous) & (previous > 0),
        )
        return out
    if name.startswith("momentum_") and name.endswith("d"):
        try:
            bars = int(name.removeprefix("momentum_").removesuffix("d"))
        except ValueError as exc:
            raise ValueError(f"unsupported matrix feature: {name}") from exc
        return _valid_return_over_bars(market.close, close_valid, bars)
    if name.startswith("ma") and name.endswith("_bias"):
        period = int(name.removeprefix("ma").removesuffix("_bias"))
        return _matrix_relative(market.close, valid_rolling_mean(market.close, close_valid, period))
    if name.startswith("ema") and name.endswith("_bias"):
        period = int(name.removeprefix("ema").removesuffix("_bias"))
        return _matrix_relative(market.close, _matrix_ema(market.close, close_valid, period))
    if name.startswith("ma") and name[2:].isdigit():
        return valid_rolling_mean(market.close, close_valid, int(name[2:]))
    if name.startswith("ema") and name[3:].isdigit():
        return _matrix_ema(market.close, close_valid, int(name[3:]))
    if name in {"macd_dif", "macd_dea", "macd_hist"}:
        dif, dea = _matrix_macd(market.close, close_valid)
        if name == "macd_dif":
            return dif
        if name == "macd_dea":
            return dea
        return (dif - dea).astype(np.float32, copy=False)
    if name in {"macd_dif_pct", "macd_dea_pct", "macd_hist_pct"}:
        source = matrix_feature(market, name.removesuffix("_pct"))
        return _matrix_ratio(source, market.close)
    if name in {"vol_ratio_5d", "vol_ratio_10d"}:
        window = 5 if name == "vol_ratio_5d" else 10
        volume_valid = close_valid & np.isfinite(market.volume)
        previous_volume = valid_shift(market.volume, 1, volume_valid)
        previous_mean = valid_rolling_mean(
            previous_volume,
            np.isfinite(previous_volume),
            window,
        )
        return _matrix_ratio(market.volume, previous_mean)
    if name in {"vol_ma5", "vol_ma10"}:
        window = 5 if name == "vol_ma5" else 10
        volume_valid = close_valid & np.isfinite(market.volume)
        return valid_rolling_mean(market.volume, volume_valid, window)
    if name == "vol_trend_5_10":
        return _matrix_relative(
            matrix_feature(market, "vol_ma5"),
            matrix_feature(market, "vol_ma10"),
        )
    if name == "turnover_ratio_5d":
        turnover = market.field("turnover_rate")
        previous = _previous_bar_values(turnover, close_valid)
        mean = np.where(
            _window_has_missing(previous, close_valid, 5),
            np.float32(np.nan),
            valid_rolling_mean(previous, np.isfinite(previous), 5),
        ).astype(np.float32, copy=False)
        return _matrix_relative(turnover, mean)
    if name == "log_amount":
        amount = market.field("amount")
        out = np.full(market.shape, np.nan, dtype=np.float32)
        valid = close_valid & np.isfinite(amount) & (amount >= 0)
        np.log(amount + np.float32(1.0), out=out, where=valid)
        return out
    if name == "amount_ratio_5d":
        amount = _traded_amount(market)
        valid = close_valid & np.isfinite(amount)
        previous = valid_shift(amount, 1, valid)
        return _matrix_relative(
            amount,
            valid_rolling_mean(previous, np.isfinite(previous), 5),
        )
    if name == "boll_upper" or name == "boll_lower":
        middle = valid_rolling_mean(market.close, close_valid, 20)
        deviation = valid_rolling_std(market.close, close_valid, 20, ddof=0)
        offset = np.float32(2.0) * deviation
        return middle + offset if name == "boll_upper" else middle - offset
    if name == "boll_position":
        return _matrix_ratio(
            market.close - matrix_feature(market, "boll_lower"),
            matrix_feature(market, "boll_upper") - matrix_feature(market, "boll_lower"),
        )
    if name == "boll_width":
        return _matrix_ratio(
            matrix_feature(market, "boll_upper") - matrix_feature(market, "boll_lower"),
            matrix_feature(market, "ma20"),
        )
    if name == "high_60d":
        return valid_rolling_max(market.close, close_valid, 60)
    if name == "low_60d":
        return valid_rolling_min(market.close, close_valid, 60)
    if name == "annual_vol_20d":
        previous = valid_shift(market.close, 1, close_valid).astype(np.float64)
        ratio = np.full(market.shape, np.nan, dtype=np.float64)
        np.divide(market.close, previous, out=ratio, where=np.isfinite(previous) & (previous > 0))
        daily = np.full(market.shape, np.nan, dtype=np.float64)
        np.log(ratio, out=daily, where=ratio > 0)
        return valid_rolling_std(
            daily,
            np.isfinite(daily),
            20,
            ddof=1,
        ) * np.float32(252 ** 0.5)
    if name in {"kdj_k", "kdj_d", "kdj_j"}:
        low_valid = close_valid & np.isfinite(market.low)
        high_valid = close_valid & np.isfinite(market.high)
        low_9 = valid_rolling_min(market.low, low_valid, 9)
        high_9 = valid_rolling_max(market.high, high_valid, 9)
        rsv = _matrix_ratio(market.close - low_9, high_9 - low_9) * np.float32(100.0)
        flat = np.isfinite(low_9) & np.isfinite(high_9) & (high_9 == low_9)
        offsets, cells = _valid_bar_runs(market.close, close_valid)
        k, d = kd_values(_gather(rsv, np.isfinite(rsv), cells), offsets, flat[cells])
        kdj = {"kdj_k": k, "kdj_d": d, "kdj_j": 3.0 * k - 2.0 * d}
        return _scatter(market.shape, cells, kdj[name])
    if name in {"atr_14", "atr_pct"}:
        previous = valid_shift(market.close, 1, close_valid)
        true_range = np.fmax.reduce([
            market.high - market.low,
            np.abs(market.high - previous),
            np.abs(market.low - previous),
        ]).astype(np.float32, copy=False)
        true_range[~close_valid | ~np.isfinite(previous)] = np.nan
        offsets, cells = _valid_bar_runs(market.close, close_valid)
        atr = _scatter(
            market.shape,
            cells,
            wilder_values(_gather(true_range, np.isfinite(true_range), cells), offsets, 14),
        )
        return atr if name == "atr_14" else _matrix_ratio(atr, market.close)
    if name.startswith("rsi_") and name[4:].isdigit():
        window = int(name[4:])
        delta = market.close - valid_shift(market.close, 1, close_valid)
        changed = np.isfinite(delta)
        offsets, cells = _valid_bar_runs(market.close, close_valid)
        gains = _gather(np.fmax(delta, np.float32(0.0)), changed, cells)
        losses = _gather(np.fmax(-delta, np.float32(0.0)), changed, cells)
        return _scatter(market.shape, cells, rsi_values(gains, losses, offsets, window))
    if name == "gap_return":
        return _matrix_relative(market.open, valid_shift(market.close, 1, close_valid))
    if name == "intraday_return":
        return _matrix_relative(market.close, market.open)
    if name == "close_position":
        return _matrix_ratio(market.close - market.low, market.high - market.low)
    if name == "distance_to_high_60d":
        return _matrix_relative(market.close, matrix_feature(market, "high_60d"))
    if name == "distance_from_low_60d":
        return _matrix_relative(market.close, matrix_feature(market, "low_60d"))
    if name == "max_ret_20d":
        daily = _close_to_close_return(market)
        return valid_rolling_max(daily, np.isfinite(daily), 20)
    if name == "ret_skew_20d":
        return _matrix_rolling_skew(_close_to_close_return(market), 20)
    if name == "up_days_20d":
        daily = _close_to_close_return(market)
        up = np.where(daily > 0, np.float32(1.0), np.float32(0.0)).astype(np.float32)
        up[~np.isfinite(daily)] = np.nan
        return valid_rolling_sum(up, np.isfinite(up), 20)
    if name == "amihud_20d":
        daily = _close_to_close_return(market)
        amount = _traded_amount(market)
        amount_yi = amount / np.float32(1e8)
        illiquidity = _matrix_ratio(np.abs(daily), amount_yi)
        return valid_rolling_mean(
            illiquidity,
            close_valid & np.isfinite(illiquidity),
            20,
        )
    if name == "turnover_z_60d":
        turnover = market.field("turnover_rate")
        previous = _previous_bar_values(turnover, close_valid)
        baseline_valid = np.isfinite(previous)
        mean = valid_rolling_mean(previous, baseline_valid, 60)
        std = valid_rolling_std(previous, baseline_valid, 60, ddof=1)
        deviation = _matrix_ratio(turnover - mean, std)
        deviation[np.isfinite(std) & (std <= 0)] = np.nan
        deviation[_window_has_missing(previous, close_valid, 60)] = np.nan
        return deviation
    if name == "vol_price_corr_20d":
        daily = _close_to_close_return(market)
        return _matrix_rolling_corr(daily, market.volume, close_valid, 20)
    if name == "vwap_bias":
        amount = _traded_amount(market)
        valid = close_valid & np.isfinite(amount) & (market.volume > 0) & (amount > 0)
        vwap = np.full(market.shape, np.nan, dtype=np.float32)
        np.divide(amount, market.volume, out=vwap, where=valid)
        # amount is unadjusted, so the VWAP is compared with the raw close, never the adjusted one.
        # Volume restated by a later share-count event still shifts the VWAP by that event's ratio.
        raw_close = market.fields.get("raw_close")
        if raw_close is None:
            return np.full(market.shape, np.nan, dtype=np.float32)
        return _matrix_relative(raw_close, vwap)

    if name == "vol_trend_5_60":
        volume_valid = close_valid & np.isfinite(market.volume)
        fast = valid_rolling_mean(market.volume, volume_valid, 5)
        slow = valid_rolling_mean(market.volume, volume_valid, 60)
        return _matrix_relative(fast, slow)
    if name in {"limit_up_count_20d", "limit_up_count_60d"}:
        window = 20 if name == "limit_up_count_20d" else 60
        consecutive = market.field("consecutive_limit_ups")
        hits = np.where(np.isfinite(consecutive) & (consecutive > 0), np.float32(1.0), np.float32(0.0))
        hits = hits.astype(np.float32)
        return valid_rolling_sum(hits, close_valid, window)
    raise ValueError(f"unsupported matrix feature: {name}")


def _matrix_rolling_skew(values: np.ndarray, window: int) -> np.ndarray:
    valid = np.isfinite(values)
    first = valid_rolling_mean(values, valid, window)
    second = valid_rolling_mean(np.square(values, dtype=np.float32), valid, window)
    third = valid_rolling_mean(
        (values * values * values).astype(np.float32), valid, window
    )
    variance = second - np.square(first, dtype=np.float32)
    central_third = (
        third
        - np.float32(3.0) * first * second
        + np.float32(2.0) * np.power(first, 3)
    )
    out = _matrix_ratio(central_third, np.sqrt(np.power(variance, 3)))
    out[np.isfinite(variance) & (variance <= 0)] = np.nan
    return out


def _matrix_rolling_corr(
    left: np.ndarray, right: np.ndarray, valid_mask: np.ndarray, window: int
) -> np.ndarray:
    valid = valid_mask & np.isfinite(left) & np.isfinite(right)
    product = (left * right).astype(np.float32)
    mean_left = valid_rolling_mean(left, valid, window)
    mean_right = valid_rolling_mean(right, valid, window)
    mean_product = valid_rolling_mean(product, valid, window)
    mean_left_sq = valid_rolling_mean(
        np.square(left, dtype=np.float32), valid, window
    )
    mean_right_sq = valid_rolling_mean(
        np.square(right, dtype=np.float32), valid, window
    )
    covariance = mean_product - mean_left * mean_right
    variance_left = mean_left_sq - np.square(mean_left, dtype=np.float32)
    variance_right = mean_right_sq - np.square(mean_right, dtype=np.float32)
    denominator = np.sqrt(variance_left * variance_right)
    out = _matrix_ratio(covariance, denominator)
    degenerate = (
        np.isfinite(variance_left)
        & np.isfinite(variance_right)
        & ((variance_left <= 0) | (variance_right <= 0))
    )
    out[degenerate] = np.nan
    return out


def _valid_bar_runs(
    values: np.ndarray, valid: np.ndarray
) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]:
    """Run offsets and (row, asset) cells of every asset's valid bars, one run per asset.

    The runs are the layout the shared indicator definitions of app.indicators.pipeline take.
    """
    index = _resolve_valid_bar_index(values, valid, None)
    assets = np.repeat(np.arange(values.shape[1]), np.diff(index.offsets))
    return index.offsets, (index.rows, assets)


def _gather(
    values: np.ndarray, valid: np.ndarray, cells: tuple[np.ndarray, np.ndarray]
) -> np.ndarray:
    return np.where(valid[cells], values[cells], np.nan).astype(np.float64)


def _scatter(
    shape: tuple[int, int], cells: tuple[np.ndarray, np.ndarray], values: np.ndarray
) -> np.ndarray:
    out = np.full(shape, np.nan, dtype=np.float32)
    out[cells] = values
    return out


def _matrix_ema(values: np.ndarray, valid: np.ndarray, period: int) -> np.ndarray:
    offsets, cells = _valid_bar_runs(values, valid)
    return _scatter(values.shape, cells, ema_values(_gather(values, valid, cells), offsets, period))


def _matrix_macd(values: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    offsets, cells = _valid_bar_runs(values, valid)
    dif, dea = macd_values(_gather(values, valid, cells), offsets)
    return _scatter(values.shape, cells, dif), _scatter(values.shape, cells, dea)


def _matrix_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = np.full(numerator.shape, np.nan, dtype=np.float32)
    valid = np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0)
    np.divide(numerator, denominator, out=out, where=valid)
    return out


def _matrix_relative(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = _matrix_ratio(numerator, denominator)
    out[np.isfinite(out)] -= np.float32(1.0)
    return out


def apply_time_masks(
    signals: SignalMatrix,
    entry_time_mask: np.ndarray,
    exit_time_mask: np.ndarray,
) -> SignalMatrix:
    if entry_time_mask.shape != (signals.shape[0],) or exit_time_mask.shape != (signals.shape[0],):
        raise ValueError("time mask length does not match SignalMatrix")
    entry_mask = np.asarray(entry_time_mask, dtype=bool)
    exit_mask = np.asarray(exit_time_mask, dtype=bool)
    entry = np.array(signals.entry, dtype=np.uint8, copy=True)
    exit_ = np.array(signals.exit, dtype=np.uint8, copy=True)
    entry[~entry_mask] = 0
    exit_[~exit_mask] = 0
    entry_codes = np.array(signals.entry_signal_code, dtype=np.int16, copy=True)
    exit_codes = np.array(signals.exit_signal_code, dtype=np.int16, copy=True)
    entry_codes[entry == 0] = -1
    exit_codes[exit_ == 0] = -1
    return _finalize_signal_matrix(
        entry,
        exit_,
        signals.score,
        entry_codes,
        exit_codes,
        entry_signal_ids=signals.entry_signal_ids,
        exit_signal_ids=signals.exit_signal_ids,
    )


def taiwan_limit_rule_exclusions(symbols: Sequence[str]) -> list[dict[str, str]]:
    """Taiwan symbols whose daily price-limit rule is unknown, with the reason.

    Such symbols cannot be judged limit-locked or tradable, so a backtest must not
    trade them. A master that fails to load raises instead of excluding everything.
    """
    taiwan = sorted({
        str(symbol)
        for symbol in symbols
        if str(symbol).endswith(TAIWAN_SUFFIXES)
    })
    if not taiwan:
        return []

    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import ListingDateUnknownError, MarketProfileBridge

    excluded: list[dict[str, str]] = []
    try:
        master = get_security_master()
        instruments = {symbol: master.get_instrument(symbol) for symbol in taiwan}
    except Exception as exc:
        raise ValueError(f"Taiwan Security Master unavailable: {exc}") from exc
    for symbol, instrument in instruments.items():
        if instrument is None:
            excluded.append({"symbol": symbol, "reason": LIMIT_RULE_NOT_IN_SECURITY_MASTER})
            continue
        try:
            MarketProfileBridge.get_price_limit_pct(instrument)
        except ListingDateUnknownError:
            excluded.append({"symbol": symbol, "reason": LIMIT_RULE_LISTING_DATE_UNKNOWN})
        except ValueError:
            excluded.append({"symbol": symbol, "reason": LIMIT_RULE_UNCONFIRMED})
    return excluded


def asset_mask_without(
    symbols: Sequence[str],
    excluded: Collection[str],
) -> np.ndarray | None:
    """Per-asset inclusion mask, or None when no listed symbol is excluded."""
    mask = np.fromiter(
        (str(symbol) not in excluded for symbol in symbols),
        dtype=bool,
        count=len(symbols),
    )
    return None if mask.all() else mask


def _valid_return_over_bars(
    values: np.ndarray,
    valid_mask: np.ndarray,
    bars: int,
) -> np.ndarray:
    return _return_from(values, valid_shift(values, bars, valid_mask))


def _return_from(values: np.ndarray, previous: np.ndarray) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=np.float32)
    np.divide(values, previous, out=out, where=np.isfinite(previous) & (previous != 0))
    out -= np.float32(1.0)
    return out


def _apply_bound(
    mask: np.ndarray,
    market: MarketDataMatrix,
    config: dict,
    prefix: str,
    field_name: str,
    *,
    scale: np.ndarray | None = None,
) -> None:
    """Keep rows whose value lies inside the configured bound; a missing value fails the bound."""
    minimum = config.get(f"{prefix}_min")
    maximum = config.get(f"{prefix}_max")
    if minimum is None and maximum is None:
        return
    values = market.fields.get(field_name)
    if values is None:
        raise ValueError(
            f"basic filter {prefix} bound needs field {field_name}, which the market data lacks"
        )
    if scale is not None:
        values = scale * values
    if minimum is not None:
        mask &= values >= float(minimum)
    if maximum is not None:
        mask &= values <= float(maximum)


def _symbol_in_boards(symbol: str, boards: list[str]) -> bool:
    for board in boards:
        if board in ("TWSE", "上市") and symbol.endswith(".TWSE"):
            return True
        if board in ("TPEX", "上櫃", "柜买") and symbol.endswith(".TPEX"):
            return True
    return False

