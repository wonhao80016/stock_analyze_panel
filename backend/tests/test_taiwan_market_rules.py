"""Unit tests for Taiwan Market Rules and Models (Hardened).

Covers:
  - Commission model: statutory default 0.0 min commission, configurable discount/fee
  - Securities tax model: ordinary stock (0.3%), domestic ETF (0.1%), day-trading (0.15%), bond ETF (0%)
  - Regulatory effective dates of the statutory tax reductions
  - Lot model: board lot (1000) vs odd lot (1), share alignment
  - Tick size model: stock 6 tiers, ETF 2 tiers, price limit boundary vs order rounding
  - Price limit single source of truth: delegation in price_limits.py, NO_LIMIT handling
  - Settlement cycle vs same-day trading decoupling (STANDARD_CASH vs DAY_TRADING)
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import polars as pl
import pytest

from app.price_limits import (
    board_limit_pct,
    numpy_limit_price,
    price_limit_pct,
    taiwan_price_limit_pct,
)
from app.taiwan.market_rules import (
    EXAMPLE_BROKER_MIN_COMMISSION,
    BacktestMode,
    LotModel,
    PriceLimitClass,
    PriceLimitModel,
    SecuritiesTaxModel,
    SettlementModel,
    TaiwanMarketProfile,
    TaxClass,
    TickSizeClass,
    TickSizeModel,
    TradingCostModel,
)
from app.taiwan.universe.models import OFFICIAL_ETF_RULE_PROFILES

# ── 1. Commission Model Tests ──────────────────────────────────


class TestCommissionModel:
    """Test configurable brokerage commission model."""

    def test_statutory_default_has_zero_minimum_commission(self):
        """Statutory default has no minimum commission floor (broker-specific)."""
        cost = TradingCostModel()
        assert cost.minimum_commission == 0.0
        # Trade value 1,000 TWD -> 1,000 * 0.001425 = 1.425 TWD
        assert cost.calc_commission(1_000.0) == pytest.approx(1.425)

    def test_explicit_broker_configuration_with_20_twd_floor(self):
        """Explicit broker profile with 20 TWD minimum charge."""
        cost = TradingCostModel(
            commission_rate=0.001425,
            discount=0.6,
            minimum_commission=EXAMPLE_BROKER_MIN_COMMISSION,
        )
        assert cost.calc_commission(100_000.0) == pytest.approx(85.5)
        # Small trade floored at 20 TWD
        assert cost.calc_commission(1_000.0) == 20.0

    def test_broker_discount_28pct(self):
        cost = TradingCostModel(
            commission_rate=0.001425,
            discount=0.28,
            minimum_commission=20.0,
        )
        assert cost.calc_commission(100_000.0) == pytest.approx(39.9)

    def test_odd_lot_configurable_min_commission(self):
        cost = TradingCostModel(
            commission_rate=0.001425,
            discount=0.6,
            minimum_commission=1.0,
        )
        assert cost.calc_commission(1_000.0) == pytest.approx(1.0)

    def test_zero_or_negative_trade_value(self):
        cost = TradingCostModel(minimum_commission=20.0)
        assert cost.calc_commission(0.0) == 0.0
        assert cost.calc_commission(-500.0) == 0.0


# ── 2. Securities Tax Model Tests & Expiry Handling ────────────


class TestSecuritiesTaxModel:
    """Test securities transaction tax rules and regulatory expiry."""

    def test_ordinary_stock_tax_sell_side(self):
        tax_model = SecuritiesTaxModel()
        assert tax_model.calc_tax(100_000.0, TaxClass.ORDINARY_STOCK) == 300.0

    def test_domestic_etf_tax(self):
        tax_model = SecuritiesTaxModel()
        assert tax_model.calc_tax(100_000.0, TaxClass.DOMESTIC_ETF) == 100.0

    def test_day_trade_reduced_tax_within_enacted_window(self):
        tax_model = SecuritiesTaxModel()
        trade_date = date(2026, 8, 28)
        assert tax_model.calc_tax(100_000.0, TaxClass.ORDINARY_STOCK, is_day_trade=True, trade_date=trade_date) == 150.0

    def test_day_trade_tax_reverts_to_the_stock_rate_when_the_reduction_ends(self):
        """Securities Transaction Tax Act art. 2-2 halves the day-trade rate only through 2027-12-31."""
        tax_model = SecuritiesTaxModel()
        last_day = date(2027, 12, 31)
        post_expiry = date(2028, 1, 3)
        assert tax_model.get_tax_rate(TaxClass.ORDINARY_STOCK, is_day_trade=True, trade_date=last_day) == 0.0015
        assert tax_model.get_tax_rate(TaxClass.ORDINARY_STOCK, is_day_trade=True, trade_date=post_expiry) == 0.003

    def test_day_trade_reduction_applies_to_stocks_only(self):
        tax_model = SecuritiesTaxModel()
        trade_date = date(2026, 8, 28)
        assert tax_model.get_tax_rate(TaxClass.DOMESTIC_ETF, is_day_trade=True, trade_date=trade_date) == 0.001
        assert tax_model.get_tax_rate(TaxClass.BOND_ETF, is_day_trade=True, trade_date=trade_date) == 0.0

    def test_bond_etf_tax_free_within_enacted_window(self):
        tax_model = SecuritiesTaxModel()
        trade_date = date(2026, 8, 28)
        assert tax_model.calc_tax(100_000.0, TaxClass.BOND_ETF, trade_date=trade_date) == 0.0

    def test_bond_etf_tax_reverts_to_the_etf_rate_when_the_suspension_ends(self):
        """The suspension ends 2026-12-31, then bond ETFs pay 0.1%.

        Securities Transaction Tax Act art. 2-1 suspension; art. 2(2) rate.
        """
        tax_model = SecuritiesTaxModel()
        assert tax_model.get_tax_rate(TaxClass.BOND_ETF, trade_date=date(2026, 12, 31)) == 0.0
        assert tax_model.get_tax_rate(TaxClass.BOND_ETF, trade_date=date(2027, 1, 4)) == 0.001


# ── 3. Lot Model Tests ─────────────────────────────────────────


class TestLotModel:
    """Test board lot (1000 shares) and odd lot sizing."""

    def test_board_lot_rounds_to_multiples_of_1000(self):
        lot = LotModel(mode="board_lot", board_lot_size=1000)
        assert lot.align_shares(allocation=250_000.0, price=100.0) == 2000

    def test_board_lot_insufficient_for_one_lot(self):
        lot = LotModel(mode="board_lot", board_lot_size=1000)
        assert lot.align_shares(allocation=50_000.0, price=100.0) == 0

    def test_odd_lot_mode_allows_single_shares(self):
        lot = LotModel(mode="odd_lot", odd_lot_size=1)
        assert lot.align_shares(allocation=50_000.0, price=100.0) == 500


# ── 4. Tick Size Model Tests (Boundary vs Order Rounding) ──────


class TestTickSizeModel:
    """Test stock 6 tiers and ETF 2 tiers price tick boundaries."""

    @pytest.mark.parametrize("price,expected_tick", [
        (5.50, 0.01),
        (9.99, 0.01),
        (10.00, 0.05),
        (25.50, 0.05),
        (49.95, 0.05),
        (50.00, 0.10),
        (99.90, 0.10),
        (100.00, 0.50),
        (245.50, 0.50),
        (499.50, 0.50),
        (500.00, 1.00),
        (850.00, 1.00),
        (999.00, 1.00),
        (1000.00, 5.00),
        (2420.00, 5.00),
    ])
    def test_stock_tick_size_boundaries(self, price: float, expected_tick: float):
        assert TickSizeModel.get_tick_size(price, TickSizeClass.ORDINARY_STOCK) == expected_tick

    @pytest.mark.parametrize("price,expected_tick", [
        (20.00, 0.01),
        (49.99, 0.01),
        (50.00, 0.05),
        (106.95, 0.05),
    ])
    def test_etf_tick_size_boundaries(self, price: float, expected_tick: float):
        assert TickSizeModel.get_tick_size(price, TickSizeClass.ETF) == expected_tick

    def test_round_price_limit_boundary_strict(self):
        # 1005.3 TWD with 5.00 tick
        # Limit-up must floor so ceiling is not breached
        assert TickSizeModel.round_price_limit_boundary(1005.3, direction="up") == 1005.0
        # Limit-down must ceil so floor is not breached
        assert TickSizeModel.round_price_limit_boundary(1005.3, direction="down") == 1010.0

    def test_round_order_price_nearest(self):
        # Order price does not force floor or ceil; rounds to nearest tick
        assert TickSizeModel.round_order_price(1006.0) == 1005.0
        assert TickSizeModel.round_order_price(1008.0) == 1010.0


# ── 5. Price Limit Single Source of Truth & Delegation ─────────


class TestPriceLimitModelAndDelegation:
    """Verify PriceLimitModel is the single authoritative source of truth."""

    def test_model_get_limit_pct_ordinary_stock(self):
        assert PriceLimitModel.get_limit_pct(PriceLimitClass.ORDINARY_TEN_PERCENT) == 0.10

    def test_model_get_limit_pct_no_limit(self):
        assert PriceLimitModel.get_limit_pct(PriceLimitClass.NO_LIMIT) is None

    def test_price_limits_py_delegation_ordinary_stock(self):
        # Ordinary stock 2330.TWSE -> 10%
        assert taiwan_price_limit_pct("2330.TWSE") == 0.10
        assert board_limit_pct("2330.TWSE") == 0.10
        assert price_limit_pct("2330.TWSE", date(2026, 8, 28)) == 0.10

    def test_price_limits_py_delegation_no_limit_etf(self):
        # Foreign ETF / Bond ETF with NO_LIMIT class must return None, NOT 10%
        assert taiwan_price_limit_pct("00646.TWSE", limit_class=PriceLimitClass.NO_LIMIT) is None
        assert board_limit_pct("00646.TWSE", limit_class=PriceLimitClass.NO_LIMIT) is None
        assert price_limit_pct("00646.TWSE", date(2026, 8, 28), limit_class=PriceLimitClass.NO_LIMIT) is None


# ── 6. Settlement and Same-Day Exit Decoupling ─────────────────


class TestSettlementAndSameDayExit:
    """Verify settlement cycle is decoupled from position exit rules."""

    def test_settlement_model_is_t_plus_2(self):
        settlement = SettlementModel()
        assert settlement.settlement_days == 2

    def test_standard_cash_disallows_same_day_exit(self):
        profile = TaiwanMarketProfile(mode=BacktestMode.STANDARD_CASH)
        assert profile.can_same_day_exit(is_day_trade_eligible=True) is False
        assert profile.can_same_day_exit(is_day_trade_eligible=False) is False

    def test_day_trading_allows_same_day_exit_for_eligible_securities(self):
        profile = TaiwanMarketProfile(mode=BacktestMode.DAY_TRADING)
        assert profile.can_same_day_exit(is_day_trade_eligible=True) is True
        assert profile.can_same_day_exit(is_day_trade_eligible=False) is False


def test_audited_leveraged_etf_rule_profiles_are_explicit_official_evidence():
    leveraged = OFFICIAL_ETF_RULE_PROFILES["00631L.TWSE"]
    inverse = OFFICIAL_ETF_RULE_PROFILES["00632R.TWSE"]
    assert (leveraged.category, leveraged.underlying_scope, leveraged.price_limit_multiplier) == ("leveraged", "domestic", 2.0)
    assert (inverse.category, inverse.underlying_scope, inverse.price_limit_multiplier) == ("inverse", "domestic", -1.0)
    assert leveraged.evidence_url.startswith("https://www.twse.com.tw/")


# ── 7. One Tick Ladder Behind Scalar, Polars and NumPy ─────────

_POLARS_FLOAT = {np.float32: pl.Float32, np.float64: pl.Float64}


def _tick_class(is_etf: bool) -> TickSizeClass:
    return TickSizeClass.ETF if is_etf else TickSizeClass.ORDINARY_STOCK


def _scalar_limits(refs, pct, is_etf, trade_date=None):
    return [
        PriceLimitModel.calc_limits_for_pct(float(r), pct, _tick_class(is_etf), trade_date=trade_date)
        for r in refs
    ]


def _polars_limits(refs, pct, is_etf, trade_date=None):
    frame = pl.DataFrame({
        "ref": pl.Series(refs),
        "pct": pl.Series([pct] * len(refs), dtype=pl.Float64),
        "etf": pl.Series([is_etf] * len(refs), dtype=pl.Boolean),
    })
    up, down = PriceLimitModel.polars_limit_prices(
        pl.col("ref"), pl.col("pct"), pl.col("etf"), trade_date=trade_date
    )
    out = frame.select(up.alias("up"), down.alias("down"))
    return list(zip(out["up"].to_list(), out["down"].to_list(), strict=True))


def _numpy_limits(refs, pct, is_etf, trade_date=None):
    pcts = np.full(len(refs), np.nan if pct is None else pct)
    up, down = PriceLimitModel.numpy_limit_prices(
        refs, pcts, np.full(len(refs), is_etf), trade_date=trade_date
    )
    return [
        (None if np.isnan(u) else float(u), None if np.isnan(d) else float(d))
        for u, d in zip(up, down, strict=True)
    ]


_LIMIT_CASES = [
    pytest.param(5180.0, False, 5695.0, 4665.0, id="2454-5180"),
    pytest.param(971.0, False, 1065.0, 874.0, id="6488-971"),
    pytest.param(535.0, False, 588.0, 481.5, id="6196-535"),
    pytest.param(111.85, True, 123.0, 100.7, id="0050-etf-111.85"),
    pytest.param(2248.99, False, 2470.0, 2025.0, id="2330-2248.99"),
    pytest.param(519.0, False, 570.0, 467.5, id="3034-519"),
    pytest.param(704.0, False, 774.0, 634.0, id="2379-704"),
    pytest.param(51.3, False, 56.4, 46.2, id="6225-51.3"),
    pytest.param(3925.0, False, 4315.0, 3535.0, id="2454-3925"),
    pytest.param(9.1, False, 10.0, 8.19, id="stock-across-10"),
    pytest.param(9.1, True, 10.01, 8.19, id="etf-across-10"),
    pytest.param(11.1, False, 12.2, 9.99, id="stock-down-below-10"),
    pytest.param(11.1, True, 12.21, 9.99, id="etf-down-below-10"),
    pytest.param(45.5, False, 50.0, 40.95, id="stock-across-50"),
    pytest.param(45.5, True, 50.05, 40.95, id="etf-across-50"),
    pytest.param(91.0, False, 100.0, 81.9, id="stock-across-100"),
    pytest.param(91.0, True, 100.1, 81.9, id="etf-across-100"),
    pytest.param(455.0, False, 500.0, 409.5, id="stock-across-500"),
    pytest.param(455.0, True, 500.5, 409.5, id="etf-across-500"),
    pytest.param(910.0, False, 1000.0, 819.0, id="stock-across-1000"),
    pytest.param(910.0, True, 1001.0, 819.0, id="etf-across-1000"),
    pytest.param(1111.0, False, 1220.0, 1000.0, id="stock-down-below-1000"),
    pytest.param(1111.0, True, 1222.1, 999.9, id="etf-down-below-1000"),
]

_TICK_BOUNDARIES = [
    (9.99, 0.01, 0.01),
    (10.0, 0.05, 0.01),
    (49.99, 0.05, 0.01),
    (50.0, 0.10, 0.05),
    (99.99, 0.10, 0.05),
    (100.0, 0.50, 0.05),
    (499.5, 0.50, 0.05),
    (500.0, 1.00, 0.05),
    (999.0, 1.00, 0.05),
    (1000.0, 5.00, 0.05),
]


class TestSharedTickLadder:
    """Scalar, Polars and NumPy entry points share one ladder and one rounding rule."""

    @pytest.mark.parametrize("dtype", [np.float32, np.float64])
    @pytest.mark.parametrize("ref,is_etf,expected_up,expected_down", _LIMIT_CASES)
    def test_limit_prices_agree_across_interfaces(self, dtype, ref, is_etf, expected_up, expected_down):
        refs = np.array([ref], dtype=dtype)
        expected = [(expected_up, expected_down)]
        assert _scalar_limits(refs, 0.10, is_etf) == expected
        assert _polars_limits(refs, 0.10, is_etf) == expected
        assert _numpy_limits(refs, 0.10, is_etf) == expected

    def test_2454_differs_from_a_share_cent_rounding(self):
        refs = np.array([5180.0])
        pcts = np.array([0.10])
        assert numpy_limit_price(refs, pcts, up=True).tolist() == [5698.0]
        assert numpy_limit_price(refs, pcts, up=False).tolist() == [4662.0]
        assert _numpy_limits(refs, 0.10, False) == [(5695.0, 4665.0)]

    @pytest.mark.parametrize("dtype", [np.float32, np.float64])
    @pytest.mark.parametrize("price,stock_tick,etf_tick", _TICK_BOUNDARIES)
    def test_tick_size_boundaries_agree_across_interfaces(self, dtype, price, stock_tick, etf_tick):
        prices = np.array([price, price], dtype=dtype)
        is_etf = np.array([False, True])
        expected = [stock_tick, etf_tick]
        scalar = [
            TickSizeModel.get_tick_size(float(p), _tick_class(bool(e)))
            for p, e in zip(prices, is_etf, strict=True)
        ]
        frame = pl.DataFrame({"price": pl.Series(prices), "etf": pl.Series(is_etf)})
        polars = frame.select(TickSizeModel.polars_tick_size(pl.col("price"), pl.col("etf")))
        assert frame["price"].dtype == _POLARS_FLOAT[dtype]
        assert scalar == expected
        assert polars.to_series().to_list() == expected
        assert TickSizeModel.numpy_tick_size(prices, is_etf).tolist() == expected

    def test_trade_date_none_matches_current_session(self):
        refs = np.array([5180.0, 111.85, 9.1, 45.5, 910.0, 1111.0])
        today = date(2026, 10, 6)
        for is_etf in (False, True):
            assert _scalar_limits(refs, 0.10, is_etf) == _scalar_limits(refs, 0.10, is_etf, today)
            assert _polars_limits(refs, 0.10, is_etf) == _polars_limits(refs, 0.10, is_etf, today)
            assert _numpy_limits(refs, 0.10, is_etf) == _numpy_limits(refs, 0.10, is_etf, today)
            flags = np.full(refs.size, is_etf)
            assert (
                TickSizeModel.numpy_tick_size(refs, flags).tolist()
                == TickSizeModel.numpy_tick_size(refs, flags, trade_date=today).tolist()
            )
            assert [TickSizeModel.get_tick_size(float(p), _tick_class(is_etf)) for p in refs] == [
                TickSizeModel.get_tick_size(float(p), _tick_class(is_etf), trade_date=today) for p in refs
            ]

    def test_missing_percentage_yields_missing_limits(self):
        refs = np.array([100.0, 5180.0])
        assert _scalar_limits(refs, None, False) == [(None, None), (None, None)]
        assert _polars_limits(refs, None, False) == [(None, None), (None, None)]
        assert _numpy_limits(refs, None, False) == [(None, None), (None, None)]

    @pytest.mark.parametrize("pct,is_etf", [(0.10, False), (0.10, True), (0.20, True)])
    def test_vector_interfaces_match_scalar_on_dense_price_grid(self, pct, is_etf):
        refs = np.concatenate([np.arange(1, 120_001) * 0.01, 1200.0 + np.arange(0, 96_001) * 0.05])
        scalar = _scalar_limits(refs, pct, is_etf)
        assert _numpy_limits(refs, pct, is_etf) == scalar
        assert _polars_limits(refs, pct, is_etf) == scalar


def test_enriched_limit_signals_use_the_shared_ladder():
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

    service = TaiwanEnrichedRefreshService.__new__(TaiwanEnrichedRefreshService)
    service._limit_profile = lambda symbols, _sessions: pl.DataFrame(
        {"symbol": symbols, "limit_pct": [0.10] * len(symbols), "is_etf_tick": [False] * len(symbols),
         "ipo_start": [None] * len(symbols), "ipo_end": [None] * len(symbols)},
        schema_overrides={"ipo_start": pl.Date, "ipo_end": pl.Date},
    )
    closes = [5695.0, 5690.0, 4665.0, 4670.0]
    frame = pl.DataFrame({
        "symbol": [f"S{i}" for i in range(len(closes))],
        "date": [date(2026, 10, 6)] * len(closes),
        "prev_close": [5180.0] * len(closes),
        "close": closes,
        "raw_close": closes,
        "raw_high": [5695.0, 5695.0, 4670.0, 4700.0],
        "raw_low": [5600.0, 5600.0, 4665.0, 4665.0],
    })

    out = service._add_limit_signals(frame)

    assert out["signal_limit_up"].to_list() == [True, False, False, False]
    assert out["signal_broken_limit_up"].to_list() == [False, True, False, False]
    assert out["signal_limit_down"].to_list() == [False, False, True, False]
    assert out["signal_limit_down_recovery"].to_list() == [False, False, False, True]


def test_pick_performance_limit_up_uses_the_shared_ladder():
    from app.services.strategy_pick_performance import _limit_up_expr

    frame = pl.DataFrame({
        "_signal_close": [5180.0, 111.85, 5180.0],
        "_limit_pct": [0.10, 0.10, 0.10],
        "_is_etf_tick": [False, True, False],
        "_entry_ref_prev_close": [None, None, 5000.0],
        "_entry_ref_limit_up": [None, None, 5500.0],
    })

    assert frame.select(_limit_up_expr()).to_series().to_list() == [5695.0, 123.0, 5500.0]


@pytest.mark.parametrize("refs,is_etf,expected", [
    pytest.param(np.array([0.7], dtype=np.float32), False, [(0.77, 0.63)], id="float32-0.7"),
    pytest.param(np.array([9.1 * (1 - 1e-12)]), True, [(10.01, 8.19)], id="etf-9.1-noise-below"),
    pytest.param(np.array([9.1 * (1 + 1e-12)]), True, [(10.01, 8.19)], id="etf-9.1-noise-above"),
])
def test_reference_noise_does_not_move_the_limit_a_tick(refs, is_etf, expected):
    assert _scalar_limits(refs, 0.10, is_etf) == expected
    assert _polars_limits(refs, 0.10, is_etf) == expected
    assert _numpy_limits(refs, 0.10, is_etf) == expected


def test_vector_limits_of_degenerate_references():
    frame = pl.DataFrame({"ref": [0.0, -1.0, float("nan"), None]}, schema={"ref": pl.Float64})
    up, down = PriceLimitModel.polars_limit_prices(pl.col("ref"), pl.lit(0.10), pl.lit(False))
    rows = frame.select(up.alias("up"), down.alias("down")).rows()
    assert rows[:2] == [(0.0, 0.01), (-1.1, 0.01)]
    assert all(np.isnan(v) for v in rows[2])
    assert rows[3] == (None, None)

    np_up, np_down = PriceLimitModel.numpy_limit_prices(np.array([0.0, -1.0, np.nan]), 0.10, False)
    assert np_up[:2].tolist() == [0.0, -1.1]
    assert np_down[:2].tolist() == [0.01, 0.01]
    assert np.isnan(np_up[2]) and np.isnan(np_down[2])


def test_trade_date_accepts_a_datetime_and_rejects_other_types():
    moment = datetime(2026, 10, 6, 13, 30)
    assert TickSizeModel.get_tick_size(1000.0, trade_date=moment) == 5.0
    assert PriceLimitModel.calc_limits_for_pct(5180.0, 0.10, trade_date=moment) == (5695.0, 4665.0)
    assert _polars_limits(np.array([5180.0]), 0.10, False, moment) == [(5695.0, 4665.0)]
    assert _numpy_limits(np.array([5180.0]), 0.10, False, moment) == [(5695.0, 4665.0)]
    assert TickSizeModel.numpy_tick_size(np.array([1000.0]), False, trade_date=moment).tolist() == [5.0]

    for bad in ("2026-10-06", np.datetime64("2026-10-06"), 20261006):
        with pytest.raises(TypeError, match="trade_date"):
            TickSizeModel.get_tick_size(1000.0, trade_date=bad)
        with pytest.raises(TypeError, match="trade_date"):
            TickSizeModel.get_tick_size(-1.0, trade_date=bad)
        with pytest.raises(TypeError, match="trade_date"):
            TickSizeModel.polars_tick_size(pl.col("p"), pl.lit(False), trade_date=bad)
        with pytest.raises(TypeError, match="trade_date"):
            PriceLimitModel.numpy_limit_prices(np.array([5180.0]), 0.10, False, trade_date=bad)
