"""A matrix loaded for a few symbols counts sessions on the market's trading calendar.

Session ids come from the partition dates, not from the dates the selected symbols traded, so a
symbol's missed sessions stay visible and lorentzian_knn classifies it in a few-symbol backtest
exactly as in a full-market one.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import (
    MatrixPipelineConfig,
    MatrixStrategyPipeline,
    load_market_data_matrix_from_parquet,
)
from app.strategy.builtin import lorentzian_knn

pytestmark = pytest.mark.usefixtures("deny_network")

SESSIONS = 460
REGULAR = ("1101.TWSE", "1102.TWSE", "1103.TWSE")
SPARSE = "1104.TWSE"
LONG_GAP = "1105.TWSE"
SHORT_GAP = "1106.TWSE"
PARAMS = {"min_prediction": 0.0, "use_volatility_filter": False, "use_regime_filter": False}
NO_FILTER = MatrixPipelineConfig(
    basic_filter={"enabled": False}, scoring={}, order_by=None, descending=True
)


def _days() -> list[date]:
    out, day = [], date(2023, 1, 2)
    while len(out) < SESSIONS:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _write_partitions(root) -> list[date]:
    """SPARSE trades every other session from 300 on; LONG_GAP misses sessions 200-259, more
    than lorentzian_knn allows, and SHORT_GAP misses sessions 100-129, fewer than it allows.
    """
    rng = np.random.default_rng(20261009)
    days = _days()
    drift = np.array([0.004, 0.0, -0.006, 0.002])[(np.arange(SESSIONS) // 30) % 4]
    frames = []
    for symbol in (*REGULAR, SPARSE, LONG_GAP, SHORT_GAP):
        close = 50.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.018, SESSIONS))
        frame = pl.DataFrame(
            {
                "symbol": [symbol] * SESSIONS,
                "date": days,
                "open": close * (1.0 + rng.normal(0.0, 0.004, SESSIONS)),
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.lognormal(14.0, 0.35, SESSIONS),
                "signal_limit_up": [False] * SESSIONS,
                "signal_limit_down": [False] * SESSIONS,
            }
        )
        if symbol == SPARSE:
            frame = frame.filter(~pl.col("date").is_in(days[300::2]))
        if symbol == LONG_GAP:
            frame = frame.filter(~pl.col("date").is_in(days[200:260]))
        if symbol == SHORT_GAP:
            frame = frame.filter(~pl.col("date").is_in(days[100:130]))
        frames.append(frame)
    panel = pl.concat(frames)
    for current in days:
        partition = root / f"date={current.isoformat()}"
        partition.mkdir(parents=True)
        panel.filter(pl.col("date") == current).write_parquet(partition / "part.parquet")
    return days


@pytest.mark.parametrize("disk_cache", [False, True], ids=["direct", "disk_cache"])
def test_few_symbol_matrix_classifies_gappy_symbols_like_the_full_market(tmp_path, disk_cache):
    root = tmp_path / "kline_daily_enriched"
    days = _write_partitions(root)

    def load(symbols):
        return load_market_data_matrix_from_parquet(
            root,
            days[0],
            days[-1],
            field_columns=set(),
            symbols=symbols,
            cache_root=tmp_path / "matrix_cache" if disk_cache else None,
        )

    def run(market):
        return MatrixStrategyPipeline().run(
            lorentzian_knn.MATRIX_STRATEGY, market, PARAMS, NO_FILTER
        )

    full = load(None)
    full_signals = run(full)
    for chosen in ([SPARSE], [LONG_GAP], [SHORT_GAP], [SPARSE, SHORT_GAP]):
        few = load(chosen)
        signals = run(few)
        rows = [full.timestamp_labels.index(label) for label in few.timestamp_labels]
        np.testing.assert_array_equal(few.session_ids, full.session_ids[rows])
        for symbol in chosen:
            asset, full_asset = few.symbols.index(symbol), full.symbols.index(symbol)
            message = f"{symbol} loaded with {chosen}"
            np.testing.assert_array_equal(
                signals.entry[:, asset], full_signals.entry[rows, full_asset], err_msg=message
            )
            np.testing.assert_array_equal(
                signals.exit[:, asset], full_signals.exit[rows, full_asset], err_msg=message
            )

    assert full_signals.entry[:, full.symbols.index(SHORT_GAP)].any()
