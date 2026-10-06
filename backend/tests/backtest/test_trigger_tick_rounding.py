"""Intrabar trigger fills land on the Taiwan exchange tick grid of the traded price.

Stop-type exits (stop loss, trailing stop, trailing take profit) floor the raw
trigger line to the tick and take profit ceils it; a gap through the line still
fills at the open. Rounding happens on the raw price of the fill bar and the
result is mapped back to the forward-adjusted scale for the return series.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.strategy import _configure_taiwan_matcher
from app.taiwan.corporate_actions import TaiwanCorporateActionStore

MODES = ("simulate_portfolio", "simulate_independent_candidates")
FILLS = ("close_t", "open_t+1")


class _Master:
    def __init__(self, instruments: dict[str, SimpleNamespace]) -> None:
        self.instruments = instruments

    def get_instrument(self, symbol: str) -> SimpleNamespace | None:
        return self.instruments.get(symbol)


def _instrument(symbol: str, instrument_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        instrument_type=instrument_type,
        lot_size=1000,
        etf_category="domestic_equity" if instrument_type == "etf" else None,
        leverage_multiplier=1.0,
        etf_management_style=None,
        classification_source="official_metadata",
        underlying_scope="domestic",
    )


def _panel(symbol: str, bars: list[tuple[float, float, float, float]], raw_close=None) -> pl.DataFrame:
    """bars: adjusted (open, high, low, close); raw_close defaults to the adjusted close."""
    raw_close = raw_close or [bar[3] for bar in bars]
    start = date(2026, 9, 1)
    return pl.DataFrame({
        "symbol": [symbol] * len(bars),
        "name": [symbol] * len(bars),
        "date": [start + timedelta(days=i) for i in range(len(bars))],
        "open": [float(bar[0]) for bar in bars],
        "high": [float(bar[1]) for bar in bars],
        "low": [float(bar[2]) for bar in bars],
        "close": [float(bar[3]) for bar in bars],
        "raw_close": [float(value) for value in raw_close],
        "volume": [1_000_000.0] * len(bars),
        "score": [1.0] * len(bars),
        "signal_limit_up": [False] * len(bars),
        "signal_limit_down": [False] * len(bars),
    })


def _config(tmp_path, symbol: str, instrument_type: str, fill: str, **risk) -> MatcherConfig:
    config = MatcherConfig(
        entry_fill=fill,
        exit_fill=fill,
        slippage_bps=0,
        max_positions=1,
        initial_capital=20_000_000,
        **risk,
    )
    _configure_taiwan_matcher(
        config,
        [symbol],
        security_master=_Master({symbol: _instrument(symbol, instrument_type)}),
        corporate_actions=TaiwanCorporateActionStore(tmp_path / "corporate_actions"),
    )
    return config


def _run(method: str, config: MatcherConfig, panel: pl.DataFrame):
    entries = [True] + [False] * (panel.height - 1)
    exits = [False] * panel.height
    result = getattr(BacktestEngine(repo=None), method)(panel, pl.Series(entries), pl.Series(exits), config)
    assert len(result.trades) == 1
    return result.trades[0]


def _flat(price: float) -> tuple[float, float, float, float]:
    return (price, price, price, price)


# (symbol, instrument type, entry, risk config, hold bar, trigger bar, expected fill, reason)
GRID_CASES = [
    pytest.param(
        "2330.TWSE", "stock", 5180.0, {"stop_loss_pct": 0.05},
        _flat(5180.0), (5100.0, 5100.0, 4900.0, 4950.0), 4920.0, "stop_loss",
        id="stop-loss-4921-floors-to-5-tick",
    ),
    pytest.param(
        "2379.TWSE", "stock", 713.0, {"stop_loss_pct": 0.03},
        _flat(713.0), (700.0, 705.0, 690.0, 695.0), 691.0, "stop_loss",
        id="stop-loss-691.61-floors-to-1-tick",
    ),
    pytest.param(
        "2379.TWSE", "stock", 713.0, {"trailing_stop_pct": 0.05},
        _flat(713.0), (700.0, 705.0, 670.0, 690.0), 677.0, "trailing_stop",
        id="trailing-stop-677.35-floors",
    ),
    pytest.param(
        "2379.TWSE", "stock", 713.0,
        {"trailing_take_profit_activate_pct": 0.10, "trailing_take_profit_drawdown_pct": 0.03},
        (713.0, 801.0, 713.0, 790.0), (790.0, 795.0, 770.0, 775.0), 776.0, "trailing_take_profit",
        id="trailing-take-profit-776.97-floors",
    ),
    pytest.param(
        "2379.TWSE", "stock", 713.0, {"take_profit_pct": 0.10},
        _flat(713.0), (720.0, 790.0, 715.0, 780.0), 785.0, "take_profit",
        id="take-profit-784.3-ceils",
    ),
    pytest.param(
        "0050.TWSE", "etf", 111.85, {"stop_loss_pct": 0.05},
        _flat(111.85), (108.0, 108.0, 105.0, 106.5), 106.25, "stop_loss",
        id="etf-stop-loss-106.2575-floors-to-0.05",
    ),
    pytest.param(
        "0050.TWSE", "etf", 52.6, {"stop_loss_pct": 0.05},
        _flat(52.6), (51.0, 51.0, 49.5, 50.0), 49.97, "stop_loss",
        id="etf-stop-loss-float32-line-on-tick-keeps-49.97",
    ),
    pytest.param(
        "0056.TWSE", "etf", 35.17, {"take_profit_pct": 0.07},
        _flat(35.17), (36.0, 37.8, 35.9, 37.5), 37.64, "take_profit",
        id="etf-take-profit-37.6319-ceils-to-0.01",
    ),
]


@pytest.mark.parametrize("fill", FILLS)
@pytest.mark.parametrize("method", MODES)
@pytest.mark.parametrize(
    ("symbol", "instrument_type", "entry", "risk", "hold_bar", "trigger_bar", "expected", "reason"),
    GRID_CASES,
)
def test_trigger_fill_is_rounded_to_the_tick_in_the_trigger_direction(
    tmp_path, method, fill, symbol, instrument_type, entry, risk, hold_bar, trigger_bar, expected, reason,
) -> None:
    config = _config(tmp_path, symbol, instrument_type, fill, **risk)
    panel = _panel(symbol, [_flat(entry), hold_bar, trigger_bar])

    trade = _run(method, config, panel)

    assert trade.entry_price == pytest.approx(entry)
    assert trade.exit_reason == reason
    assert trade.exit_date == "2026-09-03"
    assert trade.exit_price == pytest.approx(expected, abs=1e-9)
    assert trade.exit_price_adj == pytest.approx(expected, abs=1e-4)


@pytest.mark.parametrize(
    ("method", "shares", "entry_value", "exit_value"),
    [
        # 28 lots: 19,964,000 + 0.1425% commission; 19,348,000 - 0.1425% commission - 0.3% tax.
        ("simulate_portfolio", 28_000, 19_992_448.70, 19_262_385.10),
        ("simulate_independent_candidates", 1_000, 714_016.03, 687_942.33),
    ],
)
def test_trigger_fill_values_the_exit_at_the_rounded_price(tmp_path, method, shares, entry_value, exit_value) -> None:
    config = _config(tmp_path, "2379.TWSE", "stock", "close_t", stop_loss_pct=0.03)
    panel = _panel("2379.TWSE", [_flat(713.0), _flat(713.0), (700.0, 705.0, 690.0, 695.0)])

    trade = _run(method, config, panel)

    assert trade.shares == shares
    assert trade.exit_price == 691.0
    assert trade.entry_value == pytest.approx(entry_value, abs=0.011)
    assert trade.exit_value == pytest.approx(exit_value, abs=0.011)
    assert trade.pnl_amount == pytest.approx(exit_value - entry_value, abs=0.011)


@pytest.mark.parametrize("fill", FILLS)
@pytest.mark.parametrize("method", MODES)
@pytest.mark.parametrize(
    ("risk", "trigger_bar", "reason"),
    [
        ({"stop_loss_pct": 0.05}, (4800.0, 4850.0, 4750.0, 4800.0), "stop_loss"),
        ({"take_profit_pct": 0.05}, (5600.0, 5650.0, 5550.0, 5600.0), "take_profit"),
    ],
)
def test_gap_through_the_trigger_line_fills_at_the_open(tmp_path, method, fill, risk, trigger_bar, reason) -> None:
    config = _config(tmp_path, "2330.TWSE", "stock", fill, **risk)
    panel = _panel("2330.TWSE", [_flat(5180.0), _flat(5180.0), trigger_bar])

    trade = _run(method, config, panel)

    assert trade.exit_reason == reason
    assert trade.exit_price == pytest.approx(trigger_bar[0])


@pytest.mark.parametrize("method", MODES)
@pytest.mark.parametrize(
    ("risk", "trigger_bar", "expected"),
    [
        ({"stop_loss_pct": 0.03}, (700.0, 705.0, 691.5, 695.0), 691.5),
        ({"take_profit_pct": 0.10}, (720.0, 784.5, 715.0, 780.0), 784.5),
    ],
)
def test_rounded_fill_never_leaves_an_off_grid_bar_range(tmp_path, method, risk, trigger_bar, expected) -> None:
    config = _config(tmp_path, "2379.TWSE", "stock", "close_t", **risk)
    panel = _panel("2379.TWSE", [_flat(713.0), _flat(713.0), trigger_bar])

    trade = _run(method, config, panel)

    assert trade.exit_price == pytest.approx(expected)


@pytest.mark.parametrize("method", MODES)
def test_held_position_across_ex_dividend_rounds_on_the_raw_price(tmp_path, method) -> None:
    """Cash dividends go ex on bar 2 (720 -> 702) and bar 4 (700 -> 693) after the entry."""
    first, second = 702.0 / 720.0, 693.0 / 700.0
    raw_bars = [
        _flat(713.0),
        _flat(720.0),
        (702.0, 705.0, 700.0, 702.0),
        (670.0, 672.0, 655.0, 665.0),
        _flat(693.0),
    ]
    factors = [first * second, first * second, second, second, 1.0]
    bars = [tuple(price * factor for price in bar) for bar, factor in zip(raw_bars, factors, strict=True)]
    config = _config(tmp_path, "2379.TWSE", "stock", "close_t", stop_loss_pct=0.05)
    panel = _panel("2379.TWSE", bars, raw_close=[bar[3] for bar in raw_bars])

    trade = _run(method, config, panel)

    # Line 713 * 0.975 * 0.95 = 660.42 raw on the trigger bar floors to 660, not 659.6 from flooring the adjusted line.
    assert trade.exit_reason == "stop_loss"
    assert trade.entry_price == pytest.approx(713.0)
    assert trade.exit_price == pytest.approx(660.0, abs=1e-9)
    assert trade.exit_price_adj == pytest.approx(660.0 * second, abs=1e-4)


@pytest.mark.parametrize("method", MODES)
def test_non_taiwan_trigger_fill_keeps_the_exact_line(method) -> None:
    config = MatcherConfig(
        entry_fill="close_t",
        exit_fill="close_t",
        fees_pct=0,
        slippage_bps=0,
        max_positions=1,
        initial_capital=20_000_000,
        stop_loss_pct=0.05,
    )
    panel = _panel("600000.SH", [_flat(5180.0), _flat(5180.0), (5100.0, 5100.0, 4900.0, 4950.0)])

    trade = _run(method, config, panel)

    assert trade.exit_price == pytest.approx(4921.0)
