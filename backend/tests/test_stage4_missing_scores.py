"""A score that cannot be computed is null/NaN, kept as a candidate and ranked last everywhere."""

from __future__ import annotations

import math
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig, SimResult
from app.backtest.matrix import (
    build_basic_filter_mask,
    build_market_data_matrix,
    build_market_matrix_from_signals,
    build_matrix_score,
    make_signal_matrix,
)
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.engine import StrategyDataContext, StrategyDef, StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

AS_OF = date(2026, 1, 2)
WEIGHTS = {"f1": 0.7, "turnover_rate": 0.3}

# E is an ETF-like row: the highest f1 but no turnover, so its score cannot be computed.
# D has the lowest value of every input, so 0 is its valid, lowest score.
# F has the same inputs as B; the tie is broken by symbol.
_FIXTURE = {
    "symbol": ["F", "E", "D", "C", "B", "A"],
    "f1": [3.0, 6.0, 1.0, 4.0, 3.0, 5.0],
    "turnover_rate": [1.0, None, 0.0, 1.5, 1.0, 2.0],
}


def _frame() -> pl.DataFrame:
    count = len(_FIXTURE["symbol"])
    return pl.DataFrame(
        {
            **_FIXTURE,
            "name": _FIXTURE["symbol"],
            "date": [AS_OF] * count,
            "open": [10.0] * count,
            "high": [10.0] * count,
            "low": [10.0] * count,
            "close": [10.0] * count,
            "raw_close": [10.0] * count,
            "volume": [1_000.0] * count,
            "amount": [1e9] * count,
        }
    )


def _polars_strategy_code(strategy_id: str) -> str:
    return f'''import polars as pl
META = {{
    "id": "{strategy_id}",
    "name": "{strategy_id}",
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "scoring": {WEIGHTS!r},
    "order_by": "score",
    "descending": True,
}}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
'''


def _matrix_strategy_code(strategy_id: str) -> str:
    return f'''import numpy as np
from app.backtest.matrix import make_signal_matrix
META = {{
    "id": "{strategy_id}",
    "name": "{strategy_id}",
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "scoring": {WEIGHTS!r},
    "order_by": "score",
    "descending": True,
}}
EXECUTION_BACKEND = "matrix_native"
class _AllEntries:
    def required_fields(self):
        return frozenset({{"close"}})
    def required_warmup_bars(self, params):
        return 1
    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))
MATRIX_STRATEGY = _AllEntries()
'''


def _composite_code(strategy_id: str, child_id: str) -> str:
    return f'''META = {{
    "id": "{strategy_id}",
    "name": "{strategy_id}",
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [{{"id": "merge_mode", "type": "select", "options": ["union", "intersect"], "default": "union"}}],
    "children": [{{"strategy_id": "{child_id}", "weight": 1.0}}],
}}
EXECUTION_BACKEND = "composite"
'''


@pytest.fixture
def engine(tmp_path) -> StrategyEngine:
    custom = tmp_path / "custom"
    composite = tmp_path / "composite"
    custom.mkdir()
    composite.mkdir()
    (custom / "polars_child.py").write_text(_polars_strategy_code("polars_child"), encoding="utf-8")
    (custom / "matrix_child.py").write_text(_matrix_strategy_code("matrix_child"), encoding="utf-8")
    (composite / "blend.py").write_text(_composite_code("blend", "polars_child"), encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[custom, composite])
    assert engine.load_errors() == []
    return engine


def _context() -> StrategyDataContext:
    frame = _frame()
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=AS_OF,
        current=frame,
        history=frame,
    )


_NO_BASIC_FILTER = {"basic_filter": {"enabled": False}}


def _by_symbol(frame: pl.DataFrame) -> dict[str, float | None]:
    return dict(zip(frame["symbol"].to_list(), frame["score"].to_list(), strict=True))


