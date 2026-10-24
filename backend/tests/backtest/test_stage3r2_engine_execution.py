from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.matrix import build_market_matrix
from app.taiwan.market_rules import BacktestMode, LotModel, TaiwanMarketProfile, TradingCostModel

START = date(2024, 1, 1)


def _panel(
    symbols: list[str],
    days: int,
    overrides: dict[tuple[str, int], dict] | None = None,
) -> pl.DataFrame:
    overrides = overrides or {}
    rows = []
    for symbol in symbols:
        for offset in range(days):
            patch = overrides.get((symbol, offset), {})
            rows.append({
                "symbol": symbol,
                "name": symbol,
                "date": START + timedelta(days=offset),
                "open": patch.get("open", 10.0),
                "high": patch.get("high", 10.0),
                "low": patch.get("low", 10.0),
                "close": patch.get("close", 10.0),
                "volume": 100_000,
                "score": 1.0,
                "ma5": patch.get("ma5", 10.0),
                "signal_limit_up": patch.get("limit_up", False),
                "signal_limit_down": patch.get("limit_down", False),
                "signal_x": patch.get("signal_x", False),
            })
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _mask(panel: pl.DataFrame, marks: set[tuple[str, int]]) -> pl.Series:
    return pl.Series(
        [
            (row["symbol"], (row["date"] - START).days) in marks
            for row in panel.select(["symbol", "date"]).iter_rows(named=True)
        ],
        dtype=pl.Boolean,
    )


def _config(**kwargs) -> MatcherConfig:
    base = {
        "entry_fill": "close_t",
        "exit_fill": "close_t",
        "fees_pct": 0,
        "slippage_bps": 0,
        "max_positions": 1,
        "initial_capital": 100_000,
    }
    base.update(kwargs)
    return MatcherConfig(**base)


def _run(mode: str, panel, entries, exits, config, **kwargs):
    engine = BacktestEngine(repo=kwargs.pop("repo", None))
    run = engine.simulate_portfolio if mode == "portfolio" else engine.simulate_independent_candidates
    return run(panel, entries, exits, config, **kwargs)


# B098: a fill at the session's limit price is refused when the limit is locked.

LOCKED_UP_CLOSE = {"open": 10.0, "high": 11.0, "low": 10.0, "close": 11.0, "limit_up": True}


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_close_buy_is_refused_when_the_close_is_locked_limit_up(mode: str) -> None:
    panel = _panel(["A"], days=4, overrides={("A", 1): LOCKED_UP_CLOSE})

    result = _run(mode, panel, _mask(panel, {("A", 1)}), _mask(panel, set()), _config())

    assert result.trades == []
    assert result.stats["execution"]["buy_limit_up"] == 1


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_open_buy_is_refused_when_the_session_opens_at_the_locked_limit_up(mode: str) -> None:
    panel = _panel(["A"], days=4, overrides={
        ("A", 1): {"open": 11.0, "high": 11.0, "low": 10.5, "close": 11.0, "limit_up": True},
    })

    result = _run(
        mode, panel, _mask(panel, {("A", 0)}), _mask(panel, set()), _config(entry_fill="open_t+1"),
    )

    assert result.trades == []
    assert result.stats["execution"]["buy_limit_up"] == 1


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_open_buy_below_the_limit_still_fills_on_a_locked_close(mode: str) -> None:
    panel = _panel(["A"], days=4, overrides={("A", 1): LOCKED_UP_CLOSE})

    result = _run(
        mode, panel, _mask(panel, {("A", 0)}), _mask(panel, set()),
        _config(entry_fill="open_t+1", max_hold_days=1),
    )

    assert [trade.entry_date for trade in result.trades] == ["2024-01-02"]
    assert result.trades[0].entry_price == pytest.approx(10.0)


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_close_sell_waits_while_the_close_is_locked_limit_down(mode: str) -> None:
    panel = _panel(["A"], days=4, overrides={
        ("A", 2): {"open": 10.0, "high": 10.0, "low": 9.0, "close": 9.0, "limit_down": True},
        ("A", 3): {"open": 9.0, "high": 9.2, "low": 8.8, "close": 9.1},
    })

    result = _run(mode, panel, _mask(panel, {("A", 0)}), _mask(panel, {("A", 2)}), _config())

    trade = result.trades[0]
    assert trade.exit_date == "2024-01-04"
    assert trade.exit_price == pytest.approx(9.1)
    assert result.stats["execution"]["sell_limit_down"] == 1


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_gap_stop_at_a_locked_limit_down_open_waits(mode: str) -> None:
    panel = _panel(["A"], days=4, overrides={
        ("A", 2): {"open": 9.0, "high": 9.5, "low": 9.0, "close": 9.0, "limit_down": True},
        ("A", 3): {"open": 8.8, "high": 9.0, "low": 8.7, "close": 8.9},
    })

    result = _run(
        mode, panel, _mask(panel, {("A", 0)}), _mask(panel, set()), _config(stop_loss_pct=0.05),
    )

    trade = result.trades[0]
    assert trade.exit_date == "2024-01-04"
    assert result.stats["execution"]["sell_limit_down"] > 0


