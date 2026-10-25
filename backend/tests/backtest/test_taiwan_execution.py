from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.api.backtest import StrategyBacktestRequest
from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.strategy import (
    StrategyBacktestConfig,
    _configure_taiwan_matcher,
    _minute_fill_reference,
)
from app.backtest.worker import _decode_backtest_config, encode_backtest_config
from app.taiwan.market_rules import (
    BacktestMode,
    LotModel,
    TaiwanMarketProfile,
    TradingCostModel,
)


class _SecurityMaster:
    def __init__(self, instruments: dict[str, SimpleNamespace]) -> None:
        self.instruments = instruments

    def get_instrument(self, symbol: str) -> SimpleNamespace | None:
        return self.instruments.get(symbol)


def _instrument(
    symbol: str,
    instrument_type: str,
    *,
    lot_size: int,
    etf_category: str | None = None,
    leverage_multiplier: float = 1.0,
    etf_management_style: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        instrument_type=instrument_type,
        lot_size=lot_size,
        etf_category=etf_category,
        leverage_multiplier=leverage_multiplier,
        etf_management_style=etf_management_style,
        classification_source="official_metadata",
        underlying_scope="domestic",
    )


def test_taiwan_profiles_apply_symbol_tax_and_trading_units() -> None:
    instruments = {
        "2330.TWSE": _instrument("2330.TWSE", "stock", lot_size=1000),
        "0050.TWSE": _instrument(
            "0050.TWSE",
            "etf",
            lot_size=1000,
            etf_category="domestic_equity",
        ),
        "00720B.TWSE": _instrument(
            "00720B.TWSE",
            "etf",
            lot_size=1000,
            etf_category="bond",
            etf_management_style="passive",
        ),
    }
    config = MatcherConfig(fees_pct=0.0002, slippage_bps=0)

    _configure_taiwan_matcher(
        config,
        tuple(instruments),
        security_master=_SecurityMaster(instruments),
    )

    assert config.lot_size("2330.TWSE") == 1000
    assert config.lot_size("0050.TWSE") == 1000
    assert config.buy_transaction_cost(100_000) == pytest.approx(142.5)
    assert config.sell_transaction_cost(100_000, "2330.TWSE", date(2026, 10, 3)) == pytest.approx(442.5)
    assert config.sell_transaction_cost(100_000, "0050.TWSE", date(2026, 10, 3)) == pytest.approx(242.5)
    assert config.sell_transaction_cost(100_000, "00720B.TWSE", date(2026, 10, 3)) == pytest.approx(142.5)
    assert config.sell_transaction_cost(100_000, "00720B.TWSE", date(2016, 12, 31)) == pytest.approx(242.5)
    assert config.sell_transaction_cost(100_000, "00720B.TWSE", date(2017, 1, 1)) == pytest.approx(142.5)
    assert config.sell_transaction_cost(100_000, "00720B.TWSE", date(2026, 12, 31)) == pytest.approx(142.5)
    assert config.sell_transaction_cost(100_000, "00720B.TWSE", date(2027, 1, 4)) == pytest.approx(242.5)
    config.day_trade_eligible_by_symbol = {"2330.TWSE": True}
    assert config.sell_transaction_cost(
        100_000,
        "2330.TWSE",
        date(2027, 12, 31),
        is_day_trade=True,
    ) == pytest.approx(292.5)
    assert config.sell_transaction_cost(
        100_000,
        "2330.TWSE",
        date(2028, 1, 3),
        is_day_trade=True,
    ) == pytest.approx(442.5)


def test_taiwan_profile_isolated_from_non_taiwan_universes() -> None:
    instrument = _instrument("2330.TWSE", "stock", lot_size=1000)
    non_taiwan = MatcherConfig(fees_pct=0.0002, slippage_bps=0)

    _configure_taiwan_matcher(non_taiwan, ["600519.SH"])

    assert non_taiwan.market_profile is None
    assert non_taiwan.buy_transaction_cost(100_000) == pytest.approx(20.0)

    mixed = MatcherConfig(fees_pct=0.0002, slippage_bps=0)
    with pytest.raises(ValueError, match="mixed Taiwan and non-Taiwan"):
        _configure_taiwan_matcher(
            mixed,
            ["2330.TWSE", "600519.SH"],
            security_master=_SecurityMaster({"2330.TWSE": instrument}),
        )
    assert mixed.market_profile is None


