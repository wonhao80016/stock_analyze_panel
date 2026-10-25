from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl

from app.backtest.engine import BacktestEngine, SimResult
from app.backtest.matrix import build_market_data_matrix, make_signal_matrix
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.services import regime_builder
from app.strategy.engine import StrategyDef


def _write_regime_history(data_dir, rows: pl.DataFrame) -> None:
    path = regime_builder.regime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.sort("date").write_parquet(path)


def _strategy(**kwargs) -> StrategyDef:
    defaults = dict(
        meta={"id": "test", "name": "test", "scoring": {}, "params": [], "limit": 100},
        basic_filter={"enabled": True, "amount_min": 100.0},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=lambda df, params: pl.lit(True),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
        file_path=None,
    )
    defaults.update(kwargs)
    return StrategyDef(**defaults)


class _StrategyEngineStub:
    def __init__(self, strategy: StrategyDef) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategy


class _RepoStub:
    def __init__(self, data_dir=None) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)

    def get_index_daily(self, *args, **kwargs) -> pl.DataFrame:
        return pl.DataFrame()


class _EngineStub:
    def __init__(self, panel: pl.DataFrame, data_dir=None) -> None:
        self.panel = panel
        self.repo = _RepoStub(data_dir)
        self.load_args = None
        self.load_count = 0
        self.sim_panel: pl.DataFrame | None = None
        self.sim_matrix = None
        self.sim_entries: pl.Series | None = None

    def load_panel(self, symbols, start: date, end: date, columns=None, asset_type: str = "stock") -> pl.DataFrame:
        self.load_count += 1
        self.load_args = (symbols, start, end)
        self.load_asset_type = asset_type
        return self.panel

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock") -> pl.DataFrame:
        return self.load_panel(symbols, start, end, columns=sorted(feature_plan.base_columns), asset_type=asset_type)

    def load_market_data_matrix_for_backtest(
        self,
        symbols,
        start,
        end,
        feature_plan,
        asset_type="stock",
        **kwargs,
    ):
        panel = self.load_panel_for_backtest(
            symbols,
            start,
            end,
            feature_plan,
            asset_type=asset_type,
        )
        field_columns = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        return build_market_data_matrix(panel, field_columns=field_columns)

    def simulate_portfolio(self, panel, entries, exits, config, progress_cb=None, cancel_event=None, entry_signal_ids=None, exit_signal_ids=None) -> SimResult:
        self.sim_panel = panel
        self.sim_entries = entries
        return SimResult(
            equity_curve=[{"date": "2024-01-01", "value": config.initial_capital}],
            drawdown_curve=[{"date": "2024-01-01", "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )

    def simulate_market_matrix(
        self,
        matrix,
        config,
        progress_cb=None,
        cancel_event=None,
        options=None,
    ) -> SimResult:
        self.sim_matrix = matrix
        return SimResult(
            equity_curve=[{"date": "2024-01-01", "value": config.initial_capital}],
            drawdown_curve=[{"date": "2024-01-01", "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )


def test_basic_filter_only_limits_entries_not_panel_rows():
    start = date(2024, 1, 1)
    rows = []
    for i, amount in enumerate([1000.0, 0.0, 1000.0]):
        rows.append({
            "symbol": "A",
            "name": "A",
            "date": start + timedelta(days=i),
            "open": 10.0 + i,
            "high": 10.0 + i,
            "low": 10.0 + i,
            "close": 10.0 + i,
            "volume": 100_000,
            "amount": amount,
            "signal_limit_up": False,
            "signal_limit_down": False,
        })
    panel = pl.DataFrame(rows).sort(["symbol", "date"])
    engine = _EngineStub(panel)
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(_strategy()))

    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=start,
        end=start + timedelta(days=2),
        matching="close_t",
        mode="position",
    ))

    assert result.error is None
    assert engine.sim_matrix is not None
    assert engine.sim_matrix.shape == (3, 1)
    assert engine.sim_matrix.entry[:, 0].tolist() == [1, 0, 1]
    assert engine.load_args is not None
    assert engine.load_args[1] < start  # warmup 只用于计算, 不参与正式交易
    assert result.stats["selection"] == {
        "strategy_matches": 2,
        "entry_candidates": 2,
        "entry_trigger_filtered": 0,
        "entry_trigger_enabled": False,
    }


def test_implicit_taiwan_universe_rejects_single_stamp_tax_override():
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {
            "symbol": "2330.TWSE",
            "name": "2330",
            "date": start + timedelta(days=i),
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 100_000,
            "amount": 1000.0,
            "signal_limit_up": False,
            "signal_limit_down": False,
        }
        for i in range(3)
    ])
    engine = _EngineStub(panel)
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(_strategy()))

    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=start,
        end=start + timedelta(days=2),
        matching="close_t",
        stamp_tax_pct=0.0,
    ))

    assert result.error == "stamp_tax_pct cannot override per-instrument Taiwan tax rules"
    assert engine.sim_matrix is None
    assert engine.sim_panel is None


