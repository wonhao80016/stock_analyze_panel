"""Industry intelligence and abnormal diagnostics count a symbol delisted after the target date.

The security master keeps a vanished symbol as delisted with the date of the refresh that first
missed it; a historical target still covers it, the delisting day and later do not.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.taiwan.universe.service import UniverseType
from tests.test_stage2_quality_gates import (
    ALL_SYMBOLS,
    ETF_SYMBOLS,
    TARGET,
    _abnormal_service,
    _daily_store,
    _industry_service,
)

pytestmark = pytest.mark.usefixtures("deny_network")

DELISTED = "1098.TWSE"


def _with_delisted(service, delisted_at: date):
    master = service.security_master
    frame = master.to_dataframe.return_value.with_columns(
        pl.when(pl.col("symbol") == DELISTED)
        .then(pl.lit("delisted"))
        .otherwise(pl.col("listing_status"))
        .alias("listing_status"),
        pl.when(pl.col("symbol") == DELISTED)
        .then(pl.lit(delisted_at.isoformat()))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("delisted_at"),
    )
    master.to_dataframe.return_value = frame
    active_stocks = sorted(s for s in ALL_SYMBOLS if s != DELISTED)
    master.get_universe.side_effect = lambda kind: {
        UniverseType.TAIWAN_STOCKS: active_stocks,
        UniverseType.TAIWAN_ETFS: sorted(ETF_SYMBOLS),
    }.get(kind, [])
    return service


@pytest.mark.parametrize(
    ("delisted_at", "expected"),
    [(TARGET + timedelta(days=1), 100), (TARGET, 99)],
    ids=["delisted_after_target", "delisted_on_target"],
)
def test_industry_counts_a_symbol_until_its_delisting(tmp_path, delisted_at, expected):
    service = _with_delisted(_industry_service(tmp_path, _daily_store(tmp_path)), delisted_at)

    snapshot = service.get_snapshot(TARGET)

    assert snapshot.data_quality.supported_stock_count == expected
    assert snapshot.data_quality.overall_status == "complete"


@pytest.mark.parametrize(
    ("delisted_at", "expected"),
    [(TARGET + timedelta(days=1), 100), (TARGET, 99)],
    ids=["delisted_after_target", "delisted_on_target"],
)
def test_abnormal_counts_a_symbol_until_its_delisting(tmp_path, delisted_at, expected):
    service = _with_delisted(_abnormal_service(tmp_path, _daily_store(tmp_path)), delisted_at)

    snapshot = service.get_diagnostics(TARGET)

    assert snapshot.universe_count == expected
    assert snapshot.data_quality.overall_status == "complete"


def test_abnormal_with_etfs_appends_only_delisted_products_of_the_requested_types(tmp_path):
    service = _with_delisted(
        _abnormal_service(tmp_path, _daily_store(tmp_path)), TARGET + timedelta(days=1)
    )

    snapshot = service.get_diagnostics(TARGET, include_etfs=True)

    assert snapshot.universe_count == 100 + len(ETF_SYMBOLS)
