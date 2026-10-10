"""Market-cap and float-cap bounds: selection and backtest screen the same candidates.

Share counts come only from the instrument table. Taiwan has none, so both paths ignore such a
bound there; where share counts exist, both paths apply it.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.indicators.pipeline import compute_indicators, compute_signals
from app.repository import DataStore, KlineRepository
from app.services.screener import ScreenerService
from app.strategy.engine import StrategyEngine
from app.taiwan import universe
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

NAMES = {"1101.TWSE": "台泥", "2330.TWSE": "台積電", "6488.TPEX": "環球晶"}
SMALL_CAP = "6488.TPEX"
BOUND = 1e10  # the market_cap_min of every strategy below
# Close about 20 / 30 / 40: caps about 2e10 / 3e10 / 4e9, so only SMALL_CAP fails BOUND.
SHARES = {"1101.TWSE": 1e9, "2330.TWSE": 1e9, "6488.TPEX": 1e8}


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _weekdays(date(2025, 1, 6), 120)

_MATRIX = """import numpy as np
from app.backtest.matrix import make_signal_matrix
META = {"id": "cap_matrix", "name": "cap_matrix", "asset_types": ["stock"], "timeframes": ["1d"],
        "basic_filter": {"market_cap_min": 1e10}, "scoring": {"amount": 1.0}}
EXECUTION_BACKEND = "matrix_native"
MAX_HOLD_DAYS = 2
class _EveryBar:
    def required_fields(self):
        return frozenset({"close"})
    def required_warmup_bars(self, params):
        return 1
    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.isfinite(market.close).astype(np.uint8))
MATRIX_STRATEGY = _EveryBar()
"""

_POLARS = """import polars as pl
META = {"id": "cap_polars", "name": "cap_polars", "asset_types": ["stock"], "timeframes": ["1d"],
        "basic_filter": {"market_cap_min": 1e10}, "scoring": {"amount": 1.0}}
EXECUTION_BACKEND = "polars_expr"
MAX_HOLD_DAYS = 2
def filter(df, params):
    return pl.col("close") > 0
"""

_COMPOSITE = """META = {"id": "cap_blend", "name": "cap_blend", "asset_types": ["stock"], "timeframes": ["1d"],
        "basic_filter": {"market_cap_min": 1e10},
        "params": [{"id": "merge_mode", "type": "select", "options": ["union", "intersect"], "default": "union"}],
        "children": [{"strategy_id": "cap_matrix", "weight": 1.0}]}
