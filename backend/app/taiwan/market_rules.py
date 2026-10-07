"""Taiwan market trading rules, models, and backtest market profiles.

Responsibilities:
  - Brokerage commission model (configurable rate, discount, and min fee)
  - Securities transaction tax model (stock, day-trade, ETF, regulatory expiry)
  - Trading unit lot model (board lot 1000 shares vs odd lot 1 share)
  - Price limit model (ordinary 10% vs no-limit ETF/IPO)
  - Tick size model (ordinary stock 6 tiers vs ETF 2 tiers)
  - Settlement model (T+2) decoupled from same-day trading eligibility
  - TaiwanMarketProfile uniting all models for backtest engine integration
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Literal

import numpy as np
import polars as pl


# ── Enums ──────────────────────────────────────────────────────


class TaxClass(str, Enum):
    """Securities transaction tax classification."""
    ORDINARY_STOCK = "ordinary_stock"  # 0.3%
    DOMESTIC_ETF = "domestic_etf"      # 0.1%
    BOND_ETF = "bond_etf"              # 0.0% (effective until 2026-12-31)
    FOREIGN_ETF = "foreign_etf"        # 0.1%
    TDR = "tdr"                        # 0.1% (Taiwan depositary receipt)


class PriceLimitClass(str, Enum):
    """Price limit rule classification."""
    ORDINARY_TEN_PERCENT = "ordinary_10pct"
    NO_LIMIT = "no_limit"              # Foreign-component ETF (incl. bond ETF), IPO 5 days
    LEVERAGED_DOMESTIC = "leveraged_domestic"  # Domestic leveraged/inverse ETF (10% * abs(multiplier))



class TickSizeClass(str, Enum):
    """Price tick size classification."""
    ORDINARY_STOCK = "ordinary_stock"  # 6 tiers
    ETF = "etf"                        # 2 tiers (<50: 0.01, >=50: 0.05)


class BacktestMode(str, Enum):
    """Backtest simulation modes."""
    STANDARD_CASH = "standard_cash"    # Conservative cash mode: same-day exit prohibited
    DAY_TRADING = "day_trading"        # Day-trading enabled: same-day exit allowed with 0.15% tax
    ODD_LOT = "odd_lot"                # Odd-lot mode: 1-share sizing with odd-lot min commission


# ── Exceptions ──────────────────────────────────────────────────


class RegulatoryRuleUnavailableError(ValueError):
    """Raised when a regulatory rule is unverified or expired beyond enacted statutory windows."""


# ── 1. Trading Cost (Commission) Model ─────────────────────────

TAIWAN_SIMULATION_COMMISSION_RATE: float = 0.001425
EXAMPLE_BROKER_MIN_COMMISSION: float = 20.0  # Common broker floor for reference only


@dataclass(frozen=True)
class TradingCostModel:
    """Brokerage commission model.

    The legacy backtest default is 0.1425% (0.001425); it is not a statutory rate.
    Current TWSE guidance requires brokers to set their own commission rate.
    Minimum commission is strictly broker-specific. The statutory default is 0.0 (no exchange floor).
    Specific broker settings (e.g. 20 TWD standard, 1 TWD odd-lot) must be explicitly configured.
    """
    commission_rate: float = TAIWAN_SIMULATION_COMMISSION_RATE
    discount: float = 1.0              # 1.0 = full price; 0.6 = 60%; 0.28 = 28%
    minimum_commission: float = 0.0    # Statutory default 0.0 (broker-specific when > 0)

    def calc_commission(self, trade_value: float) -> float:
        """Calculate commission in TWD for a given trade value."""
        if trade_value <= 0:
            return 0.0
        fee = trade_value * self.commission_rate * self.discount
        if self.minimum_commission > 0:
            return max(self.minimum_commission, fee)
        return fee


# ── 2. Securities Transaction Tax Model ────────────────────────


@dataclass(frozen=True)
class SecuritiesTaxModel:
    """Securities transaction tax model.

    Taxes are levied EXCLUSIVELY on the SELL side.
    Day-trade and bond ETF exemptions have statutory effective windows.
    After each window ends the regular rate applies again under current law: the ETF rate after the
    bond ETF suspension (Securities Transaction Tax Act art. 2-1), the class rate after the day-trade
    reduction (art. 2-2).
    """
    ordinary_stock_rate: float = 0.003
    day_trade_stock_rate: float = 0.0015
    domestic_etf_rate: float = 0.001
    foreign_etf_rate: float = 0.001
    bond_etf_rate: float = 0.0
    tdr_rate: float = 0.001

    # Statutory expiry dates from enacted legislation
    day_trade_expiry: date = date(2027, 12, 31)
    bond_etf_start: date = date(2017, 1, 1)
    bond_etf_expiry: date = date(2026, 12, 31)

    def get_tax_rate(
        self,
        tax_class: TaxClass = TaxClass.ORDINARY_STOCK,
        is_day_trade: bool = False,
        trade_date: date | None = None,
    ) -> float:
        """Get applicable tax rate based on asset class, day-trade status, and date."""
        # TDR transfers are taxed at 0.1% (MOF ruling 811672688); the stock day-trade rate does not apply.
        if tax_class == TaxClass.TDR:
            return self.tdr_rate
        if (
            is_day_trade
            and tax_class == TaxClass.ORDINARY_STOCK
            and (trade_date is None or trade_date <= self.day_trade_expiry)
        ):
            return self.day_trade_stock_rate

        if tax_class == TaxClass.BOND_ETF:
            if trade_date is not None and trade_date < self.bond_etf_start:
                return self.domestic_etf_rate
            if trade_date is not None and trade_date > self.bond_etf_expiry:
                return self.domestic_etf_rate
            return self.bond_etf_rate

        if tax_class in (TaxClass.DOMESTIC_ETF, TaxClass.FOREIGN_ETF):
            return self.domestic_etf_rate

        return self.ordinary_stock_rate


    def calc_tax(
        self,
        trade_value: float,
        tax_class: TaxClass = TaxClass.ORDINARY_STOCK,
        is_day_trade: bool = False,
        trade_date: date | None = None,
    ) -> float:
        """Calculate transaction tax in TWD (sell-side only)."""
        if trade_value <= 0:
            return 0.0
        rate = self.get_tax_rate(tax_class=tax_class, is_day_trade=is_day_trade, trade_date=trade_date)
        return trade_value * rate


# ── 3. Trading Unit (Lot) Model ────────────────────────────────


@dataclass(frozen=True)
class LotModel:
    """Trading unit model.

    Taiwan standard board lot (張) is 1,000 shares.
    Odd-lot trading allows 1 to 999 shares.
    """
    mode: Literal["board_lot", "odd_lot"] = "board_lot"
    board_lot_size: int = 1000
    odd_lot_size: int = 1

    @property
    def lot_size(self) -> int:
        return self.board_lot_size if self.mode == "board_lot" else self.odd_lot_size

    def align_shares(self, allocation: float, price: float, buy_cost_pct: float = 0.0) -> int:
        """Calculate max allowable integer shares within allocation adhering to lot size."""
        if price <= 0 or allocation <= 0:
            return 0
        raw_shares = allocation / (price * (1.0 + buy_cost_pct))
        unit = self.lot_size
        return int(math.floor(raw_shares / unit) * unit)


# ── 4. Price Tick Size Model ───────────────────────────────────

# (exclusive upper price bound, tick); the last bound is infinite.
TickLadder = tuple[tuple[float, float], ...]

_STOCK_TICK_LADDER: TickLadder = (
    (10.0, 0.01), (50.0, 0.05), (100.0, 0.10), (500.0, 0.50), (1000.0, 1.00), (math.inf, 5.00),
)
_ETF_TICK_LADDER: TickLadder = ((50.0, 0.01), (math.inf, 0.05))

# (effective date, stock ladder, ETF ladder), ascending; trade_date=None resolves to the newest row.
# Add a row only for a rule change that has been officially promulgated.
_TICK_LADDERS: tuple[tuple[date, TickLadder, TickLadder], ...] = (
    (date.min, _STOCK_TICK_LADDER, _ETF_TICK_LADDER),
)

_LIMIT_ROUNDING_TOLERANCE = 1e-9
_MIN_LIMIT_DOWN_PRICE = 0.01


def _tick_ladders(trade_date: date | datetime | None) -> tuple[TickLadder, TickLadder]:
    if isinstance(trade_date, datetime):
        trade_date = trade_date.date()
    elif trade_date is not None and not isinstance(trade_date, date):
        raise TypeError(f"trade_date must be a date, datetime or None, not {type(trade_date).__name__}")
    for effective, stock, etf in reversed(_TICK_LADDERS):
        if trade_date is None or effective <= trade_date:
            return stock, etf
    raise RegulatoryRuleUnavailableError(f"No tick ladder is effective on {trade_date}.")


def _polars_ladder(price: pl.Expr, ladder: TickLadder) -> pl.Expr:
    (first_bound, first_tick), *middle, (_, last_tick) = ladder
    expr = pl.when(price < first_bound).then(first_tick)
    for bound, tick in middle:
        expr = expr.when(price < bound).then(tick)
    return expr.otherwise(last_tick)


def _numpy_ladder(price: np.ndarray, ladder: TickLadder) -> np.ndarray:
    *bounded, (_, last_tick) = ladder
    return np.select([price < bound for bound, _ in bounded], [tick for _, tick in bounded], default=last_tick)


class TickSizeModel:
    """Price tick size model adhering to TWSE Rule 62 & ETF regulations."""

    @staticmethod
    def get_tick_size(
        price: float,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        trade_date: date | None = None,
    ) -> float:
        """Get minimum price movement for a given price and asset category."""
        stock, etf = _tick_ladders(trade_date)
        if price <= 0:
            return 0.01
        ladder = etf if tick_class == TickSizeClass.ETF else stock
        for bound, tick in ladder:
            if price < bound:
                return tick
        return ladder[-1][1]

    @staticmethod
    def polars_tick_size(price: pl.Expr, is_etf: pl.Expr, trade_date: date | None = None) -> pl.Expr:
        """Vectorised get_tick_size over a price expression and a boolean ETF-ladder flag."""
        stock, etf = _tick_ladders(trade_date)
        return pl.when(is_etf).then(_polars_ladder(price, etf)).otherwise(_polars_ladder(price, stock))

    @staticmethod
    def numpy_tick_size(price: np.ndarray, is_etf: np.ndarray | bool, trade_date: date | None = None) -> np.ndarray:
        """Vectorised get_tick_size over a price array and a boolean ETF-ladder flag."""
        stock, etf = _tick_ladders(trade_date)
        price = np.asarray(price, dtype=np.float64)
        return np.where(np.asarray(is_etf, dtype=bool), _numpy_ladder(price, etf), _numpy_ladder(price, stock))

    @classmethod
    def round_price_limit_boundary(
        cls,
        raw_price: float,
        direction: Literal["up", "down"],
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        trade_date: date | None = None,
    ) -> float:
        """Round statutory price limit boundaries strictly without violating exchange bands.

        direction='up': floor to tick so limit-up never exceeds statutory ceiling (+10%).
        direction='down': ceil to tick so limit-down never breaches statutory floor (-10%).
        """
        if raw_price <= 0:
            return 0.0
        tick = cls.get_tick_size(raw_price, tick_class=tick_class, trade_date=trade_date)
        if direction == "up":
            steps = math.floor(raw_price / tick + _LIMIT_ROUNDING_TOLERANCE)
        else:
            steps = math.ceil(raw_price / tick - _LIMIT_ROUNDING_TOLERANCE)
        return round(steps * tick, 4)

    @classmethod
    def round_order_price(
        cls,
        price: float,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        mode: Literal["nearest", "floor", "ceil"] = "nearest",
        trade_date: date | None = None,
    ) -> float:
        """Round order or execution fill price to valid tick without skewing strategy intent.

        mode='nearest': standard closest tick (default for orders/fills).
        mode='floor': round downward to tick.
        mode='ceil': round upward to tick.
        """
        if price <= 0:
            return 0.0
        tick = cls.get_tick_size(price, tick_class=tick_class, trade_date=trade_date)
        if mode == "floor":
            steps = math.floor(price / tick + 1e-9)
        elif mode == "ceil":
            steps = math.ceil(price / tick - 1e-9)
        else:
            steps = round(price / tick)
        return round(steps * tick, 4)

    @classmethod
    def round_to_valid_tick(
        cls,
        price: float,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        side: Literal["buy", "sell", "round"] = "round",
        trade_date: date | None = None,
    ) -> float:
        """General rounding entry point.

        side='round': standard nearest tick (recommended for general orders/fills).
        side='buy': floor to tick (conservative boundary limit).
        side='sell': ceil to tick (conservative boundary limit).
        """
        if side == "buy":
            return cls.round_price_limit_boundary(price, direction="up", tick_class=tick_class, trade_date=trade_date)
        if side == "sell":
            return cls.round_price_limit_boundary(price, direction="down", tick_class=tick_class, trade_date=trade_date)
        return cls.round_order_price(price, tick_class=tick_class, mode="nearest", trade_date=trade_date)


# ── 5. Price Limit Model ───────────────────────────────────────

IPO_NO_LIMIT_SESSIONS = 5


@dataclass(frozen=True)
class ListingSessions:
    """Trading sessions, ascending, and each symbol's first session, both from the stored daily bars."""
    trading_dates: tuple[date, ...]
    first_sessions: Mapping[str, date] = field(default_factory=dict)


