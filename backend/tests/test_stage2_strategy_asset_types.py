"""A strategy's asset_types limit the symbols it may pick, not only whether it runs."""

from __future__ import annotations

import os
import types
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import build_market_data_matrix, make_signal_matrix
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.services import screener as screener_module
from app.services.screener import ScreenerService
from app.strategy import monitor_rules
from app.strategy.engine import StrategyDataContext, StrategyDef, StrategyEngine
from app.strategy.monitor import MonitorRuleEngine
from app.taiwan import universe
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument
from app.taiwan.universe.service import SecurityMasterUnavailableError

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"

STOCKS = ("2330.TWSE", "6488.TPEX")
TDR = "9103.TWSE"
ETFS = ("00985A.TWSE", "00915.TWSE", "00982A.TWSE")
NOT_IN_MASTER = "9999.TWSE"
ALL_SYMBOLS = (*STOCKS, TDR, *ETFS, NOT_IN_MASTER)
STOCK_PICKS = {*STOCKS, TDR}
TYPES = {**{s: "stock" for s in (*STOCKS, TDR)}, **{s: "etf" for s in ETFS}}
DAYS = [date(2026, 6, 1) + timedelta(days=offset) for offset in range(80)]
AS_OF = DAYS[-1]

POLARS_STRATEGY = """
import polars as pl

META = {{"id": "every_symbol", "name": "every symbol", "asset_types": {asset_types!r}, "limit": 0}}
BASIC_FILTER = {{"enabled": False}}
EXIT_SIGNALS = ["signal_test_exit"]


def filter(df, params):
    return pl.lit(True)
"""

MATRIX_STRATEGY = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {{"id": "every_symbol", "name": "every symbol", "asset_types": {asset_types!r}, "limit": 0}}
BASIC_FILTER = {{"enabled": False}}
EXECUTION_BACKEND = "matrix_native"


class _EveryBar:
    def required_fields(self):
        return frozenset({{"close"}})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(
            market.shape,
            entry=np.ones(market.shape, dtype=np.uint8),
            exit=np.ones(market.shape, dtype=np.uint8),
            score=np.ones(market.shape, dtype=np.float32),
            exit_signal_code=np.zeros(market.shape, dtype=np.int16),
            exit_signal_ids=("signal_test_exit",),
        )


