from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig

SYMBOL = "2330.TWSE"
DAY = date(2024, 1, 2)


def _minutes(times: list[tuple[int, int]], *, unattributed: float | None = None) -> pl.DataFrame:
    count = len(times)
    frame = pl.DataFrame({
        "symbol": [SYMBOL] * count,
        "datetime": [datetime(DAY.year, DAY.month, DAY.day, hour, minute) for hour, minute in times],
        "open": [10.0] * count,
        "high": [10.8] * count,
        "low": [9.9] * count,
        "close": [10.0] * count,
        "volume": [100.0] * count,
        "amount": [1000.0] * count,
    })
    if unattributed is not None:
        frame = frame.with_columns(
            pl.Series("unattributed_volume", [unattributed] + [None] * (count - 1), dtype=pl.Float64)
        )
    return frame


def _full_session() -> list[tuple[int, int]]:
    start = datetime(2024, 1, 2, 9, 0)
    end = datetime(2024, 1, 2, 13, 24)
    times = []
    while start <= end:
        times.append((start.hour, start.minute))
        start += timedelta(minutes=1)
    return [*times, (13, 30)]


class _Repo:
    def __init__(self, rows: pl.DataFrame) -> None:
        self.rows = rows

    def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
        return self.rows.filter(pl.col("symbol").is_in(symbols))


def _load(rows: pl.DataFrame) -> dict:
    return BacktestEngine._load_minute_for_fills(_Repo(rows), [SYMBOL], {DAY.isoformat()})


# B102: minute fills need minute bars covering the whole Taiwan session.

def test_a_full_session_is_kept() -> None:
    assert (SYMBOL, DAY.isoformat()) in _load(_minutes(_full_session()))


@pytest.mark.parametrize(
    "times",
    [
        pytest.param(_full_session()[60:], id="recorder_started_late"),
        pytest.param(_full_session()[:-30], id="recorder_stopped_early"),
        pytest.param(_full_session()[:100] + _full_session()[130:], id="gap_inside_session"),
    ],
)
def test_an_incomplete_session_is_dropped(times) -> None:
    assert _load(_minutes(times)) == {}


def test_a_session_with_unattributed_volume_is_dropped() -> None:
    assert _load(_minutes(_full_session(), unattributed=5_000.0)) == {}


def _panel() -> pl.DataFrame:
    rows = []
    for offset, (open_, close) in enumerate([(10.0, 10.0), (10.0, 10.6), (10.6, 10.6), (10.6, 10.6)]):
        rows.append({
            "symbol": SYMBOL,
            "name": SYMBOL,
            "date": date(2024, 1, 1) + timedelta(days=offset),
            "open": open_,
            "high": max(open_, close) + 0.2,
            "low": min(open_, close) - 0.1,
            "close": close,
            "raw_close": close,
            "volume": 100_000,
            "score": 1.0,
            "ma5": 10.5,
            "signal_limit_up": False,
            "signal_limit_down": False,
        })
    return pl.DataFrame(rows)


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_an_incomplete_minute_session_falls_back_to_the_daily_open(mode: str) -> None:
    panel = _panel()
    # Only 10:00-10:30 was recorded; its high crosses the prior-day ma5 of 10.5.
    late = _minutes([(10, minute) for minute in range(31)])
    entries = pl.Series([True, False, False, False])
    exits = pl.Series([False, False, False, False])
    config = MatcherConfig(
        entry_fill="open_t+1", exit_fill="close_t", fees_pct=0, slippage_bps=0,
        max_positions=1, initial_capital=100_000, minute_fill=True, max_hold_days=1,
    )
    engine = BacktestEngine(repo=_Repo(late))
    run = engine.simulate_portfolio if mode == "portfolio" else engine.simulate_independent_candidates

    result = run(panel, entries, exits, config)

    assert result.trades[0].entry_price == pytest.approx(10.0)
    assert result.stats["execution"]["minute_incomplete_sessions"] == 1