@pytest.mark.parametrize("stamp_tax_pct", [0.0, 0.003])
def test_taiwan_matcher_rejects_single_stamp_tax_override(stamp_tax_pct: float) -> None:
    instrument = _instrument("2330.TWSE", "stock", lot_size=1000)
    config = MatcherConfig(stamp_tax_pct=stamp_tax_pct, slippage_bps=0)

    with pytest.raises(ValueError, match="stamp_tax_pct cannot override"):
        _configure_taiwan_matcher(
            config,
            ["2330.TWSE"],
            security_master=_SecurityMaster({"2330.TWSE": instrument}),
        )
    assert config.market_profile is None
    assert config.tax_class_by_symbol is None

    non_taiwan = MatcherConfig(stamp_tax_pct=stamp_tax_pct, fees_pct=0, slippage_bps=0)
    _configure_taiwan_matcher(non_taiwan, ["600519.SH"])
    assert non_taiwan.sell_transaction_cost(100_000, "600519.SH", date(2026, 10, 3)) == pytest.approx(
        100_000 * stamp_tax_pct
    )


@pytest.mark.parametrize("management_style", ["active", None])
def test_bond_etf_requires_explicit_passive_management_style(
    management_style: str | None,
) -> None:
    symbol = "00999B.TWSE"
    instrument = _instrument(
        symbol,
        "etf",
        lot_size=1000,
        etf_category="bond",
        etf_management_style=management_style,
    )
    config = MatcherConfig(commission_pct=0, slippage_bps=0)

    _configure_taiwan_matcher(
        config,
        [symbol],
        security_master=_SecurityMaster({symbol: instrument}),
    )

    assert config.sell_transaction_cost(100_000, symbol, date(2026, 10, 3)) == 100


def test_minimum_commission_and_odd_lot_sizing_use_profile_contract() -> None:
    profile = TaiwanMarketProfile(
        mode=BacktestMode.ODD_LOT,
        cost=TradingCostModel(
            commission_rate=0.001425,
            minimum_commission=20,
        ),
        lot=LotModel(mode="odd_lot"),
    )
    config = MatcherConfig(
        market_profile=profile,
        lot_size_by_symbol={"2330.TWSE": 1000},
        slippage_bps=0,
    )

    assert config.lot_size("2330.TWSE") == 1
    assert config.buy_transaction_cost(1_000) == 20
    assert config.align_shares(1_000, 10, "2330.TWSE") == 98


def test_taiwan_matcher_injects_minimum_commission_and_odd_lot_mode() -> None:
    instrument = _instrument("2330.TWSE", "stock", lot_size=1000)
    config = MatcherConfig(
        fees_pct=0.001425,
        minimum_commission=20,
        lot_mode="odd_lot",
        slippage_bps=0,
    )

    _configure_taiwan_matcher(
        config,
        ["2330.TWSE"],
        security_master=_SecurityMaster({"2330.TWSE": instrument}),
    )

    assert config.lot_size("2330.TWSE") == 1
    assert config.buy_transaction_cost(1_000) == 20


