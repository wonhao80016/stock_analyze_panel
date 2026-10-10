"""A matrix strategy picks the same rows run alone as in the batch run, whatever else the batch holds."""

from __future__ import annotations

import math
import types
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.api import screener as screener_api
from app.api import strategy as strategy_api
from app.repository import HISTORY_WARMUP_SESSIONS
from app.services.screener import ScreenerService
from app.strategy.engine import StrategyEngine
from app.taiwan import universe
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOLS = ("1101.TWSE", "2330.TWSE", "6488.TPEX")
GAPPY = "2330.TWSE"

WINDOW_STRATEGY = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": %(sid)r, "name": %(sid)r, "asset_types": ["stock"], "limit": 0}
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "matrix_native"


class _ValidBars:
    def __init__(self):
        self.sessions = []

    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return %(warmup)d

    def compute_signals(self, market, params):
        self.sessions.append(market.shape[0])
        valid = np.isfinite(market.close)
        return make_signal_matrix(
            market.shape,
            entry=valid.astype(np.uint8),
            score=np.cumsum(valid, axis=0).astype(np.float32),
        )


MATRIX_STRATEGY = _ValidBars()
"""


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _write_partitions(data_dir: Path, days: list[date]) -> None:
    rows = []
    for index, symbol in enumerate(SYMBOLS):
        for offset, day in enumerate(days):
            if symbol == GAPPY and offset % 3 == 0 and offset < len(days) - 1:
                continue
            close = 50.0 + index + 5.0 * math.sin(offset / 9.0) + offset * 0.01
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": close - 0.1,
                    "high": close + 0.3,
                    "low": close - 0.3,
                    "close": close,
                    "volume": 1_000_000.0,
                    "amount": 5e7,
                    "raw_close": close,
                    "raw_high": close + 0.3,
                    "raw_low": close - 0.3,
                }
            )
    frame = pl.DataFrame(rows)
    for (day,), part in frame.partition_by("date", as_dict=True).items():
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)


class _DiskRepo:
    """Serves nothing from caches, so the screener reads and computes the partitions."""

    def __init__(self, data_dir: Path) -> None:
        self.store = types.SimpleNamespace(data_dir=data_dir)

    def get_enriched_latest_asset(self, asset_type):
        return pl.DataFrame(), None

    def get_enriched_history(self, target_date, lookback_days):
        return None

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame()


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="stock",
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
        etf_category=None,
        classification_source=None,
        underlying_scope=None,
    )


@pytest.fixture
def master(tmp_path, monkeypatch) -> TaiwanSecurityMaster:
    path = tmp_path / "security_master.parquet"
    pl.DataFrame([_instrument(symbol).to_dict() for symbol in SYMBOLS]).write_parquet(path)
    loaded = TaiwanSecurityMaster(cache_path=path)
    assert loaded.load_cache()
    monkeypatch.setattr(universe, "_default_master", loaded)
    return loaded


@pytest.fixture(autouse=True)
def _clean_history_cache():
    ScreenerService.clear_history_cache()
    yield
    ScreenerService.clear_history_cache()


def _rows(rows: list[dict]) -> list[tuple[str, float]]:
    return [(str(row["symbol"]), row["score"]) for row in rows]


def test_a_strategy_run_alone_picks_what_the_batch_run_picks(tmp_path, master):
    data_dir = tmp_path / "data"
    days = _weekdays(date(2025, 1, 6), 300)
    _write_partitions(data_dir, days)
    target = days[-1]
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    for sid, warmup in (("short_window", 20), ("long_window", 150)):
        source = WINDOW_STRATEGY % {"sid": sid, "warmup": warmup}
        (strategy_dir / f"{sid}.py").write_text(source, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[strategy_dir])
    assert engine.load_errors() == []
    repo = _DiskRepo(data_dir)
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(repo=repo, strategy_engine=engine))
    )

    _, batch = screener_api.compute_and_cache_all(
        repo, engine, as_of=target, strategy_ids=["short_window", "long_window"]
    )
    for sid in ("short_window", "long_window"):
        ScreenerService.clear_history_cache()
        preset = screener_api.run_preset(
            screener_api.PresetRequest(strategy_id=sid, as_of=target), request
        )
        ScreenerService.clear_history_cache()
        single = strategy_api.run_strategy(
            strategy_api.RunRequest(strategy_id=sid, as_of=target), request
        )

        assert {symbol for symbol, _ in _rows(batch[sid]["rows"])} == set(SYMBOLS)
        assert _rows(preset["rows"]) == _rows(batch[sid]["rows"]), sid
        assert _rows(single["rows"]) == _rows(batch[sid]["rows"]), sid
        own_sessions = 1 + HISTORY_WARMUP_SESSIONS + engine.required_history_bars([sid])
        assert set(engine.get(sid).matrix_strategy.sessions) == {own_sessions}, sid