def test_polars_backtest_and_matrix_scorers_agree_on_missing_input():
    frame = _frame()
    polars_scores = _by_symbol(StrategyEngine._apply_scoring(frame, WEIGHTS))
    backtest_scores = _by_symbol(
        StrategyBacktestService._apply_score(
            frame,
            SimpleNamespace(meta={"scoring": WEIGHTS, "order_by": "score"}),
            None,
        )
    )
    market = build_market_data_matrix(frame, field_columns=set(WEIGHTS))
    matrix_row = build_matrix_score(
        market,
        np.ones(market.shape, dtype=bool),
        WEIGHTS,
        "score",
        True,
        fallback=np.zeros(market.shape, dtype=np.float32),
    )[0]
    matrix_scores = {
        symbol: float(matrix_row[asset_id]) for asset_id, symbol in enumerate(market.symbols)
    }

    assert polars_scores["E"] is None
    assert backtest_scores["E"] is None
    assert math.isnan(matrix_scores["E"])
    assert polars_scores["D"] == 0.0
    assert backtest_scores["D"] == 0.0
    assert matrix_scores["D"] == 0.0
    for symbol in ("A", "B", "C", "D", "F"):
        assert backtest_scores[symbol] == pytest.approx(polars_scores[symbol])
        assert matrix_scores[symbol] == pytest.approx(polars_scores[symbol], abs=1e-4)


def test_all_null_factor_gives_null_scores_not_neutral():
    frame = _frame().with_columns(pl.lit(None, dtype=pl.Float64).alias("turnover_rate"))

    scored = StrategyEngine._apply_scoring(frame, WEIGHTS)

    assert scored["score"].null_count() == frame.height


def test_scoring_field_without_its_dependency_raises():
    frame = _frame().drop("turnover_rate")

    with pytest.raises(ValueError, match="turnover_rate"):
        StrategyEngine._apply_scoring(frame, WEIGHTS)
    with pytest.raises(ValueError, match="turnover_rate"):
        StrategyBacktestService._apply_score(
            frame,
            SimpleNamespace(meta={"scoring": WEIGHTS, "order_by": "score"}),
            None,
        )


def test_polars_screener_ranks_missing_last_and_ties_by_symbol(engine):
    result = engine.run("polars_child", _context(), overrides=_NO_BASIC_FILTER)

    order = [row["symbol"] for row in result.rows]
    assert order == ["A", "C", "B", "F", "D", "E"]
    assert result.scores["E"] is None
    assert result.rows[-1]["score"] is None
    assert result.scores["D"] == 0.0


def test_matrix_screener_etf_without_turnover_scores_none_and_ranks_last(engine):
    result = engine.run("matrix_child", _context(), overrides=_NO_BASIC_FILTER)

    order = [row["symbol"] for row in result.rows]
    assert order == ["A", "C", "B", "F", "D", "E"]
    assert result.scores["E"] is None
    assert result.rows[-1]["score"] is None
    assert result.scores["D"] == 0.0


def test_composite_ranks_a_child_missing_score_last(engine):
    result = engine.run("blend", _context(), overrides=_NO_BASIC_FILTER)

    order = [row["symbol"] for row in result.rows]
    assert order[-1] == "E"
    assert set(order) == {"A", "B", "C", "D", "E", "F"}


def test_matrix_bound_on_all_nan_field_excludes_rows():
    frame = _frame().with_columns(pl.lit(None, dtype=pl.Float64).alias("turnover_rate"))
    market = build_market_data_matrix(frame, field_columns={"turnover_rate"})

    mask = build_basic_filter_mask(market, {"enabled": True, "turnover_min": 0.5})

    assert not mask.any()


def test_polars_bound_excludes_null_and_nan_values():
    frame = _frame().with_columns(
        pl.Series("turnover_rate", [1.0, None, float("nan"), 1.5, 1.0, 2.0]),
    )

    kept = StrategyEngine._apply_basic_filter(frame, {"turnover_min": 0.5})

    assert sorted(kept["symbol"].to_list()) == ["A", "B", "C", "F"]


def test_bound_on_unknown_field_raises():
    frame = _frame().drop("turnover_rate")
    market = build_market_data_matrix(frame)

    with pytest.raises(ValueError, match="turnover_rate"):
        build_basic_filter_mask(market, {"enabled": True, "turnover_min": 0.5})
    with pytest.raises(ValueError, match="turnover_rate"):
        StrategyEngine._apply_basic_filter(frame, {"turnover_min": 0.5})


