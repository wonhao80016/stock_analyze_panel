"""Daily volume units: Taiwan daily volume is stored in shares."""

from __future__ import annotations

import polars as pl

TAIWAN_SUFFIXES = (".TWSE", ".TPEX")


def volume_shares_expr() -> pl.Expr:
    """Daily volume in shares."""
    return pl.col("volume")


def turnover_rate_pct_expr() -> pl.Expr:
    """Return percentage turnover from share volume and share-count capital."""
    return volume_shares_expr() * 100.0 / pl.col("float_shares")