MATRIX_STRATEGY = _EveryBar()
"""

COMPOSITE_STRATEGY = """
META = {
    "id": "combo",
    "name": "combo",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"enabled": False},
    "children": [{"strategy_id": "every_symbol", "weight": 1.0}],
    "limit": 0,
}
EXECUTION_BACKEND = "composite"
"""

TEMPLATES = {"polars_expr": POLARS_STRATEGY, "matrix_native": MATRIX_STRATEGY}


def _history(symbols=ALL_SYMBOLS) -> pl.DataFrame:
    rows = []
    for index, symbol in enumerate(symbols):
        for offset, day in enumerate(DAYS):
            close = 50.0 + index + offset * 0.02
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": day,
                    "open": close - 0.01,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "raw_close": close,
                    "volume": 1_000_000.0,
                    "amount": 1e9,
                    "turnover_rate": 1.0,
                    "signal_test_exit": True,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _context(history: pl.DataFrame, **kwargs) -> StrategyDataContext:
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=AS_OF,
        current=history.filter(pl.col("date") == AS_OF),
        history=history,
        **kwargs,
    )


def _custom_engine(
    tmp_path: Path, backend: str, asset_types: list[str], *, composite: bool = False
) -> StrategyEngine:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(exist_ok=True)
    source = TEMPLATES[backend].format(asset_types=asset_types)
    (strategy_dir / "every_symbol.py").write_text(source, encoding="utf-8")
    strategy_dirs = [strategy_dir]
    if composite:
        composite_dir = tmp_path / "composite"
        composite_dir.mkdir(exist_ok=True)
        (composite_dir / "combo.py").write_text(COMPOSITE_STRATEGY, encoding="utf-8")
        strategy_dirs.append(composite_dir)
    engine = StrategyEngine(strategy_dirs=strategy_dirs)
    assert engine.load_errors() == []
    assert engine.get("every_symbol").execution_backend == backend
    return engine


def _picked(result) -> set[str]:
    return {str(row["symbol"]) for row in result.rows}


def _hit_symbols(hits: list[dict]) -> set[str]:
    return {str(hit["symbol"]) for hit in hits}


def test_pullback_ma20_bounce_never_picks_etfs():
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    params = {
        "use_ma20_proximity": False,
        "require_ma_alignment": False,
        "require_positive_change": False,
    }

    result = engine.run(
        "pullback_ma20_bounce",
        _context(_history(), symbol_asset_types=TYPES),
        pool=list(ALL_SYMBOLS),
        params=params,
    )

    assert _picked(result) == STOCK_PICKS
    assert _hit_symbols(result.entry_signal_hits) == STOCK_PICKS


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_stock_only_strategy_skips_etfs_and_symbols_missing_from_the_master(tmp_path, backend):
    engine = _custom_engine(tmp_path, backend, ["stock"])

    result = engine.run("every_symbol", _context(_history(), symbol_asset_types=TYPES))

    assert _picked(result) == STOCK_PICKS
    assert not _picked(result) & set(ETFS)


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_pool_cannot_reintroduce_an_unsupported_asset_type(tmp_path, backend):
    engine = _custom_engine(tmp_path, backend, ["stock"])

    result = engine.run(
        "every_symbol",
        _context(_history(), symbol_asset_types=TYPES),
        pool=[ETFS[0], STOCKS[0]],
    )

    assert _picked(result) == {STOCKS[0]}


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_stock_and_etf_strategy_still_picks_etfs(tmp_path, backend):
    engine = _custom_engine(tmp_path, backend, ["stock", "etf"])

    result = engine.run("every_symbol", _context(_history(), symbol_asset_types=TYPES))

    assert _picked(result) == STOCK_PICKS | set(ETFS)


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_context_without_asset_types_keeps_every_symbol(tmp_path, backend):
    engine = _custom_engine(tmp_path, backend, ["stock"])

    result = engine.run("every_symbol", _context(_history()))

    assert _picked(result) == set(ALL_SYMBOLS)


@pytest.mark.parametrize("strategy_id", ["every_symbol", "combo"])
def test_asset_types_never_drop_non_taiwan_symbols_of_a_mixed_frame(tmp_path, strategy_id):
    # Matrix strategies reject frames mixing Taiwan and other markets before any filtering.
    engine = _custom_engine(tmp_path, "polars_expr", ["stock"], composite=True)
    others = ("000001.SZ", "510300.SH")

    result = engine.run(
        strategy_id, _context(_history((*ALL_SYMBOLS, *others)), symbol_asset_types=TYPES)
    )

    assert _picked(result) == STOCK_PICKS | set(others)


def test_run_all_shares_the_asset_types_with_every_strategy(tmp_path):
    engine = _custom_engine(tmp_path, "matrix_native", ["stock"])

    results = engine.run_all(_context(_history(), symbol_asset_types=TYPES))

    assert _picked(results["every_symbol"]) == STOCK_PICKS


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_exit_signals_still_fire_for_symbols_the_strategy_may_not_pick(tmp_path, backend):
    engine = _custom_engine(tmp_path, backend, ["stock"])

    result = engine.run("every_symbol", _context(_history(), symbol_asset_types=TYPES))

    assert _picked(result) == STOCK_PICKS
    assert _hit_symbols(result.exit_signal_hits) == set(ALL_SYMBOLS)


@pytest.mark.parametrize("child_asset_types", [["stock"], ["stock", "etf"]])
@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_composite_strategy_picks_only_its_asset_types(tmp_path, backend, child_asset_types):
    engine = _custom_engine(tmp_path, backend, child_asset_types, composite=True)

    filtered = engine.run("combo", _context(_history(), symbol_asset_types=TYPES))
    unfiltered = engine.run("combo", _context(_history()))

    assert _picked(filtered) == STOCK_PICKS
    assert set(filtered.scores) == STOCK_PICKS
    assert _picked(unfiltered) == set(ALL_SYMBOLS)


def test_strategy_with_an_unknown_asset_type_does_not_load(tmp_path):
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    source = POLARS_STRATEGY.format(asset_types=["stock", "bond"])
    (strategy_dir / "every_symbol.py").write_text(source, encoding="utf-8")

    engine = StrategyEngine(strategy_dirs=[strategy_dir])

    assert not engine.has("every_symbol")
    assert "bond" in engine.load_errors()[0]["error"]


def _instrument(symbol: str, instrument_type: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=f"{code}-DR" if symbol == TDR else code,
        instrument_type=instrument_type,
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="ETF" if instrument_type == "etf" else "股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
        etf_category="equity" if instrument_type == "etf" else None,
        classification_source="official_metadata" if instrument_type == "etf" else None,
        underlying_scope="domestic" if instrument_type == "etf" else None,
    )


@pytest.fixture
def master(tmp_path, monkeypatch) -> TaiwanSecurityMaster:
    path = tmp_path / "security_master.parquet"
    pl.DataFrame(
        [_instrument(symbol, kind).to_dict() for symbol, kind in TYPES.items()]
    ).write_parquet(path)
    loaded = TaiwanSecurityMaster(cache_path=path)
    assert loaded.load_cache()
    monkeypatch.setattr(universe, "_default_master", loaded)
    return loaded


class _FakeRepo:
    def __init__(self, data_dir: Path, current: pl.DataFrame) -> None:
        self.store = types.SimpleNamespace(data_dir=data_dir)
        self._current = current

    def get_enriched_latest_asset(self, asset_type):
        return self._current, AS_OF

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame()

    def get_enriched_history(self, target_date, lookback_days):
        return None


def test_screener_context_takes_asset_types_from_the_security_master(tmp_path, master):
    engine = _custom_engine(tmp_path, "polars_expr", ["stock"])
    current = _history().filter(pl.col("date") == AS_OF)
    service = ScreenerService(_FakeRepo(tmp_path, current))

    context = service.build_strategy_context(engine, AS_OF, ["every_symbol"])
    result = engine.run("every_symbol", context)

    assert dict(context.symbol_asset_types) == TYPES
    assert _picked(result) == STOCK_PICKS


def test_screener_context_leaves_non_taiwan_data_unfiltered(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("the security master only classifies Taiwan symbols")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", forbidden)
    engine = _custom_engine(tmp_path, "polars_expr", ["stock"])
    current = _history(("000001.SZ", "510300.SH")).filter(pl.col("date") == AS_OF)
    service = ScreenerService(_FakeRepo(tmp_path, current))

    context = service.build_strategy_context(engine, AS_OF, ["every_symbol"])
    result = engine.run("every_symbol", context)

    assert context.symbol_asset_types is None
    assert _picked(result) == {"000001.SZ", "510300.SH"}


def test_index_context_is_never_filtered_by_the_security_master(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("index contexts must not consult the security master")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", forbidden)
    engine = _custom_engine(tmp_path, "polars_expr", ["index"])
    current = _history(("t00.TWSE",)).filter(pl.col("date") == AS_OF)
    service = ScreenerService(_FakeRepo(tmp_path, current), asset_type="index")

    context = service.build_strategy_context(engine, AS_OF, ["every_symbol"])
    result = engine.run("every_symbol", context)

    assert context.symbol_asset_types is None
    assert _picked(result) == {"t00.TWSE"}


def _two_strategy_engine(tmp_path: Path) -> StrategyEngine:
    engine = _custom_engine(tmp_path, "polars_expr", ["stock"])
    second = POLARS_STRATEGY.format(asset_types=["stock"]).replace('"every_symbol"', '"second"')
    (tmp_path / "strategies" / "second.py").write_text(second, encoding="utf-8")
    engine.reload()
    return engine


def test_unavailable_master_fails_clearly_and_is_tried_once_per_context(tmp_path, monkeypatch):
    calls = []

    def broken():
        calls.append(1)
        raise OSError("disk gone")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", broken)
    engine = _two_strategy_engine(tmp_path)
    current = _history().filter(pl.col("date") == AS_OF)
    service = ScreenerService(_FakeRepo(tmp_path, current))
    context = service.build_strategy_context(engine, AS_OF, ["every_symbol", "second"])

    with pytest.raises(
        SecurityMasterUnavailableError, match="Taiwan Security Master unavailable: disk gone"
    ):
        engine.run("every_symbol", context)
    results = engine.run_all(context, strategy_ids=["every_symbol", "second"], skip_failed=True)

    assert results == {}
    assert len(calls) == 1


class _RecordingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def get_instruments(self, html=None):
        self.calls += 1
        return []


def _unloaded_master(monkeypatch, cache_path: Path) -> list[_RecordingAdapter]:
    adapters = [_RecordingAdapter(), _RecordingAdapter()]
    unloaded = TaiwanSecurityMaster(
        cache_path=cache_path, twse_adapter=adapters[0], tpex_adapter=adapters[1]
    )
    monkeypatch.setattr(universe, "_default_master", unloaded)
    return adapters


def _screener_context(tmp_path: Path, engine: StrategyEngine) -> StrategyDataContext:
    current = _history().filter(pl.col("date") == AS_OF)
    service = ScreenerService(_FakeRepo(tmp_path, current))
    return service.build_strategy_context(engine, AS_OF, ["every_symbol"])


def test_master_without_a_local_cache_never_fetches_during_a_strategy_run(tmp_path, monkeypatch):
    cache_path = tmp_path / "absent.parquet"
    adapters = _unloaded_master(monkeypatch, cache_path)
    engine = _custom_engine(tmp_path, "polars_expr", ["stock"])

    with pytest.raises(SecurityMasterUnavailableError, match="Taiwan Security Master unavailable"):
        engine.run("every_symbol", _screener_context(tmp_path, engine))

    assert [adapter.calls for adapter in adapters] == [0, 0]
    assert not cache_path.exists()


def test_corrupt_master_cache_fails_without_fetching_or_rewriting(tmp_path, monkeypatch):
    cache_path = tmp_path / "security_master.parquet"
    cache_path.write_bytes(b"not a parquet file")
    before = cache_path.stat().st_mtime_ns
    adapters = _unloaded_master(monkeypatch, cache_path)
    engine = _custom_engine(tmp_path, "polars_expr", ["stock"])

    with pytest.raises(SecurityMasterUnavailableError, match="Taiwan Security Master unavailable"):
        engine.run("every_symbol", _screener_context(tmp_path, engine))

    assert [adapter.calls for adapter in adapters] == [0, 0]
    assert cache_path.read_bytes() == b"not a parquet file"
    assert cache_path.stat().st_mtime_ns == before


def test_master_cache_file_is_read_again_only_after_it_changes(master, monkeypatch):
    monkeypatch.setattr(screener_module, "_master_asset_types", None)
    reads = []
    real_read_parquet = pl.read_parquet

    def counting_read_parquet(*args, **kwargs):
        reads.append(args[0])
        return real_read_parquet(*args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", counting_read_parquet)
    quotes = _history().filter(pl.col("date") == AS_OF)

    def lookup() -> str:
        return screener_module.taiwan_symbol_asset_types("stock", quotes)[ETFS[0]]

    assert lookup() == "etf"
    assert lookup() == "etf"
    assert len(reads) == 1

    stat = master.cache_path.stat()
    os.utime(master.cache_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert lookup() == "etf"
    assert len(reads) == 2


def test_realtime_monitor_skips_a_strategy_while_the_master_is_unavailable(tmp_path, monkeypatch):
    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", broken)
    history = _history((*STOCKS, *ETFS))
    monitor = _monitor(tmp_path, monkeypatch, "polars_expr", history)

    assert monitor.evaluate(_quotes(history, (*STOCKS, *ETFS))) == []


def _monitor(
    tmp_path,
    monkeypatch,
    backend: str,
    history: pl.DataFrame,
    notify_events=("pool_entry", "buy_signal"),
):
    monkeypatch.setattr("app.strategy.monitor.cn_today", lambda: AS_OF)
    engine = _custom_engine(tmp_path, backend, ["stock"])
    monitor = MonitorRuleEngine()
    monitor.set_strategy_engine(engine)
    monitor.set_history_loader(lambda target, bars: history.filter(pl.col("date") <= target))
    monitor.set_rules(
        [
            monitor_rules.normalize(
                {
                    "id": "every_symbol_rule",
                    "name": "every symbol",
                    "type": "strategy",
                    "asset_type": "stock",
                    "scope": "all",
                    "symbols": [],
                    "strategy_id": "every_symbol",
                    "notify_events": list(notify_events),
                    "conditions": [],
                    "cooldown_seconds": 0,
                }
            )
        ]
    )
    return monitor


def _quotes(history: pl.DataFrame, symbols) -> pl.DataFrame:
    return history.filter((pl.col("date") == AS_OF) & pl.col("symbol").is_in(list(symbols)))


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_realtime_monitor_of_a_stock_only_strategy_never_fires_on_an_etf(
    tmp_path, monkeypatch, master, backend
):
    history = _history((*STOCKS, TDR, *ETFS))
    monitor = _monitor(tmp_path, monkeypatch, backend, history)

    assert monitor.evaluate(_quotes(history, STOCKS)) == []
    events = monitor.evaluate(_quotes(history, (*STOCKS, TDR, *ETFS)))

    rows = monitor.latest_strategy_results()["every_symbol"]["rows"]
    assert {row["symbol"] for row in rows} == STOCK_PICKS
    assert {event["symbol"] for event in events} == {TDR}


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_realtime_monitor_still_sends_sell_signals_for_an_etf(
    tmp_path, monkeypatch, master, backend
):
    history = _history((*STOCKS, TDR, *ETFS))
    monitor = _monitor(tmp_path, monkeypatch, backend, history, notify_events=("sell_signal",))

    assert monitor.evaluate(_quotes(history, STOCKS)) == []
    events = monitor.evaluate(_quotes(history, (*STOCKS, TDR, *ETFS)))

    assert {(event["type"], event["symbol"]) for event in events} == {
        ("sell_signal", symbol) for symbol in (TDR, *ETFS)
    }


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_realtime_monitor_leaves_non_taiwan_quotes_unfiltered(tmp_path, monkeypatch, backend):
    def forbidden():
        raise AssertionError("the security master only classifies Taiwan symbols")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", forbidden)
    symbols = ("000001.SZ", "510300.SH")
    history = _history(symbols)
    monitor = _monitor(tmp_path, monkeypatch, backend, history)

    monitor.evaluate(_quotes(history, symbols))

    rows = monitor.latest_strategy_results()["every_symbol"]["rows"]
    assert {row["symbol"] for row in rows} == set(symbols)


class _Master:
    def __init__(self, instruments: dict[str, TaiwanInstrument]) -> None:
        self._instruments = instruments

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return self._instruments.get(symbol)


class _StrategyEngineStub:
    def __init__(self, strategy: StrategyDef) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategy


class _BacktestEngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = None
        self.sim_matrix = None
        self.matcher_config = None

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
        from app.backtest.engine import SimResult

        self.sim_matrix = matrix
        self.matcher_config = config
        return SimResult(
            equity_curve=[{"date": str(DAYS[0]), "value": config.initial_capital}],
            drawdown_curve=[{"date": str(DAYS[0]), "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )


class _EveryBar:
    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(
            market.shape,
            entry=np.ones(market.shape, dtype=np.uint8),
            exit=np.zeros(market.shape, dtype=np.uint8),
            score=np.ones(market.shape, dtype=np.float32),
        )


def _strategy_def(backend: str, asset_types: list[str]) -> StrategyDef:
    extra = (
        {"execution_backend": "matrix_native", "matrix_strategy": _EveryBar(), "filter_fn": None}
        if backend == "matrix_native"
        else {}
    )
    return StrategyDef(
        **{
            "meta": {
                "id": "every_symbol",
                "name": "every symbol",
                "asset_types": asset_types,
                "scoring": {},
                "params": [],
                "limit": 100,
            },
            "basic_filter": {"enabled": False},
            "entry_signals": [],
            "exit_signals": [],
            "stop_loss": None,
            "trailing_stop": None,
            "trailing_take_profit_activate": None,
            "trailing_take_profit_drawdown": None,
            "max_hold_days": None,
            "filter_fn": lambda df, params: pl.lit(True),
            "filter_history_fn": None,
            "lookback_days": 1,
            "source": "custom",
            "file_path": None,
            **extra,
        }
    )


def _backtest(monkeypatch, backend: str, asset_types: list[str], symbols=(*STOCKS, TDR, *ETFS)):
    instruments = {symbol: _instrument(symbol, kind) for symbol, kind in TYPES.items()}
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: _Master(instruments))
    panel = _history(symbols).filter(pl.col("date") >= DAYS[-3])
    engine = _BacktestEngineStub(
        panel.with_columns(
            pl.col("close").alias("raw_high"),
            pl.col("close").alias("raw_low"),
            pl.lit(1.0).alias("score"),
            pl.lit(False).alias("signal_limit_up"),
            pl.lit(False).alias("signal_limit_down"),
        )
    )
    service = StrategyBacktestService(
        engine=engine,
        strategy_engine=_StrategyEngineStub(_strategy_def(backend, asset_types)),
    )
    result = service.run(
        StrategyBacktestConfig(
            strategy_id="every_symbol",
            symbols=None,
            start=DAYS[-3],
            end=DAYS[-1],
            matching="close_t",
        )
    )
    return result, engine


def _entered(engine: _BacktestEngineStub) -> set[str]:
    matrix = engine.sim_matrix
    return {
        symbol
        for index, symbol in enumerate(matrix.symbols)
        if np.asarray(matrix.entry)[:, index].any()
    }


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_backtest_execution_universe_excludes_etfs_for_a_stock_only_strategy(monkeypatch, backend):
    result, engine = _backtest(monkeypatch, backend, ["stock"])

    assert result.error is None
    assert _entered(engine) == STOCK_PICKS
    assert set(engine.matcher_config.lot_size_by_symbol) == STOCK_PICKS
    assert result.stats["excluded_symbols"] == []


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_backtest_stock_and_etf_strategy_still_trades_etfs(monkeypatch, backend):
    result, engine = _backtest(monkeypatch, backend, ["stock", "etf"])

    assert result.error is None
    assert _entered(engine) == STOCK_PICKS | set(ETFS)


def test_backtest_of_only_etfs_with_a_stock_only_strategy_has_nothing_to_trade(monkeypatch):
    result, engine = _backtest(monkeypatch, "polars_expr", ["stock"], symbols=ETFS)

    assert (
        result.error == "策略僅支援 stock, 以下標的類型不符: 00915.TWSE, 00982A.TWSE, 00985A.TWSE"
    )
    assert engine.sim_matrix is None
