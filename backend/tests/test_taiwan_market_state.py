from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.taiwan.benchmarks import TAIEX, TPEX_INDEX
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.market_state import compute_market_state

T = date(2026, 9, 30)


def _weekdays_through(end: date, count: int) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _calendar(data_dir, days: list[date]) -> None:
    for d in days:
        part = data_dir / "taiwan" / "daily" / f"date={d.isoformat()}"
        part.mkdir(parents=True, exist_ok=True)
        (part / "part.parquet").touch()


def _index(data_dir, closes: dict[date, float], symbol: str = TAIEX.symbol) -> None:
    frame = pl.DataFrame(
        {
            "symbol": [symbol] * len(closes),
            "date": list(closes),
            "open": list(closes.values()),
            "high": list(closes.values()),
            "low": list(closes.values()),
            "close": list(closes.values()),
            "volume": [None] * len(closes),
            "amount": [None] * len(closes),
            "quote_ts": [None] * len(closes),
        },
        schema={
            "symbol": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
            "amount": pl.Float64, "quote_ts": pl.Float64,
        },
    )
    TaiwanDailyStore(data_dir / "taiwan" / "index_daily").write_batch(frame)


def _flat_then(days: list[date], last_close: float, flat: float = 100.0) -> dict[date, float]:
    closes = {d: flat for d in days}
    closes[days[-1]] = last_close
    return closes


@pytest.fixture
def days() -> list[date]:
    return _weekdays_through(T, 25)


def test_close_below_the_20_day_average_is_below(tmp_path, days):
    _calendar(tmp_path, days)
    _index(tmp_path, _flat_then(days, 81.0))

    state = compute_market_state(tmp_path, T)

    assert state["status"] == "ok"
    assert state["reason"] is None
    assert state["below_ma"] is True
    assert state["close"] == 81.0
    assert state["ma"] == pytest.approx((19 * 100.0 + 81.0) / 20)
    assert state["window"] == 20
    assert state["as_of"] == "2026-09-30"
    assert state["index_symbol"] == TAIEX.symbol


def test_close_above_the_average_is_not_below(tmp_path, days):
    _calendar(tmp_path, days)
    _index(tmp_path, _flat_then(days, 120.0))

    state = compute_market_state(tmp_path, T)

    assert state["status"] == "ok"
    assert state["below_ma"] is False


def test_close_equal_to_the_average_is_not_below(tmp_path, days):
    _calendar(tmp_path, days)
    _index(tmp_path, {d: 100.0 for d in days})

    state = compute_market_state(tmp_path, T)

    assert state["status"] == "ok"
    assert state["close"] == state["ma"] == 100.0
    assert state["below_ma"] is False


def test_window_uses_only_the_last_20_trading_days_including_t(tmp_path, days):
    closes = _flat_then(days, 100.0)
    for d in days[:5]:
        closes[d] = 10_000.0
    _calendar(tmp_path, days)
    _index(tmp_path, closes)

    state = compute_market_state(tmp_path, T)

    assert state["ma"] == 100.0
    assert state["below_ma"] is False


def test_tpex_rows_in_the_same_store_are_ignored(tmp_path, days):
    _calendar(tmp_path, days)
    _index(tmp_path, _flat_then(days, 120.0))
    _index(tmp_path, {d: 1.0 for d in days}, symbol=TPEX_INDEX.symbol)

    assert compute_market_state(tmp_path, T)["below_ma"] is False


def test_fewer_than_20_trading_days_is_insufficient_history(tmp_path):
    short = _weekdays_through(T, 19)
    _calendar(tmp_path, short)
    _index(tmp_path, _flat_then(short, 50.0))

    state = compute_market_state(tmp_path, T)

    assert state["status"] == "unavailable"
    assert state["reason"] == "insufficient_history"
    assert state["below_ma"] is None
    assert state["close"] is None
    assert state["ma"] is None


def test_index_without_a_bar_on_t_is_index_missing(tmp_path, days):
    _calendar(tmp_path, days)
    closes = _flat_then(days, 50.0)
    del closes[T]
    _index(tmp_path, closes)

    state = compute_market_state(tmp_path, T)

    assert state["status"] == "unavailable"
    assert state["reason"] == "index_missing"


def test_a_trading_day_missing_from_the_index_window_is_a_gap(tmp_path, days):
    _calendar(tmp_path, days)
    closes = _flat_then(days, 50.0)
    del closes[days[-10]]
    _index(tmp_path, closes)

    state = compute_market_state(tmp_path, T)

    assert state["status"] == "unavailable"
    assert state["reason"] == "index_gap"


def test_an_index_date_that_is_not_a_stock_trading_day_is_a_gap(tmp_path, days):
    calendar = [d for d in days if d != days[-10]]
    _calendar(tmp_path, calendar)
    _index(tmp_path, _flat_then(days, 50.0))

    state = compute_market_state(tmp_path, T)

    assert state["status"] == "unavailable"
    assert state["reason"] == "index_gap"


def test_a_gap_before_the_window_does_not_matter(tmp_path, days):
    _calendar(tmp_path, days)
    closes = _flat_then(days, 50.0)
    del closes[days[0]]
    _index(tmp_path, closes)

    assert compute_market_state(tmp_path, T)["status"] == "ok"


def test_missing_t_wins_over_short_history_and_gaps(tmp_path):
    short = _weekdays_through(T, 10)
    _calendar(tmp_path, short)
    closes = {d: 100.0 for d in short[:-1] if d != short[3]}
    closes[short[0] - timedelta(days=1)] = 100.0
    _index(tmp_path, closes)

    assert compute_market_state(tmp_path, T)["reason"] == "index_missing"


def test_short_history_wins_over_gaps(tmp_path):
    short = _weekdays_through(T, 10)
    _calendar(tmp_path, short)
    closes = {d: 100.0 for d in short if d != short[3]}
    _index(tmp_path, closes)

    assert compute_market_state(tmp_path, T)["reason"] == "insufficient_history"


def test_missing_directories_are_unavailable_and_not_created(tmp_path):
    state = compute_market_state(tmp_path, T)

    assert state["status"] == "unavailable"
    assert state["reason"] == "index_missing"
    assert list(tmp_path.iterdir()) == []


def test_missing_calendar_directory_is_not_created(tmp_path, days):
    _index(tmp_path, _flat_then(days, 50.0))

    state = compute_market_state(tmp_path, T)

    assert state["reason"] == "insufficient_history"
    assert not (tmp_path / "taiwan" / "daily").exists()
