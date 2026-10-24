"""Taiwan executions size, charge and report at the price actually traded that day.

The matcher reads forward-adjusted prices, which move every bar before a later
corporate action off the exchange's price grid. Shares, commission, minimum
commission and tax are therefore taken at raw = adjusted * raw_close / close of
the fill bar, while equity keeps marking the position on adjusted prices so that
dividends and splits never show up as losses.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig, TradeRecord
from app.backtest.matrix import build_market_matrix
from app.backtest.strategy import StrategyBacktestService, _configure_taiwan_matcher
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema

COMMISSION = 0.001425
STOCK_TAX = 0.003
ETF_TAX = 0.001


class _Master:
    def __init__(self, instruments: dict[str, SimpleNamespace]) -> None:
        self.instruments = instruments

    def get_instrument(self, symbol: str) -> SimpleNamespace | None:
        return self.instruments.get(symbol)


def _instrument(symbol: str, instrument_type: str = "stock") -> SimpleNamespace:
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


def _store(tmp_path, events: list[tuple[str, date, str, float, float]]) -> TaiwanCorporateActionStore:
    store = TaiwanCorporateActionStore(tmp_path / "corporate_actions")
    if events:
        frame = pl.DataFrame(
            [
                {"symbol": s, "date": d, "kind": k, "pre_close": pre, "ref_price": ref}
                for s, d, k, pre, ref in events
            ],
            schema_overrides={"date": pl.Date},
        )
        missing = [
            pl.lit(None, dtype=dtype).alias(name)
            for name, dtype in corporate_action_schema().items()
            if name not in frame.columns
        ]
        store.upsert(frame.with_columns(missing))
    return store


def _panel(symbol: str, bars: list[tuple]) -> pl.DataFrame:
    """bars: (date, adjusted open, adjusted close, raw_close[, ma5])."""
    rows = []
    for bar in bars:
        day, open_, close, raw_close = bar[:4]
        rows.append({
            "symbol": symbol,
            "name": symbol,
            "date": day,
            "open": float(open_),
            "high": float(max(open_, close)),
            "low": float(min(open_, close)),
            "close": float(close),
            "raw_close": float(raw_close),
            "volume": 1_000_000.0,
            "score": 1.0,
            "ma5": float(bar[4]) if len(bar) > 4 else None,
            "signal_limit_up": False,
            "signal_limit_down": False,
        })
    return pl.DataFrame(rows, schema_overrides={"ma5": pl.Float64})


def _config(
    tmp_path,
    symbol: str,
    events,
    *,
    instrument_type: str = "stock",
    capital: float,
    fill: str = "close_t",
    **kwargs,
) -> MatcherConfig:
    config = MatcherConfig(
        entry_fill=fill,
        exit_fill=kwargs.pop("exit_fill", fill),
        slippage_bps=0,
        max_positions=1,
        initial_capital=capital,
        **kwargs,
    )
    _configure_taiwan_matcher(
        config,
        [symbol],
        security_master=_Master({symbol: _instrument(symbol, instrument_type)}),
        corporate_actions=_store(tmp_path, events),
    )
    return config


# Cash dividend of 10 on 2025-07-02: raw 100 before it is 90 forward-adjusted.
DIVIDEND_SYMBOL = "2330.TWSE"
DIVIDEND_EVENTS = [(DIVIDEND_SYMBOL, date(2025, 7, 2), "exdiv", 100.0, 90.0)]
DIVIDEND_BARS = [
    (date(2025, 7, 1), 90.0, 90.0, 100.0),
    (date(2025, 7, 2), 90.0, 90.0, 90.0),
    (date(2025, 7, 3), 95.0, 95.0, 95.0),
]


def _run(config: MatcherConfig, panel: pl.DataFrame, entries, exits, *, mode="portfolio", repo=None):
    engine = BacktestEngine(repo=repo)
    run = engine.simulate_portfolio if mode == "portfolio" else engine.simulate_independent_candidates
    return run(panel, pl.Series(entries), pl.Series(exits), config)


def test_board_lot_is_unaffordable_at_the_raw_price_before_an_ex_dividend(tmp_path) -> None:
    config = _config(tmp_path, DIVIDEND_SYMBOL, DIVIDEND_EVENTS, capital=95_000)

    result = _run(config, _panel(DIVIDEND_SYMBOL, DIVIDEND_BARS), [True, False, False], [False, False, True])

    assert result.trades == []
    assert result.stats["execution"]["buy_lot_size"] == 1


def test_ex_dividend_trade_is_sized_and_charged_on_raw_prices(tmp_path) -> None:
    config = _config(tmp_path, DIVIDEND_SYMBOL, DIVIDEND_EVENTS, capital=200_000)

    result = _run(config, _panel(DIVIDEND_SYMBOL, DIVIDEND_BARS), [True, False, False], [False, False, True])

    trade = result.trades[0]
    assert trade.shares == 1000
    assert trade.exit_shares == 1000
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.entry_price_adj == pytest.approx(90.0)
    assert trade.exit_price == pytest.approx(95.0)
    assert trade.exit_price_adj == pytest.approx(95.0)
    assert trade.entry_value == pytest.approx(100_000 * (1 + COMMISSION), abs=0.01)
    held_value = 1000 * 100 / 90 * 95
    assert trade.exit_value == pytest.approx(held_value - 95_000 * (COMMISSION + STOCK_TAX), abs=0.01)
    assert trade.pnl_amount > 0
    curve = {row["date"]: row for row in result.equity_curve}
    assert curve["2025-07-01"]["value"] == pytest.approx(200_000 - 100_000 * COMMISSION, abs=0.01)
    assert curve["2025-07-02"]["value"] == curve["2025-07-01"]["value"]


@pytest.mark.parametrize(("minimum_commission", "buy_commission"), [(0, 8_900 * COMMISSION), (20, 20.0)])
def test_minimum_commission_applies_to_the_raw_trade_value(
    tmp_path,
    minimum_commission: float,
    buy_commission: float,
) -> None:
    config = _config(
        tmp_path,
        DIVIDEND_SYMBOL,
        DIVIDEND_EVENTS,
        capital=9_000,
        lot_mode="odd_lot",
        minimum_commission=minimum_commission,
    )

    result = _run(config, _panel(DIVIDEND_SYMBOL, DIVIDEND_BARS), [True, False, False], [False, False, True])

    trade = result.trades[0]
    assert trade.shares == 89
    assert trade.entry_value == pytest.approx(8_900 + buy_commission, abs=0.01)
    sell_gross = 89 * 95
    sell_commission = max(sell_gross * COMMISSION, minimum_commission)
    expected_exit = 89 * 100 / 90 * 95 - sell_commission - sell_gross * STOCK_TAX
    assert trade.exit_value == pytest.approx(expected_exit, abs=0.01)


SPLIT_SYMBOL = "0050.TWSE"
SPLIT_EVENTS = [(SPLIT_SYMBOL, date(2025, 6, 18), "etf_split", 188.65, 47.16)]
SPLIT_BARS = [
    (date(2025, 6, 10), 188.65 / 4, 188.65 / 4, 188.65),
    (date(2025, 6, 18), 47.57, 47.57, 47.57),
    (date(2025, 6, 19), 47.10, 47.10, 47.10),
]


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_split_multiplies_held_shares_and_keeps_market_value_continuous(tmp_path, mode: str) -> None:
    config = _config(
        tmp_path,
        SPLIT_SYMBOL,
        SPLIT_EVENTS,
        instrument_type="etf",
        capital=1_000_000,
    )

    result = _run(
        config,
        _panel(SPLIT_SYMBOL, SPLIT_BARS),
        [True, False, False],
        [False, False, True],
        mode=mode,
    )

    trade = result.trades[0]
    shares = 5000 if mode == "portfolio" else 1000
    assert trade.shares == shares
    assert trade.exit_shares == shares * 4
    assert trade.entry_price == pytest.approx(188.65)
    assert trade.entry_price_adj == pytest.approx(188.65 / 4, rel=1e-6)
    assert trade.exit_price == pytest.approx(47.10)
    assert trade.entry_value == pytest.approx(shares * 188.65 * (1 + COMMISSION), abs=0.01)
    exit_gross = shares * 4 * 47.10
    assert trade.exit_value == pytest.approx(exit_gross * (1 - COMMISSION - ETF_TAX), abs=0.05)
    if mode == "portfolio":
        marks = {row["date"]: row["value"] - row["cash"] for row in result.equity_curve}
        assert marks["2025-06-10"] == pytest.approx(5000 * 188.65, abs=0.05)
        assert marks["2025-06-18"] == pytest.approx(20_000 * 47.57, abs=0.05)


# Next-open fill on a bar before a 10.2 dividend: raw open = 90 * 102 / 91.8 = 100.
NEXT_OPEN_EVENTS = [(DIVIDEND_SYMBOL, date(2025, 7, 3), "exdiv", 102.0, 91.8)]
NEXT_OPEN_BARS = [
    (date(2025, 7, 1), 91.8, 91.8, 102.0),
    (date(2025, 7, 2), 90.0, 91.8, 102.0),
    (date(2025, 7, 3), 92.0, 92.0, 92.0),
    (date(2025, 7, 4), 93.0, 93.0, 93.0),
]


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_next_open_fill_takes_the_raw_open_from_the_bar_scale(tmp_path, mode: str) -> None:
    config = _config(tmp_path, DIVIDEND_SYMBOL, NEXT_OPEN_EVENTS, capital=200_000, fill="open_t+1")

    result = _run(
        config,
        _panel(DIVIDEND_SYMBOL, NEXT_OPEN_BARS),
        [True, False, False, False],
        [False, False, True, False],
        mode=mode,
    )

    trade = result.trades[0]
    assert trade.entry_date == "2025-07-02"
    assert trade.exit_date == "2025-07-04"
    assert trade.shares == 1000
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.entry_price_adj == pytest.approx(90.0)
    assert trade.exit_price == pytest.approx(93.0)
    assert trade.entry_value == pytest.approx(100_000 * (1 + COMMISSION), abs=0.01)
    held_value = 1000 * 100 / 90 * 93
    assert trade.exit_value == pytest.approx(held_value - 93_000 * (COMMISSION + STOCK_TAX), abs=0.01)


class _MinuteRepo:
    def __init__(self, rows: pl.DataFrame) -> None:
        self.rows = rows

    def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
        return self.rows.filter(pl.col("symbol").is_in(symbols))


def test_minute_fill_compares_raw_minutes_with_the_reference_on_the_same_scale(tmp_path) -> None:
    # 2025-07-02 precedes a dividend of 20.5, so its adjusted bar is raw / 1.25.
    events = [(DIVIDEND_SYMBOL, date(2025, 7, 3), "exdiv", 102.5, 82.0)]
    panel = _panel(DIVIDEND_SYMBOL, [
        (date(2025, 7, 1), 82.0, 82.0, 102.5, 82.0),
        (date(2025, 7, 2), 80.0, 82.0, 102.5, 82.0),
        (date(2025, 7, 3), 83.0, 83.0, 83.0, 82.0),
        (date(2025, 7, 4), 84.0, 84.0, 84.0, 82.0),
    ])
    minutes = pl.DataFrame({
        "symbol": [DIVIDEND_SYMBOL] * 2,
        "datetime": [datetime(2025, 7, 2, 9, 1), datetime(2025, 7, 2, 9, 2)],
        "open": [100.0, 100.5],
        "high": [101.0, 103.0],
        "low": [99.5, 100.4],
        "close": [100.5, 102.8],
        "volume": [10_000.0, 10_000.0],
        "amount": [1_005_000.0, 1_028_000.0],
    })
    # Flat bars through the 13:30 close, so the minute bars cover the whole session.
    rest = [datetime(2025, 7, 2, 9, 3) + timedelta(minutes=step) for step in range(262)]
    minutes = pl.concat([minutes, pl.DataFrame({
        "symbol": [DIVIDEND_SYMBOL] * (len(rest) + 1),
        "datetime": [*rest, datetime(2025, 7, 2, 13, 30)],
        **{column: [102.8] * (len(rest) + 1) for column in ("open", "high", "low", "close")},
        "volume": [1_000.0] * (len(rest) + 1),
        "amount": [102_800.0] * (len(rest) + 1),
    })])
    config = _config(
        tmp_path,
        DIVIDEND_SYMBOL,
        events,
        capital=200_000,
        fill="open_t+1",
        exit_fill="close_t",
        minute_fill=True,
    )

    result = _run(
        config,
        panel,
        [True, False, False, False],
        [False, False, False, True],
        repo=_MinuteRepo(minutes),
    )

    trade = result.trades[0]
    assert trade.entry_date == "2025-07-02"
    assert trade.entry_price == pytest.approx(102.5)
    assert trade.entry_price_adj == pytest.approx(82.0)
    assert trade.shares == 1000


def test_taiwan_market_matrix_requires_raw_close() -> None:
    panel = _panel(DIVIDEND_SYMBOL, DIVIDEND_BARS).drop("raw_close")

    with pytest.raises(ValueError, match="raw_close"):
        build_market_matrix(panel, pl.Series([True, False, False]), pl.Series([False] * 3))


def test_market_matrix_raw_scale_is_one_outside_taiwan() -> None:
    panel = _panel(DIVIDEND_SYMBOL, DIVIDEND_BARS).with_columns(pl.lit("600519.SH").alias("symbol"))

    matrix = build_market_matrix(panel, pl.Series([True, False, False]), pl.Series([False] * 3))

    assert np.all(matrix.raw_scale == 1.0)


def test_trade_dict_keeps_old_fields_and_adds_adjusted_prices() -> None:
    legacy = TradeRecord(
        symbol="2330.TWSE",
        entry_date=date(2025, 7, 1),
        exit_date=date(2025, 7, 3),
        entry_price=100.0,
        exit_price=95.0,
        pnl_pct=0.05,
        duration=2,
        exit_reason="signal",
    )

    payload = StrategyBacktestService._trade_to_dict(legacy)

    assert payload["entry_price"] == 100.0
    assert payload["entry_price_adj"] is None
    assert payload["exit_price_adj"] is None
    assert payload["exit_shares"] is None
