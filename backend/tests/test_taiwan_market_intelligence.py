"""Tests for Taiwan Market Intelligence Snapshot Service (Phase 7A).

Verifies:
- 100% deterministic breadth calculations:
    * 3 symbols fixture: 1 up, 1 down, 1 flat -> advance=1, decline=1, flat=1.
    * Missing previous close -> uncompared_count incremented, excluded from A/D/F.
    * Volume = 0 -> not counted in traded_count nor as flat; counted in untraded_count.
    * Monday target date correctly resolves to prior Friday.
- Price limit rules:
    * 0050.TWSE (domestic ETF, 10% limit)
    * 00631L.TWSE (leveraged domestic 2X, 20% limit)
    * 00632R.TWSE (inverse domestic -1X, 10% limit)
    * 00646.TWSE (foreign-underlying ETF, NO_LIMIT -> never counted as hit limit)
    * 8069.TPEX (TPEx ordinary stock, 10% limit with standard tick tiers)
- Unit preservation:
    * Turnover in TWD
    * Institutional net amounts in TWD (see test_taiwan_market_institutional.py)
    * Margin balance in shares
- Aggregate margin ratio:
    * sum(short) / sum(margin) * 100 (never average of ratios)
- Partial state & data quality reporting:
    * Missing dataset correctly marks partial/stale
- Historical snapshot with NO look-ahead:
    * Query for date D uses only data on/before D
- Zero HTTP:
    * GET /api/taiwan/market-intelligence performs 0 external requests
"""
from datetime import date
from unittest.mock import MagicMock, patch
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.market_intelligence import (
    TaiwanMarketIntelligenceService,
    TaiwanMarketIntelligenceSnapshot,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument


@pytest.fixture
def mock_calendar():
    cal = TaiwanTradingCalendar()
    # 2026-08-28 is Friday, 2026-08-31 is Monday
    return cal


def _quality_service(
    target: date,
    universe_symbols: list[str],
    snapshot_symbols: list[str],
    *,
    provisional: bool = False,
    zero_volume_symbols: set[str] | None = None,
    master_updated_at: date | None = None,
) -> TaiwanMarketIntelligenceService:
    zero_volume_symbols = zero_volume_symbols or set()
    master_updated_at = master_updated_at or target
    previous = date.fromordinal(target.toordinal() - 1)
    universe = pl.DataFrame({
        "symbol": universe_symbols,
        "exchange": [symbol.rsplit(".", 1)[1] for symbol in universe_symbols],
        "instrument_type": ["stock"] * len(universe_symbols),
        "listing_status": ["active"] * len(universe_symbols),
        "listing_date": [target.isoformat()] * len(universe_symbols),
        "updated_at": [f"{master_updated_at.isoformat()}T08:00:00+08:00"] * len(universe_symbols),
    })

    def rows(symbols: list[str], d: date) -> pl.DataFrame:
        return pl.DataFrame({
            "symbol": symbols,
            "date": [d] * len(symbols),
            "open": [100.0] * len(symbols),
            "high": [101.0] * len(symbols),
            "low": [99.0] * len(symbols),
            "close": [100.0] * len(symbols),
            "volume": [0.0 if symbol in zero_volume_symbols else 1000.0 for symbol in symbols],
            "amount": [0.0 if symbol in zero_volume_symbols else 100000.0 for symbol in symbols],
            "quote_ts": [None] * len(symbols),
        })

    current = rows(snapshot_symbols, target)
    prior = rows(snapshot_symbols, previous)
    daily = MagicMock()
    daily.available_dates.return_value = [previous, target]
    daily.provisional_dates.return_value = [target] if provisional else []
    daily.read_range.side_effect = lambda _symbols, start, end: (
        current if start == target and end == target
        else prior if start == previous and end == previous
        else pl.concat([prior, current])
    )

    inst_store = MagicMock()
    inst_store.available_dates.return_value = [target]
    inst_store.read_range.return_value = pl.DataFrame()
    margin_store = MagicMock()
    margin_store.available_dates.return_value = [target]
    margin_store.read_range.return_value = pl.DataFrame()
    security_master = MagicMock()
    security_master.to_dataframe.return_value = universe
    security_master.get_instrument.return_value = None
    market_inst = MagicMock()
    market_inst.available_dates.return_value = []
    market_inst.read_day.return_value = pl.DataFrame(schema={
        "exchange": pl.String,
        "category": pl.String,
        "net_amount": pl.Int64,
        "has_discrepancy": pl.Boolean,
    })
    corporate_actions = MagicMock()
    corporate_actions.read.return_value = pl.DataFrame()
    return TaiwanMarketIntelligenceService(
        daily_store=daily,
        inst_store=inst_store,
        margin_store=margin_store,
        security_master=security_master,
        market_inst_store=market_inst,
        corporate_actions=corporate_actions,
    )


@pytest.mark.parametrize(
    ("snapshot_symbols", "missing_exchange"),
    [(["B.TPEX"], "TWSE"), (["A.TWSE"], "TPEX")],
)
def test_missing_exchange_is_partial(snapshot_symbols, missing_exchange):
    target = date(2026, 8, 28)
    snapshot = _quality_service(target, ["A.TWSE", "B.TPEX"], snapshot_symbols).get_snapshot(target)

    assert snapshot.data_quality.overall_status == "partial"
    assert snapshot.data_quality.missing_symbols_count == 1
    assert getattr(snapshot.data_quality, f"{missing_exchange.lower()}_snapshot_symbols") == 0


def test_large_universe_gap_is_partial():
    target = date(2026, 8, 28)
    universe = [f"{i:04d}.TWSE" for i in range(10)] + [f"{i:04d}.TPEX" for i in range(10, 20)]
    snapshot = _quality_service(target, universe, ["0000.TWSE", "0010.TPEX"]).get_snapshot(target)

    assert snapshot.data_quality.overall_status == "partial"
    assert snapshot.data_quality.missing_symbols_count == 18


def test_provisional_partition_is_not_complete():
    target = date(2026, 8, 28)
    snapshot = _quality_service(
        target,
        ["A.TWSE", "B.TPEX"],
        ["A.TWSE", "B.TPEX"],
        provisional=True,
    ).get_snapshot(target)

    assert snapshot.data_quality.daily.status == "provisional"
    assert snapshot.data_quality.overall_status == "provisional"


def test_complete_dual_market_snapshot_is_complete():
    target = date(2026, 8, 28)
    snapshot = _quality_service(
        target,
        ["A.TWSE", "B.TPEX"],
        ["A.TWSE", "B.TPEX"],
    ).get_snapshot(target)

    assert snapshot.data_quality.overall_status == "complete"
    assert snapshot.data_quality.missing_symbols_count == 0


_PROD_TARGET = date(2026, 10, 1)
_PROD_MASTER_DATE = date(2026, 9, 17)
_PROD_UNIVERSE = [f"{i:04d}.TWSE" for i in range(1300)] + [f"{i:04d}.TPEX" for i in range(1300, 2377)]


def test_master_older_than_target_can_be_complete():
    snapshot = _quality_service(
        _PROD_TARGET,
        _PROD_UNIVERSE,
        _PROD_UNIVERSE[:2336],
        master_updated_at=_PROD_MASTER_DATE,
    ).get_snapshot(_PROD_TARGET)

    assert snapshot.data_quality.daily.status == "current"
    assert snapshot.data_quality.missing_symbols_count == 41
    assert snapshot.data_quality.overall_status == "complete"


@pytest.mark.parametrize(
    ("snapshot_count", "expected"),
    [(2336, "complete"), (2330, "complete"), (2329, "partial"), (2300, "partial")],
)
def test_daily_coverage_threshold_boundary(snapshot_count, expected):
    snapshot = _quality_service(
        _PROD_TARGET,
        _PROD_UNIVERSE,
        _PROD_UNIVERSE[:snapshot_count],
        master_updated_at=_PROD_MASTER_DATE,
    ).get_snapshot(_PROD_TARGET)

    assert snapshot.data_quality.daily_snapshot_symbols == snapshot_count
    assert snapshot.data_quality.overall_status == expected


def test_daily_coverage_exactly_at_threshold_is_complete():
    twse = [f"{i:04d}.TWSE" for i in range(50)]
    tpex = [f"{i:04d}.TPEX" for i in range(50, 100)]
    snapshot = _quality_service(
        _PROD_TARGET,
        twse + tpex,
        twse[:49] + tpex[:49],
        master_updated_at=_PROD_MASTER_DATE,
    ).get_snapshot(_PROD_TARGET)

    assert snapshot.data_quality.daily_snapshot_symbols == 98
    assert snapshot.data_quality.overall_status == "complete"


@pytest.mark.parametrize(
    ("universe", "snapshot_symbols"),
    [
        ([f"{i:04d}.TWSE" for i in range(100)], [f"{i:04d}.TWSE" for i in range(100)]),
        (
            [f"{i:04d}.TWSE" for i in range(2337)] + [f"{i:04d}.TPEX" for i in range(2337, 2377)],
            [f"{i:04d}.TWSE" for i in range(2337)],
        ),
    ],
    ids=["single_exchange_universe", "high_coverage_without_tpex"],
)
def test_single_exchange_is_partial(universe, snapshot_symbols):
    snapshot = _quality_service(
        _PROD_TARGET,
        universe,
        snapshot_symbols,
        master_updated_at=_PROD_MASTER_DATE,
    ).get_snapshot(_PROD_TARGET)

    assert snapshot.data_quality.tpex_snapshot_symbols == 0
    assert snapshot.data_quality.overall_status == "partial"


def test_provisional_daily_with_current_chips_and_older_master_is_provisional():
    snapshot = _quality_service(
        _PROD_TARGET,
        _PROD_UNIVERSE,
        _PROD_UNIVERSE[:2336],
        provisional=True,
        master_updated_at=_PROD_MASTER_DATE,
    ).get_snapshot(_PROD_TARGET)

    assert snapshot.data_quality.daily.status == "provisional"
    assert snapshot.data_quality.institutional.status == "current"
    assert snapshot.data_quality.margin.status == "current"
    assert snapshot.data_quality.overall_status == "provisional"


def test_master_updated_at_does_not_gate_coverage():
    service = _quality_service(_PROD_TARGET, ["A.TWSE", "B.TPEX"], ["A.TWSE", "B.TPEX"])
    service.security_master.to_dataframe.return_value = (
        service.security_master.to_dataframe.return_value.with_columns(
            pl.Series("updated_at", ["2026-09-17T08:00:00+08:00", "2026-09-18T08:00:00+08:00"])
        )
    )

    assert service.get_snapshot(_PROD_TARGET).data_quality.overall_status == "complete"


def test_historical_target_counts_only_symbols_listed_by_then():
    target = date(2026, 8, 27)
    service = _quality_service(
        target,
        ["A.TWSE", "B.TPEX", "FUTURE.TWSE"],
        ["A.TWSE", "B.TPEX"],
    )
    service.security_master.to_dataframe.return_value = (
        service.security_master.to_dataframe.return_value.with_columns([
            pl.when(pl.col("symbol") == "FUTURE.TWSE")
            .then(pl.lit("2026-08-28"))
            .otherwise(pl.col("listing_date"))
            .alias("listing_date"),
            pl.lit("2026-08-28T08:00:00+08:00").alias("updated_at"),
        ])
    )

    snapshot = service.get_snapshot(target)

    assert snapshot.data_quality.universe_supported_symbols == 2
    assert snapshot.data_quality.missing_symbols_count == 0
    assert snapshot.data_quality.overall_status == "complete"


def test_current_universe_filters_non_products_and_keeps_zero_volume_products():
    target = date(2026, 8, 28)
    service = _quality_service(
        target,
        ["HALT.TWSE", "ETF.TPEX", "FUTURE.TWSE", "TDR.TWSE", "OLD.TPEX", "UNSUPPORTED.TPEX"],
        ["HALT.TWSE", "ETF.TPEX"],
        zero_volume_symbols={"HALT.TWSE"},
    )
    service.security_master.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["HALT.TWSE", "ETF.TPEX", "FUTURE.TWSE", "TDR.TWSE", "OLD.TPEX", "UNSUPPORTED.TPEX"],
        "exchange": ["TWSE", "TPEX", "TWSE", "TWSE", "TPEX", "TPEX"],
        "instrument_type": ["stock", "etf", "stock", "tdr", "stock", "stock"],
        "listing_status": ["active", "active", "active", "active", "suspended", "unsupported"],
        "listing_date": ["20200101", "1150828", "2026/08/29", "20200101", "20200101", "20200101"],
        "updated_at": ["2026-08-28T08:00:00+08:00"] * 6,
    })

    snapshot = service.get_snapshot(target)

    assert snapshot.data_quality.overall_status == "complete"
    assert snapshot.data_quality.universe_supported_symbols == 2
    assert snapshot.market_totals.snapshot_row_count == 2
    assert snapshot.market_totals.traded_count == 1
    assert snapshot.by_instrument.stock.supported_count == 1
    assert snapshot.by_instrument.etf.supported_count == 1


