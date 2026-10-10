"""META scoring_directions: a strategy declares which end of a factor ranks first."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.api.strategy import _strategy_detail
from app.backtest.engine import SimResult
from app.backtest.matrix import build_market_data_matrix
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.engine import StrategyDataContext, StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

DAYS = [date(2026, 3, 2) + timedelta(days=offset) for offset in range(10)]
AS_OF = DAYS[-1]
FACTOR = {"1101.TWSE": 3.0, "1102.TWSE": 1.0, "1103.TWSE": 2.0}
LOWEST, HIGHEST = "1102.TWSE", "1101.TWSE"

META = (
    '{"id": "low_first", "name": "low first", "asset_types": ["stock"], "limit": 0, '
    '"scoring": {"f1": 1.0}, "scoring_directions": {"f1": "%s"}}'
)

POLARS_STRATEGY = """
import polars as pl

META = %s
BASIC_FILTER = {"enabled": False}


def filter(df, params):
    return pl.lit(True)
"""

MATRIX_STRATEGY = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = %s
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "matrix_native"


class _EveryBar:
    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


MATRIX_STRATEGY = _EveryBar()
"""

COMPOSITE_STRATEGY = """
META = {
    "id": "combo",
    "name": "combo",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "children": [{"strategy_id": "low_first", "weight": 1.0}],
    "limit": 0,
}
EXECUTION_BACKEND = "composite"
"""


def _panel() -> pl.DataFrame:
    rows = []
    for offset, day in enumerate(DAYS):
        for symbol, value in FACTOR.items():
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": day,
                    "open": 50.0,
                    "high": 50.5,
                    "low": 49.5,
                    "close": 50.0 + offset * 0.1,
                    "raw_close": 50.0 + offset * 0.1,
                    "raw_high": 50.6,
                    "raw_low": 49.4,
                    "volume": 1_000_000.0,
                    "amount": 1e9,
                    "f1": value,
                    "signal_limit_up": False,
                    "signal_limit_down": False,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _engine(
    tmp_path: Path, backend: str, direction: str = "low", *, composite: bool = False
) -> StrategyEngine:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(exist_ok=True)
    template = POLARS_STRATEGY if backend == "polars_expr" else MATRIX_STRATEGY
    (strategy_dir / "low_first.py").write_text(template % (META % direction), encoding="utf-8")
    dirs = [strategy_dir]
    if composite:
        composite_dir = tmp_path / "composite"
        composite_dir.mkdir(exist_ok=True)
        (composite_dir / "combo.py").write_text(COMPOSITE_STRATEGY, encoding="utf-8")
        dirs.append(composite_dir)
    return StrategyEngine(strategy_dirs=dirs)


def _ranked(engine: StrategyEngine, overrides: dict | None = None) -> list[str]:
    panel = _panel()
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=AS_OF,
        current=panel.filter(pl.col("date") == AS_OF),
        history=panel,
    )
    result = engine.run("low_first", context, overrides=overrides)
    return [str(row["symbol"]) for row in result.rows]


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_meta_direction_ranks_low_values_first(tmp_path, backend):
    engine = _engine(tmp_path, backend)
    assert engine.load_errors() == []

    assert _ranked(engine)[0] == LOWEST


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_user_direction_overrides_meta(tmp_path, backend):
    engine = _engine(tmp_path, backend)

    ranked = _ranked(engine, {"scoring_directions": {"f1": "high"}})

    assert ranked[0] == HIGHEST


def test_invalid_meta_direction_does_not_load(tmp_path):
    engine = _engine(tmp_path, "polars_expr", direction="sideways")

    assert not engine.has("low_first")
    assert "scoring_directions" in engine.load_errors()[0]["error"]


def test_detail_reports_meta_direction_and_user_override(tmp_path):
    engine = _engine(tmp_path, "polars_expr")
    strategy = engine.get("low_first")

    assert _strategy_detail(strategy)["scoring_directions"] == {"f1": "low"}
    overridden = _strategy_detail(strategy, {"scoring_directions": {"f1": "high"}})
    assert overridden["scoring_directions"] == {"f1": "high"}


def test_polars_backtest_score_uses_meta_direction(tmp_path):
    strategy = _engine(tmp_path, "polars_expr").get("low_first")

    scored = StrategyBacktestService._apply_score(_panel(), strategy, None)

    best = scored.filter(pl.col("date") == AS_OF).sort("score", descending=True)["symbol"][0]
    assert best == LOWEST


class _BacktestEngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = SimpleNamespace(store=SimpleNamespace(data_dir=None))
        self.matrix = None

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs
    ):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
            | {"f1"}
        )
        return build_market_data_matrix(self.panel, field_columns=fields)

    def simulate_market_matrix(
        self, matrix, config, progress_cb=None, cancel_event=None, options=None
    ):
        self.matrix = matrix
        return SimResult(
            equity_curve=[{"date": str(AS_OF), "value": config.initial_capital}],
            drawdown_curve=[{"date": str(AS_OF), "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )


@pytest.mark.parametrize("strategy_id", ["low_first", "combo"])
def test_matrix_backtest_score_uses_meta_direction(tmp_path, strategy_id):
    stub = _BacktestEngineStub(_panel())
    service = StrategyBacktestService(
        engine=stub, strategy_engine=_engine(tmp_path, "matrix_native", composite=True)
    )

    result = service.run(
        StrategyBacktestConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=DAYS[2],
            end=AS_OF,
            matching="close_t",
            mode="position",
        )
    )

    assert result.error is None, result.error
    last = stub.matrix.score[-1]
    assert stub.matrix.symbols[int(np.argmax(last))] == LOWEST