def test_non_matrix_strategy_applies_regime_filter_and_reports_config(tmp_path):
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {
            "symbol": "A",
            "name": "A",
            "date": start + timedelta(days=offset),
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1000.0,
            "amount": 1000.0,
            "signal_limit_up": False,
            "signal_limit_down": False,
        }
        for offset in range(-1, 3)
    ]).sort(["symbol", "date"])
    _write_regime_history(tmp_path, pl.DataFrame({
        "date": [
            start - timedelta(days=1),
            start,
            start + timedelta(days=1),
            start + timedelta(days=2),
        ],
        "state": ["weak", "weak", "strong", "strong"],
        "score": [10, 10, 85, 85],
    }))
    engine = _EngineStub(panel, data_dir=tmp_path)
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(_strategy()))
    regime_filter = {"states": ["strong"]}

    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=start,
        end=start + timedelta(days=2),
        matching="close_t",
        mode="position",
        regime_filter=regime_filter,
    ))

    assert result.error is None
    assert engine.sim_matrix is not None
    assert engine.sim_matrix.entry[:, 0].tolist() == [0, 0, 1]
    assert result.config["regime_filter"] == regime_filter
    assert result.stats["selection"] == {
        "strategy_matches": 1,
        "entry_candidates": 1,
        "entry_trigger_filtered": 0,
        "entry_trigger_enabled": False,
    }


def test_regime_filter_matches_raw_five_level_states(tmp_path):
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {
            "symbol": "A",
            "name": "A",
            "date": start + timedelta(days=offset),
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1000.0,
            "amount": 1000.0,
            "signal_limit_up": False,
            "signal_limit_down": False,
        }
        for offset in range(-1, 3)
    ]).sort(["symbol", "date"])
    _write_regime_history(tmp_path, pl.DataFrame({
        "date": [
            start - timedelta(days=1),
            start,
            start + timedelta(days=1),
            start + timedelta(days=2),
        ],
        "state": ["weak", "lean_strong", "strong", "strong"],
        "score": [10, 60, 85, 85],
    }))

    def run_with(states: list[str]):
        engine = _EngineStub(panel, data_dir=tmp_path)
        service = StrategyBacktestService(
            engine=engine,
            strategy_engine=_StrategyEngineStub(_strategy()),
        )
        result = service.run(StrategyBacktestConfig(
            strategy_id="test",
            symbols=None,
            start=start,
            end=start + timedelta(days=2),
            matching="close_t",
            mode="position",
            regime_filter={"states": states},
        ))
        assert result.error is None
        assert engine.sim_matrix is not None
        return engine.sim_matrix.entry[:, 0].tolist()

    # 强势与偏强是两个独立档位; 只选强势时偏强日不入场
    assert run_with(["strong"]) == [0, 0, 1]
    assert run_with(["strong", "lean_strong"]) == [0, 1, 1]
    assert run_with(["lean_strong"]) == [0, 1, 0]


