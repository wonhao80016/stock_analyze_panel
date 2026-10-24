from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig


def _panel(
    symbols: list[str],
    days: int,
    overrides: dict[tuple[str, int], dict] | None = None,
) -> pl.DataFrame:
    overrides = overrides or {}
    start = date(2024, 1, 1)
    rows = []
    for symbol in symbols:
        for offset in range(days):
            patch = overrides.get((symbol, offset), {})
            rows.append({
                "symbol": symbol,
                "name": symbol,
                "date": start + timedelta(days=offset),
                "open": patch.get("open", 10.0),
                "high": patch.get("high", 10.0),
                "low": patch.get("low", 10.0),
                "close": patch.get("close", 10.0),
                "volume": 100_000,
                "score": {"A": 2, "B": 1}.get(symbol, 0),
                "signal_limit_up": False,
                "signal_limit_down": False,
            })
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _mask(panel: pl.DataFrame, marks: set[tuple[str, int]]) -> pl.Series:
    base = date(2024, 1, 1)
    return pl.Series(
        [
            (row["symbol"], (row["date"] - base).days) in marks
            for row in panel.select(["symbol", "date"]).iter_rows(named=True)
        ],
        dtype=pl.Boolean,
    )


def _config(**kwargs) -> MatcherConfig:
    base = {
        "entry_fill": "open_t+1",
        "exit_fill": "close_t",
        "fees_pct": 0,
        "slippage_bps": 0,
        "max_positions": 1,
        "initial_capital": 100_000,
    }
    base.update(kwargs)
    return MatcherConfig(**base)


def _symbols(result) -> set[str]:
    return {trade.symbol for trade in result.trades}


# B006: an open_t+1 buy may only use the slots and cash known at the open.

def test_intraday_stop_loss_slot_is_not_reused_by_the_same_open_buy():
    panel = _panel(["A", "B"], days=4, overrides={
        ("A", 2): {"open": 10.0, "high": 10.0, "low": 9.4, "close": 9.6},
    })
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _mask(panel, {("A", 0), ("B", 1)}),
        _mask(panel, set()),
        _config(stop_loss_pct=0.05),
    )

    a_trade = next(t for t in result.trades if t.symbol == "A")
    assert a_trade.exit_reason == "stop_loss"
    assert a_trade.exit_date == "2024-01-03"
    assert "B" not in _symbols(result)
    assert result.stats["execution"]["buy_no_slot"] == 1


def test_close_exit_slot_is_not_reused_by_the_same_open_buy():
    panel = _panel(["A", "B"], days=4)
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _mask(panel, {("A", 0), ("B", 1)}),
        _mask(panel, {("A", 2)}),
        _config(),
    )

    a_trade = next(t for t in result.trades if t.symbol == "A")
    assert a_trade.exit_date == "2024-01-03"
    assert "B" not in _symbols(result)
    assert result.stats["execution"]["buy_no_slot"] == 1


def test_gap_open_stop_frees_the_slot_for_the_same_open_buy():
    panel = _panel(["A", "B"], days=4, overrides={
        ("A", 2): {"open": 9.0, "high": 9.2, "low": 9.0, "close": 9.1},
    })
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _mask(panel, {("A", 0), ("B", 1)}),
        _mask(panel, set()),
        _config(stop_loss_pct=0.05),
    )

    b_trade = next(t for t in result.trades if t.symbol == "B")
    assert b_trade.entry_date == "2024-01-03"
    assert result.stats["execution"]["buy_no_slot"] == 0


def test_open_fill_exit_frees_the_slot_for_the_same_open_buy():
    panel = _panel(["A", "B"], days=4)
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _mask(panel, {("A", 0), ("B", 1)}),
        _mask(panel, {("A", 1)}),
        _config(exit_fill="open_t+1"),
    )

    b_trade = next(t for t in result.trades if t.symbol == "B")
    assert b_trade.entry_date == "2024-01-03"


def test_intraday_release_is_usable_from_the_next_bar():
    panel = _panel(["A", "B"], days=5, overrides={
        ("A", 2): {"open": 10.0, "high": 10.0, "low": 9.4, "close": 9.6},
    })
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _mask(panel, {("A", 0), ("B", 1), ("B", 2)}),
        _mask(panel, set()),
        _config(stop_loss_pct=0.05),
    )

    b_trade = next(t for t in result.trades if t.symbol == "B")
    assert b_trade.entry_date == "2024-01-04"


