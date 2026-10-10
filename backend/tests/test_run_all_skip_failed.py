"""One unrunnable strategy must not cost the page every other one.

The post-close refresh writes the picks cache unattended, so a single raise
inside run_all froze the dashboard card on whatever day last succeeded. Opt-in,
because a composite strategy needs its children to fail loudly.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.strategy.engine import StrategyDataContext, StrategyEngine


def _engine(ids: list[str]) -> StrategyEngine:
    engine = StrategyEngine.__new__(StrategyEngine)
    engine._strategies = {sid: MagicMock() for sid in ids}
    engine.get = lambda sid: engine._strategies[sid]
    engine.validate_context = lambda *_a, **_k: None
    engine._matrix_field_columns = lambda *_a, **_k: set()
    return engine


def _context() -> StrategyDataContext:
    df = pl.DataFrame({"symbol": ["2330.TWSE"], "close": [1000.0]})
    return StrategyDataContext(
        current=df,
        history=pl.DataFrame(),
        market=None,
        asset_type="stock",
        timeframe="1d",
        as_of=date(2026, 9, 22),
    )


def _run(engine, *, skip_failed: bool, failing: set[str]):
    def fake_run(sid, _ctx, pool=None, params=None, overrides=None):
        if sid in failing:
            raise ValueError(f"unsupported matrix feature: turnover_rate ({sid})")
        return MagicMock(total=1)

    with patch.object(StrategyEngine, "run", side_effect=fake_run):
        return StrategyEngine.run_all(
            engine, _context(), strategy_ids=list(engine._strategies), skip_failed=skip_failed
        )


def test_a_failing_strategy_is_dropped_and_the_rest_are_kept():
    engine = _engine(["good_a", "broken", "good_b"])
    results = _run(engine, skip_failed=True, failing={"broken"})
    assert sorted(results) == ["good_a", "good_b"]


def test_every_strategy_failing_yields_an_empty_batch_rather_than_a_raise():
    engine = _engine(["broken_a", "broken_b"])
    assert _run(engine, skip_failed=True, failing={"broken_a", "broken_b"}) == {}


def test_the_default_still_raises_so_composites_cannot_silently_lose_a_child():
    engine = _engine(["good_a", "broken"])
    with pytest.raises(ValueError, match="turnover_rate"):
        _run(engine, skip_failed=False, failing={"broken"})


def test_nothing_is_dropped_when_nothing_fails():
    engine = _engine(["good_a", "good_b"])
    assert sorted(_run(engine, skip_failed=True, failing=set())) == ["good_a", "good_b"]