@pytest.mark.parametrize(
    ("master", "match"),
    [
        (_SecurityMaster({}), "has no execution profile"),
        (
            _SecurityMaster({"2330.TWSE": _instrument("2330.TWSE", "stock", lot_size=0)}),
            "invalid trading unit",
        ),
    ],
)
def test_taiwan_matcher_fails_closed_for_missing_or_invalid_lot(master, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _configure_taiwan_matcher(
            MatcherConfig(),
            ["2330.TWSE"],
            security_master=master,
        )


def test_taiwan_matcher_fails_closed_when_lot_lookup_raises() -> None:
    class _FailingMaster:
        def get_instrument(self, symbol: str):
            raise RuntimeError("lookup failed")

    with pytest.raises(ValueError, match="Security Master unavailable"):
        _configure_taiwan_matcher(
            MatcherConfig(),
            ["2330.TWSE"],
            security_master=_FailingMaster(),
        )


def test_taiwan_matcher_enables_only_explicit_day_trade_symbols() -> None:
    instrument = _instrument("2330.TWSE", "stock", lot_size=1000)
    config = MatcherConfig(
        minimum_holding_sessions=0,
        day_trade_eligible_symbols=("2330.TWSE",),
    )

    _configure_taiwan_matcher(
        config,
        ["2330.TWSE"],
        security_master=_SecurityMaster({"2330.TWSE": instrument}),
    )

    assert config.market_profile.mode == BacktestMode.DAY_TRADING
    assert config.can_exit_after_sessions("2330.TWSE", 0) is True
    assert config.can_exit_after_sessions("2317.TWSE", 0) is False


def test_commission_discount_applies_before_minimum_commission() -> None:
    instrument = _instrument("2330.TWSE", "stock", lot_size=1000)
    config = MatcherConfig(
        commission_pct=0.001425,
        commission_discount=0.5,
        minimum_commission=20,
        slippage_bps=0,
    )

    _configure_taiwan_matcher(
        config,
        ["2330.TWSE"],
        security_master=_SecurityMaster({"2330.TWSE": instrument}),
    )

    assert config.buy_transaction_cost(100_000) == pytest.approx(71.25)
    assert config.buy_transaction_cost(1_000) == 20


def test_same_day_exit_requires_an_eligible_day_trading_profile() -> None:
    standard = MatcherConfig(
        minimum_holding_sessions=0,
        market_profile=TaiwanMarketProfile(mode=BacktestMode.STANDARD_CASH),
        day_trade_eligible_by_symbol={"2330.TWSE": True},
    )
    ineligible = MatcherConfig(
        minimum_holding_sessions=0,
        market_profile=TaiwanMarketProfile(mode=BacktestMode.DAY_TRADING),
        day_trade_eligible_by_symbol={"2330.TWSE": False},
    )
    eligible = MatcherConfig(
        minimum_holding_sessions=0,
        market_profile=TaiwanMarketProfile(mode=BacktestMode.DAY_TRADING),
        day_trade_eligible_by_symbol={"2330.TWSE": True},
    )

    assert standard.can_exit_after_sessions("2330.TWSE", 0) is False
    assert ineligible.can_exit_after_sessions("2330.TWSE", 0) is False
    assert eligible.can_exit_after_sessions("2330.TWSE", 0) is True


def test_commission_discount_round_trips_api_and_worker() -> None:
    request = StrategyBacktestRequest(strategy_id="test", commission_discount=0.5)
    config = StrategyBacktestConfig(
        strategy_id=request.strategy_id,
        symbols=None,
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
        commission_discount=request.commission_discount,
        day_trade_eligible_symbols=("2330.TWSE",),
    )
    decoded = _decode_backtest_config(encode_backtest_config(config))

    assert decoded.commission_discount == 0.5
    assert decoded.day_trade_eligible_symbols == ("2330.TWSE",)


def test_board_lot_portfolio_holds_until_the_next_session() -> None:
    start = date(2026, 9, 1)
    panel = pl.DataFrame({
        "symbol": ["2330.TWSE"] * 4,
        "name": ["TSMC"] * 4,
        "date": [start + timedelta(days=i) for i in range(4)],
        "open": [100.0, 100.0, 101.0, 102.0],
        "high": [100.0, 100.0, 101.0, 102.0],
        "low": [100.0, 100.0, 101.0, 102.0],
        "close": [100.0, 100.0, 101.0, 102.0],
        "raw_close": [100.0, 100.0, 101.0, 102.0],
        "volume": [100_000.0] * 4,
        "score": [1.0] * 4,
        "signal_limit_up": [False] * 4,
        "signal_limit_down": [False] * 4,
    })
    entries = pl.Series([True, False, False, False])
    exits = pl.Series([True, False, False, False])
    config = MatcherConfig(
        entry_fill="open_t+1",
        exit_fill="open_t+1",
        fees_pct=0,
        slippage_bps=0,
        max_hold_days=1,
        max_positions=1,
        initial_capital=150_000,
        lot_size_by_symbol={"2330.TWSE": 1000},
    )

    result = BacktestEngine(repo=None).simulate_portfolio(panel, entries, exits, config)

    assert len(result.trades) == 1
    assert result.trades[0].shares == 1000
    assert result.trades[0].lots == 1
    assert result.trades[0].entry_date == "2026-09-02"
    assert result.trades[0].exit_date == "2026-09-03"


def test_locked_limit_up_session_cannot_fill_a_board_lot() -> None:
    panel = pl.DataFrame({
        "symbol": ["2330.TWSE"] * 3,
        "name": ["TSMC"] * 3,
        "date": [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)],
        "open": [100.0, 110.0, 109.0],
        "high": [100.0, 110.0, 109.0],
        "low": [100.0, 110.0, 109.0],
        "close": [100.0, 110.0, 109.0],
        "raw_close": [100.0, 110.0, 109.0],
        "volume": [100_000.0] * 3,
        "score": [1.0] * 3,
        "signal_limit_up": [False, True, False],
        "signal_limit_down": [False, False, False],
    })
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        pl.Series([True, False, False]),
        pl.Series([False, False, False]),
        MatcherConfig(
            entry_fill="open_t+1",
            fees_pct=0,
            slippage_bps=0,
            max_positions=1,
            initial_capital=200_000,
            lot_size_by_symbol={"2330.TWSE": 1000},
        ),
    )

    assert result.trades == []
    assert result.stats["execution"]["buy_limit_up"] == 1


