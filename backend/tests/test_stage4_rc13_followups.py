"""Screening keeps working when share data or stored columns are missing; failures surface clearly."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import HTTPException

from app.api import strategy as strategy_api
from app.backtest.mining import (
    FactorMetric,
    MiningBudget,
    MiningRequest,
    MiningService,
    NestedValidationConfig,
)
from app.backtest.optimizer import _no_best_reason
from app.backtest.walkforward import WalkForwardConfig, WalkForwardService
from app.strategy.engine import StrategyDataContext, StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

AS_OF = date(2026, 1, 2)
SYMBOLS = ["F", "E", "D", "C", "B", "A"]


def _frame(**columns) -> pl.DataFrame:
    count = len(SYMBOLS)
    return pl.DataFrame(
        {
            "symbol": SYMBOLS,
            "name": SYMBOLS,
            "date": [AS_OF] * count,
            "open": [10.0] * count,
            "high": [10.0] * count,
            "low": [10.0] * count,
            "close": [10.0] * count,
            "volume": [1_000.0] * count,
            "amount": [1e9] * count,
            "f1": [3.0, 6.0, 1.0, 4.0, 3.0, 5.0],
            **columns,
        }
    )


_POLARS = """import polars as pl
META = {
    "id": "polars_cap",
    "name": "polars_cap",
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "basic_filter": {"boards": None, "market_cap_min": 5e8, "float_cap_min": 5e8},
    "scoring": {"f1": 1.0},
}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
"""

_MATRIX = """import numpy as np
from app.backtest.matrix import make_signal_matrix
META = {
    "id": "matrix_cap",
    "name": "matrix_cap",
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "basic_filter": {"boards": None, "market_cap_min": 5e8, "float_cap_min": 5e8},
    "scoring": {"f1": 1.0},
}
EXECUTION_BACKEND = "matrix_native"
class _AllEntries:
    def required_fields(self):
        return frozenset({"close"})
    def required_warmup_bars(self, params):
        return 1
    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))