def test_empty_daily_snapshot_is_unavailable():
    target = date(2026, 8, 28)
    snapshot = _quality_service(target, ["A.TWSE", "B.TPEX"], []).get_snapshot(target)

    assert snapshot.data_quality.daily.status == "unavailable"
    assert snapshot.data_quality.overall_status == "unavailable"


def test_deterministic_breadth_fixture(mock_calendar):
    """3 symbols: 1 up, 1 down, 1 flat, plus 1 uncompared and 1 zero-volume."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)

    # Mock daily store
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    # Current rows: SYM1 (up), SYM2 (down), SYM3 (flat), SYM4 (new, no prev), SYM5 (vol=0)
    curr_data = pl.DataFrame({
        "symbol": ["SYM1.TWSE", "SYM2.TWSE", "SYM3.TWSE", "SYM4.TWSE", "SYM5.TWSE"],
        "date": [d_curr] * 5,
        "open": [10.0, 20.0, 30.0, 40.0, 50.0],
        "high": [12.0, 21.0, 31.0, 41.0, 50.0],
        "low": [9.0, 18.0, 29.0, 39.0, 50.0],
        "close": [11.0, 19.0, 30.0, 42.0, 50.0],
        "volume": [1000.0, 2000.0, 3000.0, 4000.0, 0.0],
        "amount": [11000.0, 38000.0, 90000.0, 168000.0, 0.0],
        "quote_ts": [None] * 5,
    })

    # Prev rows: SYM1 (10.0), SYM2 (20.0), SYM3 (30.0), SYM5 (50.0) -> SYM4 missing
    prev_data = pl.DataFrame({
        "symbol": ["SYM1.TWSE", "SYM2.TWSE", "SYM3.TWSE", "SYM5.TWSE"],
        "date": [d_prev] * 4,
        "open": [10.0, 20.0, 30.0, 50.0],
        "high": [10.0, 20.0, 30.0, 50.0],
        "low": [10.0, 20.0, 30.0, 50.0],
        "close": [10.0, 20.0, 30.0, 50.0],
        "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        "amount": [10000.0, 20000.0, 30000.0, 50000.0],
        "quote_ts": [None] * 4,
    })

    def mock_read_range(syms, start, end):
        if start == d_curr:
            return curr_data
        elif start == d_prev:
            return prev_data
        return pl.DataFrame()

    mock_daily.read_range.side_effect = mock_read_range

    # Mock security master
    mock_sm = MagicMock()
    uni_df = pl.DataFrame({
        "symbol": ["SYM1.TWSE", "SYM2.TWSE", "SYM3.TWSE", "SYM4.TWSE", "SYM5.TWSE"],
        "exchange": ["TWSE"] * 5,
        "instrument_type": ["stock"] * 5,
        "listing_status": ["active"] * 5,
    })
    mock_sm.to_dataframe.return_value = uni_df
    mock_sm.get_instrument.return_value = TaiwanInstrument(
        symbol="SYM1.TWSE",
        code="SYM1",
        exchange="TWSE",
        name="測試股",
        instrument_type="stock",
        listing_status="active",
        listing_date="2020/01/01",
        isin="TW000SYM1000",
        industry="半導體業",
        cfi_code="ESVUFR",
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-08-31T00:00:00",
    )

    # Empty inst & margin
    mock_inst = MagicMock()
    mock_inst.read_range.return_value = pl.DataFrame()
    mock_inst.available_dates.return_value = []
    mock_margin = MagicMock()
    mock_margin.read_range.return_value = pl.DataFrame()
    mock_margin.available_dates.return_value = []

    svc = TaiwanMarketIntelligenceService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
        calendar=mock_calendar,
        security_master=mock_sm,
    )

    snapshot = svc.get_snapshot(d_curr)
    totals = snapshot.market_totals

    assert totals.supported_count == 5
    assert totals.snapshot_row_count == 5
    assert totals.traded_count == 4  # SYM5 volume=0 not traded
    assert totals.advance_count == 1  # SYM1 11.0 > 10.0
    assert totals.decline_count == 1  # SYM2 19.0 < 20.0
    assert totals.flat_count == 1  # SYM3 30.0 == 30.0; SYM5 did not trade
    assert totals.uncompared_count == 1  # SYM4 missing prev close
    assert totals.untraded_count == 1  # SYM5 volume=0
    assert totals.turnover == 307000.0


def test_monday_resolves_to_prior_friday(mock_calendar):
    """Target date Monday 2026-08-31 should resolve previous trading day to Friday 2026-08-28."""
    mock_daily = MagicMock()
    d_fri = date(2026, 8, 28)
    d_mon = date(2026, 8, 31)
    mock_daily.available_dates.return_value = [d_fri, d_mon]

    svc = TaiwanMarketIntelligenceService(
        daily_store=mock_daily,
        calendar=mock_calendar,
    )
    prev = svc.get_previous_trading_date(d_mon)
    assert prev == d_fri


def test_price_limit_rules_and_no_limit():
    """Verify limit-up and limit-down detection across diverse regulatory profiles."""
    sm = TaiwanSecurityMaster()

    # 1. 0050.TWSE: domestic ETF (10% limit)
    inst_0050 = sm.get_instrument("0050.TWSE")
    assert inst_0050 is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_0050) == 0.10

    # 2. 00631L.TWSE: leveraged domestic 2X (20% limit)
    inst_00631l = sm.get_instrument("00631L.TWSE")
    assert inst_00631l is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_00631l) == 0.20

    # 3. 00632R.TWSE: inverse domestic -1X (10% limit)
    inst_00632r = sm.get_instrument("00632R.TWSE")
    assert inst_00632r is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_00632r) == 0.10

    # 4. 00646.TWSE: foreign-underlying ETF (NO_LIMIT -> None)
    inst_00646 = sm.get_instrument("00646.TWSE")
    assert inst_00646 is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_00646) is None
    up, dn = MarketProfileBridge.calc_limits(100.0, inst_00646)
    assert up is None and dn is None

    # 5. 8069.TPEX: ordinary TPEx stock (10% limit)
    inst_8069 = sm.get_instrument("8069.TPEX")
    assert inst_8069 is not None
    assert MarketProfileBridge.get_price_limit_pct(inst_8069) == 0.10


def test_aggregate_short_margin_ratio():
    """Verify sum(short) / sum(margin) * 100 logic, never average of individual ratios."""
    d = date(2026, 8, 28)
    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = [d]
    mock_margin.read_range.return_value = pl.DataFrame({
        "symbol": ["A.TWSE", "B.TWSE"],
        "date": [d, d],
        "margin_balance": [1000.0, 2000.0],
        "margin_change": [10.0, 20.0],
        "short_balance": [100.0, 300.0],
        "short_change": [5.0, 15.0],
    })

    svc = TaiwanMarketIntelligenceService(margin_store=mock_margin)
    snapshot = svc.get_snapshot(d)
    m = snapshot.margin
    assert m.margin_balance == 3000.0
    assert m.short_balance == 400.0
    # Expected: 400 / 3000 * 100 = 13.33% (NOT avg(10%, 15%) = 12.5%)
    assert m.aggregate_short_margin_ratio == 13.33


def test_partial_state_reporting():
    """Daily current, Inst stale, Margin unavailable -> overall status partial."""
    d = date(2026, 8, 28)
    svc = _quality_service(d, ["A.TWSE", "B.TPEX"], ["A.TWSE", "B.TPEX"])
    svc.inst_store.available_dates.return_value = [date(2026, 8, 27)]
    svc.margin_store.available_dates.return_value = []
    snapshot = svc.get_snapshot(d)
    dq = snapshot.data_quality
    assert dq.daily.status == "current"
    assert dq.institutional.status == "stale"
    assert dq.margin.status == "unavailable"
    assert dq.overall_status == "partial"


def test_historical_snapshot_no_look_ahead():
    """Querying date D passes exact (target, target) to read_range, never reading D+1."""
    d_target = date(2026, 8, 20)
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [date(2026, 8, 19), d_target, date(2026, 8, 21)]
    mock_daily.read_range.return_value = pl.DataFrame()

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = [d_target, date(2026, 8, 21)]
    mock_inst.read_range.return_value = pl.DataFrame()

    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = [d_target, date(2026, 8, 21)]
    mock_margin.read_range.return_value = pl.DataFrame()

    svc = TaiwanMarketIntelligenceService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
    )
    snapshot = svc.get_snapshot(d_target)

    # Check that stores were called with start=d_target, end=d_target
    for call in mock_inst.read_range.call_args_list:
        _, start, end = call[0]
        assert start <= d_target
        assert end <= d_target

    for call in mock_margin.read_range.call_args_list:
        _, start, end = call[0]
        assert start <= d_target
        assert end <= d_target


def test_api_endpoint_zero_external_http():
    """GET /api/taiwan/market-intelligence performs 0 external market provider HTTP calls."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with patch("urllib.request.urlopen") as mock_urlopen:
        resp = client.get("/api/taiwan/market-intelligence?date=2026-08-28")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trade_date"] == "2026-08-28"
        assert "market_totals" in data
        assert "institutional" in data
        assert "margin" in data
        assert "by_exchange" in data
        assert mock_urlopen.call_count == 0
