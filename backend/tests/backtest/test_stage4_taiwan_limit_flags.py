"""Taiwan limit flags in polars backtests and recomputed rows come from the publish (stage 4).

Taiwan has no instrument table, so the limit flags a polars backtest needs are the ones the enriched
publish wrote. A row written without the publish (storage-only) gets them from the publish's own
rule, never null and never a blanket False, and instrument names come from the security master.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import (
    StrategyBacktestConfig,
    StrategyBacktestService,
    StrategyDependencyResolver,
)
from app.indicators.pipeline import compute_indicators, compute_signals
from app.parquet import ENRICHED_STORAGE_SCHEMA
from app.repository import DataStore, KlineRepository, read_enriched_window
from app.strategy.engine import StrategyEngine
from app.taiwan import universe
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

NAMES = {"1101.TWSE": "台泥", "2330.TWSE": "台積電", "6488.TPEX": "環球晶"}
FLAGS = [
    "signal_limit_up",
    "signal_limit_down",
    "signal_broken_limit_up",
    "signal_limit_down_recovery",
]


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _weekdays(date(2025, 1, 6), 120)
LIMIT_UP, BROKEN, LIMIT_DOWN, RECOVERY, LATE_LIMIT_UP = 100, 103, 106, 108, 110
# (open, high, low, close) as multiples of the previous close.
EVENTS = {
    ("2330.TWSE", LIMIT_UP): (1.05, 1.10, 1.05, 1.10),
    ("2330.TWSE", BROKEN): (1.01, 1.10, 1.00, 1.03),
    ("2330.TWSE", LIMIT_DOWN): (0.95, 0.95, 0.90, 0.90),
    ("2330.TWSE", RECOVERY): (0.95, 0.98, 0.90, 0.97),
    ("1101.TWSE", LATE_LIMIT_UP): (1.05, 1.10, 1.05, 1.10),
}
# Rows written without the publish: every derived column is null.
STORAGE_ONLY = {("2330.TWSE", index) for index in range(LIMIT_UP, RECOVERY + 1)} | {
    ("1101.TWSE", LATE_LIMIT_UP)
}


def _storage() -> pl.DataFrame:
    rows = []
    for rank, symbol in enumerate(NAMES):
        previous = None
        for index, day in enumerate(DAYS):
            close = (20.0 + 10.0 * rank) * (1.0 + 0.02 * math.sin(index / 5.0))
            open_, high, low = close * 0.995, close * 1.005, close * 0.99
            event = EVENTS.get((symbol, index))
            if event is not None:
                open_, high, low, close = (previous * ratio for ratio in event)
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 5_000_000.0,
                    "amount": close * 5_000_000.0,
                    "raw_close": close,
                    "raw_high": high,
                    "raw_low": low,
                }
            )
            previous = close
    return pl.DataFrame(rows).sort(["symbol", "date"])


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


def _is_storage_only() -> pl.Expr:
    rows = [(symbol, DAYS[index]) for symbol, index in sorted(STORAGE_ONLY)]
    return pl.any_horizontal(
        (pl.col("symbol") == symbol) & (pl.col("date") == day) for symbol, day in rows
    )


def _write(data_dir: Path) -> pl.DataFrame:
    """The publish's partitions with the STORAGE_ONLY rows stripped to storage columns."""
    service = object.__new__(TaiwanEnrichedRefreshService)
    published = service._add_limit_signals(compute_signals(compute_indicators(_storage())))
    derived = [name for name in published.columns if name not in ENRICHED_STORAGE_SCHEMA]
    stored = published.with_columns(
        pl.when(_is_storage_only()).then(None).otherwise(pl.col(name)).alias(name)
        for name in derived
    )
    for (day,), part in stored.partition_by("date", as_dict=True).items():
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)
    return published


POLARS_STRATEGY = """
import polars as pl

META = {"id": "limit_probe", "name": "limit probe", "asset_types": ["stock"],
        "scoring": {"change_pct": 1.0}, "order_by": "score"}
BASIC_FILTER = {"enabled": False}
ENTRY_SIGNALS = ["signal_limit_down_recovery"]
EXIT_SIGNALS = ["signal_broken_limit_up"]


def filter(df, params):
    return pl.col("close") > 0
"""


def _strategies(tmp_path: Path) -> StrategyEngine:
    directory = tmp_path / "custom"
    directory.mkdir()
    (directory / "limit_probe.py").write_text(POLARS_STRATEGY, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[directory])
    assert engine.load_errors() == []
    return engine


