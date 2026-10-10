"""Mining factor panels load enough history for the recursive indicators they use (stage 4, D7).

With SMA- and Wilder-seeded EMA/RSI/MACD/ATR, a factor needs its full warm-up of sessions before
its value no longer depends on where the loaded panel starts.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.factor import FACTOR_WARMUP_DAYS, FactorBacktestService
from app.backtest.mining_runtime import _load_compact_factor_panel

pytestmark = pytest.mark.usefixtures("deny_network")


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _weekdays(date(2022, 1, 3), 900)
MINING_START = DAYS[700]
MINING_END = DAYS[720]


def _history() -> pl.DataFrame:
    rows = []
    for rank, symbol in enumerate(("1101.TWSE", "2330.TWSE", "6488.TPEX")):
        for offset, day in enumerate(DAYS):
            close = (40.0 + 20.0 * rank) * (1.0 + 0.3 * math.sin(offset / (23.0 + 7 * rank)))
            close += 0.02 * offset
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": close * 0.995,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 1_000_000.0 * (1.5 + math.cos(offset / 5.0)),
                    "amount": close * 1_000_000.0,
                    "turnover_rate": None,
                }
            )
    return pl.DataFrame(rows).with_columns(pl.col("turnover_rate").cast(pl.Float64))


HISTORY = _history()


class _Engine:
    """Serves the stored history from the requested start, as the real loader does."""

    def __init__(self) -> None:
        self.starts: list[date] = []

    def load_panel(self, symbols, start, end, *, columns, asset_type, expected_generation):
        self.starts.append(start)
        return HISTORY.filter((pl.col("date") >= start) & (pl.col("date") <= end)).select(
            [c for c in columns if c in HISTORY.columns]
        )


def _compact(names: tuple[str, ...]) -> tuple[pl.DataFrame, _Engine]:
    engine = _Engine()
    panel = _load_compact_factor_panel(
        FactorBacktestService(engine),
        SimpleNamespace(symbols=None, start=MINING_START, end=MINING_END, asset_type="stock"),
        names,
        expected_generation="generation",
        cancel_check=None,
    )
    return panel, engine


def _from_full_history(names: tuple[str, ...]) -> pl.DataFrame:
    full = FactorBacktestService._compute_missing_factors(HISTORY, set(names), assume_sorted=True)
    return full.filter(pl.col("date") == MINING_START).select(["symbol", *names]).sort("symbol")


@pytest.mark.parametrize(
    ("factor", "tolerance"),
    [
        ("ema60_bias", 1e-4),
        ("ema20_bias", 1e-4),
        ("rsi_24", 1e-2),
        ("rsi_14", 1e-2),
        ("macd_hist_pct", 1e-4),
        ("macd_dea_pct", 1e-4),
        ("atr_pct", 1e-4),
    ],
)
def test_the_first_mining_session_has_the_full_history_factor_value(factor, tolerance):
    names = (factor,)

    panel, _ = _compact(names)

    first = panel.filter(pl.col("date") == MINING_START).select(["symbol", factor]).sort("symbol")
    expected = _from_full_history(names)
    assert first["symbol"].to_list() == expected["symbol"].to_list()
    for got, want in zip(first[factor].to_list(), expected[factor].to_list(), strict=True):
        assert got == pytest.approx(want, abs=tolerance)


def test_factors_without_a_recursive_warmup_keep_the_shorter_load():
    _, engine = _compact(("momentum_20d", "ma60_bias", "vol_ratio_5d"))

    assert engine.starts == [MINING_START - timedelta(days=FACTOR_WARMUP_DAYS)]


def test_the_load_covers_the_longest_warmup_among_the_factors():
    _, short = _compact(("rsi_6",))
    _, long = _compact(("rsi_6", "ema60_bias"))

    assert long.starts[0] < short.starts[0] <= MINING_START - timedelta(days=FACTOR_WARMUP_DAYS)
