"""_strategy_detail must report the risk levels a run would actually use.

backtest/strategy.py resolves stop_loss, take_profit, trailing_stop and both
trailing take-profit fields through the same override lookup, so a detail
response that returns the module constant describes a run that does not happen.
Only stop_loss and max_hold_days did so; the dashboard card now prints these
levels, which is where the discrepancy would surface.
"""
from __future__ import annotations

import pytest

from app.api.strategy import _strategy_detail
from app.strategy.engine import StrategyDef

RISK_FIELDS = (
    "stop_loss",
    "take_profit",
    "trailing_stop",
    "trailing_take_profit_activate",
    "trailing_take_profit_drawdown",
    "max_hold_days",
)


def _strategy() -> StrategyDef:
    s = StrategyDef(
        meta={"id": "risk_strat", "name": "風控策略"},
        basic_filter={"enabled": True},
        entry_signals=["signal_ma20_breakout"],
        exit_signals=["signal_ma20_breakdown"],
        stop_loss=-0.08,
        trailing_stop=0.05,
        trailing_take_profit_activate=0.12,
        trailing_take_profit_drawdown=0.04,
        max_hold_days=10,
        filter_fn=None,
        filter_history_fn=None,
        lookback_days=60,
        source="builtin",
    )
    s.take_profit = 0.20
    return s


def test_without_overrides_the_module_constants_are_reported():
    detail = _strategy_detail(_strategy(), overrides=None)
    assert [detail[f] for f in RISK_FIELDS] == [-0.08, 0.20, 0.05, 0.12, 0.04, 10]


@pytest.mark.parametrize(
    ("field", "saved"),
    [
        ("stop_loss", -0.03),
        ("take_profit", 0.5),
        ("trailing_stop", 0.02),
        ("trailing_take_profit_activate", 0.3),
        ("trailing_take_profit_drawdown", 0.01),
        ("max_hold_days", 2),
    ],
)
def test_every_risk_field_reports_the_saved_override(field, saved):
    detail = _strategy_detail(_strategy(), overrides={field: saved})
    assert detail[field] == saved


def test_an_override_of_none_is_reported_rather_than_falling_back():
    """Clearing a level is a decision; reporting the constant would undo it."""
    detail = _strategy_detail(_strategy(), overrides={"take_profit": None})
    assert detail["take_profit"] is None


def test_unrelated_overrides_leave_the_other_levels_alone():
    detail = _strategy_detail(_strategy(), overrides={"take_profit": 0.5})
    assert detail["stop_loss"] == -0.08
    assert detail["trailing_stop"] == 0.05
    assert detail["max_hold_days"] == 10