def test_selection_stats_explain_entry_trigger_filtering():
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {
            "symbol": symbol,
            "name": symbol,
            "date": start,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1000.0,
            "amount": 1000.0,
            "signal_limit_up": symbol == "A",
            "signal_limit_down": False,
        }
        for symbol in ("A", "B")
    ]).sort(["symbol", "date"])
    engine = _EngineStub(panel)
    service = StrategyBacktestService(
        engine=engine,
        strategy_engine=_StrategyEngineStub(
            _strategy(entry_signals=["signal_limit_up"]),
        ),
    )

    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=start,
        end=start,
        matching="close_t",
        mode="position",
    ))

    assert result.error is None
    assert result.stats["selection"] == {
        "strategy_matches": 2,
        "entry_candidates": 1,
        "entry_trigger_filtered": 1,
        "entry_trigger_enabled": True,
    }


def test_score_normalizes_inside_strategy_candidate_universe():
    panel = pl.DataFrame({
        "symbol": ["A", "B", "C"],
        "date": [date(2024, 1, 1)] * 3,
        "factor": [10.0, 20.0, 1000.0],
    })
    universe = pl.Series([True, True, False], dtype=pl.Boolean)
    strategy = SimpleNamespace(meta={"scoring": {"factor": 1.0}, "order_by": "score", "descending": True})

    scored = StrategyBacktestService._apply_score(panel, strategy, None, universe_mask=universe)
    scores = dict(zip(scored["symbol"].to_list(), scored["score"].to_list()))

    assert scores["A"] == 0.0
    assert scores["B"] == 100.0
    assert scores["C"] == 0.0


def test_full_mode_executes_every_candidate_with_strategy_rules():
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {"symbol": "A", "name": "A", "date": start, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1, "amount": 1000.0, "signal_limit_up": False, "signal_limit_down": False},
        {"symbol": "A", "name": "A", "date": start + timedelta(days=1), "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "volume": 1, "amount": 0.0, "signal_limit_up": False, "signal_limit_down": False},
        {"symbol": "A", "name": "A", "date": start + timedelta(days=2), "open": 20.0, "high": 20.0, "low": 20.0, "close": 20.0, "volume": 1, "amount": 1000.0, "signal_limit_up": False, "signal_limit_down": False},
    ]).sort(["symbol", "date"])

    engine = BacktestEngine(repo=None)  # type: ignore[arg-type]
    engine.load_panel_for_backtest = lambda symbols, s, e, plan, asset_type="stock": panel  # type: ignore[method-assign]
    strategy = _strategy(
        filter_fn=lambda df, params: pl.col("date") == start,
        max_hold_days=1,
    )
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(strategy))

    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=start,
        end=start,
        mode="full",
        matching="open_t+1",
        fees_pct=0,
        slippage_bps=0,
        holding_days=1,
    ))

    assert result.error is None
    assert result.stats["full_kind"] == "candidate_execution"
    assert result.stats["n_candidates"] == 1
    assert result.stats["n_trades"] == 1
    assert result.trades[0]["entry_date"] == str(start + timedelta(days=1))
    assert result.trades[0]["exit_reason"] == "max_hold"
    assert result.stats["avg_return"] == round(20 / 11 - 1, 4)


