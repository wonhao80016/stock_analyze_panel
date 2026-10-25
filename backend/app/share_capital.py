"""Turnover denominators by session; Taiwan publishes no share history, so only today's count is used."""
from __future__ import annotations

from datetime import date

import polars as pl


def apply_historical_float_shares(rows: pl.DataFrame, *, today: date) -> pl.DataFrame:
    """*rows* with float_shares kept on *today* only and null on every earlier session.

    The share-capital file holds the current count, so applying it backward would price an old
    session with a later count (CONTRIBUTING 3.4).
    """
    required = {"symbol", "date", "float_shares"}
    if rows.is_empty() or not required <= set(rows.columns):
        return rows
    trade_date = pl.col("date").cast(pl.Date, strict=False)
    return rows.with_columns(
        pl.when(trade_date != pl.lit(today))
        .then(None)
        .otherwise(pl.col("float_shares"))
        .cast(pl.Float64)
        .alias("float_shares")
    )
