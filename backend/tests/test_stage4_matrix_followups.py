"""Matrix strategy layer: live amount estimates, monitor history, field ordering, composite warm-up."""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.matrix import build_market_data_matrix, matrix_feature
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.repository import HISTORY_WARMUP_SESSIONS
from app.services.screener import ScreenerService
from app.services.strategy_pick_backfill import input_quality
from app.strategy import monitor_rules
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.strategy.monitor import MonitorRuleEngine
from app.strategy.scoring import AMOUNT_ESTIMATED, TRADED_AMOUNT_FIELDS

pytestmark = pytest.mark.usefixtures("deny_network")


def _sessions(count: int, first: date = date(2025, 1, 2)) -> list[date]:
    out, day = [], first
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _panel(symbols, days, *, closes=None) -> pl.DataFrame:
    rng = np.random.default_rng(7)
    frames = []
    for index, symbol in enumerate(symbols):
        base = 20.0 + 10.0 * index if closes is None else closes[symbol]
        close = base * np.cumprod(1.0 + rng.normal(0.0, 0.01, len(days)))
        if closes is not None:
            close = np.full(len(days), base)
        volume = rng.lognormal(13.0, 0.3, len(days))
        frames.append(
            pl.DataFrame(
                {
                    "symbol": [symbol] * len(days),
                    "name": [symbol[:4]] * len(days),
                    "date": days,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "raw_close": close,
                    "raw_high": close * 1.01,
                    "raw_low": close * 0.99,
                    "volume": volume,
                    "amount": close * volume * 1.002,
                }
            )
        )
    return pl.concat(frames).sort(["symbol", "date"])


def _write(directory, name: str, source: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(source, encoding="utf-8")


def _history_loader(panel: pl.DataFrame):
    """The last lookback + 1 sessions through the target, as ScreenerService loads them."""
    days = panel["date"].unique().sort().to_list()

    def load(target: date, lookback: int) -> pl.DataFrame:
        through = [day for day in days if day <= target]
        return panel.filter(pl.col("date").is_in(through[-(lookback + 1) :]))

    return load


# --------------------------------------------------------------------------------------------
# Live amount estimates
# --------------------------------------------------------------------------------------------

AMOUNT_SCORED = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "amount_scored", "name": "amount scored", "asset_types": ["stock"],
        "scoring": {"vwap_bias": 1.0}, "limit": 0}
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

AMOUNT_FILTERED = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "amount_filtered", "name": "amount filtered", "asset_types": ["stock"], "limit": 0}
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

ESTIMATED, QUOTED = "2330.TWSE", "2317.TWSE"


def _live(panel: pl.DataFrame, day: date, estimated: dict[str, bool]) -> pl.DataFrame:
    """The rows of ``day`` as live quotes; a flagged symbol's amount is an estimate of half the official one."""
    flag = pl.col("symbol").replace_strict(estimated, return_dtype=pl.Boolean)
    return panel.filter(pl.col("date") == day).with_columns(
        pl.when(flag).then(pl.col("amount") * 0.5).otherwise(pl.col("amount")).alias("amount"),
        flag.alias(AMOUNT_ESTIMATED),
    )


def _with_amount(panel: pl.DataFrame, day: date, symbol: str, amount: pl.Expr) -> pl.DataFrame:
    target = (pl.col("date") == day) & (pl.col("symbol") == symbol)
    return panel.with_columns(
        pl.when(target).then(amount).otherwise(pl.col("amount")).alias("amount")
    )


def _realtime_setup(tmp_path):
    _write(tmp_path / "strategies", "amount_scored", AMOUNT_SCORED)
    engine = StrategyEngine(strategy_dirs=[tmp_path / "strategies"])
    days = _sessions(45)
    panel = _panel((ESTIMATED, QUOTED), days)
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=days[-2],
        current=_live(panel, days[-2], {ESTIMATED: True, QUOTED: False}),
        history=panel.filter(pl.col("date") < days[-2]),
        cache_key="test:amount",
    )
    return engine, panel, days, context


def test_realtime_matrix_has_no_amount_ratio_fields_on_an_estimated_amount(tmp_path):
    engine, panel, days, context = _realtime_setup(tmp_path)
    snapshot = engine.prepare_realtime_matrix(context, ["amount_scored"])
    official = build_market_data_matrix(
        panel.filter(pl.col("date") <= days[-2]), field_columns={"amount"}
    )
    estimated, quoted = snapshot.symbols.index(ESTIMATED), snapshot.symbols.index(QUOTED)

    assert snapshot.field("amount")[-1, estimated] == 0.5 * official.field("amount")[-1, estimated]
    for name in sorted(TRADED_AMOUNT_FIELDS):
        values = matrix_feature(snapshot, name)
        assert np.isnan(values[-1, estimated]), name
        assert np.isfinite(values[-1, quoted]), name
        assert values[-1, quoted] == matrix_feature(official, name)[-1, quoted], name
        np.testing.assert_array_equal(values[:-1], matrix_feature(official, name)[:-1])

    result = engine.run(
        "amount_scored", dataclasses.replace(context, history=None, market=snapshot)
    )
    assert result.scores[ESTIMATED] is None
    assert result.scores[QUOTED] is not None
    assert [row["symbol"] for row in result.rows] == [QUOTED, ESTIMATED]


