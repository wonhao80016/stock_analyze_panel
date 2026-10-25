from __future__ import annotations

import json
from types import SimpleNamespace

from app.api.monitor_rules import get_options
from app.indicators.pipeline import ENRICHED_COLUMNS
from app.services import kline_sync
from app.strategy.ai_generator import _POLARS_SCORING_FIELDS, AIStrategyGenerator
from app.taiwan.volume_patterns import VOLUME_PATTERN_COLUMNS


def test_volume_patterns_stay_out_of_strategy_scoring_and_monitor_fields(monkeypatch):
    patterns = set(VOLUME_PATTERN_COLUMNS)
    assert not patterns & set(ENRICHED_COLUMNS)
    assert not patterns & _POLARS_SCORING_FIELDS

    monkeypatch.setattr(kline_sync, "intraday_monitor_support", lambda: {})
    options = get_options(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
    assert '"vp_' not in json.dumps(options, ensure_ascii=False)


def test_matrix_strategy_imports_pass_the_safety_lint():
    AIStrategyGenerator._validate_safety(
        "import numpy as np\n"
        "from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix\n"
    )
