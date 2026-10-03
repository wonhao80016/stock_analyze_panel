"""Volume-price patterns for the Taiwan enriched publish.

A session is traded when its row has OHLC and volume > 0; a pattern whose window holds an
untraded session is null. Kept out of ENRICHED_COLUMNS so strategy scoring never sees them.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import polars as pl

VOLUME_PATTERN_COLUMNS: tuple[str, ...] = (
    "vp_double",
    "vp_step_up",
    "vp_step_down",
    "vp_low",
    "vp_flat",
    "vp_up_stall",
)

LOW_WINDOW = 20
LOW_MIN_TRADED = 15
STALL_WINDOW = 5


def _lag(col: str, k: int) -> pl.Expr:
    """Value k trading days back, null when the symbol has no row on that exact day."""
    day = pl.col("_day")
    return pl.when(day.shift(k).over("symbol") == day - k).then(pl.col(col).shift(k).over("symbol"))


def _prior_sum(col: str, window: int) -> pl.Expr:
    """Sum over the *window* trading days before the row, excluding the row itself."""
    return (
        pl.col(col)
        .rolling_sum_by("_day", window_size=f"{window}i", closed="left")
        .over("symbol")
        .fill_null(0)
    )


def add_volume_patterns(enriched: pl.DataFrame, trading_days: Iterable[date]) -> pl.DataFrame:
    """Append the VOLUME_PATTERN_COLUMNS as nullable booleans, keeping row order.

    *trading_days* is the market calendar; rows dated outside it get nulls.
    """
    if enriched.is_empty():
        return enriched.with_columns([pl.lit(None, dtype=pl.Boolean).alias(c) for c in VOLUME_PATTERN_COLUMNS])

    days = sorted(set(trading_days))
    calendar = pl.DataFrame(
        {"date": days, "_day": list(range(len(days)))},
        schema={"date": enriched.schema["date"], "_day": pl.Int64},
    )
    traded = (
        (pl.col("volume") > 0)
        & pl.col("open").is_not_null()
        & pl.col("high").is_not_null()
        & pl.col("low").is_not_null()
        & pl.col("close").is_not_null()
    ).fill_null(False)

    frame = (
        enriched.select("symbol", "date", "open", "high", "low", "close", "volume", "change_pct")
        .with_row_index("_row")
        .join(calendar, on="date", how="inner")
        .sort(["symbol", "_day"])
        .with_columns(
            traded.cast(pl.Int64).alias("_traded"),
            pl.when(traded).then(pl.col("volume")).alias("_v"),
            pl.when(traded).then(pl.col("volume")).otherwise(0.0).alias("_v_sum"),
        )
        .with_columns(
            _lag("_v", 1).alias("_v1"),
            _lag("_v", 2).alias("_v2"),
            _lag("close", 2).alias("_c2"),
            _prior_sum("_traded", LOW_WINDOW).alias("_low_n"),
            _prior_sum("_v_sum", LOW_WINDOW).alias("_low_sum"),
            _prior_sum("_traded", STALL_WINDOW).alias("_stall_n"),
            _prior_sum("_v_sum", STALL_WINDOW).alias("_stall_sum"),
        )
    )

    v0, v1, v2 = pl.col("_v"), pl.col("_v1"), pl.col("_v2")
    c0, c2, o0, h0 = pl.col("close"), pl.col("_c2"), pl.col("open"), pl.col("high")
    three_traded = v0.is_not_null() & v1.is_not_null() & v2.is_not_null()
    vmax = pl.max_horizontal(v0, v1, v2)
    vmin = pl.min_horizontal(v0, v1, v2)
    low_mean = pl.col("_low_sum") / pl.col("_low_n")
    stall_mean = pl.col("_stall_sum") / STALL_WINDOW
    body = (c0 - o0).abs()
    upper = h0 - pl.max_horizontal(c0, o0)
    weak_candle = (
        (c0 < o0)
        | ((body > 0) & (upper > 2 * body))
        | ((pl.col("change_pct") >= 0) & (pl.col("change_pct") < 0.01))
    )

    patterns = frame.select(
        "_row",
        pl.when(v0.is_not_null() & v1.is_not_null()).then(v0 >= 2 * v1).alias("vp_double"),
        pl.when(three_traded).then((v2 < v1) & (v1 < v0) & (c0 > c2)).alias("vp_step_up"),
        pl.when(three_traded).then((v2 > v1) & (v1 > v0) & (c0 < c2)).alias("vp_step_down"),
        pl.when(v0.is_not_null() & (pl.col("_low_n") >= LOW_MIN_TRADED))
        .then(v0 < 0.6 * low_mean)
        .alias("vp_low"),
        pl.when(three_traded).then((vmax - vmin) / vmax < 0.15).alias("vp_flat"),
        pl.when(v0.is_not_null() & (pl.col("_stall_n") == STALL_WINDOW) & (stall_mean > 0))
        .then((v0 >= 1.5 * stall_mean) & weak_candle)
        .alias("vp_up_stall"),
    )

    return (
        enriched.with_row_index("_row")
        .join(patterns, on="_row", how="left")
        .sort("_row")
        .drop("_row")
    )