def test_realtime_matrix_follows_the_flag_of_each_live_update(tmp_path):
    engine, panel, days, context = _realtime_setup(tmp_path)
    engine.prepare_realtime_matrix(context, ["amount_scored"])
    quoted_now = dataclasses.replace(
        context, current=_live(panel, days[-2], {ESTIMATED: False, QUOTED: False})
    )

    snapshot = engine.prepare_realtime_matrix(quoted_now, ["amount_scored"])

    assert engine.realtime_matrix_stats("test:amount")["update_count"] == 1
    for name in sorted(TRADED_AMOUNT_FIELDS):
        assert np.isfinite(matrix_feature(snapshot, name)[-1]).all(), name


def test_an_estimated_row_left_in_the_buffer_is_skipped_by_later_windows(tmp_path):
    """Like a missing bar, an estimated amount drops out of later 5- and 20-bar windows."""
    engine, panel, days, context = _realtime_setup(tmp_path)
    engine.prepare_realtime_matrix(context, ["amount_scored"])
    # The engine rebuilds on a new session; append to its buffer to keep the estimated row.
    buffer = engine._realtime_matrices["test:amount"].buffer
    buffer.update(_live(panel, days[-1], {ESTIMATED: False, QUOTED: False}))

    snapshot = buffer.snapshot()

    assert snapshot.timestamp_labels[-1][:10] == days[-1].isoformat()
    assert buffer.update_count == 1
    asset = snapshot.symbols.index(ESTIMATED)
    skipped = build_market_data_matrix(
        _with_amount(panel, days[-2], ESTIMATED, pl.lit(None, dtype=pl.Float64)),
        field_columns={"amount"},
    )
    on_estimate = build_market_data_matrix(
        _with_amount(panel, days[-2], ESTIMATED, pl.col("amount") * 0.5),
        field_columns={"amount"},
    )
    for name in sorted(TRADED_AMOUNT_FIELDS):
        values = matrix_feature(snapshot, name)
        assert np.isnan(values[-2, asset]), name
        assert values[-1, asset] == matrix_feature(skipped, name)[-1, asset], name
    for name in ("amount_ratio_5d", "amihud_20d"):
        assert (
            matrix_feature(on_estimate, name)[-1, asset] != matrix_feature(skipped, name)[-1, asset]
        )


def test_pick_backfill_input_check_needs_no_estimate_flag_in_history(tmp_path):
    _write(tmp_path / "strategies", "amount_filtered", AMOUNT_FILTERED)
    engine = StrategyEngine(strategy_dirs=[tmp_path / "strategies"])
    strategy = engine.get("amount_filtered")
    params = engine.resolve_params(strategy)
    days = _sessions(30)
    context = SimpleNamespace(
        history=_panel((ESTIMATED, QUOTED), days), as_of=days[-1], market=None
    )

    assert "amount" in engine._matrix_field_columns(strategy, {}, params)
    assert input_quality(engine, context, "amount_filtered", {}, params) == ([], [])


# --------------------------------------------------------------------------------------------
# Realtime monitor history
# --------------------------------------------------------------------------------------------

LOADED_BARS = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {{"id": "{sid}", "name": "{sid}", "asset_types": ["stock"], "limit": 0}}
BASIC_FILTER = {{"enabled": False}}
EXECUTION_BACKEND = "matrix_native"


class _LoadedBars:
    def required_fields(self):
        return frozenset({{"close"}})

    def required_warmup_bars(self, params):
        return {warmup}

    def compute_signals(self, market, params):
        loaded = np.cumsum(np.isfinite(market.close), axis=0).astype(np.float32)
        return make_signal_matrix(
            market.shape, entry=np.ones(market.shape, dtype=np.uint8), score=loaded
        )