def ipo_no_limit_until(
    listing_date: date,
    trading_dates: Sequence[date],
    sessions: int = IPO_NO_LIMIT_SESSIONS,
) -> date | None:
    """Last session of the no-limit window an initial listing opens; the listing session is the first.

    None when the calendar is empty or starts after listing_date, so the sessions cannot be counted;
    date.max while fewer than *sessions* sessions from listing_date on are recorded.
    """
    if not trading_dates or listing_date < trading_dates[0]:
        return None
    last = bisect_left(trading_dates, listing_date) + sessions - 1
    return trading_dates[last] if last < len(trading_dates) else date.max


class PriceLimitModel:
    """Daily price limit model (+-10% standard, multiplier-adjusted, or no-limit). Single source of truth."""

    @staticmethod
    def get_limit_pct(
        limit_class: PriceLimitClass = PriceLimitClass.ORDINARY_TEN_PERCENT,
        multiplier: float = 1.0,
    ) -> float | None:
        """Authoritative single source of truth for Taiwan price limit percentages.

        Returns:
            0.10 for ordinary stocks and domestic equity ETFs.
            0.10 * abs(multiplier) for domestic leveraged/inverse ETFs (e.g. 0.20 for 2X, 0.10 for -1X).
            None for NO_LIMIT class (foreign-component ETFs incl. bond ETFs, 5-day IPOs).
        """
        if limit_class == PriceLimitClass.NO_LIMIT:
            return None
        if limit_class == PriceLimitClass.LEVERAGED_DOMESTIC:
            return round(0.10 * abs(multiplier), 4)
        return 0.10

    @classmethod
    def calc_limits_for_pct(
        cls,
        ref_price: float,
        limit_pct: float | None,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        trade_date: date | None = None,
    ) -> tuple[float | None, float | None]:
        """Calculate (limit_up, limit_down) for an explicit percentage, aligned to valid ticks."""
        if ref_price <= 0 or limit_pct is None:
            return None, None
        ref = round(float(ref_price), 4)
        up_raw = ref * (1.0 + limit_pct)
        down_raw = max(_MIN_LIMIT_DOWN_PRICE, ref * (1.0 - limit_pct))
        limit_up = TickSizeModel.round_price_limit_boundary(
            up_raw, direction="up", tick_class=tick_class, trade_date=trade_date
        )
        limit_down = TickSizeModel.round_price_limit_boundary(
            down_raw, direction="down", tick_class=tick_class, trade_date=trade_date
        )
        return limit_up, limit_down

    @staticmethod
    def polars_limit_prices(
        ref_price: pl.Expr,
        limit_pct: pl.Expr,
        is_etf: pl.Expr,
        trade_date: date | None = None,
    ) -> tuple[pl.Expr, pl.Expr]:
        """Vectorised calc_limits_for_pct; a null or NaN reference or percentage yields null or NaN limits.

        Unlike the scalar form, a non-positive reference is not masked to null.
        """
        ref = ref_price.cast(pl.Float64).round(4)
        up_raw = ref * (1.0 + limit_pct)
        down_floor = ref * (1.0 - limit_pct)
        down_raw = pl.when(down_floor < _MIN_LIMIT_DOWN_PRICE).then(_MIN_LIMIT_DOWN_PRICE).otherwise(down_floor)
        up_tick = TickSizeModel.polars_tick_size(up_raw, is_etf, trade_date)
        down_tick = TickSizeModel.polars_tick_size(down_raw, is_etf, trade_date)
        limit_up = ((up_raw / up_tick + _LIMIT_ROUNDING_TOLERANCE).floor() * up_tick).round(4)
        limit_down = ((down_raw / down_tick - _LIMIT_ROUNDING_TOLERANCE).ceil() * down_tick).round(4)
        return limit_up, limit_down

    @staticmethod
    def polars_session_limit_pct(
        limit_pct: pl.Expr,
        trade_date: pl.Expr,
        ipo_start: pl.Expr,
        ipo_end: pl.Expr,
    ) -> pl.Expr:
        """*limit_pct*, or null on a session inside the [ipo_start, ipo_end] no-limit window of an initial listing."""
        in_window = PriceLimitModel.polars_in_ipo_window(trade_date, ipo_start, ipo_end)
        return pl.when(in_window).then(pl.lit(None, dtype=pl.Float64)).otherwise(limit_pct)

    @staticmethod
    def polars_in_ipo_window(trade_date: pl.Expr, ipo_start: pl.Expr, ipo_end: pl.Expr) -> pl.Expr:
        """Whether *trade_date* falls inside the [ipo_start, ipo_end] window; false without a window."""
        return trade_date.cast(pl.Date).is_between(ipo_start, ipo_end).fill_null(False)

    @staticmethod
    def numpy_limit_prices(
        ref_price: np.ndarray,
        limit_pct: np.ndarray | float,
        is_etf: np.ndarray | bool,
        trade_date: date | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """NumPy counterpart of polars_limit_prices; a NaN percentage yields NaN limits."""
        ref = np.round(np.asarray(ref_price, dtype=np.float64), 4)
        pct = np.asarray(limit_pct, dtype=np.float64)
        up_raw = ref * (1.0 + pct)
        down_floor = ref * (1.0 - pct)
        down_raw = np.where(down_floor < _MIN_LIMIT_DOWN_PRICE, _MIN_LIMIT_DOWN_PRICE, down_floor)
        up_tick = TickSizeModel.numpy_tick_size(up_raw, is_etf, trade_date)
        down_tick = TickSizeModel.numpy_tick_size(down_raw, is_etf, trade_date)
        limit_up = np.round(np.floor(up_raw / up_tick + _LIMIT_ROUNDING_TOLERANCE) * up_tick, 4)
        limit_down = np.round(np.ceil(down_raw / down_tick - _LIMIT_ROUNDING_TOLERANCE) * down_tick, 4)
        return limit_up, limit_down

    @classmethod
    def calc_limits(
        cls,
        ref_price: float,
        limit_class: PriceLimitClass = PriceLimitClass.ORDINARY_TEN_PERCENT,
        tick_class: TickSizeClass = TickSizeClass.ORDINARY_STOCK,
        multiplier: float = 1.0,
    ) -> tuple[float | None, float | None]:
        """Calculate (limit_up, limit_down) rounded to valid ticks."""
        pct = cls.get_limit_pct(limit_class=limit_class, multiplier=multiplier)
        return cls.calc_limits_for_pct(ref_price=ref_price, limit_pct=pct, tick_class=tick_class)




# ── 6. Settlement Model ────────────────────────────────────────


@dataclass(frozen=True)
class SettlementModel:
    """Settlement cycle model (T+2 cash/stock delivery).

    Settlement cycle is purely for delivery timing and cash accounting.
    It does NOT govern position same-day exit restrictions.
    """
    settlement_days: int = 2


# ── 7. Unified Taiwan Market Profile ───────────────────────────


@dataclass
class TaiwanMarketProfile:
    """Comprehensive Taiwan market profile for backtest execution."""
    mode: BacktestMode = BacktestMode.STANDARD_CASH
    cost: TradingCostModel = field(default_factory=TradingCostModel)
    tax: SecuritiesTaxModel = field(default_factory=SecuritiesTaxModel)
    lot: LotModel = field(default_factory=LotModel)
    settlement: SettlementModel = field(default_factory=SettlementModel)
    tick_size_model: TickSizeModel = field(default_factory=TickSizeModel)
    price_limit_model: PriceLimitModel = field(default_factory=PriceLimitModel)

    def can_same_day_exit(
        self,
        is_day_trade_eligible: bool = True,
    ) -> bool:
        """Determine if a position entered today may be exited today.

        STANDARD_CASH mode: Prohibited (must hold overnight to T+1).
        DAY_TRADING mode: Allowed if instrument is eligible.
        """
        if self.mode == BacktestMode.STANDARD_CASH:
            return False
        if self.mode == BacktestMode.DAY_TRADING:
            return bool(is_day_trade_eligible)
        if self.mode == BacktestMode.ODD_LOT:
            return False
        return False

    def buy_transaction_cost(self, trade_value: float) -> float:
        """Buy cost: commission only."""
        return self.cost.calc_commission(trade_value)

    def sell_transaction_cost(
        self,
        trade_value: float,
        tax_class: TaxClass = TaxClass.ORDINARY_STOCK,
        is_day_trade: bool = False,
        trade_date: date | None = None,
    ) -> float:
        """Sell cost: commission + securities transaction tax."""
        comm = self.cost.calc_commission(trade_value)
        tax = self.tax.calc_tax(
            trade_value,
            tax_class=tax_class,
            is_day_trade=is_day_trade,
            trade_date=trade_date,
        )
        return comm + tax