# B099: an odd-lot full-mode sample is sized by a fixed notional, not one share.

def test_full_mode_odd_lot_buys_the_notional_instead_of_one_share() -> None:
    panel = _panel(["A"], days=3, overrides={
        ("A", 0): {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
        ("A", 1): {"open": 101.0, "high": 101.0, "low": 101.0, "close": 101.0},
        ("A", 2): {"open": 101.0, "high": 101.0, "low": 101.0, "close": 101.0},
    })
    profile = TaiwanMarketProfile(
        mode=BacktestMode.ODD_LOT,
        cost=TradingCostModel(commission_rate=0.001425, minimum_commission=20),
        lot=LotModel(mode="odd_lot"),
    )
    config = _config(
        market_profile=profile, lot_mode="odd_lot", initial_capital=1_000_000, max_positions=10,
        stamp_tax_pct=0, max_hold_days=1,
    )

    result = _run("full", panel, _mask(panel, {("A", 0)}), _mask(panel, set()), config)

    trade = result.trades[0]
    assert trade.shares == config.align_shares(100_000, 100.0, "A")
    assert trade.shares > 900
    assert trade.pnl_pct == pytest.approx(0.01 - 2 * 0.001425, abs=1e-4)
    assert result.stats["sample_sizing"] == {"basis": "odd_lot_notional", "notional": 100_000.0}


def test_full_mode_odd_lot_notional_follows_the_portfolio_exposure_cap() -> None:
    panel = _panel(["A"], days=3, overrides={
        ("A", offset): {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
        for offset in range(3)
    })
    config = _config(
        lot_mode="odd_lot", initial_capital=1_000_000, max_positions=10, max_exposure_pct=0.5,
        max_hold_days=1,
    )

    result = _run("full", panel, _mask(panel, {("A", 0)}), _mask(panel, set()), config)

    assert result.trades[0].shares == config.align_shares(50_000, 100.0, "A")
    assert result.stats["sample_sizing"] == {"basis": "odd_lot_notional", "notional": 50_000.0}


# B100: each candidate stops reading bars once it has exited.

class _CountingArray(np.ndarray):
    reads = 0

    def __getitem__(self, key):
        if isinstance(key, tuple) and all(isinstance(part, (int, np.integer)) for part in key):
            type(self).reads += 1
        return super().__getitem__(key)


def test_full_mode_candidate_does_not_scan_bars_after_its_exit() -> None:
    days = 400
    panel = _panel(["A"], days=days)
    entries = _mask(panel, {("A", 0)})
    exits = _mask(panel, {("A", 2)})
    matrix = build_market_matrix(panel, entries, exits)
    counted = {
        name: np.asarray(getattr(matrix, name)).view(_CountingArray)
        for name in ("open", "high", "low", "close")
    }
    matrix = dataclasses.replace(matrix, **counted)
    _CountingArray.reads = 0

    result = BacktestEngine(repo=None).simulate_independent_market_matrix(matrix, 1, _config())

    assert result.trades[0].exit_date == "2024-01-03"
    assert _CountingArray.reads < 50


# B101: candidates still open at the end of the data are valued, not dropped.

def test_full_mode_values_a_candidate_whose_last_exit_is_blocked() -> None:
    panel = _panel(["A"], days=3, overrides={
        ("A", 2): {"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0, "limit_down": True},
    })

    result = _run("full", panel, _mask(panel, {("A", 0)}), _mask(panel, set()), _config())

    trade = result.trades[0]
    assert trade.exit_reason == "end_unclosed"
    assert trade.exit_date == "2024-01-03"
    assert trade.exit_price == pytest.approx(9.0)
    assert trade.pnl_pct == pytest.approx(-0.1)
    assert result.stats["execution"]["unclosed_at_end"] == 1
    assert result.stats["n_trades"] == 0
    assert result.stats["unclosed_at_end"]["count"] == 1
    assert result.stats["unclosed_at_end"]["avg_return"] == pytest.approx(-0.1)


def test_full_mode_reports_unclosed_candidates_apart_from_realized_ones() -> None:
    panel = _panel(["A", "B"], days=3, overrides={
        ("A", 1): {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0},
        ("B", 2): {"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0, "limit_down": True},
    })

    result = _run(
        "full", panel, _mask(panel, {("A", 0), ("B", 0)}), _mask(panel, {("A", 1)}), _config(),
    )

    assert sorted(trade.exit_reason for trade in result.trades) == ["end_unclosed", "signal"]
    assert result.stats["n_trades"] == 1
    assert result.stats["win_rate"] == 1.0
    assert result.stats["avg_return"] == pytest.approx(0.1)
    assert result.stats["unclosed_at_end"] == {
        "count": 1,
        "avg_return": pytest.approx(-0.1),
        "pnl_amount": pytest.approx(-100.0),
        "valuation": "last_close",
    }


def test_full_mode_keeps_a_candidate_bought_on_the_last_bar_out_of_the_trades() -> None:
    panel = _panel(["A"], days=3)

    result = _run("full", panel, _mask(panel, {("A", 2)}), _mask(panel, set()), _config())

    assert result.trades == []
    assert result.stats["execution"]["sell_no_future"] == 1
    assert result.stats["execution"]["unclosed_at_end"] == 0


# B103: signal_next_minute exits without a signal never refine on a line holding the close.

class _MinuteRepo:
    def __init__(self, rows: pl.DataFrame) -> None:
        self.rows = rows

    def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
        wanted = {value.isoformat() for value in dates}
        return self.rows.filter(
            pl.col("symbol").is_in(symbols)
            & pl.col("datetime").dt.strftime("%Y-%m-%d").is_in(sorted(wanted))
        )


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_max_hold_exit_with_minute_trigger_keeps_the_daily_close(mode: str) -> None:
    # Day 2's ma5 of 9.8 includes that day's close; the minute low 9.5 crosses it.
    panel = _panel(["A", "B"], days=4, overrides={
        ("A", 2): {"open": 10.0, "high": 10.2, "low": 9.5, "close": 10.0, "ma5": 9.8},
    })
    minute = pl.DataFrame({
        "symbol": ["A"] * 3,
        "datetime": [datetime(2024, 1, 3, 9, 1), datetime(2024, 1, 3, 10, 0), datetime(2024, 1, 3, 13, 30)],
        "open": [10.0, 9.6, 10.0],
        "high": [10.2, 9.7, 10.0],
        "low": [9.9, 9.5, 10.0],
        "close": [10.0, 9.6, 10.0],
        "volume": [100.0, 100.0, 100.0],
        "amount": [1000.0, 960.0, 1000.0],
    })
    config = _config(exit_fill="signal_next_minute", minute_fill=True, max_hold_days=2, max_positions=2)

    result = _run(
        mode, panel, _mask(panel, {("A", 0)}), _mask(panel, {("B", 2)}), config,
        repo=_MinuteRepo(minute),
    )

    trade = next(t for t in result.trades if t.symbol == "A")
    assert trade.exit_reason == "max_hold"
    assert trade.exit_price == pytest.approx(10.0)


# B104: a signal exit deferred by the minimum holding keeps its signal id.

@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_minimum_holding_deferred_signal_exit_keeps_its_signal_id(mode: str) -> None:
    panel = _panel(["A"], days=5, overrides={("A", 1): {"signal_x": True}})

    result = _run(
        mode, panel, _mask(panel, {("A", 0)}), _mask(panel, {("A", 1)}),
        _config(minimum_holding_sessions=3),
        exit_signal_ids=["signal_x"],
    )

    trade = result.trades[0]
    assert trade.exit_reason == "signal"
    assert trade.exit_date == "2024-01-04"
    assert trade.exit_signal_id == "signal_x"