# B007: the minute replay day is chosen with intraday information only.

class _MinuteRepo:
    def __init__(self, rows: pl.DataFrame) -> None:
        self.rows = rows

    def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
        wanted = {value.isoformat() for value in dates}
        return self.rows.filter(
            pl.col("symbol").is_in(symbols)
            & pl.col("datetime").dt.strftime("%Y-%m-%d").is_in(sorted(wanted))
        )


def _false_breakdown_case() -> tuple[pl.DataFrame, pl.Series, pl.Series, pl.DataFrame]:
    # Day 1 closes strictly above its MA20, which arms day 2; a close equal to the line has no side.
    # Day 2 dips below the MA20 trigger line of 10.0 at 09:32 and closes back above it.
    panel = _panel(["A"], days=5, overrides={
        ("A", 1): {"high": 10.1, "close": 10.1},
        ("A", 2): {"open": 10.1, "high": 10.3, "low": 9.6, "close": 10.1},
    }).with_columns(
        pl.Series("ma20", [10.0, 10.0, 10.005, 10.005, 10.005]),
        pl.Series("signal_ma20_breakdown", [False] * 5),
    )
    minute = pl.DataFrame({
        "symbol": ["A"] * 4,
        "datetime": [
            datetime(2024, 1, 3, 9, 31),
            datetime(2024, 1, 3, 9, 32),
            datetime(2024, 1, 3, 9, 33),
            datetime(2024, 1, 3, 13, 30),
        ],
        "open": [10.2, 10.1, 9.7, 10.1],
        "high": [10.3, 10.2, 9.8, 10.1],
        "low": [10.1, 9.8, 9.6, 10.1],
        "close": [10.2, 9.9, 9.7, 10.1],
        "volume": [100.0] * 4,
        "amount": [1020.0, 990.0, 970.0, 1010.0],
    })
    return panel, _mask(panel, {("A", 0)}), _mask(panel, set()), minute


def _minute_config() -> MatcherConfig:
    return _config(exit_fill="signal_next_minute", minute_fill=True)


