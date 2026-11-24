"""An open_t+1 entry fills on the next trading session or not at all; an exit waits out a suspension."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig

METHODS = ("simulate_portfolio", "simulate_independent_candidates")
START = date(2024, 1, 1)


def _panel(days: int, a_missing: set[int]) -> pl.DataFrame:
    """A has no bar on ``a_missing`` sessions; B trades every session and keeps the calendar."""
    return pl.DataFrame([
        {
            "symbol": symbol,
            "name": symbol,
            "date": START + timedelta(days=day),
            "open": 10.0 + day,
            "high": 10.0 + day,
            "low": 10.0 + day,
            "close": 10.0 + day,
            "volume": 100_000,
            "score": 1.0,
            "signal_limit_up": False,
            "signal_limit_down": False,
        }
        for symbol in ("A", "B")
        for day in range(days)
        if not (symbol == "A" and day in a_missing)
    ])


def _a_days(panel: pl.DataFrame, days: set[int]) -> pl.Series:
    return pl.Series(
        [
            row["symbol"] == "A" and (row["date"] - START).days in days
            for row in panel.select(["symbol", "date"]).iter_rows(named=True)
        ],
        dtype=pl.Boolean,
    )


def _simulate(method: str, panel: pl.DataFrame, entry_days: set[int], exit_days: set[int]):
    return getattr(BacktestEngine(repo=None), method)(
        panel,
        _a_days(panel, entry_days),
        _a_days(panel, exit_days),
        MatcherConfig(matching="open_t+1", fees_pct=0, slippage_bps=0, max_positions=1, initial_capital=100_000),
    )


@pytest.mark.parametrize("method", METHODS)
def test_entry_is_dropped_when_the_asset_has_no_bar_on_the_next_session(method):
    panel = _panel(days=5, a_missing={1})

    result = _simulate(method, panel, entry_days={0}, exit_days=set())

    assert result.trades == []


@pytest.mark.parametrize("method", METHODS)
def test_entry_fills_on_the_next_session_when_the_asset_trades_then(method):
    panel = _panel(days=5, a_missing={2})

    result = _simulate(method, panel, entry_days={0}, exit_days={3})

    assert [(t.entry_signal_date, t.entry_date, t.entry_price) for t in result.trades] == [
        ("2024-01-01", "2024-01-02", 11.0)
    ]


@pytest.mark.parametrize("method", METHODS)
def test_exit_signal_of_a_held_position_fills_when_trading_resumes(method):
    panel = _panel(days=7, a_missing={3, 4})

    result = _simulate(method, panel, entry_days={0}, exit_days={2})

    assert [(t.exit_signal_date, t.exit_date, t.exit_price, t.exit_reason) for t in result.trades] == [
        ("2024-01-03", "2024-01-06", 15.0, "signal")
    ]