def _plan(engine: StrategyEngine):
    strategy = engine.get("limit_probe")
    return StrategyDependencyResolver().resolve(
        strategy,
        params=StrategyEngine.resolve_params(strategy),
        basic_filter={"enabled": False},
        entry_signals=strategy.entry_signals,
        exit_signals=strategy.exit_signals,
    )


def _repo(data_dir: Path, instruments: pl.DataFrame | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        store=SimpleNamespace(data_dir=data_dir),
        get_enriched_range=lambda *args, **kwargs: None,
        get_instruments_asset=lambda asset_type: (
            pl.DataFrame() if instruments is None else instruments
        ),
        get_historical_shares=lambda: pl.DataFrame(),
    )


def _flags(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.select(["symbol", "date", *FLAGS]).sort(["symbol", "date"])


def test_each_event_publishes_its_limit_flag(tmp_path, master):
    published = _write(tmp_path)

    for (symbol, index), flag in {
        ("2330.TWSE", LIMIT_UP): "signal_limit_up",
        ("2330.TWSE", BROKEN): "signal_broken_limit_up",
        ("2330.TWSE", LIMIT_DOWN): "signal_limit_down",
        ("2330.TWSE", RECOVERY): "signal_limit_down_recovery",
        ("1101.TWSE", LATE_LIMIT_UP): "signal_limit_up",
    }.items():
        row = published.filter((pl.col("symbol") == symbol) & (pl.col("date") == DAYS[index]))
        assert row[flag].to_list() == [True], (symbol, index, flag)


def test_a_polars_panel_keeps_published_flags_and_derives_storage_only_rows_by_the_publish_rule(
    tmp_path, master
):
    published = _write(tmp_path)

    panel = BacktestEngine(_repo(tmp_path)).load_panel_for_backtest(
        None, DAYS[0], DAYS[-1], _plan(_strategies(tmp_path))
    )

    assert panel.select(pl.col(flag).null_count() for flag in FLAGS).row(0) == (0, 0, 0, 0)
    assert_frame_equal(_flags(panel), _flags(published))
    assert dict(panel.select("symbol", "name").unique().iter_rows()) == NAMES


def test_a_polars_backtest_in_the_taiwan_deployment_runs_and_enters_on_a_derived_flag(
    tmp_path, master
):
    _write(tmp_path)
    service = StrategyBacktestService(
        BacktestEngine(KlineRepository(DataStore(tmp_path))), _strategies(tmp_path)
    )

    result = service.run(
        StrategyBacktestConfig(
            strategy_id="limit_probe",
            symbols=None,
            start=DAYS[90],
            end=DAYS[-1],
            mode="position",
            max_positions=5,
            matching="open_t+1",
        )
    )

    assert result.error is None, result.error
    entries = [(trade["symbol"], str(trade["entry_date"])[:10]) for trade in result.trades]
    assert ("2330.TWSE", DAYS[RECOVERY + 1].isoformat()) in entries


def test_with_instruments_published_flags_are_kept_and_only_missing_rows_are_computed(
    tmp_path, master
):
    published = _write(tmp_path)
    instruments = pl.DataFrame({"symbol": list(NAMES), "name": list(NAMES.values())})

    panel = BacktestEngine(_repo(tmp_path, instruments)).load_panel_for_backtest(
        None, DAYS[0], DAYS[-1], _plan(_strategies(tmp_path))
    )

    kept = _flags(panel.filter(~_is_storage_only()))
    assert_frame_equal(kept, _flags(published.filter(~_is_storage_only())))
    computed = panel.filter(_is_storage_only() & (pl.col("date") > DAYS[0]))
    assert computed.select(pl.col(flag).null_count() for flag in FLAGS).row(0) == (0, 0, 0, 0)


def test_recomputed_rows_of_an_enriched_window_get_the_published_limit_flags(tmp_path, master):
    published = _write(tmp_path)

    window = read_enriched_window(tmp_path, "kline_daily_enriched", DAYS[95], DAYS[-1])

    assert_frame_equal(_flags(window), _flags(published.filter(pl.col("date") >= DAYS[95])))


def test_the_repository_history_cache_gives_storage_only_rows_the_published_limit_flags(
    tmp_path, master
):
    published = _write(tmp_path)
    repo = KlineRepository(DataStore(tmp_path))

    repo._refresh_enriched()

    cache = repo._enriched_history_cache.filter(_is_storage_only())
    assert cache.height == len(STORAGE_ONLY)
    assert_frame_equal(_flags(cache), _flags(published.filter(_is_storage_only())))
