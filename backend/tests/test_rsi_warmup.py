"""RSI is null until a symbol has accumulated n price changes, then follows Wilder's definition."""
from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators.pipeline import compute_indicators


def _bars(symbol: str, n: int, phase: float = 0.0) -> pl.DataFrame:
    closes = [round(100.0 + i * 0.4 + 4.0 * math.sin(i / 2.5 + phase), 4) for i in range(n)]
    start = date(2026, 1, 5)
    return pl.DataFrame({
        "symbol": [symbol] * n,
        "date": [start + timedelta(days=i) for i in range(n)],
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000.0 + i for i in range(n)],
    })


def _reference_rsi(closes: list[float], n: int) -> list[float | None]:
    """Wilder RSI: the mean of the first n gains and losses, then (prev * (n - 1) + change) / n."""
    out: list[float | None] = [None] * len(closes)
    changes = [closes[t] - closes[t - 1] for t in range(1, len(closes))]
    if len(changes) < n:
        return out
    gain = sum(max(c, 0.0) for c in changes[:n]) / n
    loss = sum(max(-c, 0.0) for c in changes[:n]) / n
    for t in range(n, len(closes)):
        if t > n:
            gain = (gain * (n - 1) + max(changes[t - 1], 0.0)) / n
            loss = (loss * (n - 1) + max(-changes[t - 1], 0.0)) / n
        out[t] = 100 - 100 / (1 + gain / (loss if loss != 0 else 1e-12))
    return out


def test_rsi_is_null_until_n_changes_then_follows_wilders_definition():
    long_bars = _bars("LONG.TWSE", 60)
    out = compute_indicators(long_bars, needed={"rsi_6", "rsi_14", "rsi_24"})
    closes = long_bars["close"].to_list()
    for n in (6, 14, 24):
        got = out[f"rsi_{n}"].to_list()
        want = _reference_rsi(closes, n)
        assert got[:n] == [None] * n
        assert got[n:] == pytest.approx(want[n:], rel=1e-12, abs=1e-9)


def test_a_newly_listed_symbol_never_shows_an_extreme_rsi():
    bars = pl.concat([_bars("OLD.TWSE", 60), _bars("NEW.TWSE", 10, phase=1.0)])
    out = compute_indicators(bars, needed={"rsi_6", "rsi_14", "rsi_24"})
    new = out.filter(pl.col("symbol") == "NEW.TWSE").sort("date")
    assert new["rsi_14"].null_count() == 10
    assert new["rsi_24"].null_count() == 10
    assert new["rsi_6"].null_count() == 6
    assert new["rsi_6"].drop_nulls().to_list() == pytest.approx(
        _reference_rsi(new["close"].to_list(), 6)[6:], rel=1e-12, abs=1e-9
    )


def test_masking_is_per_symbol_and_leaves_other_symbols_unchanged():
    old = _bars("OLD.TWSE", 60)
    alone = compute_indicators(old, needed={"rsi_14"}).sort("date")["rsi_14"].to_list()
    mixed = compute_indicators(pl.concat([old, _bars("NEW.TWSE", 10, phase=1.0)]), needed={"rsi_14"})
    assert mixed.filter(pl.col("symbol") == "OLD.TWSE").sort("date")["rsi_14"].to_list() == alone


def test_a_missing_close_does_not_count_as_a_price_change():
    bars = _bars("GAP.TWSE", 30).with_columns(
        pl.when(pl.col("date") == date(2026, 1, 12)).then(None).otherwise(pl.col("close")).alias("close")
    )
    out = compute_indicators(bars, needed={"rsi_14"}).sort("date")
    valid_changes = out["close"].diff().is_not_null().cast(pl.UInt32).cum_sum()
    assert out.filter(valid_changes < 14)["rsi_14"].null_count() == out.filter(valid_changes < 14).height
    assert out.filter(valid_changes >= 14)["rsi_14"].null_count() == 0
