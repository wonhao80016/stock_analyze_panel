"""Walk-forward must not score overlapping out-of-sample windows."""
from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise
from types import SimpleNamespace

import pytest

from app.backtest.walkforward import WalkForwardConfig, WalkForwardService, generate_folds

_DATES = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]


def test_generate_folds_rejects_step_shorter_than_test_window():
    with pytest.raises(ValueError, match="step_days"):
        generate_folds(_DATES, train_days=10, test_days=5, step_days=2)


def test_generate_folds_keeps_oos_windows_disjoint_when_step_equals_test():
    folds = generate_folds(_DATES, train_days=10, test_days=5, step_days=5)
    for prev, nxt in pairwise(folds):
        assert prev.test_end < nxt.test_start


def test_walkforward_run_rejects_overlap_before_optimizing():
    class Optimizer:
        calls = 0

        def optimize(self, *_args, **_kwargs):
            Optimizer.calls += 1
            raise AssertionError("optimizer must not run for an overlapping configuration")

    service = WalkForwardService(
        Optimizer(),
        service=SimpleNamespace(engine=SimpleNamespace(cache_stats=lambda: {})),
        strategy_engine=None,
        trading_dates_loader=lambda _asset, _start, _end: _DATES,
    )
    cfg = WalkForwardConfig(
        strategy_id="any",
        symbols=None,
        start=_DATES[0],
        end=_DATES[-1],
        param_grid={"p": [1]},
        objective="total_return",
        train_days=10,
        test_days=5,
        step_days=2,
    )

    with pytest.raises(ValueError, match="step_days"):
        service.run(cfg)
    assert Optimizer.calls == 0