def _execution_panel(scores: dict[str, float | None]) -> pl.DataFrame:
    start = date(2024, 1, 1)
    rows = []
    for symbol, score in scores.items():
        for offset in range(3):
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": start + timedelta(days=offset),
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 100_000.0,
                    "score": score if offset == 0 else 0.0,
                    "signal_limit_up": False,
                    "signal_limit_down": False,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _first_day_entries(panel: pl.DataFrame) -> pl.Series:
    return panel.select((pl.col("date") == date(2024, 1, 1)).alias("entry"))["entry"]


def _config(**kwargs) -> MatcherConfig:
    return MatcherConfig(
        matching="open_t+1",
        fees_pct=0,
        slippage_bps=0,
        initial_capital=1_000_000,
        **kwargs,
    )


def test_backtest_ranks_missing_score_below_negative_scores():
    panel = _execution_panel({"A": None, "B": -5.0, "C": 10.0})
    exits = pl.Series([False] * panel.height)

    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _first_day_entries(panel),
        exits,
        _config(max_positions=2),
    )

    assert {trade.symbol for trade in result.trades} == {"B", "C"}


def test_backtest_score_min_excludes_missing_scores():
    panel = _execution_panel({"A": None, "B": 50.0, "C": 80.0})
    exits = pl.Series([False] * panel.height)

    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _first_day_entries(panel),
        exits,
        _config(max_positions=3, score_min=-10.0),
    )

    assert {trade.symbol for trade in result.trades} == {"B", "C"}
    assert result.stats["execution"]["buy_score_filter"] == 1


def test_backtest_score_weighted_sizing_ignores_missing_scores():
    panel = _execution_panel({"A": None, "B": 1.0, "C": 3.0})
    market = build_market_data_matrix(panel)
    score = np.zeros(market.shape, dtype=np.float32)
    score[0] = [np.nan, 1.0, 3.0]
    entry = np.zeros(market.shape, dtype=np.uint8)
    entry[0] = 1
    matrix = build_market_matrix_from_signals(
        market,
        make_signal_matrix(market.shape, entry=entry, score=score),
        entry_delay_bars=1,
        exit_delay_bars=1,
    )

    result = BacktestEngine(repo=None).simulate_market_matrix(
        matrix,
        _config(max_positions=3, position_sizing="score_weight"),
    )

    by_symbol = {trade.symbol: trade for trade in result.trades}
    assert set(by_symbol) == {"B", "C"}
    # B receives 1/4 of the budget; C's 3/4 share is capped at one slot (1/3).
    assert by_symbol["B"].position_pct == pytest.approx(0.25, abs=1e-3)
    assert by_symbol["C"].position_pct == pytest.approx(1 / 3, abs=1e-3)
    assert all(trade.entry_score is not None for trade in result.trades)


def test_backtest_trade_with_missing_score_records_none():
    panel = _execution_panel({"A": None})
    exits = pl.Series([False] * panel.height)

    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _first_day_entries(panel),
        exits,
        _config(max_positions=1),
    )

    assert [trade.entry_score for trade in result.trades] == [None]


class _StrategyEngineStub:
    def __init__(self, strategy: StrategyDef) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategy


