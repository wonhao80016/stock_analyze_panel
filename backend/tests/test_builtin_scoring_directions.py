"""A built-in strategy's META scoring_directions names only fields its META scoring weights."""
from __future__ import annotations

from pathlib import Path

from app.strategy.engine import StrategyEngine

BUILTIN = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"


def test_builtin_scoring_directions_name_only_scored_fields():
    engine = StrategyEngine(strategy_dirs=[BUILTIN])

    unscored = {
        strategy.meta["id"]: sorted(
            set(strategy.meta.get("scoring_directions") or {})
            - {name for name, weight in (strategy.meta.get("scoring") or {}).items() if weight}
        )
        for strategy in engine.strategy_definitions()
    }

    assert {sid: fields for sid, fields in unscored.items() if fields} == {}
    assert engine.get("bottom_reversal").meta["scoring_directions"] == {"rsi_14": "low"}
    assert engine.get("n_day_low_reversal").meta["scoring_directions"] == {"momentum_5d": "low"}
