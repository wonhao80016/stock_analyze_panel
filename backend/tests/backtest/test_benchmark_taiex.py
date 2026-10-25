"""Strategy backtests benchmark Taiwan universes against the local TAIEX price index.

The index rows are written to an isolated data_dir, so the benchmark asserted here is
read from the local store only; a period the store does not cover has no benchmark
return instead of a zero one.
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.matrix import build_market_data_matrix
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.config import settings
from app.strategy.engine import StrategyDef
from app.taiwan.benchmarks import TAIEX
from app.taiwan.daily_store import TAIWAN_DAILY_COLUMNS, TaiwanDailyStore
from app.taiwan.universe.models import TaiwanInstrument

SYMBOL = "2330.TWSE"
CLOSES = [100.0, 102.0, 101.0, 104.0, 106.0]
INDEX_CLOSES = [20000.0, 20100.0, 19900.0, 20300.0, 20500.0]


def _sessions(start: date, count: int) -> list[date]:
    days = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


DAYS = _sessions(date(2025, 3, 3), len(CLOSES))
DAY_TEXT = [day.isoformat() for day in DAYS]


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="stock",
        listing_status="active",
        listing_date="2000/01/01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-09-17",
        etf_category=None,
        classification_source=None,
        underlying_scope=None,
    )


class _Master:
    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return _instrument(symbol) if symbol == SYMBOL else None


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: _Master())
    return tmp_path


def _write_index(data_dir, days: list[date], closes: list[float]) -> None:
    rows = pl.DataFrame({
        "symbol": [TAIEX.symbol] * len(days),
        "date": days,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [0.0] * len(days),
        "amount": [0.0] * len(days),
        "quote_ts": [None] * len(days),
    }, schema_overrides={"quote_ts": pl.Utf8}).select(TAIWAN_DAILY_COLUMNS)
    TaiwanDailyStore(data_dir / "taiwan" / "index_daily").write_batch(rows)


def _panel(symbol: str) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol] * len(DAYS),
        "name": [symbol] * len(DAYS),
        "date": DAYS,
        "open": CLOSES,
        "high": CLOSES,
        "low": CLOSES,
        "close": CLOSES,
        "raw_close": CLOSES,
        "raw_high": CLOSES,
        "raw_low": CLOSES,
        "volume": [1_000_000.0] * len(DAYS),
        "amount": [1e8] * len(DAYS),
        "score": [1.0] * len(DAYS),
        "signal_limit_up": [False] * len(DAYS),
        "signal_limit_down": [False] * len(DAYS),
    })


class _Repo:
    def __init__(self, index_rows: dict[str, list[tuple[date, float]]]) -> None:
        self.index_rows = index_rows
        self.index_requests: list[str] = []

    def get_index_daily(self, symbol, start, end, columns=None):
        self.index_requests.append(symbol)
        rows = [(d, c) for d, c in self.index_rows.get(symbol, []) if start <= d <= end]
        return pl.DataFrame(
            {"date": [d for d, _ in rows], "close": [c for _, c in rows]},
            schema={"date": pl.Date, "close": pl.Float64},
        )


class _Engine(BacktestEngine):
    """Serves a fixed panel and matches it with the production matcher."""

    def __init__(self, panel: pl.DataFrame, repo: _Repo) -> None:
        super().__init__(repo=repo)
        self.panel = panel

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock", **kwargs):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        return build_market_data_matrix(self.panel, field_columns=fields)


class _StrategyEngine:
    def get(self, strategy_id: str) -> StrategyDef:
        return StrategyDef(
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


def _run(symbol: str = SYMBOL, repo: _Repo | None = None, mode: str = "position"):
    repo = repo or _Repo({})
    service = StrategyBacktestService(engine=_Engine(_panel(symbol), repo), strategy_engine=_StrategyEngine())
    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=DAYS[0],
        end=DAYS[-1],
        matching="close_t",
        initial_capital=1_000_000,
        max_positions=1,
        mode=mode,
    ))
    assert result.error is None
    return result, repo


def test_taiwan_benchmark_is_the_local_taiex_price_index(data_dir):
    _write_index(data_dir, DAYS, INDEX_CLOSES)

    result, repo = _run()

    stats = result.stats
    assert stats["benchmark_return"] == round(20500.0 / 20000.0 - 1, 4) == 0.025
    # 9 lots bought at 100 and closed at 106 net of commission, slippage and tax: equity 1,047,569.05.
    assert stats["total_return"] == 0.0476
    assert stats["excess"] == 0.0226
    assert stats["benchmark"] == {
        "symbol": "t00.TWSE",
        "name": TAIEX.name,
        "price_basis": "price_index",
        "includes_dividends": False,
        "base_date": DAY_TEXT[0],
        "coverage_start": DAY_TEXT[0],
        "coverage_end": DAY_TEXT[-1],
        "missing_sessions": 0,
    }
    assert [point["date"] for point in result.benchmark_curve] == [
        point["date"] for point in result.equity_curve
    ] == DAY_TEXT
    assert [point["close"] for point in result.benchmark_curve] == INDEX_CLOSES
    assert {point["symbol"] for point in result.benchmark_curve} == {"t00.TWSE"}
    assert repo.index_requests == []


def test_period_starting_before_the_local_index_has_no_benchmark_return(data_dir):
    _write_index(data_dir, DAYS[2:], INDEX_CLOSES[2:])

    result, _ = _run()

    stats = result.stats
    assert stats["benchmark_return"] is None
    assert stats["excess"] is None
    assert stats["benchmark"]["coverage_start"] == DAY_TEXT[2]
    assert stats["benchmark"]["coverage_end"] == DAY_TEXT[-1]
    assert stats["benchmark"]["missing_sessions"] == 2
    assert [point["date"] for point in result.benchmark_curve] == DAY_TEXT[2:]


def test_period_ending_after_the_local_index_has_no_benchmark_return(data_dir):
    _write_index(data_dir, DAYS[:-1], INDEX_CLOSES[:-1])

    stats = _run()[0].stats

    assert stats["benchmark_return"] is None
    assert stats["excess"] is None
    assert stats["benchmark"]["coverage_end"] == DAY_TEXT[-2]


def test_missing_local_index_has_no_benchmark_return(data_dir):
    result, repo = _run()

    stats = result.stats
    assert stats["benchmark_return"] is None
    assert stats["excess"] is None
    assert stats["benchmark"]["symbol"] == "t00.TWSE"
    assert stats["benchmark"]["coverage_start"] is None
    assert stats["benchmark"]["coverage_end"] is None
    assert result.benchmark_curve == []
    assert repo.index_requests == []
    assert not (data_dir / "taiwan" / "index_daily").exists()


def test_a_session_missing_inside_the_period_keeps_the_endpoint_return(data_dir):
    kept = [0, 1, 3, 4]
    _write_index(data_dir, [DAYS[i] for i in kept], [INDEX_CLOSES[i] for i in kept])

    result, _ = _run()

    stats = result.stats
    assert stats["benchmark_return"] == 0.025
    assert stats["benchmark"]["missing_sessions"] == 1
    assert [point["date"] for point in result.benchmark_curve] == [DAY_TEXT[i] for i in kept]


def test_full_mode_reports_the_benchmark_return_without_an_excess(data_dir):
    _write_index(data_dir, DAYS, INDEX_CLOSES)

    stats = _run(mode="full")[0].stats

    assert stats["mode"] == "full"
    assert stats["benchmark_return"] == 0.025
    assert stats["excess"] is None
    assert stats["benchmark"]["base_date"] == DAY_TEXT[0]
