"""A polars_expr strategy and its matrix_native twin trade alike through StrategyBacktestService.

Both score a session's entries (signal and filter passed) among themselves, and both drop an
open_t+1 entry whose next trading session has no bar for the asset.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.matrix import build_market_data_matrix, make_signal_matrix
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.engine import StrategyDef

START = date(2024, 1, 1)
TWO_FACTORS = {"volume": 0.6, "amount": 0.4}


class _Engine:
    """Serves a fixed panel to both strategy backends and runs the real matcher."""

    def __init__(self, panel: pl.DataFrame, data_dir=None) -> None:
        self.panel = panel
        self.repo = SimpleNamespace(
            store=SimpleNamespace(data_dir=data_dir),
            get_index_daily=lambda *args, **kwargs: pl.DataFrame(),
        )
        self.matcher = BacktestEngine(repo=None)  # type: ignore[arg-type]
        self.sim_matrix = None

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock", **kwargs):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        return build_market_data_matrix(self.panel, field_columns=fields)

    def simulate_market_matrix(self, matrix, config, progress_cb=None, cancel_event=None, options=None):
        self.sim_matrix = matrix
        return self.matcher.simulate_market_matrix(matrix, config, progress_cb, cancel_event, options)


class _Strategies:
    def __init__(self, strategy: StrategyDef) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategy


class _NativeEntries:
    """matrix_native twin whose entries are the panel's signal_limit_up rows."""

    def __init__(self, panel: pl.DataFrame) -> None:
        self.marks = {
            (row["symbol"], str(row["date"]))
            for row in panel.filter(pl.col("signal_limit_up")).iter_rows(named=True)
        }

    def required_fields(self):
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        entry = np.zeros(market.shape, dtype=np.uint8)
        for time_id, label in enumerate(market.timestamp_labels):
            for asset_id, symbol in enumerate(market.symbols):
                entry[time_id, asset_id] = (symbol, label[:10]) in self.marks
        return make_signal_matrix(market.shape, entry=entry)


def _panel(day0: dict[str, tuple[float, float, bool]], days: int = 4) -> pl.DataFrame:
    """day0 maps symbol -> (volume, amount, entry signal); later sessions are quiet."""
    rows = []
    for symbol, (volume, amount, signal) in day0.items():
        for day in range(days):
            rows.append({
                "symbol": symbol,
                "name": symbol,
                "date": START + timedelta(days=day),
                "open": 10.0 + day,
                "high": 10.0 + day,
                "low": 10.0 + day,
                "close": 10.0 + day,
                "volume": volume if day == 0 else 100.0,
                "amount": amount if day == 0 else 1000.0,
                "raw_close": 10.0 + day,
                "raw_high": 10.0 + day,
                "signal_limit_up": signal and day == 0,
                "signal_limit_down": False,
            })
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _strategy(scoring: dict[str, float], native: _NativeEntries | None = None) -> StrategyDef:
    return StrategyDef(
        meta={"id": "twin", "name": "twin", "scoring": scoring, "order_by": "score", "descending": True,
              "params": [], "limit": 100},
        basic_filter={"enabled": False},
        entry_signals=[] if native else ["signal_limit_up"],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=1,
        filter_fn=None if native else (lambda df, params: pl.lit(True)),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
        required_features=frozenset({"amount"}),
        execution_backend="matrix_native" if native else "polars_expr",
        matrix_strategy=native,
    )


def _run(panel: pl.DataFrame, strategy: StrategyDef, data_dir=None, *, expect_trades: bool = True):
    engine = _Engine(panel, data_dir)
    result = StrategyBacktestService(engine=engine, strategy_engine=_Strategies(strategy)).run(
        StrategyBacktestConfig(
            strategy_id="twin",
            symbols=None,
            start=START,
            end=START + timedelta(days=3),
            matching="open_t+1",
            fees_pct=0,
            slippage_bps=0,
            max_positions=1,
            initial_capital=100_000,
        )
    )
    assert (result.error is None) is expect_trades, result.error
    return result, engine


def _both(panel: pl.DataFrame, scoring: dict[str, float]) -> tuple[list[dict], list[dict]]:
    polars_result, _ = _run(panel, _strategy(scoring))
    native_result, _ = _run(panel, _strategy(scoring, _NativeEntries(panel)))
    return polars_result.trades, native_result.trades


def _summary(trades: list[dict]) -> list[tuple]:
    keys = ("symbol", "entry_date", "exit_date", "entry_price", "exit_price", "exit_reason", "shares",
            "pnl_amount", "entry_score")
    return [tuple(trade[key] for key in keys) for trade in trades]


# A and B signal; C passes the filter without a signal and stretches both factor ranges. Among A
# and B, B ranks first (60 vs 40); with C in the range A would rank first (40 vs 0.6).
SIGNAL_AND_FILTER_ONLY = {"A": (1.0, 1000.0, True), "B": (2.0, 0.0, True), "C": (100.0, 500.0, False)}


def test_multi_factor_polars_strategy_trades_like_its_matrix_twin():
    polars_trades, native_trades = _both(_panel(SIGNAL_AND_FILTER_ONLY), TWO_FACTORS)

    assert _summary(polars_trades) == _summary(native_trades)
    assert [(t["symbol"], t["entry_score"]) for t in polars_trades] == [("B", 60.0)]


def test_single_factor_polars_strategy_keeps_its_order_and_matches_its_matrix_twin():
    polars_trades, native_trades = _both(_panel(SIGNAL_AND_FILTER_ONLY), {"volume": 1.0})

    assert _summary(polars_trades) == _summary(native_trades)
    assert [(t["symbol"], t["entry_score"]) for t in polars_trades] == [("B", 100.0)]


def test_a_lone_entry_scores_fifty_in_both_engines():
    day0 = {"A": (1.0, 1000.0, True), "B": (2.0, 0.0, False), "C": (100.0, 500.0, False)}
    polars_trades, native_trades = _both(_panel(day0), TWO_FACTORS)

    assert _summary(polars_trades) == _summary(native_trades)
    assert [(t["symbol"], t["entry_score"]) for t in polars_trades] == [("A", 50.0)]


@pytest.mark.parametrize("has_next_session_bar", [False, True])
def test_polars_entry_is_dropped_when_the_next_calendar_session_has_no_bar(tmp_path, has_next_session_bar):
    panel = _panel({"A": (100.0, 1000.0, True)}, days=5)
    if not has_next_session_bar:
        panel = panel.filter(pl.col("date") != START + timedelta(days=1))
    for day in range(5):
        partition = tmp_path / "kline_daily_enriched" / f"date={START + timedelta(days=day)}"
        partition.mkdir(parents=True)
        pl.DataFrame({"symbol": ["A"]}).write_parquet(partition / "part.parquet")

    result, engine = _run(
        panel, _strategy({"volume": 1.0}), data_dir=tmp_path, expect_trades=has_next_session_bar,
    )

    if has_next_session_bar:
        assert [(t["entry_signal_date"], t["entry_date"]) for t in result.trades] == [
            ("2024-01-01", "2024-01-02")
        ]
    else:
        assert result.trades == []
        assert int(engine.sim_matrix.entry.sum()) == 0