def test_portfolio_minute_replay_sells_on_an_intraday_false_breakdown():
    panel, entries, exits, minute = _false_breakdown_case()
    result = BacktestEngine(repo=_MinuteRepo(minute)).simulate_portfolio(
        panel, entries, exits, _minute_config(),
        exit_signal_ids=["signal_ma20_breakdown"],
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "signal"
    assert trade.exit_date == "2024-01-03"
    assert trade.exit_price == 9.7
    assert trade.exit_signal_id == "signal_ma20_breakdown"


def test_full_mode_minute_replay_sells_on_an_intraday_false_breakdown():
    panel, entries, exits, minute = _false_breakdown_case()
    result = BacktestEngine(repo=_MinuteRepo(minute)).simulate_independent_candidates(
        panel, entries, exits, _minute_config(),
        exit_signal_ids=["signal_ma20_breakdown"],
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "signal"
    assert trade.exit_date == "2024-01-03"
    assert trade.exit_price == 9.7


def test_minute_replay_ignores_days_whose_line_is_not_armed_at_the_open():
    panel, entries, exits, minute = _false_breakdown_case()
    # The prior close sits below its MA20, so no fresh breakdown can fire on day 2.
    panel = panel.with_columns(
        pl.when(pl.col("date") == date(2024, 1, 2)).then(9.9).otherwise(pl.col("close")).alias("close")
    )
    result = BacktestEngine(repo=_MinuteRepo(minute)).simulate_portfolio(
        panel, entries, exits, _minute_config(),
        exit_signal_ids=["signal_ma20_breakdown"],
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "end"


# B015: an exit signal on the entry bar is handled alike in position and full modes.

def test_exit_signal_on_entry_bar_is_deferred_in_both_modes():
    panel = _panel(["A"], days=6, overrides={
        ("A", 2): {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0},
        ("A", 5): {"open": 14.0, "high": 14.0, "low": 14.0, "close": 14.0},
    })
    entries = _mask(panel, {("A", 0)})
    exits = _mask(panel, {("A", 0)})
    config = _config(exit_fill="open_t+1")
    engine = BacktestEngine(repo=None)

    position = engine.simulate_portfolio(panel, entries, exits, config)
    full = engine.simulate_independent_candidates(panel, entries, exits, config)

    assert len(position.trades) == len(full.trades) == 1
    position_trade, full_trade = position.trades[0], full.trades[0]
    assert full_trade.exit_date == "2024-01-03"
    assert position_trade.exit_date == full_trade.exit_date
    assert position_trade.exit_reason == full_trade.exit_reason == "signal"
    assert position_trade.pnl_pct == full_trade.pnl_pct


def test_open_buy_sizes_from_the_cash_and_exposure_known_at_the_open():
    # A sells at the close at 20 on day 2; the open buy of B must not size from that profit.
    panel = _panel(["A", "B"], days=4, overrides={
        ("A", 2): {"open": 10.0, "high": 20.0, "low": 10.0, "close": 20.0},
    })
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _mask(panel, {("A", 0), ("B", 1)}),
        _mask(panel, {("A", 2)}),
        _config(max_positions=2, max_exposure_pct=0.5),
    )

    a_trade = next(t for t in result.trades if t.symbol == "A")
    b_trade = next(t for t in result.trades if t.symbol == "B")
    assert a_trade.exit_date == b_trade.entry_date == "2024-01-03"
    assert a_trade.entry_value == pytest.approx(25_000)
    assert b_trade.entry_value <= 25_000 + 1e-6


def test_open_buy_cash_excludes_proceeds_of_a_later_same_bar_exit():
    panel = _panel(["A", "B", "C"], days=4, overrides={
        ("A", 2): {"open": 10.0, "high": 20.0, "low": 10.0, "close": 20.0},
    })
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        _mask(panel, {("A", 0), ("B", 0), ("C", 1)}),
        _mask(panel, {("A", 2)}),
        _config(max_positions=3, initial_capital=90_000),
    )

    c_trade = next(t for t in result.trades if t.symbol == "C")
    assert c_trade.entry_date == "2024-01-03"
    assert c_trade.entry_value <= 30_000 + 1e-6


def test_minute_refined_sell_at_an_intraday_touch_is_not_an_open_release():
    # Daily open 10.0; the 09:31 minute opens at 10.2, so the 10.0 fill is an intraday touch.
    panel = _panel(["A", "B"], days=4).with_columns(
        pl.Series("ma5", [10.0] * 8),
    )
    minute = pl.DataFrame({
        "symbol": ["A", "A"],
        "datetime": [datetime(2024, 1, 3, 9, 31), datetime(2024, 1, 3, 9, 32)],
        "open": [10.2, 10.1],
        "high": [10.3, 10.1],
        "low": [10.1, 9.9],
        "close": [10.2, 10.0],
        "volume": [100.0, 100.0],
        "amount": [1020.0, 1000.0],
    })
    result = BacktestEngine(repo=_MinuteRepo(minute)).simulate_portfolio(
        panel,
        _mask(panel, {("A", 0), ("B", 1)}),
        _mask(panel, {("A", 1)}),
        _config(exit_fill="open_t+1", minute_fill=True),
    )

    a_trade = next(t for t in result.trades if t.symbol == "A")
    assert a_trade.exit_date == "2024-01-03"
    assert a_trade.exit_price == 10.0
    assert "B" not in _symbols(result)


class _CountingMinuteRepo(_MinuteRepo):
    def __init__(self, rows: pl.DataFrame) -> None:
        super().__init__(rows)
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
        self.calls.append((tuple(sorted(symbols)), tuple(sorted(d.isoformat() for d in dates))))
        return super().get_minute_by_dates(symbols, dates, asset_type)


def _armed_two_symbol_case() -> tuple[pl.DataFrame, pl.Series, pl.Series, pl.DataFrame]:
    # A is held throughout; B is bought on day 1 and stopped out on day 2, yet stays armed.
    panel = _panel(["A", "B"], days=6, overrides={
        ("B", 2): {"open": 10.0, "high": 10.0, "low": 9.0, "close": 9.6},
    }).with_columns(
        pl.Series("ma20", [10.0] * 12),
        pl.Series("signal_ma20_breakdown", [False] * 12),
    )
    minute = pl.DataFrame({
        "symbol": ["A", "A"],
        "datetime": [datetime(2024, 1, 5, 9, 31), datetime(2024, 1, 5, 9, 32)],
        "open": [10.0, 10.0],
        "high": [10.0, 10.0],
        "low": [10.0, 10.0],
        "close": [10.0, 10.0],
        "volume": [100.0, 100.0],
        "amount": [1000.0, 1000.0],
    })
    return panel, _mask(panel, {("A", 0), ("B", 0)}), _mask(panel, set()), minute


def _held_by_day(trades) -> dict[str, set[str]]:
    held: dict[str, set[str]] = {}
    for trade in trades:
        day = date.fromisoformat(trade.entry_date)
        while day <= date.fromisoformat(trade.exit_date):
            held.setdefault(day.isoformat(), set()).add(trade.symbol)
            day += timedelta(days=1)
    return held


def test_portfolio_minute_reads_are_one_session_of_held_symbols_per_call():
    panel, entries, exits, minute = _armed_two_symbol_case()
    repo = _CountingMinuteRepo(minute)
    result = BacktestEngine(repo=repo).simulate_portfolio(
        panel, entries, exits,
        _config(exit_fill="signal_next_minute", minute_fill=True, max_positions=2),
        exit_signal_ids=["signal_ma20_breakdown"],
    )

    held = _held_by_day(result.trades)
    assert repo.calls
    for symbols, days in repo.calls:
        assert len(days) == 1
        assert set(symbols) <= held.get(days[0], set())
    assert len({days for _, days in repo.calls}) == len(repo.calls)


def test_full_mode_minute_reads_one_call_per_symbol_for_its_held_sessions_only():
    panel, entries, exits, minute = _armed_two_symbol_case()
    repo = _CountingMinuteRepo(minute)
    result = BacktestEngine(repo=repo).simulate_independent_candidates(
        panel, entries, exits,
        _config(exit_fill="signal_next_minute", minute_fill=True, stop_loss_pct=0.05),
        exit_signal_ids=["signal_ma20_breakdown"],
    )

    held = _held_by_day(result.trades)
    assert repo.calls
    for symbols, days in repo.calls:
        assert len(symbols) == 1
        assert all(symbols[0] in held.get(day, set()) for day in days)
    assert len({symbols for symbols, _ in repo.calls}) == len(repo.calls)


def test_full_mode_reads_a_held_session_missing_from_the_daily_prefetch():
    # The minute entry fill of 9.5 lowers the stop line, so A outlives its daily-pass stop on day 2.
    panel = _panel(["A"], days=6, overrides={
        ("A", 2): {"open": 10.0, "high": 10.0, "low": 9.4, "close": 9.6},
    }).with_columns(pl.Series("ma5", [9.5] * 6))
    minute = pl.DataFrame({
        "symbol": ["A"] * 4,
        "datetime": [
            datetime(2024, 1, 2, 9, 31), datetime(2024, 1, 2, 9, 32),
            datetime(2024, 1, 5, 9, 31), datetime(2024, 1, 5, 9, 32),
        ],
        "open": [9.0, 9.3, 10.0, 10.0],
        "high": [9.2, 9.6, 10.0, 10.0],
        "low": [9.0, 9.3, 10.0, 10.0],
        "close": [9.1, 9.5, 10.0, 10.0],
        "volume": [100.0] * 4,
        "amount": [910.0, 950.0, 1000.0, 1000.0],
    })
    repo = _CountingMinuteRepo(minute)
    result = BacktestEngine(repo=repo).simulate_independent_candidates(
        panel,
        _mask(panel, {("A", 0)}),
        _mask(panel, {("A", 3)}),
        _config(exit_fill="open_t+1", minute_fill=True, stop_loss_pct=0.05),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == 9.5
    assert trade.exit_date == "2024-01-05"
    assert trade.exit_reason == "signal"
    assert "2024-01-05" not in repo.calls[0][1]
    assert any("2024-01-05" in days for _, days in repo.calls[1:])