@pytest.mark.parametrize(
    ("minimum_holding_sessions", "expected_exit_date"),
    [(0, "2026-09-01"), (1, "2026-09-02")],
)
def test_minimum_holding_sessions_controls_same_session_exit(
    minimum_holding_sessions: int,
    expected_exit_date: str,
) -> None:
    panel = pl.DataFrame({
        "symbol": ["A", "A"],
        "name": ["A", "A"],
        "date": [date(2026, 9, 1), date(2026, 9, 2)],
        "open": [10.0, 10.0],
        "high": [10.0, 10.0],
        "low": [10.0, 10.0],
        "close": [10.0, 10.0],
        "volume": [100.0, 100.0],
        "score": [1.0, 1.0],
        "signal_limit_up": [False, False],
        "signal_limit_down": [False, False],
    })
    result = BacktestEngine(repo=None).simulate_portfolio(
        panel,
        pl.Series([True, False]),
        pl.Series([True, False]),
        MatcherConfig(
            entry_fill="close_t",
            exit_fill="close_t",
            minimum_holding_sessions=minimum_holding_sessions,
            day_trade_eligible_symbols=("A",),
            fees_pct=0,
            slippage_bps=0,
            max_positions=1,
            initial_capital=10_000,
        ),
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_date == expected_exit_date


@pytest.mark.parametrize(
    ("minimum_holding_sessions", "expected_exit_date"),
    [(0, "2026-09-01"), (1, "2026-09-02")],
)
def test_full_mode_matcher_honors_minimum_holding_sessions(
    minimum_holding_sessions: int,
    expected_exit_date: str,
) -> None:
    panel = pl.DataFrame({
        "symbol": ["A", "A"],
        "name": ["A", "A"],
        "date": [date(2026, 9, 1), date(2026, 9, 2)],
        "open": [10.0, 11.0],
        "high": [10.0, 11.0],
        "low": [10.0, 11.0],
        "close": [10.0, 11.0],
        "volume": [100.0, 100.0],
        "score": [1.0, 1.0],
        "signal_limit_up": [False, False],
        "signal_limit_down": [False, False],
    })
    result = BacktestEngine(repo=None).simulate_independent_candidates(
        panel,
        pl.Series([True, False]),
        pl.Series([True, False]),
        MatcherConfig(
            entry_fill="close_t",
            exit_fill="close_t",
            minimum_holding_sessions=minimum_holding_sessions,
            day_trade_eligible_symbols=("A",),
            fees_pct=0,
            slippage_bps=0,
        ),
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_date == expected_exit_date


def test_full_mode_same_session_exit_still_requires_taiwan_eligibility() -> None:
    panel = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE"],
        "name": ["TSMC", "TSMC"],
        "date": [date(2026, 9, 1), date(2026, 9, 2)],
        "open": [100.0, 101.0],
        "high": [100.0, 101.0],
        "low": [100.0, 101.0],
        "close": [100.0, 101.0],
        "raw_close": [100.0, 101.0],
        "volume": [100.0, 100.0],
        "score": [1.0, 1.0],
        "signal_limit_up": [False, False],
        "signal_limit_down": [False, False],
    })
    result = BacktestEngine(repo=None).simulate_independent_candidates(
        panel,
        pl.Series([True, False]),
        pl.Series([True, False]),
        MatcherConfig(
            entry_fill="close_t",
            exit_fill="close_t",
            minimum_holding_sessions=0,
            fees_pct=0,
            stamp_tax_pct=0,
            slippage_bps=0,
            market_profile=TaiwanMarketProfile(mode=BacktestMode.DAY_TRADING),
            day_trade_eligible_by_symbol={"2330.TWSE": False},
            lot_size_by_symbol={"2330.TWSE": 1000},
        ),
    )

    assert result.trades[0].exit_date == "2026-09-02"


