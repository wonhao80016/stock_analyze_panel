"""An exit due at the session open fills before the session's intraday stop or take-profit.

A prior-day exit signal, a reached max-hold and a pending exit all fill at the open; a gap
through a risk line fills at the same open and keeps the risk reason.
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig

METHODS = ("simulate_portfolio", "simulate_independent_candidates")
FLAT = (10.0, 10.0, 10.0, 10.0)


def _panel(bars: list[tuple[float, float, float, float]], limit_down_days: set[int] = frozenset()) -> pl.DataFrame:
    start = date(2024, 1, 1)
    return pl.DataFrame([
        {
            "symbol": "A",
            "name": "A",
            "date": start + timedelta(days=day),
            "open": bar[0],
            "high": bar[1],
            "low": bar[2],
            "close": bar[3],
            "volume": 100_000,
            "score": 1.0,
            "signal_limit_up": False,
            "signal_limit_down": day in limit_down_days,
        }
        for day, bar in enumerate(bars)
    ])


def _days(panel: pl.DataFrame, days: set[int]) -> pl.Series:
    return pl.Series([day in days for day in range(panel.height)], dtype=pl.Boolean)


def _trade(method: str, panel: pl.DataFrame, exit_days: set[int], **config):
    result = getattr(BacktestEngine(repo=None), method)(
        panel,
        _days(panel, {0}),
        _days(panel, exit_days),
        MatcherConfig(
            matching=config.pop("matching", "open_t+1"),
            fees_pct=0,
            slippage_bps=0,
            max_positions=1,
            initial_capital=100_000,
            **config,
        ),
    )
    assert len(result.trades) == 1
    return result.trades[0]


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize(
    ("due_bar", "risk"),
    [
        pytest.param((10.8, 11.3, 10.7, 10.9), {"take_profit_pct": 0.10}, id="take-profit-touched"),
        pytest.param((9.6, 9.7, 8.5, 8.8), {"stop_loss_pct": 0.10}, id="stop-loss-touched"),
    ],
)
def test_prior_day_exit_signal_fills_at_the_open_before_an_intraday_risk_line(method, due_bar, risk):
    panel = _panel([FLAT, FLAT, (10.0, 10.5, 9.95, 10.4), due_bar, due_bar])

    trade = _trade(method, panel, {2}, **risk)

    assert trade.entry_date == "2024-01-02"
    assert (trade.exit_date, trade.exit_price, trade.exit_reason) == ("2024-01-04", due_bar[0], "signal")
    assert trade.exit_signal_date == "2024-01-03"


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize(
    ("due_bar", "risk"),
    [
        pytest.param((10.5, 11.5, 10.4, 11.2), {"take_profit_pct": 0.10}, id="take-profit-touched"),
        pytest.param((9.5, 9.6, 8.6, 8.9), {"stop_loss_pct": 0.10}, id="stop-loss-touched"),
    ],
)
def test_max_hold_fills_at_the_open_before_an_intraday_risk_line(method, due_bar, risk):
    panel = _panel([FLAT, FLAT, FLAT, due_bar, due_bar])

    trade = _trade(method, panel, set(), max_hold_days=2, **risk)

    assert (trade.exit_date, trade.exit_price, trade.exit_reason) == ("2024-01-04", due_bar[0], "max_hold")


@pytest.mark.parametrize("method", METHODS)
def test_pending_exit_fills_at_the_next_open_before_an_intraday_stop(method):
    # The signal is due on day 2, which opens locked at limit down; day 3 dips through the stop.
    panel = _panel(
        [FLAT, FLAT, (9.0, 9.0, 9.0, 9.0), (9.2, 9.3, 8.0, 8.2), (8.2, 8.2, 8.2, 8.2)],
        limit_down_days={2},
    )

    trade = _trade(method, panel, {1}, stop_loss_pct=0.15)

    assert (trade.exit_date, trade.exit_price, trade.exit_reason) == ("2024-01-04", 9.2, "signal")
    assert trade.exit_signal_date == "2024-01-02"
    assert trade.blocked_exit_days == 1


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize(
    ("due_bar", "risk", "reason"),
    [
        pytest.param((8.8, 9.0, 8.5, 8.7), {"stop_loss_pct": 0.10}, "stop_loss", id="gap-below-stop"),
        pytest.param((11.2, 11.5, 11.1, 11.3), {"take_profit_pct": 0.10}, "take_profit", id="gap-above-take-profit"),
    ],
)
def test_gap_through_a_risk_line_keeps_the_risk_reason_when_an_exit_is_due(method, due_bar, risk, reason):
    panel = _panel([FLAT, FLAT, FLAT, due_bar, due_bar])

    trade = _trade(method, panel, {2}, **risk)

    assert (trade.exit_date, trade.exit_price, trade.exit_reason) == ("2024-01-04", due_bar[0], reason)


@pytest.mark.parametrize("method", METHODS)
def test_close_fill_exit_signal_still_follows_an_intraday_stop(method):
    panel = _panel([FLAT, FLAT, FLAT, (9.6, 9.7, 8.5, 8.8), FLAT])

    trade = _trade(method, panel, {3}, matching="close_t", stop_loss_pct=0.10)

    assert (trade.exit_date, trade.exit_price, trade.exit_reason) == ("2024-01-04", 9.0, "stop_loss")
