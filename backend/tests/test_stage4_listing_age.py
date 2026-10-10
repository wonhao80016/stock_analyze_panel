"""exclude_new_days: listings younger than N trading sessions are not picked or bought."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import SimResult
from app.backtest.matrix import (
    build_basic_filter_mask,
    build_market_data_matrix,
    make_signal_matrix,
)
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.engine import (
    DEFAULT_BASIC_FILTER,
    StrategyDataContext,
    StrategyDef,
    StrategyEngine,
)
from app.taiwan import daily_store, universe
from app.taiwan.market_rules import ListingSessions
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _weekdays(date(2026, 5, 4), 90)
AS_OF = DAYS[-1]

OLD = "1101.TWSE"
NEW_14 = "7001.TWSE"
AGE_29 = "7003.TWSE"
AGE_30 = "7004.TWSE"
AGE_31 = "7002.TPEX"
TRANSFER = "7005.TWSE"
NEW_ETF = "00999A.TWSE"
# Trading for the first time today: neither in the master nor in the stored bars.
UNSTORED = "7009.TWSE"

LISTED = {
    OLD: date(2000, 1, 4),
    NEW_14: DAYS[-15],
    AGE_29: DAYS[-30],
    AGE_30: DAYS[-31],
    AGE_31: DAYS[-32],
    # Moved from TPEx six sessions ago; its TPEx bars go back 70 sessions.
    TRANSFER: DAYS[-6],
    NEW_ETF: DAYS[-10],
}
STOCKS = (OLD, NEW_14, AGE_29, AGE_30, AGE_31, TRANSFER)
SYMBOLS = (*STOCKS, NEW_ETF)
ESTABLISHED = {OLD, AGE_30, AGE_31, TRANSFER}


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    etf = symbol == NEW_ETF
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="etf" if etf else "stock",
        listing_status="active",
        listing_date=LISTED[symbol].isoformat(),
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="ETF" if etf else "股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
        etf_category="equity" if etf else None,
        classification_source="official_metadata" if etf else None,
        underlying_scope="domestic" if etf else None,
    )


@pytest.fixture(autouse=True)
def listings(tmp_path, monkeypatch):
    path = tmp_path / "security_master.parquet"
    pl.DataFrame([_instrument(symbol).to_dict() for symbol in SYMBOLS]).write_parquet(path)
    master = TaiwanSecurityMaster(cache_path=path)
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)

    first_sessions = {symbol: max(LISTED[symbol], DAYS[0]) for symbol in SYMBOLS}
    first_sessions["7005.TPEX"] = DAYS[-70]
    sessions = ListingSessions(tuple(DAYS), first_sessions)
    monkeypatch.setattr(daily_store, "listing_sessions", lambda store=None: sessions)


def _panel(symbols=(*SYMBOLS, UNSTORED)) -> pl.DataFrame:
    rows = []
    for symbol in symbols:
        for day in DAYS:
            if symbol == UNSTORED and day != AS_OF:
                continue
            if symbol != UNSTORED and day < LISTED[symbol] and symbol != TRANSFER:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol.split(".")[0],
                    "date": day,
                    "open": 50.0,
                    "high": 50.5,
                    "low": 49.5,
                    "close": 50.0,
                    "raw_close": 50.0,
                    "raw_high": 50.5,
                    "raw_low": 49.5,
                    "volume": 1_000_000.0,
                    "amount": 1e9,
                    "signal_limit_up": False,
                    "signal_limit_down": False,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


POLARS_STRATEGY = """
import polars as pl

META = {"id": "every_symbol", "name": "every symbol", "asset_types": ["stock", "etf"], "limit": 0}


def filter(df, params):
    return pl.lit(True)
