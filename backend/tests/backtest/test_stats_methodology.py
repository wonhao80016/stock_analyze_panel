"""Backtest statistics methodology (stats_v3).

Annualization uses P = return periods / (calendar days / 365.25), so the annual return is the
calendar CAGR and Sharpe/Sortino scale by sqrt(P). Below 60 sessions annualized fields are null.
Expected numbers below are computed by hand from the closed-form fixtures, not by the engine.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig, SimulationOptions, TradeRecord
from app.backtest.strategy import StrategyBacktestConfig

# Alternating returns c + d, c - d over 121 pairs compound to exactly +10%:
# (1 + c)^2 - d^2 = 1.1^(1/121), so mean(daily) = c and population std(daily) = d.
_D = 0.01
_C = math.sqrt(1.1 ** (1 / 121) + _D * _D) - 1


def _weekdays(start: date, count: int) -> list[str]:
    out: list[str] = []
    day = start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _sessions_2025() -> list[str]:
    """243 session labels from 2025-01-02 to 2025-12-31 (363 calendar days)."""
    weekdays = _weekdays(date(2025, 1, 2), 260)
    assert weekdays[-1] == "2025-12-31"
    picked = [weekdays[round(i * (len(weekdays) - 1) / 242)] for i in range(243)]
    assert len(set(picked)) == 243
    return picked


def _alternating_equity(count: int, initial: float = 1_000_000.0) -> list[float]:
    values = [initial]
    for i in range(count - 1):
        values.append(values[-1] * (1 + (_C + _D if i % 2 == 0 else _C - _D)))
    return values


def _portfolio_stats(values, dates, trades=(), *, risk_free_rate=0.0, **kwargs):
    return BacktestEngine._calc_portfolio_stats_from_values(
        values,
        [0.0] * len(values),
        list(trades),
        1_000_000.0,
        session_dates=dates,
        risk_free_rate=risk_free_rate,
        **kwargs,
    )


def _trade(pnl_pct: float, *, pnl_amount: float = 0.0, position_pct: float = 0.0) -> TradeRecord:
    return TradeRecord(
        symbol="2330.TWSE",
        entry_date=date(2025, 1, 2),
        exit_date=date(2025, 1, 3),
        entry_price=100.0,
        exit_price=100.0 * (1 + pnl_pct),
        pnl_pct=pnl_pct,
        duration=1,
        exit_reason="signal",
        position_pct=position_pct,
        pnl_amount=pnl_amount,
    )


def test_known_curve_annualizes_by_calendar_span_without_risk_free_rate():
    values = _alternating_equity(243)
    assert values[-1] / values[0] - 1 == pytest.approx(0.10, abs=1e-12)

    stats = _portfolio_stats(values, _sessions_2025())

    # P = 242 / (363 / 365.25) = 243.5; CAGR = 1.1^(365.25/363) - 1 = 0.100650 (252-day rule: 0.103890).
    assert stats["methodology"]["periods_per_year"] == pytest.approx(243.5)
    assert stats["methodology"]["calendar_days"] == 363
    assert stats["methodology"]["return_periods"] == 242
    assert stats["annual_return"] == 0.1007
    # Sharpe = c / d * sqrt(243.5) = 0.6927 (sqrt(252) rule: 0.7047).
    assert stats["sharpe"] == 0.69
    # Sortino: downside dev = (d - c) / sqrt(2); 1.0251.
    assert stats["sortino"] == 1.03
    # max drawdown = c - d = -0.009556; calmar = 0.100650 / 0.009556 = 10.5325.
    assert stats["max_drawdown"] == -0.0096
    assert stats["calmar"] == 10.53
    assert stats["risk_free_rate"] == 0.0
    assert stats["methodology"]["version"] == "stats_v3"


def test_known_curve_subtracts_per_period_risk_free_rate():
    stats = _portfolio_stats(_alternating_equity(243), _sessions_2025(), risk_free_rate=0.015)

    # rf_d = 1.015^(1/243.5) - 1 = 6.11461e-05; Sharpe = (c - rf_d) / d * sqrt(243.5) = 0.5973.
    assert stats["methodology"]["risk_free_rate_per_period"] == pytest.approx(6.11461e-05, rel=1e-5)
    assert stats["sharpe"] == 0.60
    # Sortino with MAR = rf_d: (c - rf_d) / ((d - c + rf_d) / sqrt(2)) * sqrt(243.5) = 0.8783.
    assert stats["sortino"] == 0.88
    # The annual return and calmar do not depend on the risk-free rate.
    assert stats["annual_return"] == 0.1007
    assert stats["calmar"] == 10.53
    assert stats["risk_free_rate"] == 0.015


@pytest.mark.parametrize(("sessions", "annualized"), [(59, False), (60, True)])
def test_annualized_fields_need_sixty_sessions(sessions: int, annualized: bool):
    dates = _weekdays(date(2025, 1, 2), sessions)
    stats = _portfolio_stats(_alternating_equity(sessions), dates, risk_free_rate=0.015)

    fields = ("annual_return", "sharpe", "sortino", "calmar")
    if annualized:
        # 60 sessions span 83 calendar days: P = 59 / (83 / 365.25) = 259.636.
        assert stats["methodology"]["periods_per_year"] == pytest.approx(59 * 365.25 / 83)
        assert all(stats[name] is not None for name in fields)
        assert stats["methodology"]["annualization_unavailable_reason"] is None
    else:
        assert all(stats[name] is None for name in fields)
        assert stats["methodology"]["annualization_unavailable_reason"] == "fewer_than_60_sessions"
    assert stats["total_return"] is not None
    assert stats["max_drawdown"] is not None


def test_min_annualized_sessions_can_be_lowered_for_internal_ranking():
    dates = _weekdays(date(2025, 1, 2), 21)
    stats = _portfolio_stats(_alternating_equity(21), dates, min_annualized_sessions=0)

    assert stats["sharpe"] is not None
    assert stats["annual_return"] is not None


def test_portfolio_profit_factor_uses_amounts():
    trades = [
        _trade(0.10, pnl_amount=1_000.0, position_pct=0.01),
        _trade(-0.05, pnl_amount=-2_000.0, position_pct=0.04),
    ]
    stats = _portfolio_stats(_alternating_equity(243), _sessions_2025(), trades)

    # 1,000 / 2,000 = 0.5; the percentage sum would read 0.10 / 0.05 = 2.0.
    assert stats["profit_factor"] == 0.5
    assert stats["methodology"]["profit_factor_basis"] == "pnl_amount"


def test_portfolio_monte_carlo_weights_returns_by_position_fraction():
    trades = [_trade(-0.10, pnl_amount=-10_000.0, position_pct=0.1) for _ in range(10)]
    stats = _portfolio_stats(_alternating_equity(243), _sessions_2025(), trades)

    # Each trade moves the account by -10% * 0.1 = -1%; from the initial-capital peak every resample
    # reads 0.99^10 - 1 = -0.0956. The full-capital assumption would read 0.9^10 - 1 = -0.6513.
    assert stats["mc_maxdd_p50"] == -0.0956
    assert stats["mc_maxdd_p95"] == -0.0956
    assert stats["methodology"]["monte_carlo_basis"] == "pnl_pct_x_position_pct"


def test_portfolio_monte_carlo_resamples_weighted_trade_returns():
    pnls = np.array([0.05, -0.03, 0.08, -0.06, 0.02, -0.04, 0.10, -0.05])
    weights = np.array([0.5, 0.2, 0.1, 0.5, 0.3, 0.2, 0.1, 0.4])
    trades = [_trade(p, position_pct=w) for p, w in zip(pnls, weights, strict=True)]
    stats = _portfolio_stats(_alternating_equity(243), _sessions_2025(), trades)

    expected = BacktestEngine._mc_drawdown_percentiles(pnls * weights)
    assert stats["mc_maxdd_p50"] == expected["mc_maxdd_p50"]
    assert stats["mc_maxdd_p95"] == expected["mc_maxdd_p95"]


def test_full_mode_fills_sessions_without_exits_and_keeps_percent_profit_factor():
    sessions = _weekdays(date(2025, 1, 2), 61)
    first_exit, last_exit = date.fromisoformat(sessions[10]), date.fromisoformat(sessions[60])
    trades = [
        TradeRecord(
            symbol="2330.TWSE", entry_date=date(2025, 1, 2), exit_date=first_exit,
            entry_price=100.0, exit_price=102.0, pnl_pct=0.02, duration=8, exit_reason="signal",
        ),
        TradeRecord(
            symbol="2317.TWSE", entry_date=date(2025, 1, 2), exit_date=last_exit,
            entry_price=100.0, exit_price=99.0, pnl_pct=-0.01, duration=60, exit_reason="signal",
        ),
    ]

    result = BacktestEngine._calc_independent_candidate_result(
        trades, 2, {}, session_dates=sessions, risk_free_rate=0.0,
    )
    stats = result.stats

    # 61 sessions over 84 calendar days, zero on 59 of them: P = 60 / (84 / 365.25) = 260.893;
    # Sharpe = mean / std * sqrt(P) = 0.9264, Sortino = 2.0681. Exit days only with sqrt(252): 5.29.
    assert stats["methodology"]["periods_per_year"] == pytest.approx(60 * 365.25 / 84)
    assert stats["sharpe"] == 0.93
    assert stats["sortino"] == 2.07
    assert stats["profit_factor"] == 2.0
    assert stats["methodology"]["profit_factor_basis"] == "pnl_pct"
    assert stats["methodology"]["monte_carlo_basis"] == "pnl_pct"


def test_engine_reports_the_risk_free_rate_it_used():
    import polars as pl

    rows = []
    for offset, close in enumerate([10.0, 10.5, 10.2, 10.8, 11.0]):
        day = date(2025, 1, 6) + timedelta(days=offset)
        rows.append({
            "symbol": "AAA", "name": "AAA", "date": day,
            "open": close, "high": close, "low": close, "close": close,
            "volume": 1_000_000, "score": 1.0,
        })
    panel = pl.DataFrame(rows)
    entries = pl.Series([True, False, False, False, False])
    exits = pl.Series([False, False, True, False, False])

    result = BacktestEngine(repo=None).simulate_portfolio(
        panel, entries, exits,
        MatcherConfig(matching="close_t", fees_pct=0, slippage_bps=0, risk_free_rate=0.015),
    )

    assert result.stats["risk_free_rate"] == 0.015
    assert result.stats["methodology"]["sessions"] == 5
    assert result.stats["sharpe"] is None


@pytest.mark.parametrize("rate", [-0.051, 0.201, float("nan"), float("inf")])
def test_config_rejects_unreasonable_risk_free_rate(rate: float):
    with pytest.raises(ValueError, match="risk_free_rate"):
        StrategyBacktestConfig(
            strategy_id="x", symbols=None, start=date(2025, 1, 2), end=date(2025, 12, 31),
            risk_free_rate=rate,
        )
    with pytest.raises(ValueError, match="risk_free_rate"):
        MatcherConfig(risk_free_rate=rate)


def _full_mode_trades(sessions: list[str], exits: dict[int, float]) -> list[TradeRecord]:
    return [
        TradeRecord(
            symbol=f"{index:04d}.TWSE", entry_date=date(2025, 1, 2),
            exit_date=date.fromisoformat(sessions[index]), entry_price=100.0,
            exit_price=100.0 * (1 + pnl), pnl_pct=pnl, duration=index, exit_reason="signal",
        )
        for index, pnl in exits.items()
    ]


def test_full_mode_timeline_runs_to_formal_end_after_last_exit():
    sessions = _weekdays(date(2025, 1, 2), 70)
    trades = _full_mode_trades(sessions, {10: 0.02, 50: -0.01})

    result = BacktestEngine._calc_independent_candidate_result(
        trades, 2, {}, session_dates=sessions, formal_end=sessions[69],
    )

    # 70 sessions over 97 calendar days with 68 zero days: P = 69 / (97 / 365.25) = 259.817,
    # Sharpe = 0.8628, Sortino = 1.9266. Stopping at the last exit (session 51) would leave
    # 51 sessions, below the 60-session minimum.
    assert result.stats["methodology"]["sessions"] == 70
    assert result.stats["methodology"]["periods_per_year"] == pytest.approx(69 * 365.25 / 97, abs=1e-4)
    assert result.stats["sharpe"] == 0.86
    assert result.stats["sortino"] == 1.93


def test_full_mode_tail_zeros_are_not_dropped_when_threshold_is_lowered():
    sessions = _weekdays(date(2025, 1, 2), 70)
    trades = _full_mode_trades(sessions, {10: 0.02, 50: -0.01})

    result = BacktestEngine._calc_independent_candidate_result(
        trades, 2, {}, session_dates=sessions, formal_end=sessions[69],
        options=SimulationOptions(min_annualized_sessions=0),
    )

    # Truncating at the last exit would read 1.0135 (51 sessions); the formal window reads 0.8628.
    assert result.stats["sharpe"] == 0.86


def test_full_mode_max_drawdown_starts_from_initial_value():
    sessions = _weekdays(date(2025, 1, 2), 5)
    trades = _full_mode_trades(sessions, {1: -0.05, 2: 0.02})

    result = BacktestEngine._calc_independent_candidate_result(trades, 2, {}, session_dates=sessions)

    # Sample curve 1.0 -> 0.95 -> 0.969: drawdown from the 1.0 start is -5%, matching the curve.
    assert result.stats["max_drawdown"] == -0.05
    assert min(row["value"] for row in result.drawdown_curve) == -0.05


@pytest.mark.parametrize("rate", [0.0, 0.015])
def test_flat_equity_reports_zero_sharpe_and_sortino(rate: float):
    dates = _weekdays(date(2025, 1, 2), 60)
    stats = _portfolio_stats([1_000_000.0] * 60, dates, risk_free_rate=rate)

    # Zero volatility leaves both ratios undefined; both report the neutral 0.0.
    assert stats["sharpe"] == 0.0
    assert stats["sortino"] == 0.0


def test_portfolio_max_drawdown_includes_first_day_cost_below_initial_capital():
    values = [995_000.0, 1_000_500.0, 999_000.0]
    stats = _portfolio_stats(values, _weekdays(date(2025, 1, 2), 3))

    # Peak starts at the 1,000,000 initial capital: day 1 reads 995,000 / 1,000,000 - 1 = -0.5%.
    # Peaking at the first equity value would only see 999,000 / 1,000,500 - 1 = -0.15%.
    assert stats["max_drawdown"] == -0.005


def test_portfolio_max_drawdown_matches_drawdown_curve_after_entry_costs():
    import polars as pl

    rows = [
        {"symbol": "AAA", "name": "AAA", "date": date(2025, 1, 6) + timedelta(days=offset),
         "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1_000_000, "score": 1.0}
        for offset in range(5)
    ]
    result = BacktestEngine(repo=None).simulate_portfolio(
        pl.DataFrame(rows),
        pl.Series([True, False, False, False, False]),
        pl.Series([False] * 5),
        MatcherConfig(matching="close_t", fees_pct=0.01, slippage_bps=0),
    )

    # Flat prices: the only loss is the buy commission, so equity sits below initial capital from day 1.
    assert result.equity_curve[0]["value"] < 1_000_000
    assert result.stats["max_drawdown"] < 0
    assert result.stats["max_drawdown"] == min(row["value"] for row in result.drawdown_curve)


def test_constant_daily_return_reports_zero_sharpe_and_sortino():
    values = [1_000_000.0 * 1.001 ** i for i in range(60)]
    stats = _portfolio_stats(values, _weekdays(date(2025, 1, 2), 60))

    # Every daily return is 0.1%; the std of the ratios is float noise (~1e-17), not volatility.
    assert stats["sharpe"] == 0.0
    assert stats["sortino"] == 0.0


def test_full_mode_constant_daily_return_reports_zero_sharpe_and_sortino():
    sessions = _weekdays(date(2025, 1, 2), 60)
    trades = _full_mode_trades(sessions, dict.fromkeys(range(60), 0.001))

    stats = BacktestEngine._calc_independent_candidate_result(
        trades, 60, {}, session_dates=sessions, formal_end=sessions[-1],
    ).stats

    assert stats["sharpe"] == 0.0
    assert stats["sortino"] == 0.0