MATRIX_STRATEGY = _AllEntries()
"""


@pytest.fixture
def engine(tmp_path) -> StrategyEngine:
    (tmp_path / "polars_cap.py").write_text(_POLARS, encoding="utf-8")
    (tmp_path / "matrix_cap.py").write_text(_MATRIX, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[tmp_path])
    assert engine.load_errors() == []
    return engine


def _context(frame: pl.DataFrame, asset_type: str = "stock") -> StrategyDataContext:
    return StrategyDataContext(
        asset_type=asset_type,
        timeframe="1d",
        as_of=AS_OF,
        current=frame,
        history=frame,
    )


_STRATEGIES = ["polars_cap", "matrix_cap"]


@pytest.mark.parametrize("strategy_id", _STRATEGIES)
@pytest.mark.parametrize(
    "frame",
    [
        _frame(),
        _frame(
            total_shares=pl.Series([None] * 6, dtype=pl.Float64),
            float_shares=pl.Series([None] * 6, dtype=pl.Float64),
        ),
    ],
    ids=["share_columns_absent", "share_columns_all_null"],
)
def test_share_cap_bounds_are_dropped_without_share_data(engine, strategy_id, frame):
    result = engine.run(strategy_id, _context(frame))

    assert sorted(row["symbol"] for row in result.rows) == sorted(SYMBOLS)


@pytest.mark.parametrize("strategy_id", _STRATEGIES)
def test_share_cap_bounds_apply_when_share_data_exists(engine, strategy_id):
    shares = pl.Series([1e8, None, 1e8, 1e7, 1e8, 1e8], dtype=pl.Float64)
    frame = _frame(total_shares=shares, float_shares=shares)

    result = engine.run(strategy_id, _context(frame))

    assert sorted(row["symbol"] for row in result.rows) == ["A", "B", "D", "F"]


@pytest.mark.parametrize("strategy_id", _STRATEGIES)
def test_bound_on_a_field_the_data_lacks_still_raises(engine, strategy_id):
    with pytest.raises(ValueError, match="turnover_rate"):
        engine.run(
            strategy_id,
            _context(_frame()),
            overrides={"basic_filter": {"turnover_min": 0.5}},
        )


def _enriched_partition(root, day: date, turnover: float) -> None:
    path = root / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": ["000001.SZ", "600000.SH"],
            "date": [day, day],
            "open": [10.0, 20.0],
            "high": [10.5, 20.5],
            "low": [9.5, 19.5],
            "close": [10.0, 20.0],
            "volume": [1_000.0, 2_000.0],
            "amount": [1e9, 2e9],
            "raw_close": [10.0, 20.0],
            "raw_high": [10.5, 20.5],
            "raw_low": [9.5, 19.5],
            "turnover_rate": [turnover, turnover * 2],
        }
    ).write_parquet(path)


_TURNOVER_STRATEGY = """import polars as pl
META = {
    "id": "turnover_scored",
    "name": "turnover_scored",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"enabled": False},
    "scoring": {"turnover_rate": 1.0},
}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.col("turnover_rate") >= 0
"""


def test_app_context_carries_stored_turnover_for_scoring_and_filters(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.screener import ScreenerService
    from app.tickflow.repository import DataStore, KlineRepository

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    sessions = [date(2026, 9, 1) + timedelta(days=offset) for offset in range(5)]
    for offset, day in enumerate(sessions):
        _enriched_partition(tmp_path, day, 1.0 + offset)
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "turnover_scored.py").write_text(_TURNOVER_STRATEGY, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[strategies])
    repo = KlineRepository(DataStore(tmp_path))

    service = ScreenerService(repo)
    as_of = service.latest_date()
    context = service.build_strategy_context(engine, as_of, ["turnover_scored"])
    results = engine.run_all(context, strategy_ids=["turnover_scored"])

    assert as_of == sessions[-1]
    assert results["turnover_scored"].scores == {"600000.SH": 100.0, "000001.SZ": 0.0}


_MISSING_FIELD = """import polars as pl
META = {
    "id": "missing_field",
    "name": "missing_field",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"enabled": False},
    "scoring": {"no_such_field": 1.0},
}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
"""


def _failing_engine(tmp_path) -> StrategyEngine:
    (tmp_path / "missing_field.py").write_text(_MISSING_FIELD, encoding="utf-8")
    return StrategyEngine(strategy_dirs=[tmp_path])


def test_strategy_run_reports_a_validation_error_as_400(tmp_path, monkeypatch):
    from app.services import screener as screener_module

    engine = _failing_engine(tmp_path)
    monkeypatch.setattr(
        screener_module.ScreenerService,
        "build_strategy_context",
        lambda self, engine, as_of, ids, **kwargs: _context(_frame()),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                strategy_engine=engine,
                repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            )
        )
    )

    with pytest.raises(HTTPException) as error:
        strategy_api.run_strategy(
            strategy_api.RunRequest(strategy_id="missing_field", as_of=AS_OF), request
        )
    assert error.value.status_code == 400
    assert "no_such_field" in error.value.detail

    with pytest.raises(HTTPException) as unknown:
        strategy_api.run_strategy(
            strategy_api.RunRequest(strategy_id="not_a_strategy", as_of=AS_OF), request
        )
    assert unknown.value.status_code == 404


def test_optimizer_reports_the_error_every_combination_hit():
    ranked = [{"params": {"p": value}, "error": "turnover_rate 0.00%"} for value in (1, 2)]

    assert "turnover_rate 0.00%" in _no_best_reason(ranked, "sharpe")
    mixed = [{"error": "a"}, {"error": "b"}]
    assert _no_best_reason(mixed, "sharpe") == "every parameter combination failed"


class _FailingOptimizer:
    def optimize(self, cfg, progress_cb=None, cancel_event=None):
        return {
            "best_params": None,
            "best_score": None,
            "results": [],
            "n_completed": 2,
            "error": "every parameter combination failed: turnover_rate 0.00%",
        }


class _CacheStats:
    def cache_stats(self):
        return {"compute_seconds": 0.0, "compute_count": 0, "hit_count": 0, "reuse_count": 0}


def test_walkforward_reports_the_error_every_fold_hit():
    sessions = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(200)]
    service = WalkForwardService(
        _FailingOptimizer(),
        SimpleNamespace(engine=_CacheStats()),
        strategy_engine=None,
        trading_dates_loader=lambda asset_type, start, end: [
            day for day in sessions if start <= day <= end
        ],
    )

    out = service.run(
        WalkForwardConfig(
            strategy_id="s",
            symbols=None,
            start=sessions[0],
            end=sessions[-1],
            param_grid={"p": [1, 2]},
            objective="sortino",
            train_days=90,
            test_days=30,
            step_days=30,
        )
    )

    assert out["n_folds"] == 0
    assert out["n_skipped"] > 1
    assert "turnover_rate 0.00%" in out["error"]


def test_mining_reports_the_error_every_candidate_hit():
    rows = []
    for day_id in range(10):
        for asset_id in range(6):
            target = float(asset_id + (day_id % 2) * 0.1)
            rows.append(
                {
                    "symbol": f"{asset_id:06d}.SZ",
                    "date": date(2024, 1, 2) + timedelta(days=day_id * 2),
                    "good": target,
                    "_next_return": target,
                }
            )
    request = MiningRequest(
        factor_names=("good",),
        budget=MiningBudget(max_combination_size=1, max_proxy_trials=12, max_trials=8),
        validation=NestedValidationConfig(
            outer_train_bars=8,
            outer_test_bars=2,
            outer_step_bars=2,
            inner_train_bars=4,
            inner_test_bars=2,
            inner_step_bars=2,
            purge_bars=0,
            embargo_bars=0,
            min_train_bars=4,
        ),
        profile="exploratory",
    )

    class _Failing:
        def evaluate_candidate(self, train, test, definition):
            raise ValueError("turnover_rate 0.00%")

    result = MiningService().run(
        pl.DataFrame(rows),
        request,
        metric_provider=lambda _train, _names: (FactorMetric("good", 1.0, 1.0, 1.0, 0.1),),
        evaluator=_Failing(),
    )

    assert "turnover_rate 0.00%" in result.folds[0].error
