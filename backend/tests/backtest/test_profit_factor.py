"""profit_factor must be gross profit / gross loss, not the payoff ratio.

The two coincide only at a 50% win rate. Using avg_win/avg_loss made a strategy
with many small losses and few large wins read above 1.0 while actually losing
money.
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.backtest.engine import BacktestEngine, MatcherConfig, _gross_profit_factor


def test_gross_profit_factor_uses_totals_not_averages():
    wins = [1.0]
    losses = [-0.3, -0.3, -0.3]
    # payoff ratio would be 1.0 / 0.3 = 3.33; gross is 1.0 / 0.9 = 1.11
    assert round(_gross_profit_factor(wins, losses), 3) == 1.111


def test_gross_profit_factor_below_one_when_losses_outweigh_wins():
    wins = [0.5]
    losses = [-0.2] * 4
    # payoff ratio reads 2.5 (healthy); the strategy is in fact down 0.3
    assert _gross_profit_factor(wins, losses) < 1.0
    assert round(_gross_profit_factor(wins, losses), 3) == 0.625


def test_gross_profit_factor_none_without_losses():
    assert _gross_profit_factor([0.1, 0.2], []) is None


def _two_bar_panel(rows: list[tuple[str, float, float]]) -> pl.DataFrame:
    """One symbol per entry: buys at day-1 open, sells at day-2 open."""
    start = date(2024, 1, 1)
    out = []
    for symbol, entry_open, exit_open in rows:
        prices = [entry_open, entry_open, exit_open]
        for i, price in enumerate(prices):
            out.append({
                "symbol": symbol,
                "name": symbol,
                "date": start + timedelta(days=i),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1_000_000,
                "score": 1.0,
            })
    return pl.DataFrame(out).sort(["symbol", "date"])


def test_stats_reports_gross_profit_factor_and_payoff_ratio_separately():
    # one big winner, three small losers -> payoff > 1 but gross PF < 1
    panel = _two_bar_panel([
        ("WIN", 10.0, 14.0),
        ("LOSE1", 10.0, 9.0),
        ("LOSE2", 10.0, 9.0),
        ("LOSE3", 10.0, 9.0),
    ])
    base = date(2024, 1, 1)
    entries = pl.Series([
        (row["date"] - base).days == 0
        for row in panel.select(["symbol", "date"]).iter_rows(named=True)
    ], dtype=pl.Boolean)
    exits = pl.Series([
        (row["date"] - base).days == 1
        for row in panel.select(["symbol", "date"]).iter_rows(named=True)
    ], dtype=pl.Boolean)

    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        entries,
        exits,
        MatcherConfig(
            matching="open_t+1",
            fees_pct=0,
            slippage_bps=0,
            max_positions=4,
            initial_capital=1_000_000,
        ),
    )

    pnls = [float(t.pnl_pct) for t in result.trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    assert wins and losses, "fixture must produce both winners and losers"
    amounts = [float(t.pnl_amount) for t in result.trades]

    # Portfolio profit factor is on currency amounts; payoff ratio stays on percentages.
    expected_gross = sum(a for a in amounts if a > 0) / abs(sum(a for a in amounts if a <= 0))
    expected_payoff = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))

    assert result.stats["profit_factor"] == round(expected_gross, 2)
    assert result.stats["payoff_ratio"] == round(expected_payoff, 2)
    # The regression this guards: the two must not be conflated off 50% win rate.
    assert result.stats["profit_factor"] != result.stats["payoff_ratio"]