EXECUTION_BACKEND = "composite"
MAX_HOLD_DAYS = 2
"""

_PLAIN_MATRIX = _MATRIX.replace("cap_matrix", "plain_matrix").replace(
    '"basic_filter": {"market_cap_min": 1e10}, ', ""
)

STRATEGIES = ["cap_matrix", "cap_polars", "cap_blend"]
WITHOUT_BOUND = {"basic_filter": {"market_cap_min": None}}


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=NAMES[symbol],
        instrument_type="stock",
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source=f"{exchange}_ISIN",
        updated_at="2026-10-10",
    )


@pytest.fixture
def master(tmp_path, monkeypatch) -> TaiwanSecurityMaster:
    path = tmp_path / "security_master.parquet"
    pl.DataFrame([_instrument(symbol).to_dict() for symbol in NAMES]).write_parquet(path)
    loaded = TaiwanSecurityMaster(cache_path=path)
    assert loaded.load_cache()
    monkeypatch.setattr(universe, "_default_master", loaded)
    return loaded


def _write_published(data_dir: Path) -> None:
    rows = []
    for rank, symbol in enumerate(NAMES):
        for index, day in enumerate(DAYS):
            close = (20.0 + 10.0 * rank) * (1.0 + 0.02 * math.sin(index / 5.0 + rank))
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": close * 0.995,
                    "high": close * 1.005,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 5_000_000.0,
                    "amount": close * 5_000_000.0 * (1.0 + 0.3 * math.cos(index + rank)),
                    "raw_close": close,
                    "raw_high": close * 1.005,
                    "raw_low": close * 0.99,
                }
            )
    service = object.__new__(TaiwanEnrichedRefreshService)
    published = service._add_limit_signals(
        compute_signals(compute_indicators(pl.DataFrame(rows).sort(["symbol", "date"])))
    ).with_columns(pl.lit(None, dtype=pl.Float64).alias("turnover_rate"))
    for (day,), part in published.partition_by("date", as_dict=True).items():
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)


def _engine(data_dir: Path) -> StrategyEngine:
    custom = data_dir / "strategies" / "custom"
    composite = data_dir / "strategies" / "composite"
    custom.mkdir(parents=True)
    composite.mkdir(parents=True)
    (custom / "cap_matrix.py").write_text(_MATRIX, encoding="utf-8")
    (custom / "cap_polars.py").write_text(_POLARS, encoding="utf-8")
    (custom / "plain_matrix.py").write_text(_PLAIN_MATRIX, encoding="utf-8")
    (composite / "cap_blend.py").write_text(_COMPOSITE, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[custom, composite])
    assert engine.load_errors() == []
    return engine


@pytest.fixture
def market(tmp_path, master, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _write_published(tmp_path)
    return _engine(tmp_path), KlineRepository(DataStore(tmp_path))


def _with_share_counts(repo: KlineRepository, monkeypatch) -> None:
    instruments = pl.DataFrame(
        {
            "symbol": list(NAMES),
            "name": list(NAMES.values()),
            "total_shares": [SHARES[symbol] for symbol in NAMES],
            "float_shares": [SHARES[symbol] for symbol in NAMES],
        }
    )
    monkeypatch.setattr(
        repo,
        "get_instruments_asset",
        lambda asset_type: instruments if asset_type == "stock" else pl.DataFrame(),
    )


def _picks(engine: StrategyEngine, repo: KlineRepository, strategy_id: str, overrides=None):
    service = ScreenerService(repo)
    context = service.build_strategy_context(
        engine, DAYS[-1], [strategy_id], overrides_map={strategy_id: overrides or {}}
    )
    ScreenerService.clear_history_cache()
    result = engine.run(strategy_id, context, overrides=overrides)
    return sorted(str(row["symbol"]) for row in result.rows)


def _config(strategy_id: str, overrides=None) -> StrategyBacktestConfig:
    return StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=DAYS[70],
        end=DAYS[-1],
        overrides=overrides,
        mode="position",
        max_positions=2,
        matching="open_t+1",
    )


def _trades(engine: StrategyEngine, repo: KlineRepository, strategy_id: str, overrides=None):
    service = StrategyBacktestService(BacktestEngine(repo), engine)
    result = service.run(_config(strategy_id, overrides))
    assert result.error is None, result.error
    trades = [(str(trade["entry_date"])[:10], trade["symbol"]) for trade in result.trades]
    assert trades
    return trades


@pytest.mark.parametrize("strategy_id", STRATEGIES)
def test_without_share_counts_the_bound_is_ignored_in_selection_and_backtest(market, strategy_id):
    engine, repo = market

    assert _picks(engine, repo, strategy_id) == _picks(engine, repo, strategy_id, WITHOUT_BOUND)
    assert SMALL_CAP in _picks(engine, repo, strategy_id)
    assert _trades(engine, repo, strategy_id) == _trades(engine, repo, strategy_id, WITHOUT_BOUND)


@pytest.mark.parametrize("strategy_id", STRATEGIES)
def test_with_share_counts_the_bound_applies_in_selection_and_backtest(
    market, monkeypatch, strategy_id
):
    engine, repo = market
    _with_share_counts(repo, monkeypatch)

    assert _picks(engine, repo, strategy_id) == sorted(set(NAMES) - {SMALL_CAP})
    assert SMALL_CAP in _picks(engine, repo, strategy_id, WITHOUT_BOUND)
    assert SMALL_CAP not in {symbol for _, symbol in _trades(engine, repo, strategy_id)}
    assert SMALL_CAP in {symbol for _, symbol in _trades(engine, repo, strategy_id, WITHOUT_BOUND)}


def test_a_prepared_optimizer_matrix_ignores_the_bound_without_share_counts(market):
    engine, repo = market
    service = StrategyBacktestService(BacktestEngine(repo), engine)
    config = _config("cap_matrix")

    prepared = service.prepare_matrix_optimization([config])
    result = service.run(config, prepared=prepared)

    assert result.error is None, result.error
    assert [(str(t["entry_date"])[:10], t["symbol"]) for t in result.trades] == _trades(
        engine, repo, "cap_matrix", WITHOUT_BOUND
    )


def test_a_registered_bound_does_not_break_other_backtests_without_share_counts(market):
    engine, repo = market

    assert _trades(engine, repo, "plain_matrix")