MATRIX_STRATEGY = _LoadedBars()
"""

MONITOR_SYMBOLS = ("000001.SZ", "000002.SZ")


def _monitor_rule(sid: str) -> dict:
    return monitor_rules.normalize(
        {
            "id": f"{sid}_rule",
            "name": sid,
            "type": "strategy",
            "asset_type": "stock",
            "scope": "all",
            "symbols": [],
            "strategy_id": sid,
            "notify_events": ["pool_entry"],
            "conditions": [],
            "cooldown_seconds": 0,
        }
    )


def test_realtime_monitor_scores_like_the_post_close_run(tmp_path, monkeypatch):
    """The score is the number of sessions the strategy computed on."""
    for sid, warmup in (("long_window", 200), ("short_window", 5)):
        _write(tmp_path / "strategies", sid, LOADED_BARS.format(sid=sid, warmup=warmup))
    engine = StrategyEngine(strategy_dirs=[tmp_path / "strategies"])
    days = _sessions(300)
    as_of = days[-1]
    panel = _panel(MONITOR_SYMBOLS, days)
    loader = _history_loader(panel)

    service = ScreenerService(SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)))
    monkeypatch.setattr(service, "_load_enriched_history", loader)
    post_close = {}
    for sid in ("long_window", "short_window"):
        context = service.build_strategy_context(
            engine, as_of, [sid], current=panel.filter(pl.col("date") == as_of)
        )
        post_close[sid] = engine.run(sid, context).scores

    monkeypatch.setattr("app.strategy.monitor.cn_today", lambda: as_of)
    monitor = MonitorRuleEngine()
    monitor.set_strategy_engine(engine)
    monitor.set_history_loader(loader)
    monitor.set_rules([_monitor_rule("long_window"), _monitor_rule("short_window")])
    monitor.evaluate(panel.filter(pl.col("date") == as_of))
    results = monitor.latest_strategy_results()
    realtime = {
        sid: {row["symbol"]: row["score"] for row in results[sid]["rows"]}
        for sid in ("long_window", "short_window")
    }

    long_sessions = 200 + 1 + 1 + HISTORY_WARMUP_SESSIONS
    assert post_close["long_window"] == dict.fromkeys(MONITOR_SYMBOLS, float(long_sessions))
    assert realtime == post_close


LOADED_SESSIONS = """
import polars as pl

META = {"id": "loaded_sessions", "name": "loaded sessions", "asset_types": ["stock"], "limit": 0}
BASIC_FILTER = {"enabled": False}
LOOKBACK_DAYS = 5


def filter_history(df, params):
    return df.with_columns(pl.len().over("symbol").alias("loaded_sessions"))
"""


def test_realtime_monitor_gives_a_history_strategy_the_post_close_window(tmp_path, monkeypatch):
    _write(tmp_path / "strategies", "loaded_sessions", LOADED_SESSIONS)
    engine = StrategyEngine(strategy_dirs=[tmp_path / "strategies"])
    days = _sessions(100)
    as_of = days[-1]
    panel = _panel(MONITOR_SYMBOLS, days)
    loader = _history_loader(panel)

    service = ScreenerService(SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)))
    monkeypatch.setattr(service, "_load_enriched_history", loader)
    context = service.build_strategy_context(
        engine, as_of, ["loaded_sessions"], current=panel.filter(pl.col("date") == as_of)
    )
    post_close = {
        row["symbol"]: row["loaded_sessions"] for row in engine.run("loaded_sessions", context).rows
    }

    monkeypatch.setattr("app.strategy.monitor.cn_today", lambda: as_of)
    monitor = MonitorRuleEngine()
    monitor.set_strategy_engine(engine)
    monitor.set_history_loader(loader)
    monitor.set_rules([_monitor_rule("loaded_sessions")])
    monitor.evaluate(panel.filter(pl.col("date") == as_of))
    rows = monitor.latest_strategy_results()["loaded_sessions"]["rows"]

    assert post_close == dict.fromkeys(MONITOR_SYMBOLS, 5 + 1)
    assert {row["symbol"]: row["loaded_sessions"] for row in rows} == post_close


# --------------------------------------------------------------------------------------------
# Ordering by a field
# --------------------------------------------------------------------------------------------

ORDERED_BY_CLOSE = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {{"id": "by_close", "name": "by close", "asset_types": ["stock"], "scoring": {{}},
        "order_by": "close", "descending": {descending}, "limit": 0}}
BASIC_FILTER = {{"enabled": False}}
EXECUTION_BACKEND = "matrix_native"


class _EveryBar:
    def required_fields(self):
        return frozenset({{"close"}})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


MATRIX_STRATEGY = _EveryBar()
"""

CLOSES = {"000001.SZ": 30.0, "000002.SZ": 10.0, "000003.SZ": 20.0}


