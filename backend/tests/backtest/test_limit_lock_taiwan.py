"""Taiwan limit locks in the backtest come from the limit signals enriched publishes.

Every fixture below runs its bars through TaiwanEnrichedRefreshService._add_limit_signals,
the publisher of signal_limit_up / signal_limit_down, and then loads the partitions through
the production parquet matrix loader, so a lock asserted here is the exchange limit the
published signal saw, not a second calculation inside the matrix.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest import matrix as matrix_module
from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.matrix import (
    build_market_data_matrix,
    build_market_matrix_from_signals,
    load_market_data_matrix_from_parquet,
    make_signal_matrix,
)
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.engine import CompositeChild, CompositeSpec, StrategyDef, StrategyEngine
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.universe.models import TaiwanInstrument

FIELDS = frozenset({"raw_close", "raw_high", "raw_low"})


def _instrument(
    symbol: str,
    *,
    instrument_type: str = "stock",
    underlying_scope: str | None = None,
    classification_source: str | None = None,
) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type=instrument_type,
        listing_status="active",
        listing_date="2000/01/01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票" if instrument_type == "stock" else "ETF",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-09-17",
        etf_category=None if instrument_type == "stock" else "equity",
        classification_source=classification_source,
        underlying_scope=underlying_scope,
    )


def _foreign_etf(symbol: str) -> TaiwanInstrument:
    return _instrument(
        symbol,
        instrument_type="etf",
        underlying_scope="foreign",
        classification_source="official_metadata",
    )


def _unknown_scope_etf(symbol: str) -> TaiwanInstrument:
    return _instrument(
        symbol,
        instrument_type="etf",
        underlying_scope="unknown",
        classification_source="official_metadata",
    )


class _Master:
    def __init__(self, instruments: dict[str, TaiwanInstrument]) -> None:
        self._instruments = instruments

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return self._instruments.get(symbol)


@pytest.fixture
def master(monkeypatch):
    instruments: dict[str, TaiwanInstrument] = {}
    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master",
        lambda: _Master(instruments),
    )
    return instruments


def _bars(symbol: str, days: list[date], rows: list[tuple]) -> pl.DataFrame:
    """rows: (raw_close, adjusted_close[, ref_limit_up, ref_limit_down]) one-price bars."""
    raw = [float(row[0]) for row in rows]
    adjusted = [float(row[1]) for row in rows]
    return pl.DataFrame({
        "symbol": [symbol] * len(days),
        "date": days,
        "open": adjusted,
        "high": adjusted,
        "low": adjusted,
        "close": adjusted,
        "raw_close": raw,
        "raw_high": raw,
        "raw_low": raw,
        "prev_close": [None, *adjusted[:-1]],
        "ref_limit_up": [row[2] if len(row) > 2 else None for row in rows],
        "ref_limit_down": [row[3] if len(row) > 3 else None for row in rows],
        "volume": [1_000_000.0] * len(days),
    }, schema_overrides={
        "prev_close": pl.Float64,
        "ref_limit_up": pl.Float64,
        "ref_limit_down": pl.Float64,
    })


def _publish(frame: pl.DataFrame) -> pl.DataFrame:
    service = object.__new__(TaiwanEnrichedRefreshService)
    return service._add_limit_signals(frame)


def _write_partitions(root, frame: pl.DataFrame) -> None:
    for (trade_date,), part in frame.partition_by("date", as_dict=True).items():
        partition = root / f"date={trade_date.isoformat()}"
        partition.mkdir(parents=True, exist_ok=True)
        part.write_parquet(partition / "part.parquet")


def _load(tmp_path, frame: pl.DataFrame, *, use_cache: bool):
    root = tmp_path / "enriched"
    _write_partitions(root, frame)
    dates = frame["date"].unique().sort().to_list()
    return load_market_data_matrix_from_parquet(
        root,
        dates[0],
        dates[-1],
        field_columns=FIELDS,
        cache_root=(tmp_path / "cache") if use_cache else None,
    )


def _sessions(start: date, count: int) -> list[date]:
    days = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


FILL_MODES = {"open_t+1": 1, "close_t": 0}


def _matcher(fill: str, capital: float) -> MatcherConfig:
    return MatcherConfig(
        entry_fill=fill,
        exit_fill=fill,
        fees_pct=0,
        slippage_bps=0,
        max_positions=1,
        initial_capital=capital,
    )


@pytest.mark.parametrize("use_cache", [False, True])
@pytest.mark.parametrize("fill", list(FILL_MODES))
def test_2454_one_price_limit_up_session_cannot_be_bought(tmp_path, master, use_cache, fill):
    """2454 opened, traded and closed at its 4315 limit up on 2026-09-01 (previous close 3925)."""
    symbol = "2454.TWSE"
    master[symbol] = _instrument(symbol)
    days = _sessions(date(2026, 8, 31), 3)
    assert days[1] == date(2026, 9, 1)
    frame = _publish(_bars(symbol, days, [(3925, 3925), (4315, 4315), (4300, 4300)]))

    market = _load(tmp_path, frame, use_cache=use_cache)

    assert market.limit_up_locked[:, 0].tolist() == [0, 1, 0]
    delay = FILL_MODES[fill]
    entry = np.zeros(market.shape, dtype=np.uint8)
    entry[1 - delay, 0] = 1
    signals = make_signal_matrix(market.shape, entry=entry)
    matrix = build_market_matrix_from_signals(market, signals, entry_delay_bars=delay)
    result = BacktestEngine(repo=None).simulate_market_matrix(matrix, _matcher(fill, 10_000_000))
    assert result.trades == []
    assert result.stats["execution"]["buy_limit_up"] == 1


@pytest.mark.parametrize("use_cache", [False, True])
@pytest.mark.parametrize("fill", list(FILL_MODES))
def test_6225_one_price_limit_down_session_cannot_be_sold(tmp_path, master, use_cache, fill):
    """6225 opened, traded and closed at its 46.2 limit down on 2026-09-23 (previous close 51.3)."""
    symbol = "6225.TWSE"
    master[symbol] = _instrument(symbol)
    days = _sessions(date(2026, 9, 21), 4)
    assert days[2] == date(2026, 9, 23)
    frame = _publish(_bars(symbol, days, [(51.0, 51.0), (51.3, 51.3), (46.2, 46.2), (47.0, 47.0)]))

    market = _load(tmp_path, frame, use_cache=use_cache)

    assert market.limit_down_locked[:, 0].tolist() == [0, 0, 1, 0]
    delay = FILL_MODES[fill]
    entry = np.zeros(market.shape, dtype=np.uint8)
    exit_ = np.zeros(market.shape, dtype=np.uint8)
    entry[1 - delay, 0] = 1
    exit_[2 - delay, 0] = 1
    signals = make_signal_matrix(market.shape, entry=entry, exit=exit_)
    matrix = build_market_matrix_from_signals(
        market,
        signals,
        entry_delay_bars=delay,
        exit_delay_bars=delay,
    )
    result = BacktestEngine(repo=None).simulate_market_matrix(matrix, _matcher(fill, 1_000_000))
    assert result.stats["execution"]["sell_limit_down"] == 1
    assert [trade.entry_date for trade in result.trades] == [days[1].isoformat()]
    assert [trade.exit_date for trade in result.trades] == [days[3].isoformat()]


@pytest.mark.parametrize("use_cache", [False, True])
def test_taiwan_rows_with_null_limit_signals_are_rejected(tmp_path, master, use_cache):
    symbol = "2330.TWSE"
    master[symbol] = _instrument(symbol)
    days = _sessions(date(2026, 9, 16), 2)
    frame = _publish(_bars(symbol, days, [(100.0, 100.0), (110.0, 110.0)])).with_columns(
        pl.when(pl.col("date") == days[1])
        .then(None)
        .otherwise(pl.col("signal_limit_up"))
        .alias("signal_limit_up")
    )

    with pytest.raises(ValueError, match="rows without signal_limit_up"):
        _load(tmp_path, frame, use_cache=use_cache)


@pytest.mark.parametrize("use_cache", [False, True])
@pytest.mark.parametrize(("close", "locked"), [(5695.0, 1), (5694.0, 0)])
def test_limit_up_boundary_above_one_thousand_uses_the_five_dollar_tick(
    tmp_path, master, use_cache, close, locked,
):
    symbol = "2454.TWSE"
    master[symbol] = _instrument(symbol)
    days = _sessions(date(2026, 9, 1), 2)
    frame = _publish(_bars(symbol, days, [(5180, 5180), (close, close)]))

    market = _load(tmp_path, frame, use_cache=use_cache)

    assert market.limit_up_locked[:, 0].tolist() == [0, locked]


@pytest.mark.parametrize("use_cache", [False, True])
def test_ex_rights_session_locks_against_the_published_reference_limits(tmp_path, master, use_cache):
    """2379 goes ex-dividend at reference 704 (limits 774 / 634) and splits 2:1 a session later.

    The later split halves every earlier adjusted close, so the previous adjusted close (352)
    is not the reference price of the ex-dividend session; only the published limits are.
    """
    symbol = "2379.TWSE"
    master[symbol] = _instrument(symbol)
    days = _sessions(date(2026, 9, 7), 3)
    near_limit = _publish(_bars(symbol, days, [
        (729.0, 352.0),
        (770.0, 385.0, 774.0, 634.0),
        (390.0, 390.0),
    ]))
    at_limit_down = _publish(_bars(symbol, days, [
        (729.0, 352.0),
        (634.0, 317.0, 774.0, 634.0),
        (320.0, 320.0),
    ]))

    near = _load(tmp_path / "near", near_limit, use_cache=use_cache)
    down = _load(tmp_path / "down", at_limit_down, use_cache=use_cache)

    assert near.limit_up_locked[:, 0].tolist() == [0, 0, 0]
    assert near.limit_down_locked[:, 0].tolist() == [0, 0, 0]
    assert down.limit_down_locked[:, 0].tolist() == [0, 1, 0]


@pytest.mark.parametrize("use_cache", [False, True])
def test_published_limit_below_statutory_ten_percent_is_the_lock_price(tmp_path, master, use_cache):
    symbol = "2454.TWSE"
    master[symbol] = _instrument(symbol)
    days = _sessions(date(2026, 9, 7), 2)
    frame = _publish(_bars(symbol, days, [(1520.0, 1400.0), (1500.0, 1500.0, 1500.0, 1260.0)]))

    market = _load(tmp_path, frame, use_cache=use_cache)

    assert market.limit_up_locked[:, 0].tolist() == [0, 1]


@pytest.mark.parametrize("use_cache", [False, True])
def test_foreign_component_etf_has_no_limit_and_never_locks(tmp_path, master, use_cache):
    ordinary, foreign = "2330.TWSE", "00646.TWSE"
    master[ordinary] = _instrument(ordinary)
    master[foreign] = _foreign_etf(foreign)
    days = _sessions(date(2026, 9, 16), 2)
    frame = _publish(pl.concat([
        _bars(ordinary, days, [(100.0, 100.0), (110.0, 110.0)]),
        _bars(foreign, days, [(100.0, 100.0), (200.0, 200.0)]),
    ]))

    market = _load(tmp_path, frame, use_cache=use_cache)

    column = {symbol: index for index, symbol in enumerate(market.symbols)}
    assert market.limit_up_locked[-1, column[ordinary]] == 1
    assert market.limit_up_locked[:, column[foreign]].tolist() == [0, 0]
    assert market.limit_down_locked[:, column[foreign]].tolist() == [0, 0]
    assert matrix_module.taiwan_limit_rule_exclusions(list(market.symbols)) == []


@pytest.mark.parametrize("use_cache", [False, True])
def test_taiwan_matrix_requires_the_published_limit_signals(tmp_path, master, use_cache):
    symbol = "2330.TWSE"
    master[symbol] = _instrument(symbol)
    days = _sessions(date(2026, 9, 16), 2)
    frame = _bars(symbol, days, [(100.0, 100.0), (110.0, 110.0)])

    with pytest.raises(ValueError, match="signal_limit_up"):
        _load(tmp_path, frame, use_cache=use_cache)


def test_matrix_cache_built_by_the_previous_loader_version_is_not_reused(tmp_path, master, monkeypatch):
    symbol = "2454.TWSE"
    master[symbol] = _instrument(symbol)
    days = _sessions(date(2026, 8, 31), 3)
    frame = _publish(_bars(symbol, days, [(3925, 3925), (4315, 4315), (4300, 4300)]))
    root = tmp_path / "enriched"
    _write_partitions(root, frame)
    cache = tmp_path / "cache"

    def load():
        return load_market_data_matrix_from_parquet(
            root, days[0], days[-1], field_columns=FIELDS, cache_root=cache,
        )

    with monkeypatch.context() as patch:
        patch.setattr(matrix_module, "_DIRECT_MATRIX_LOADER_VERSION", 5)
        stale = load()
        stale_path = Path(stale.cache_path)
        del stale
    assert stale_path.name.startswith("v5-")
    manifest = json.loads((stale_path / "manifest.json").read_text("utf-8"))
    spec = manifest["arrays"]["limit_up_locked"]
    old_locks = np.memmap(
        stale_path / "matrix.bin",
        dtype=np.dtype(spec["dtype"]),
        mode="r+",
        offset=int(spec["offset"]),
        shape=tuple(spec["shape"]),
    )
    old_locks[0, 0] = 1
    old_locks.flush()
    del old_locks

    market = load()

    assert market.cache_status == "built"
    assert market.limit_up_locked[:, 0].tolist() == [0, 1, 0]


def _matrix_panel(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows).sort(["symbol", "date"])


class _StrategyEngineStub:
    def __init__(self, strategy: StrategyDef) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategy


class _EngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = None
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
        from app.backtest.engine import SimResult

        self.sim_matrix = matrix
        return SimResult(
            equity_curve=[{"date": "2026-09-01", "value": config.initial_capital}],
            drawdown_curve=[{"date": "2026-09-01", "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )


def _unknown_universe_panel() -> pl.DataFrame:
    days = _sessions(date(2026, 9, 1), 3)
    rows = []
    for symbol in ("2330.TWSE", "00888.TWSE", "9999.TPEX"):
        for day in days:
            rows.append({
                "symbol": symbol,
                "name": symbol,
                "date": day,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "raw_close": 100.0,
                "raw_high": 100.0,
                "raw_low": 100.0,
                "volume": 1_000_000.0,
                "amount": 1e8,
                "score": 1.0,
                "signal_limit_up": False,
                "signal_limit_down": False,
            })
    return _matrix_panel(rows)


EXPECTED_EXCLUSIONS = [
    {"symbol": "00888.TWSE", "reason": "price_limit_unknown"},
    {"symbol": "9999.TPEX", "reason": "security_master_missing"},
]


def _seed_unknown_universe(master: dict) -> None:
    master["2330.TWSE"] = _instrument("2330.TWSE")
    master["00888.TWSE"] = _unknown_scope_etf("00888.TWSE")


def test_unknown_limit_rule_symbols_are_reported_with_their_reason(master):
    _seed_unknown_universe(master)
    master["00646.TWSE"] = _foreign_etf("00646.TWSE")

    excluded = matrix_module.taiwan_limit_rule_exclusions(
        ["9999.TPEX", "2330.TWSE", "00646.TWSE", "00888.TWSE"],
    )

    assert excluded == EXPECTED_EXCLUSIONS


def _strategy(**kwargs) -> StrategyDef:
    defaults = dict(
        meta={"id": "test", "name": "test", "scoring": {}, "params": [], "limit": 100},
        basic_filter={"enabled": False},
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


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_unknown_limit_rule_symbols_never_enter_the_backtest(master, backend):
    _seed_unknown_universe(master)
    engine = _EngineStub(_unknown_universe_panel())
    strategy = (
        _strategy(execution_backend="matrix_native", matrix_strategy=_EveryBar())
        if backend == "matrix_native"
        else _strategy()
    )
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(strategy))
    days = _sessions(date(2026, 9, 1), 3)

    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=days[0],
        end=days[-1],
        matching="close_t",
    ))

    assert result.error is None
    assert result.stats["excluded_symbols"] == EXPECTED_EXCLUSIONS
    column = {symbol: index for index, symbol in enumerate(engine.sim_matrix.symbols)}
    assert engine.sim_matrix.entry[:, column["2330.TWSE"]].tolist() == [1, 1, 1]
    assert engine.sim_matrix.entry[:, column["00888.TWSE"]].tolist() == [0, 0, 0]
    assert engine.sim_matrix.entry[:, column["9999.TPEX"]].tolist() == [0, 0, 0]
    assert result.stats["selection"]["entry_candidates"] == 3


def test_backtest_fails_when_every_symbol_has_an_unknown_limit_rule(master):
    master["00888.TWSE"] = _unknown_scope_etf("00888.TWSE")
    panel = _unknown_universe_panel().filter(pl.col("symbol") != "2330.TWSE")
    engine = _EngineStub(panel)
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(_strategy()))
    days = _sessions(date(2026, 9, 1), 3)

    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=days[0],
        end=days[-1],
        matching="close_t",
    ))

    assert result.error is not None
    assert "00888.TWSE" in result.error
    assert engine.sim_matrix is None


class _ScoredBySymbol:
    """Enters every bar; 00888.TWSE always outscores 2330.TWSE."""

    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        per_asset = np.array(
            [2.0 if symbol == "00888.TWSE" else 1.0 for symbol in market.symbols],
            dtype=np.float32,
        )
        return make_signal_matrix(
            market.shape,
            entry=np.ones(market.shape, dtype=np.uint8),
            exit=np.zeros(market.shape, dtype=np.uint8),
            score=np.broadcast_to(per_asset, market.shape).copy(),
        )


class _CompositeEngineStub:
    def __init__(self) -> None:
        self.strategies = {
            "combo": _strategy(
                meta={"id": "combo", "name": "combo", "scoring": {}, "params": [], "limit": 100},
                execution_backend="composite",
                composite=CompositeSpec(children=(CompositeChild("child", 1.0),)),
            ),
            "child": _strategy(
                meta={"id": "child", "name": "child", "scoring": {}, "params": [], "limit": 100},
                execution_backend="matrix_native",
                matrix_strategy=_ScoredBySymbol(),
            ),
        }

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategies[strategy_id]

    resolve_params = staticmethod(StrategyEngine.resolve_params)


def _composite_scores(panel: pl.DataFrame) -> tuple[dict, dict[str, list[float]]]:
    engine = _EngineStub(panel)
    service = StrategyBacktestService(engine=engine, strategy_engine=_CompositeEngineStub())
    days = _sessions(date(2026, 9, 1), 3)
    result = service.run(StrategyBacktestConfig(
        strategy_id="combo",
        symbols=None,
        start=days[0],
        end=days[-1],
        matching="close_t",
    ))
    assert result.error is None
    column = {symbol: index for index, symbol in enumerate(engine.sim_matrix.symbols)}
    scores = {
        symbol: engine.sim_matrix.score[:, index].tolist()
        for symbol, index in column.items()
    }
    return result.stats, scores


def test_composite_children_never_rank_an_excluded_symbol(master):
    """The child hits {00888, 2330}; with 00888 excluded, 2330 must score as if it hit alone."""
    _seed_unknown_universe(master)
    panel = _unknown_universe_panel().filter(pl.col("symbol") != "9999.TPEX")

    stats, scores = _composite_scores(panel)
    _alone_stats, alone = _composite_scores(panel.filter(pl.col("symbol") == "2330.TWSE"))

    assert stats["excluded_symbols"] == [EXPECTED_EXCLUSIONS[0]]
    assert scores["2330.TWSE"] == alone["2330.TWSE"]
    assert scores["00888.TWSE"] == [0.0, 0.0, 0.0]
    assert stats["selection"]["entry_candidates"] == 3