"""

MATRIX_STRATEGY = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "every_symbol", "name": "every symbol", "asset_types": ["stock", "etf"], "limit": 0}
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


def _engine(tmp_path: Path, backend: str) -> StrategyEngine:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(exist_ok=True)
    source = POLARS_STRATEGY if backend == "polars_expr" else MATRIX_STRATEGY
    (strategy_dir / "every_symbol.py").write_text(source, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[strategy_dir])
    assert engine.load_errors() == []
    return engine


def _picked(engine: StrategyEngine, overrides: dict | None = None) -> set[str]:
    panel = _panel()
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=AS_OF,
        current=panel.filter(pl.col("date") == AS_OF),
        history=panel,
    )
    result = engine.run("every_symbol", context, overrides=overrides)
    return {str(row["symbol"]) for row in result.rows}


def test_default_basic_filter_excludes_listings_under_30_sessions():
    assert DEFAULT_BASIC_FILTER["exclude_new_days"] == 30


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_screener_skips_new_listings(tmp_path, backend):
    assert _picked(_engine(tmp_path, backend)) == ESTABLISHED


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_zero_exclude_new_days_keeps_every_listing(tmp_path, backend):
    overrides = {"basic_filter": {"exclude_new_days": 0}}
    assert _picked(_engine(tmp_path, backend), overrides) == {*SYMBOLS, UNSTORED}


def test_matrix_mask_follows_each_session_of_the_listing():
    market = build_market_data_matrix(_panel(), field_columns={"amount"})
    mask = build_basic_filter_mask(market, {"exclude_new_days": 30})
    column = {symbol: index for index, symbol in enumerate(market.symbols)}
    row = {label[:10]: index for index, label in enumerate(market.timestamp_labels)}

    def allowed(symbol: str, day: date) -> bool:
        return bool(mask[row[day.isoformat()], column[symbol]])

    assert allowed(AGE_31, AS_OF)
    assert allowed(AGE_31, DAYS[-2])
    assert not allowed(AGE_31, DAYS[-3])
    assert not allowed(NEW_14, AS_OF)
    assert allowed(TRANSFER, AS_OF)
    assert allowed(OLD, DAYS[0])
    assert not allowed(UNSTORED, AS_OF)


class _StrategyEngineStub:
    def __init__(self, strategy: StrategyDef) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategy


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


class _AllEntries:
    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


def _strategy(backend: str, basic_filter: dict) -> StrategyDef:
    return StrategyDef(
        meta={"id": "every_symbol", "name": "every", "scoring": {}, "params": [], "limit": 0},
        basic_filter=basic_filter,
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
        matrix_strategy=_AllEntries() if backend == "matrix_native" else None,
    )


def _backtest_entries(backend: str, basic_filter: dict) -> dict[str, set[date]]:
    engine = _BacktestEngineStub(_panel(STOCKS))
    service = StrategyBacktestService(
        engine=engine, strategy_engine=_StrategyEngineStub(_strategy(backend, basic_filter))
    )
    result = service.run(
        StrategyBacktestConfig(
            strategy_id="every_symbol",
            symbols=None,
            start=DAYS[-5],
            end=AS_OF,
            matching="close_t",
            mode="position",
        )
    )
    assert result.error is None, result.error
    matrix = engine.matrix
    entries: dict[str, set[date]] = {}
    for time_id, asset_id in zip(*np.nonzero(matrix.entry), strict=True):
        day = date.fromisoformat(matrix.timestamp_labels[int(time_id)][:10])
        entries.setdefault(matrix.symbols[int(asset_id)], set()).add(day)
    return entries


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_backtest_entry_universe_skips_new_listings(backend):
    entries = _backtest_entries(backend, dict(DEFAULT_BASIC_FILTER))

    assert NEW_14 not in entries
    assert AGE_29 not in entries
    assert entries[AGE_31] == {DAYS[-2], AS_OF}
    assert entries[AGE_30] == {AS_OF}
    assert entries[TRANSFER] == set(DAYS[-5:])
    assert entries[OLD] == set(DAYS[-5:])


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_backtest_with_zero_exclude_new_days_buys_every_listing(backend):
    entries = _backtest_entries(backend, {**DEFAULT_BASIC_FILTER, "exclude_new_days": 0})

    assert set(entries) == set(STOCKS)
