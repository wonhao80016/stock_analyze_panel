"""Industry intelligence and abnormal diagnostics judge daily data quality like market intelligence.

A target partition holding one exchange only, a comparison base partition missing an
exchange, or a partition written on its own trading day must never be reported as complete.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.abnormal_diagnostics import TaiwanAbnormalDiagnosticsService
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.universe.models import TaiwanInstrument
from app.taiwan.universe.service import UniverseType

TARGET = date(2026, 8, 28)
TWSE_SYMBOLS = [f"{1000 + i}.TWSE" for i in range(99)]
TPEX_SYMBOLS = ["6000.TPEX"]
ALL_SYMBOLS = TWSE_SYMBOLS + TPEX_SYMBOLS
# Never written to the daily store, so they only count when a universe includes ETFs.
ETF_SYMBOLS = ["0050.TWSE", "0056.TWSE", "00878.TWSE", "006201.TPEX", "00679B.TPEX"]

pytestmark = pytest.mark.usefixtures("deny_network")


def _sessions(end: date, count: int) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


SESSIONS = _sessions(TARGET, 22)
PRIOR = [d for d in SESSIONS if d < TARGET]
D_5D = PRIOR[-5]


def _instrument(symbol: str, updated_at: str, instrument_type: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    is_stock = instrument_type == "stock"
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=f"S{code}",
        instrument_type=instrument_type,
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=("半導體業" if int(code) % 2 else "電子零組件業") if is_stock else None,
        cfi_code=None,
        raw_category="股票" if is_stock else "ETF",
        is_supported=True,
        source=f"{exchange}_ISIN",
        updated_at=updated_at,
    )


def _security_master(master_as_of: date | None) -> MagicMock:
    """A master whose rows share *master_as_of*; None drops the updated_at column entirely."""
    updated_at = f"{master_as_of.isoformat()}T08:00:00+08:00" if master_as_of else ""
    instruments = {s: _instrument(s, updated_at, "stock") for s in ALL_SYMBOLS}
    instruments |= {s: _instrument(s, updated_at, "etf") for s in ETF_SYMBOLS}
    frame = pl.DataFrame([i.to_dict() for i in instruments.values()])
    master = MagicMock()
    master.to_dataframe.return_value = frame if master_as_of else frame.drop("updated_at")
    master.get_instrument.side_effect = instruments.get
    master.get_universe.side_effect = lambda kind: {
        UniverseType.TAIWAN_STOCKS: sorted(ALL_SYMBOLS),
        UniverseType.TAIWAN_ETFS: sorted(ETF_SYMBOLS),
    }.get(kind, [])
    return master


def _daily_rows(symbols: list[str], d: date) -> pl.DataFrame:
    n = len(symbols)
    return pl.DataFrame(
        {
            "symbol": symbols,
            "date": [d] * n,
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [1000.0] * n,
            "amount": [100000.0] * n,
            "quote_ts": [None] * n,
        },
        schema={
            "symbol": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "amount": pl.Float64,
            "quote_ts": pl.Int64,
        },
    )


def _set_written_on(store_dir: Path, d: date, written: date, hour: int = 18) -> None:
    ts = datetime.combine(written, time(hour), tzinfo=TAIPEI_TZ).timestamp()
    os.utime(store_dir / f"date={d.isoformat()}" / "part.parquet", (ts, ts))


def _daily_store(
    tmp_path: Path,
    *,
    twse_only: set[date] = frozenset(),
    provisional: set[date] = frozenset(),
) -> TaiwanDailyStore:
    store_dir = tmp_path / "daily"
    store = TaiwanDailyStore(store_dir)
    for d in SESSIONS:
        store.write_batch(_daily_rows(TWSE_SYMBOLS if d in twse_only else ALL_SYMBOLS, d))
        _set_written_on(store_dir, d, d if d in provisional else d + timedelta(days=1))
    return store


def _chip_stores() -> tuple[MagicMock, MagicMock]:
    inst = MagicMock()
    inst.available_dates.return_value = SESSIONS
    inst.read_range.return_value = pl.DataFrame(
        {
            "symbol": ALL_SYMBOLS,
            "foreign_net": [0.0] * len(ALL_SYMBOLS),
            "investment_trust_net": [0.0] * len(ALL_SYMBOLS),
            "dealer_net": [0.0] * len(ALL_SYMBOLS),
        }
    )
    margin = MagicMock()
    margin.available_dates.return_value = SESSIONS
    margin.read_range.return_value = pl.DataFrame(
        {
            "symbol": ALL_SYMBOLS,
            "margin_balance": [1000.0] * len(ALL_SYMBOLS),
            "margin_change": [0.0] * len(ALL_SYMBOLS),
            "short_balance": [10.0] * len(ALL_SYMBOLS),
            "short_change": [0.0] * len(ALL_SYMBOLS),
            "short_margin_ratio": [1.0] * len(ALL_SYMBOLS),
        }
    )
    return inst, margin


def _industry_service(tmp_path: Path, daily: TaiwanDailyStore, master_as_of: date | None = TARGET):
    inst, margin = _chip_stores()
    return TaiwanIndustryIntelligenceService(
        daily_store=daily,
        inst_store=inst,
        margin_store=margin,
        calendar=TaiwanTradingCalendar(),
        security_master=_security_master(master_as_of),
        corporate_actions=TaiwanCorporateActionStore(tmp_path / "corporate_actions"),
    )


def _abnormal_service(tmp_path: Path, daily: TaiwanDailyStore, master_as_of: date | None = TARGET):
    inst, margin = _chip_stores()
    master = _security_master(master_as_of)
    corporate_actions = TaiwanCorporateActionStore(tmp_path / "corporate_actions")
    market_inst = MagicMock()
    market_inst.available_dates.return_value = []
    market_inst.read_day.return_value = pl.DataFrame(
        schema={
            "exchange": pl.String,
            "category": pl.String,
            "net_amount": pl.Int64,
            "has_discrepancy": pl.Boolean,
        }
    )
    market = TaiwanMarketIntelligenceService(
        daily_store=daily,
        inst_store=inst,
        margin_store=margin,
        security_master=master,
        market_inst_store=market_inst,
        corporate_actions=corporate_actions,
    )
    return TaiwanAbnormalDiagnosticsService(
        daily_store=daily,
        inst_store=inst,
        margin_store=margin,
        calendar=TaiwanTradingCalendar(),
        security_master=master,
        market_intel_svc=market,
        corporate_actions=corporate_actions,
    )


def test_industry_full_dual_exchange_data_is_complete(tmp_path):
    snapshot = _industry_service(tmp_path, _daily_store(tmp_path)).get_snapshot(TARGET)

    assert snapshot.data_quality.base_date_5d == str(D_5D)
    assert snapshot.data_quality.overall_status == "complete"


def test_industry_twse_only_target_is_partial(tmp_path):
    snapshot = _industry_service(tmp_path, _daily_store(tmp_path, twse_only={TARGET})).get_snapshot(
        TARGET
    )

    assert snapshot.data_quality.overall_status == "partial"


# listed_by() already makes the universe point-in-time, so unlike market intelligence a master
# refreshed after the target, or one without updated_at, does not downgrade the status.
@pytest.mark.parametrize(
    "master_as_of", [TARGET + timedelta(days=20), None], ids=["newer_master", "no_updated_at"]
)
def test_industry_master_as_of_does_not_downgrade_full_coverage(tmp_path, master_as_of):
    service = _industry_service(tmp_path, _daily_store(tmp_path), master_as_of=master_as_of)

    assert service.get_snapshot(TARGET).data_quality.overall_status == "complete"


@pytest.mark.parametrize(
    "base_date", [PRIOR[-1], D_5D, PRIOR[-20]], ids=["d_prev", "d_5d", "d_20d"]
)
def test_industry_base_date_missing_tpex_is_partial(tmp_path, base_date):
    service = _industry_service(tmp_path, _daily_store(tmp_path, twse_only={base_date}))
    snapshot = service.get_snapshot(TARGET)

    assert snapshot.data_quality.previous_trade_date == str(PRIOR[-1])
    assert snapshot.data_quality.base_date_20d == str(PRIOR[-20])
    assert snapshot.data_quality.overall_status == "partial"


def test_industry_provisional_target_is_provisional(tmp_path):
    snapshot = _industry_service(
        tmp_path, _daily_store(tmp_path, provisional={TARGET})
    ).get_snapshot(TARGET)

    assert snapshot.data_quality.overall_status == "provisional"


def test_industry_one_exchange_outranks_provisional(tmp_path):
    daily = _daily_store(tmp_path, twse_only={D_5D}, provisional={TARGET})

    assert (
        _industry_service(tmp_path, daily).get_snapshot(TARGET).data_quality.overall_status
        == "partial"
    )


def test_abnormal_full_dual_exchange_data_is_complete(tmp_path):
    snap = _abnormal_service(tmp_path, _daily_store(tmp_path)).get_diagnostics(
        TARGET, include_all=True
    )

    assert (
        snap.data_quality.evaluated_symbol_count
        == snap.data_quality.universe_supported_count
        == 100
    )
    assert snap.data_quality.overall_status == "complete"


def test_abnormal_twse_only_target_is_partial(tmp_path):
    daily = _daily_store(tmp_path, twse_only={TARGET})
    snap = _abnormal_service(tmp_path, daily).get_diagnostics(TARGET, include_all=True)

    assert snap.data_quality.evaluated_symbol_count == 99
    assert snap.data_quality.evaluated_symbol_count < snap.data_quality.universe_supported_count
    assert snap.data_quality.overall_status == "partial"


def test_abnormal_provisional_target_is_provisional(tmp_path):
    daily = _daily_store(tmp_path, provisional={TARGET})
    snap = _abnormal_service(tmp_path, daily).get_diagnostics(TARGET, include_all=True)

    assert snap.data_quality.overall_status == "provisional"


@pytest.mark.parametrize(
    "master_as_of", [TARGET + timedelta(days=20), None], ids=["newer_master", "no_updated_at"]
)
def test_abnormal_master_as_of_does_not_downgrade_full_coverage(tmp_path, master_as_of):
    service = _abnormal_service(tmp_path, _daily_store(tmp_path), master_as_of=master_as_of)

    assert service.get_diagnostics(TARGET).data_quality.overall_status == "complete"


@pytest.mark.parametrize("prior_session", PRIOR[-5:])
def test_abnormal_prior_session_missing_tpex_is_partial(tmp_path, prior_session):
    daily = _daily_store(tmp_path, twse_only={prior_session})
    snap = _abnormal_service(tmp_path, daily).get_diagnostics(TARGET)

    assert snap.data_quality.overall_status == "partial"


def test_abnormal_coverage_follows_the_include_etfs_universe(tmp_path):
    service = _abnormal_service(tmp_path, _daily_store(tmp_path))

    stocks_only = service.get_diagnostics(TARGET).data_quality
    with_etfs = service.get_diagnostics(TARGET, include_etfs=True).data_quality

    assert (stocks_only.universe_supported_count, stocks_only.overall_status) == (100, "complete")
    assert with_etfs.universe_supported_count == 105
    assert with_etfs.evaluated_symbol_count == 100
    assert with_etfs.overall_status == "partial"