class _BacktestEngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = SimpleNamespace(store=SimpleNamespace(data_dir=None))
        self.simulated = False

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs
    ):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
            | {"f1", "turnover_rate"}
        )
        return build_market_data_matrix(self.panel, field_columns=fields)

    def _result(self, config) -> SimResult:
        self.simulated = True
        return SimResult(
            equity_curve=[{"date": "2024-01-01", "value": config.initial_capital}],
            drawdown_curve=[{"date": "2024-01-01", "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )

    def simulate_market_matrix(
        self, matrix, config, progress_cb=None, cancel_event=None, options=None
    ):
        return self._result(config)


def _coverage_panel(turnover: dict[str, list[float | None]]) -> pl.DataFrame:
    """One row per symbol and day from 2024-01-01; a symbol without turnover has a tiny amount."""
    start = date(2024, 1, 1)
    rows = []
    for symbol, values in turnover.items():
        for offset, value in enumerate(values):
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": start + timedelta(days=offset),
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1_000.0,
                    "amount": 1e9 if any(v is not None for v in values) else 1.0,
                    "raw_close": 10.0,
                    "raw_high": 10.0,
                    "raw_low": 10.0,
                    "f1": float(len(rows) % 3),
                    "turnover_rate": value,
                    "signal_limit_up": False,
                    "signal_limit_down": False,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


class _AllEntries:
    def __init__(self, parameter_fields: frozenset[str] = frozenset()) -> None:
        self._parameter_fields = parameter_fields

    def required_fields(self):
        return frozenset({"close"})

    def required_fields_for_params(self, params):
        return self._parameter_fields

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


def _strategy(
    backend: str,
    scoring: dict,
    basic_filter: dict | None = None,
    parameter_fields: frozenset[str] = frozenset(),
) -> StrategyDef:
    return StrategyDef(
        meta={"id": "cov", "name": "cov", "scoring": scoring, "params": [], "limit": 100},
        basic_filter=basic_filter or {"enabled": False},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=None if backend == "matrix_native" else (lambda df, params: pl.lit(True)),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
        execution_backend=backend,
        matrix_strategy=_AllEntries(parameter_fields) if backend == "matrix_native" else None,
    )


def _run_backtest(
    backend: str,
    turnover: dict[str, list[float | None]],
    scoring: dict,
    basic_filter=None,
    parameter_fields: frozenset[str] = frozenset(),
):
    engine = _BacktestEngineStub(_coverage_panel(turnover))
    service = StrategyBacktestService(
        engine=engine,
        strategy_engine=_StrategyEngineStub(
            _strategy(backend, scoring, basic_filter, parameter_fields)
        ),
    )
    result = service.run(
        StrategyBacktestConfig(
            strategy_id="cov",
            symbols=None,
            start=date(2024, 1, 2),
            end=date(2024, 1, 4),
            matching="close_t",
            mode="position",
        )
    )
    return result, engine


_BACKENDS = ["polars_expr", "matrix_native"]
_TURNOVER_SCORING = {"f1": 0.5, "turnover_rate": 0.5}


@pytest.mark.parametrize("backend", _BACKENDS)
def test_backtest_refuses_scoring_field_without_coverage(backend):
    result, engine = _run_backtest(backend, {"A": [1.0, None, None, None]}, _TURNOVER_SCORING)

    assert result.error is not None
    assert "換手率" in result.error
    assert not engine.simulated


@pytest.mark.parametrize("backend", _BACKENDS)
def test_backtest_refuses_scoring_field_below_half_of_candidates(backend):
    result, engine = _run_backtest(backend, {"A": [1.0, 1.0, None, None]}, _TURNOVER_SCORING)

    assert result.error is not None
    assert "換手率 33%" in result.error
    assert not engine.simulated


@pytest.mark.parametrize("backend", _BACKENDS)
def test_backtest_refuses_filter_field_without_coverage(backend):
    result, engine = _run_backtest(
        backend,
        {"A": [1.0, None, None, None]},
        {"f1": 1.0},
        {"enabled": True, "turnover_min": 0.5},
    )

    assert result.error is not None
    assert "換手率" in result.error
    assert not engine.simulated


@pytest.mark.parametrize("backend", _BACKENDS)
def test_backtest_reports_partial_field_coverage(backend):
    result, engine = _run_backtest(backend, {"A": [1.0, 1.0, None, 2.0]}, _TURNOVER_SCORING)

    assert result.error is None
    assert engine.simulated
    assert result.stats["field_coverage"]["turnover_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert result.stats["field_coverage"]["f1"] == 1.0


@pytest.mark.parametrize("backend", _BACKENDS)
def test_scoring_coverage_counts_only_candidate_rows(backend):
    result, engine = _run_backtest(
        backend,
        {"A": [1.0, 1.0, 1.0, 1.0], "B": [None] * 4, "C": [None] * 4},
        _TURNOVER_SCORING,
        {"enabled": True, "amount_min": 100.0},
    )

    assert result.error is None
    assert engine.simulated
    assert result.stats["field_coverage"]["turnover_rate"] == 1.0


def test_parameter_scoring_fields_are_coverage_checked():
    result, engine = _run_backtest(
        "matrix_native",
        {"A": [1.0, 1.0, None, None], "B": [1.0, 1.0, None, None]},
        {},
        parameter_fields=frozenset({"turnover_rate"}),
    )

    assert result.error is not None
    assert "換手率 33%" in result.error
    assert not engine.simulated