def test_minute_reference_excludes_the_fill_session_close() -> None:
    close = np.array([[10.0], [11.0], [12.0], [13.0], [14.0], [999.0]], dtype=np.float32)

    reference = _minute_fill_reference(close, "open_t+1")

    assert reference[5, 0] == pytest.approx(12.0)
    assert np.isnan(_minute_fill_reference(close, "close_t")).all()


class _MinuteRepo:
    def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
        return pl.DataFrame({
            "symbol": [symbols[0]],
            "datetime": [datetime(2026, 9, 1, 9, 1)],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
            "amount": [1.0],
        })


class _RowsMinuteRepo:
    def __init__(self, rows: pl.DataFrame) -> None:
        self.rows = rows

    def get_minute_by_dates(self, symbols, dates, asset_type="stock"):
        return self.rows.filter(pl.col("symbol").is_in(symbols))


@pytest.mark.parametrize("mode", ["portfolio", "full"])
def test_missing_minute_exit_reference_falls_back_to_next_open(mode: str) -> None:
    panel = pl.DataFrame({
        "symbol": ["A", "A"],
        "name": ["A", "A"],
        "date": [date(2026, 9, 1), date(2026, 9, 2)],
        "open": [10.0, 20.0],
        "high": [10.0, 21.0],
        "low": [10.0, 17.0],
        "close": [10.0, 19.0],
        "volume": [100.0, 100.0],
        "score": [1.0, 1.0],
        "signal_limit_up": [False, False],
        "signal_limit_down": [False, False],
    })
    minute = pl.DataFrame({
        "symbol": ["A", "A"],
        "datetime": [
            datetime(2026, 9, 2, 9, 1),
            datetime(2026, 9, 2, 9, 2),
        ],
        "open": [20.0, 18.0],
        "high": [21.0, 19.0],
        "low": [19.0, 17.0],
        "close": [19.0, 18.0],
        "volume": [100.0, 100.0],
        "amount": [1_900.0, 1_800.0],
    })
    engine = BacktestEngine(repo=_RowsMinuteRepo(minute))
    config = MatcherConfig(
        entry_fill="close_t",
        exit_fill="open_t+1",
        minute_fill=True,
        fees_pct=0,
        slippage_bps=0,
        max_positions=1,
        initial_capital=10_000,
    )
    run = (
        engine.simulate_portfolio
        if mode == "portfolio"
        else engine.simulate_independent_candidates
    )

    result = run(
        panel,
        pl.Series([True, False]),
        pl.Series([True, False]),
        config,
    )

    assert result.trades[0].exit_price == 20.0


def test_mixed_close_entry_and_next_open_exit_use_separate_minute_references() -> None:
    panel = pl.DataFrame({
        "symbol": ["A", "A"],
        "name": ["A", "A"],
        "date": [date(2026, 9, 1), date(2026, 9, 2)],
        "open": [10.0, 20.0],
        "high": [10.0, 20.0],
        "low": [10.0, 14.0],
        "close": [10.0, 19.0],
        "volume": [100.0, 100.0],
        "score": [1.0, 1.0],
        "ma5": [15.0, 999.0],
        "signal_limit_up": [False, False],
        "signal_limit_down": [False, False],
    })
    minute = pl.DataFrame({
        "symbol": ["A", "A"],
        "datetime": [
            datetime(2026, 9, 2, 9, 1),
            datetime(2026, 9, 2, 9, 2),
        ],
        "open": [20.0, 18.0],
        "high": [20.0, 19.0],
        "low": [16.0, 14.0],
        "close": [18.0, 19.0],
        "volume": [100.0, 100.0],
        "amount": [1_800.0, 1_900.0],
    })
    result = BacktestEngine(repo=_RowsMinuteRepo(minute)).simulate_portfolio(
        panel,
        pl.Series([True, False]),
        pl.Series([True, False]),
        MatcherConfig(
            entry_fill="close_t",
            exit_fill="open_t+1",
            minute_fill=True,
            minimum_holding_sessions=1,
            fees_pct=0,
            slippage_bps=0,
            max_positions=1,
            initial_capital=10_000,
        ),
    )

    assert result.trades[0].entry_price == 10.0
    assert result.trades[0].exit_price == 15.0


