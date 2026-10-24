"""The live strategy matrix is rebuilt for a new trading day and for a new enriched publication."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.config import settings
from app.enriched_generation import bump_enriched_generation
from app.strategy.engine import StrategyDataContext, StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

EVERY_BAR = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "every_bar", "name": "every bar", "asset_types": ["stock"], "limit": 0}
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "matrix_native"


class _EveryBar:
    def required_fields(self):
        return frozenset({"close", "amount"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


MATRIX_STRATEGY = _EveryBar()
"""

SYMBOLS = ("2317.TWSE", "2330.TWSE")
CACHE_KEY = "monitor:stock"


def _sessions(count: int, first: date = date(2025, 1, 2)) -> list[date]:
    out, day = [], first
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _panel(days: list[date], *, amount_scale: float = 1.0) -> pl.DataFrame:
    frames = []
    for index, symbol in enumerate(SYMBOLS):
        close = np.linspace(20.0, 30.0, len(days)) + 10.0 * index
        volume = np.full(len(days), 1_000_000.0)
        frames.append(
            pl.DataFrame(
                {
                    "symbol": [symbol] * len(days),
                    "name": [symbol[:4]] * len(days),
                    "date": days,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "raw_close": close,
                    "raw_high": close,
                    "raw_low": close,
                    "volume": volume,
                    "amount": close * volume * amount_scale,
                }
            )
        )
    return pl.concat(frames).sort(["symbol", "date"])


def _context(day: date, current: pl.DataFrame, history: pl.DataFrame | None) -> StrategyDataContext:
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=day,
        current=current,
        history=history,
        cache_key=CACHE_KEY,
    )


def _prepare(engine: StrategyEngine, panel: pl.DataFrame, day: date, live: pl.DataFrame):
    """The monitor's protocol: reuse the cached matrix, else rebuild on history through the prior day."""
    try:
        return engine.prepare_realtime_matrix(_context(day, live, None), ["every_bar"])
    except ValueError as exc:
        assert "requires history data" in str(exc)
    history = panel.filter(pl.col("date") < day)
    return engine.prepare_realtime_matrix(_context(day, live, history), ["every_bar"])


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    (tmp_path / "strategies").mkdir()
    (tmp_path / "strategies" / "every_bar.py").write_text(EVERY_BAR, encoding="utf-8")
    return StrategyEngine(strategy_dirs=[tmp_path / "strategies"])


def test_a_new_trading_day_rebuilds_on_the_published_history(engine):
    days = _sessions(30)
    official = _panel(days)
    live_amount = pl.col("amount") * 0.5
    yesterday, today = days[-2], days[-1]

    _prepare(engine, official, yesterday, official.filter(pl.col("date") == yesterday).with_columns(live_amount))
    snapshot = _prepare(engine, official, today, official.filter(pl.col("date") == today))

    stats = engine.realtime_matrix_stats(CACHE_KEY)
    assert stats["build_count"] == 2
    assert stats["update_count"] == 0
    expected = official.filter(pl.col("date") == yesterday).sort("symbol")["amount"].to_numpy()
    np.testing.assert_allclose(snapshot.field("amount")[-2], expected)


def test_the_same_trading_day_keeps_updating_the_cached_matrix(engine):
    days = _sessions(30)
    official = _panel(days)
    today = days[-1]
    live = official.filter(pl.col("date") == today)

    _prepare(engine, official, today, live)
    _prepare(engine, official, today, live)

    stats = engine.realtime_matrix_stats(CACHE_KEY)
    assert stats["build_count"] == 1
    assert stats["update_count"] == 1


def test_a_new_enriched_publication_rebuilds_the_matrix(engine):
    days = _sessions(30)
    today = days[-1]
    bump_enriched_generation(settings.data_dir, "stock")
    first = _panel(days)
    _prepare(engine, first, today, first.filter(pl.col("date") == today))

    republished = _panel(days, amount_scale=2.0)
    bump_enriched_generation(settings.data_dir, "stock")
    snapshot = _prepare(engine, republished, today, republished.filter(pl.col("date") == today))

    assert engine.realtime_matrix_stats(CACHE_KEY)["build_count"] == 2
    expected = republished.filter(pl.col("date") == days[-2]).sort("symbol")["amount"].to_numpy()
    np.testing.assert_allclose(snapshot.field("amount")[-2], expected)
