"""Polars parquet helpers."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import polars as pl

DAILY_STORAGE_SCHEMA: dict[str, pl.DataType] = {
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

ENRICHED_STORAGE_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "raw_close": pl.Float64,
    "raw_high": pl.Float64,
    "raw_low": pl.Float64,
    "turnover_rate": pl.Float64,
    "consecutive_limit_ups": pl.UInt32,
    "consecutive_limit_downs": pl.UInt32,
    "quote_ts": pl.Int64,
    "ref_prev_close": pl.Float64,
    "ref_limit_up": pl.Float64,
    "ref_limit_down": pl.Float64,
    "ref_base_missing": pl.Boolean,
}


def scan_parquet_compat(source: Any, **kwargs: Any) -> pl.LazyFrame:
    """Scan partitioned parquet while tolerating additive schema changes."""
    kwargs.setdefault("missing_columns", "insert")
    kwargs.setdefault("extra_columns", "ignore")
    return pl.scan_parquet(source, **kwargs)


def scan_daily_parquet(source: Any, **kwargs: Any) -> pl.LazyFrame:
    kwargs.setdefault("schema", DAILY_STORAGE_SCHEMA)
    kwargs.setdefault("cast_options", pl.ScanCastOptions(integer_cast="allow-float"))
    return scan_parquet_compat(source, **kwargs)


def scan_enriched_parquet(source: Any, **kwargs: Any) -> pl.LazyFrame:
    kwargs.setdefault("schema", ENRICHED_STORAGE_SCHEMA)
    kwargs.setdefault("cast_options", pl.ScanCastOptions(integer_cast="allow-float"))
    return scan_parquet_compat(source, **kwargs)


def storage_only_rows(columns: Iterable[str]) -> pl.Expr:
    """True on enriched rows with no value in any of *columns* outside ENRICHED_STORAGE_SCHEMA.

    Such rows were written without the publish, e.g. by a storage-only live merge. The Taiwan publish
    fills its limit flags on every row, so even a listing's first session, with no indicator, has one.
    """
    derived = [name for name in columns if name not in ENRICHED_STORAGE_SCHEMA]
    if not derived:
        return pl.lit(True)
    return pl.all_horizontal(pl.col(name).is_null() for name in derived)