def test_close_fill_does_not_replay_minutes_before_the_daily_signal() -> None:
    panel = pl.DataFrame({
        "symbol": ["A", "A"],
        "name": ["A", "A"],
        "date": [date(2026, 9, 1), date(2026, 9, 2)],
        "open": [10.0, 11.0],
        "high": [10.0, 11.0],
        "low": [10.0, 11.0],
        "close": [10.0, 11.0],
        "volume": [100.0, 100.0],
        "score": [1.0, 1.0],
        "signal_limit_up": [False, False],
        "signal_limit_down": [False, False],
    })
    result = BacktestEngine(repo=_MinuteRepo()).simulate_portfolio(
        panel,
        pl.Series([True, False]),
        pl.Series([False, True]),
        MatcherConfig(
            entry_fill="close_t",
            exit_fill="close_t",
            minute_fill=True,
            fees_pct=0,
            slippage_bps=0,
            max_positions=1,
            initial_capital=10_000,
        ),
    )

    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 10.0
    assert result.trades[0].exit_price == 11.0


@pytest.mark.parametrize(
    "method_name",
    [
        "simulate_independent_candidates",
        "simulate_portfolio",
    ],
)
@pytest.mark.parametrize(
    "risk_config",
    [
        {"trailing_stop_pct": 0.05},
        {
            "trailing_take_profit_activate_pct": 0.10,
            "trailing_take_profit_drawdown_pct": 0.03,
        },
    ],
)
def test_close_entry_does_not_use_pre_entry_daily_high(
    method_name: str,
    risk_config: dict[str, float],
) -> None:
    panel = pl.DataFrame({
        "symbol": ["A", "A"],
        "name": ["A", "A"],
        "date": [date(2026, 9, 1), date(2026, 9, 2)],
        "open": [10.0, 10.0],
        "high": [20.0, 10.0],
        "low": [9.0, 9.6],
        "close": [10.0, 9.8],
        "volume": [100.0, 100.0],
        "score": [1.0, 1.0],
        "signal_limit_up": [False, False],
        "signal_limit_down": [False, False],
    })
    config = MatcherConfig(
        entry_fill="close_t",
        exit_fill="close_t",
        fees_pct=0,
        slippage_bps=0,
        max_positions=1,
        initial_capital=10_000,
        **risk_config,
    )

    result = getattr(BacktestEngine(repo=None), method_name)(
        panel,
        pl.Series([True, False]),
        pl.Series([False, False]),
        config,
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "end"
    assert result.trades[0].exit_price == pytest.approx(9.8)


@pytest.mark.parametrize(
    ("lot_mode", "minimum_commission", "trade_value", "expected_cost"),
    [
        ("board_lot", None, 1_000, 20.0),
        ("odd_lot", None, 100, 1.0),
        ("odd_lot", 0.0, 100, 100 * 0.001425),
        ("board_lot", 5.0, 1_000, 5.0),
    ],
)
def test_request_minimum_commission_defaults_by_lot_mode(
    lot_mode: str,
    minimum_commission: float | None,
    trade_value: float,
    expected_cost: float,
) -> None:
    fields = {} if minimum_commission is None else {"minimum_commission": minimum_commission}
    request = StrategyBacktestRequest(strategy_id="test", lot_mode=lot_mode, **fields)
    backtest = StrategyBacktestConfig(
        strategy_id=request.strategy_id,
        symbols=None,
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
        lot_mode=request.lot_mode,
        minimum_commission=request.minimum_commission,
    )
    matcher = MatcherConfig(
        commission_pct=0.001425,
        minimum_commission=backtest.minimum_commission,
        lot_mode=backtest.lot_mode,
        slippage_bps=0,
    )

    _configure_taiwan_matcher(
        matcher,
        ["2330.TWSE"],
        security_master=_SecurityMaster({"2330.TWSE": _instrument("2330.TWSE", "stock", lot_size=1000)}),
    )

    assert matcher.buy_transaction_cost(trade_value) == pytest.approx(expected_cost)
