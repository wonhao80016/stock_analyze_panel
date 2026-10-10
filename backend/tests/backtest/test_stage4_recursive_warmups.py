"""Backtest and selection warm-ups cover the recursive indicators a strategy reads (stage 4, D7).

Under SMA- and Wilder-seeded EMA/MACD/RSI/ATR a value stops depending on where the loaded window
starts only after recursive_warmup_bars sessions. Polars backtests recompute every indicator their
filter or signals read, and a matrix strategy computes what its entry rule reads, on that window.
"""

from __future__ import annotations

import ast
import types
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

import app.backtest as backtest_package
from app.backtest import strategy as backtest_strategy
from app.backtest.engine import BacktestEngine
from app.backtest.matrix import build_market_data_matrix, matrix_feature
from app.backtest.strategy import StrategyDependencyResolver
from app.indicators.pipeline import INDICATOR_COLUMNS, compute_indicators
from app.strategy import scoring
from app.strategy.engine import StrategyDef, StrategyEngine
from app.strategy.scoring import recursive_warmup_bars

pytestmark = pytest.mark.usefixtures("deny_network")

BACKTEST_DIR = Path(backtest_package.__file__).resolve().parent
BUILTIN_DIR = BACKTEST_DIR.parent / "strategy" / "builtin"
SYMBOLS = ("1101.TWSE", "2330.TWSE", "6488.TPEX")


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _weekdays(date(2023, 1, 2), 700)


def _storage_rows() -> pl.DataFrame:
    """A steep first year then a random walk, so an indicator seeded inside the climb stays off."""
    rng = np.random.default_rng(7)
    frames = []
    for index, symbol in enumerate(SYMBOLS):
        drift = np.where(np.arange(len(DAYS)) < 250, 0.006 - 0.004 * index, 0.0)
        close = (30.0 + 25.0 * index) * np.exp(np.cumsum(drift + rng.normal(0.0, 0.02, len(DAYS))))
        spread = close * rng.uniform(0.005, 0.03, len(DAYS))
        frames.append(
            pl.DataFrame(
                {
                    "symbol": [symbol] * len(DAYS),
                    "date": DAYS,
                    "open": close * (1.0 + rng.normal(0.0, 0.005, len(DAYS))),
                    "high": close + spread,
                    "low": close - spread,
                    "close": close,
                    "volume": rng.uniform(5e5, 3e6, len(DAYS)),
                    "amount": close * 1e6,
                    "raw_close": close,
                    "raw_high": close + spread,
                    "raw_low": close - spread,
                }
            )
        )
    return pl.concat(frames).sort(["symbol", "date"])


STORAGE = _storage_rows()
FULL = compute_indicators(STORAGE)


def _strategy(**overrides) -> StrategyDef:
    values = dict(
        meta={"id": "recursive_reader", "scoring": {"change_pct": 1.0}, "order_by": "score"},
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=lambda df, params: pl.col("close") > pl.col("ema60"),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
    )
    values.update(overrides)
    return StrategyDef(**values)


def _backtest_warmup_days(bars: int) -> int:
    """Calendar days a backtest loads before its formal start for a plan of *bars* sessions."""
    return max(120, int(max(bars, 1) * 1.6))


def _plan(strategy: StrategyDef, *, entry_signals=(), exit_signals=()):
    return StrategyDependencyResolver().resolve(
        strategy,
        params={},
        basic_filter={"enabled": False},
        entry_signals=list(entry_signals),
        exit_signals=list(exit_signals),
    )


def test_the_calendar_days_per_session_ratio_is_defined_once():
    literals = []
    for name in ("strategy.py", "mining_runtime.py", "factor.py"):
        tree = ast.parse((BACKTEST_DIR / name).read_text(encoding="utf-8"))
        literals += [
            f"{name}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == 1.6
        ]

    assert literals == []
    assert scoring.CALENDAR_DAYS_PER_SESSION == 1.6
    for bars in (0, 1, 60, 75, 140, 337):
        assert backtest_strategy.warmup_calendar_days(bars) == _backtest_warmup_days(bars)


def test_a_polars_filter_reading_ema60_loads_its_recursive_warmup():
    plan = _plan(_strategy())

    assert "ema60" in plan.indicator_columns
    assert plan.warmup_bars >= recursive_warmup_bars("ema60")


def test_a_polars_entry_signal_loads_the_warmup_of_its_indicators_and_the_bar_before():
    strategy = _strategy(filter_fn=lambda df, params: pl.col("close") > 0)

    plan = _plan(strategy, entry_signals=["signal_macd_golden"])

    assert plan.warmup_bars >= recursive_warmup_bars("macd_dea") + 1