def test_matrix_native_strategy_uses_shared_orchestrator_path():
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {"symbol": "A", "name": "A", "date": start, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0, "amount": 1000.0, "raw_close": 10.0, "raw_high": 10.0, "signal_limit_up": False, "signal_limit_down": False},
        {"symbol": "A", "name": "A", "date": start + timedelta(days=1), "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "volume": 1.0, "amount": 1000.0, "raw_close": 11.0, "raw_high": 11.0, "signal_limit_up": False, "signal_limit_down": False},
    ])

    class NativeStrategy:
        def required_fields(self):
            return frozenset({"open", "high", "low", "close", "volume"})

        def required_warmup_bars(self, params):
            return 1

        def compute_signals(self, market, params):
            return make_signal_matrix(
                market.shape,
                entry=np.ones(market.shape, dtype=np.uint8),
            )

    engine = _EngineStub(panel)
    strategy = _strategy(
        meta={"id": "native", "name": "native", "scoring": {}, "params": [], "limit": 100},
        basic_filter={"enabled": True, "amount_min": 100.0},
        filter_fn=None,
        execution_backend="matrix_native",
        matrix_strategy=NativeStrategy(),
        required_features=frozenset({"amount"}),
    )
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(strategy))

    result = service.run(StrategyBacktestConfig(
        strategy_id="native",
        symbols=None,
        start=start,
        end=start + timedelta(days=1),
        matching="close_t",
        mode="position",
    ))

    assert result.error is None
    assert engine.sim_matrix is not None
    assert engine.sim_matrix.entry[:, 0].tolist() == [1, 1]
    assert result.stats["execution_backend"] == "matrix_native"
    assert result.stats["selection"] == {
        "strategy_matches": 2,
        "entry_candidates": 2,
        "entry_trigger_filtered": 0,
        "entry_trigger_enabled": False,
    }


def test_matrix_native_accepts_legacy_default_signal_overrides_but_rejects_replacements():
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {"symbol": "A", "name": "A", "date": start, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0, "amount": 1000.0, "raw_close": 10.0, "raw_high": 10.0, "signal_limit_up": False, "signal_limit_down": False},
        {"symbol": "A", "name": "A", "date": start + timedelta(days=1), "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "volume": 1.0, "amount": 1000.0, "raw_close": 11.0, "raw_high": 11.0, "signal_limit_up": False, "signal_limit_down": False},
    ])

    class NativeStrategy:
        def required_fields(self):
            return frozenset({"open", "high", "low", "close", "volume"})

        def required_warmup_bars(self, params):
            return 1

        def compute_signals(self, market, params):
            return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))

    engine = _EngineStub(panel)
    strategy = _strategy(
        meta={"id": "native_defaults", "name": "native", "scoring": {}, "params": [], "limit": 100},
        filter_fn=None,
        execution_backend="matrix_native",
        matrix_strategy=NativeStrategy(),
        entry_signals=["signal_custom_entry"],
        exit_signals=["signal_custom_exit"],
        required_features=frozenset(),
    )
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(strategy))

    accepted = service.run(StrategyBacktestConfig(
        strategy_id="native_defaults",
        symbols=None,
        start=start,
        end=start + timedelta(days=1),
        matching="close_t",
        overrides={
            "entry_signals": ["signal_custom_entry"],
            "exit_signals": ["signal_custom_exit"],
        },
    ))
    assert accepted.error is None

    rejected = service.run(StrategyBacktestConfig(
        strategy_id="native_defaults",
        symbols=None,
        start=start,
        end=start + timedelta(days=1),
        matching="close_t",
        overrides={"entry_signals": ["signal_other"]},
    ))
    assert rejected.error == "matrix_native 策略的进出场信号由策略协议生成，不支持列信号覆盖"


def test_result_reports_the_resolved_minimum_commission():
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {
            "symbol": "A",
            "name": "A",
            "date": start + timedelta(days=i),
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 100_000,
            "amount": 1000.0,
            "signal_limit_up": False,
            "signal_limit_down": False,
        }
        for i in range(2)
    ])
    cases = [("board_lot", None, 20.0), ("odd_lot", None, 1.0), ("odd_lot", 7.0, 7.0)]
    for lot_mode, requested, expected in cases:
        service = StrategyBacktestService(
            engine=_EngineStub(panel),
            strategy_engine=_StrategyEngineStub(_strategy()),
        )

        result = service.run(StrategyBacktestConfig(
            strategy_id="test",
            symbols=None,
            start=start,
            end=start + timedelta(days=1),
            lot_mode=lot_mode,
            minimum_commission=requested,
        ))

        assert result.error is None
        assert result.stats["minimum_commission"] == expected
        assert result.config["minimum_commission"] == expected
