"""filter_history sees the target session plus its LOOKBACK sessions, alone and in a batch."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import polars as pl
import pytest

from app.repository import HISTORY_WARMUP_SESSIONS
from app.strategy.engine import StrategyDataContext, StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOLS = ("1101.TWSE", "1102.TWSE")

LEGACY = """
import polars as pl

META = {{"id": "{sid}", "name": "{sid}", "asset_types": ["stock"], "limit": 0,
        "params": {params}}}
BASIC_FILTER = {{"enabled": False}}
EXECUTION_BACKEND = "python_history_legacy"
{lookback}


def filter_history(df, params):
    return df.with_columns(
        pl.col("date").n_unique().alias("sessions"),
        pl.col("date").min().alias("first_day"),
        pl.col("date").max().alias("last_day"),
    )
"""


def _sessions(count: int) -> list[date]:
    out, day = [], date(2025, 1, 2)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _panel(days: list[date]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"symbol": symbol, "name": symbol, "date": day, "close": 10.0 + i, "volume": 1e6}
            for symbol in SYMBOLS
            for i, day in enumerate(days)
        ]
    ).sort(["symbol", "date"])


def _write(directory, sid: str, *, lookback: int | None = None, param_lookback: int | None = None):
    params = (
        []
        if param_lookback is None
        else [{"id": "lookback_days", "type": "int", "default": param_lookback}]
    )
    source = LEGACY.format(
        sid=sid,
        params=repr(params),
        lookback="" if lookback is None else f"LOOKBACK_DAYS = {lookback}",
    )
    (directory / f"{sid}.py").write_text(source, encoding="utf-8")


@pytest.fixture
def engine(tmp_path) -> StrategyEngine:
    _write(tmp_path, "short_window", lookback=5)
    _write(tmp_path, "param_window", param_lookback=8)
    _write(tmp_path, "long_window", lookback=40)
    return StrategyEngine(strategy_dirs=[tmp_path])


def _context(panel: pl.DataFrame, as_of: date, sessions: int) -> StrategyDataContext:
    """The last ``sessions`` sessions through as_of, plus one later session the run must ignore."""
    days = panel["date"].unique().sort().to_list()
    through = [day for day in days if day <= as_of][-sessions:]
    later = [day for day in days if day > as_of][:1]
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=as_of,
        current=panel.filter(pl.col("date") == as_of),
        history=panel.filter(pl.col("date").is_in(through + later)),
    )


def _windows(result) -> dict[str, tuple]:
    return {
        row["symbol"]: (row["sessions"], row["first_day"], row["last_day"]) for row in result.rows
    }


@pytest.mark.parametrize(
    ("sid", "lookback", "overrides"),
    [
        ("short_window", 5, None),
        ("param_window", 8, None),
        ("param_window", 12, {"params": {"lookback_days": 12}}),
    ],
)
def test_a_history_strategy_sees_lookback_sessions_alone_and_in_a_batch(
    engine, sid, lookback, overrides
):
    days = _sessions(200)
    as_of = days[150]
    panel = _panel(days)
    alone_bars = engine.required_history_bars([sid], overrides_map={sid: overrides or {}})
    batch_ids = [sid, "long_window"]
    batch_bars = engine.required_history_bars(batch_ids, overrides_map={sid: overrides or {}})

    alone = engine.run(
        sid, _context(panel, as_of, alone_bars + 1 + HISTORY_WARMUP_SESSIONS), overrides=overrides
    )
    batch = engine.run_all(
        _context(panel, as_of, batch_bars + 1 + HISTORY_WARMUP_SESSIONS),
        overrides_map={sid: overrides or {}},
        strategy_ids=batch_ids,
    )[sid]

    expected = (lookback + 1, days[150 - lookback], as_of)
    assert alone_bars == lookback
    assert _windows(alone) == dict.fromkeys(SYMBOLS, expected)
    assert _windows(batch) == _windows(alone)


@pytest.mark.parametrize("dtype", [pl.Datetime("us"), pl.Utf8], ids=["datetime", "string"])
def test_a_history_whose_dates_are_not_date_typed_is_still_windowed(engine, dtype):
    days = _sessions(60)
    as_of = days[40]
    context = _context(_panel(days), as_of, 30)
    history = context.history.with_columns(pl.col("date").cast(dtype))

    result = engine.run("short_window", replace(context, history=history))

    assert _windows(result) == dict.fromkeys(SYMBOLS, (6, days[35], as_of))


@pytest.mark.parametrize("change", ["integer", "missing"])
def test_a_history_without_readable_dates_is_refused(engine, change):
    days = _sessions(60)
    as_of = days[40]
    context = _context(_panel(days), as_of, 30)
    history = (
        context.history.with_columns(pl.col("date").cast(pl.Int32))
        if change == "integer"
        else context.history.drop("date")
    )

    with pytest.raises(ValueError, match="date"):
        engine.run("short_window", replace(context, history=history))