def test_a_full_feature_fallback_loads_the_warmup_of_every_indicator():
    strategy = _strategy(
        filter_fn=None, filter_history_fn=lambda history, params: history, lookback_days=20
    )

    plan = _plan(strategy)

    assert plan.full_feature_fallback is True
    assert plan.warmup_bars >= max(recursive_warmup_bars(name) for name in INDICATOR_COLUMNS)


def test_a_polars_filter_without_recursive_indicators_keeps_its_window():
    strategy = _strategy(filter_fn=lambda df, params: pl.col("close") > pl.col("ma20"))

    assert _plan(strategy).warmup_bars == 60


def _write_partitions(data_dir: Path) -> None:
    for (day,), part in STORAGE.partition_by("date", as_dict=True).items():
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)


def test_a_polars_backtest_panel_matches_the_full_history_from_its_first_formal_session(tmp_path):
    _write_partitions(tmp_path)
    instruments = pl.DataFrame({"symbol": list(SYMBOLS), "name": list(SYMBOLS)})
    repo = types.SimpleNamespace(
        store=types.SimpleNamespace(data_dir=tmp_path),
        get_enriched_range=lambda *args, **kwargs: None,
        get_instruments_asset=lambda asset_type: instruments,
        get_historical_shares=lambda: pl.DataFrame(),
    )
    plan = _plan(_strategy(), entry_signals=["signal_macd_golden"])
    start = DAYS[-60]

    panel = BacktestEngine(repo).load_panel_for_backtest(
        None, start - timedelta(days=_backtest_warmup_days(plan.warmup_bars)), DAYS[-1], plan
    )

    columns = ["ema60", "macd_dif", "macd_dea"]
    loaded = (
        panel.filter(pl.col("date") >= start)
        .select(["symbol", "date", *columns])
        .sort(["symbol", "date"])
    )
    full = (
        FULL.filter(pl.col("date") >= start)
        .select(["symbol", "date", *columns])
        .sort(["symbol", "date"])
    )
    assert loaded.height == full.height
    for name in columns:
        np.testing.assert_allclose(loaded[name].to_numpy(), full[name].to_numpy(), rtol=1e-4)


# Entry rules of the builtins read rsi_14 whether or not a saved override keeps it in scoring.
RSI_ENTRY_BUILTINS = ("oversold_bounce", "oversold_reversal")
NO_RSI_SCORING = {"scoring": {"change_pct": 1.0}, "scoring_replace": True}


@pytest.fixture(scope="module")
def builtin_engine() -> StrategyEngine:
    return StrategyEngine(strategy_dirs=[BUILTIN_DIR])


@pytest.mark.parametrize("strategy_id", RSI_ENTRY_BUILTINS)
def test_rsi_entry_builtins_declare_the_rsi_warmup(builtin_engine, strategy_id):
    strategy = builtin_engine.get(strategy_id)
    params = StrategyEngine.resolve_params(strategy)

    assert params["use_rsi_filter"] is True
    assert strategy.matrix_strategy.required_warmup_bars(params) >= recursive_warmup_bars("rsi_14")
    assert strategy.matrix_strategy.required_warmup_bars({**params, "use_rsi_filter": False}) == 60


@pytest.mark.parametrize("strategy_id", RSI_ENTRY_BUILTINS)
def test_an_override_without_rsi_scoring_keeps_the_entry_rsi_warmup(builtin_engine, strategy_id):
    strategy = builtin_engine.get(strategy_id)
    params = StrategyEngine.resolve_params(strategy)

    plan = StrategyDependencyResolver().resolve(
        strategy,
        params=params,
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
        overrides=NO_RSI_SCORING,
    )
    selection_bars = builtin_engine.required_history_bars(
        [strategy_id], overrides_map={strategy_id: NO_RSI_SCORING}
    )

    assert plan.warmup_bars >= recursive_warmup_bars("rsi_14")
    assert selection_bars >= recursive_warmup_bars("rsi_14")


@pytest.mark.parametrize("strategy_id", RSI_ENTRY_BUILTINS)
def test_the_entry_rsi_on_the_first_scored_bar_matches_the_full_history(
    builtin_engine, strategy_id
):
    strategy = builtin_engine.get(strategy_id)
    params = StrategyEngine.resolve_params(strategy)
    plan = StrategyDependencyResolver().resolve(
        strategy,
        params=params,
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
        overrides=NO_RSI_SCORING,
    )
    days = STORAGE["date"].unique().sort()
    window = STORAGE.filter(pl.col("date") >= days[-plan.warmup_bars])

    rsi = matrix_feature(build_market_data_matrix(window), "rsi_14")[-1]

    published = FULL.filter(pl.col("date") == days[-1]).sort("symbol")["rsi_14"].to_numpy()
    np.testing.assert_allclose(rsi, published, atol=0.01)