class _SimulatingBacktestEngine:
    """Serves a fixed panel and runs the production matcher on it."""

    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = None

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs
    ):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        return build_market_data_matrix(self.panel, field_columns=fields)

    def simulate_market_matrix(
        self, matrix, config, progress_cb=None, cancel_event=None, options=None
    ):
        return BacktestEngine(None).simulate_market_matrix(
            matrix, config, progress_cb, cancel_event, options
        )


@pytest.mark.parametrize(
    ("descending", "expected"),
    [
        (False, ["000002.SZ", "000003.SZ", "000001.SZ"]),
        (True, ["000001.SZ", "000003.SZ", "000002.SZ"]),
    ],
    ids=["ascending", "descending"],
)
def test_field_order_of_the_list_is_the_order_the_backtest_buys_in(tmp_path, descending, expected):
    _write(tmp_path / "strategies", "by_close", ORDERED_BY_CLOSE.format(descending=descending))
    engine = StrategyEngine(strategy_dirs=[tmp_path / "strategies"])
    days = _sessions(5)
    panel = _panel(tuple(CLOSES), days, closes=CLOSES)
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=days[1],
        current=panel.filter(pl.col("date") == days[1]),
        history=panel.filter(pl.col("date") <= days[1]),
    )

    listed = [row["symbol"] for row in engine.run("by_close", context).rows]
    result = StrategyBacktestService(
        engine=_SimulatingBacktestEngine(panel), strategy_engine=engine
    ).run(
        StrategyBacktestConfig(
            strategy_id="by_close",
            symbols=None,
            start=days[1],
            end=days[-1],
            matching="close_t",
            max_positions=1,
        )
    )

    assert listed == expected
    assert result.error is None, result.error
    first = min(result.trades, key=lambda trade: str(trade["entry_date"]))
    assert str(first["entry_date"]) == days[1].isoformat()
    assert first["symbol"] == listed[0]


# --------------------------------------------------------------------------------------------
# Composite warm-up
# --------------------------------------------------------------------------------------------

WINDOW_CHILD = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {{"id": "{sid}", "name": "{sid}", "asset_types": ["stock"], "limit": 0,
        "params": [{{"id": "window", "label": "window", "type": "int", "default": {window}}}]}}
EXECUTION_BACKEND = "matrix_native"


class _Window:
    def required_fields(self):
        return frozenset({{"close"}})

    def required_warmup_bars(self, params):
        return int(params.get("window", {window}))

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


MATRIX_STRATEGY = _Window()
"""

COMBO = """
META = {
    "id": "combo",
    "name": "combo",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "children": [{"strategy_id": "short_child", "weight": 1.0}],
}
EXECUTION_BACKEND = "composite"
"""


def _composite_engine(tmp_path, saved: dict[str, dict]) -> StrategyEngine:
    directory = tmp_path / "strategies"
    _write(directory, "short_child", WINDOW_CHILD.format(sid="short_child", window=10))
    _write(directory, "long_child", WINDOW_CHILD.format(sid="long_child", window=150))
    _write(directory, "combo", COMBO)
    return StrategyEngine(strategy_dirs=[directory], override_loader=lambda sid: saved.get(sid, {}))


def test_composite_loads_the_history_its_child_saved_override_needs(tmp_path, monkeypatch):
    saved = {"short_child": {"params": {"window": 200}}}
    engine = _composite_engine(tmp_path, saved)
    child_needs = engine.required_history_bars(["short_child"], overrides_map=saved)

    assert child_needs == 201
    assert engine.required_history_bars(["combo"]) == child_needs

    service = ScreenerService(SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)))
    loaded: list[int] = []

    def load(target, lookback):
        loaded.append(lookback)
        return pl.DataFrame({"symbol": ["000001.SZ"], "date": [target]})

    monkeypatch.setattr(service, "_load_enriched_history", load)
    as_of = date(2026, 10, 8)
    service.build_strategy_context(
        engine, as_of, ["combo"], current=pl.DataFrame({"symbol": ["000001.SZ"], "date": [as_of]})
    )
    assert loaded == [child_needs + HISTORY_WARMUP_SESSIONS]


def test_composite_loads_the_history_of_its_saved_override_children(tmp_path):
    engine = _composite_engine(tmp_path, {})
    overrides = {"combo": {"children": [{"strategy_id": "long_child", "weight": 1.0}]}}

    assert engine.required_history_bars(["combo"]) == 11
    assert engine.required_history_bars(["combo"], overrides_map=overrides) == 151


def test_invalid_saved_composite_children_do_not_break_the_history_estimate(tmp_path):
    engine = _composite_engine(tmp_path, {})
    overrides = {"combo": {"children": [{"strategy_id": "missing_child", "weight": 1.0}]}}

    assert engine.required_history_bars(["combo", "long_child"], overrides_map=overrides) == 151
