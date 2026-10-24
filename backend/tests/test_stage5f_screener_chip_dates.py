"""Screener chip columns all come from one market-wide data date.

Institutional flows follow T86: on a date that holds the symbol's exchange, a missing row is
zero; on a date without that exchange the flow is unknown. A 5-session sum is null when any
session of the window lacks the exchange. Margin rows are never zero-filled.
"""
from __future__ import annotations

import polars as pl
import pytest

from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from tests.test_taiwan_screener import tmp_store  # noqa: F401


def _inst_rows(symbol, days, foreign):
    return [
        {
            "symbol": symbol,
            "date": d,
            "trade_date": d,
            "foreign_net": f,
            "investment_trust_net": f // 10,
            "dealer_net": 1,
            "status": "available",
            "source": "test",
        }
        for d, f in zip(days, foreign, strict=True)
    ]


def _margin_rows(symbol, days, balance):
    return [
        {
            "symbol": symbol,
            "date": d,
            "trade_date": d,
            "margin_balance": b,
            "margin_change": 5,
            "short_balance": 50,
            "short_margin_ratio": 5.0,
            "status": "available",
            "source": "test",
        }
        for d, b in zip(days, balance, strict=True)
    ]


@pytest.fixture
def chip_days(tmp_store):  # noqa: F811
    return tmp_store.available_dates()[-6:]


def _service(tmp_store, tmp_path, inst_rows, margin_rows):  # noqa: F811
    inst = TaiwanInstitutionalStore(data_dir=tmp_path / "inst")
    margin = TaiwanMarginStore(data_dir=tmp_path / "margin")
    inst.write_batch(pl.DataFrame(inst_rows))
    margin.write_batch(pl.DataFrame(margin_rows))
    return TaiwanScreenerService(daily_store=tmp_store, institutional_store=inst, margin_store=margin)


@pytest.fixture
def chip_service(tmp_store, tmp_path, chip_days):  # noqa: F811
    days = chip_days
    svc = _service(
        tmp_store,
        tmp_path,
        _inst_rows("2330.TWSE", days, [100, 200, 300, 400, 500, 600])
        # 0050 stops one session early while TWSE still has a table: zero that day.
        + _inst_rows("0050.TWSE", days[:-1], [7, 7, 7, 7, 9_000_000])
        # 8069 is the only TPEx row, so days[2] has no TPEx table at all.
        + _inst_rows("8069.TPEX", days[:2] + days[3:], [10, 20, 40, 50, 60]),
        _margin_rows("2330.TWSE", days, [1000] * 6)
        + _margin_rows("0050.TWSE", days[:-1], [2000] * 5)
        + _margin_rows("8069.TPEX", days, [10] * 6),
    )
    return svc, days


def _items(res):
    return {i.symbol: i for i in res.items}


def test_symbol_missing_from_a_stored_exchange_table_has_zero_flows(chip_service):
    svc, days = chip_service
    items = _items(svc.run(TaiwanScreenerRequest()))

    etf = items["0050.TWSE"]
    assert (etf.foreign_net, etf.investment_trust_net, etf.dealer_net) == (0, 0, 0)
    assert etf.institutional_date == str(days[-1])
    assert etf.institutional_status == "available"

    tsmc = items["2330.TWSE"]
    assert tsmc.foreign_net == 600
    assert tsmc.institutional_date == str(days[-1])
    assert tsmc.margin_date == str(days[-1])


def test_margin_row_missing_on_the_chip_date_stays_null(chip_service):
    svc, _ = chip_service
    etf = _items(svc.run(TaiwanScreenerRequest()))["0050.TWSE"]
    assert etf.margin_balance is None
    assert etf.margin_date is None
    assert etf.margin_status == "unavailable"


def test_chip_filters_never_match_an_older_row(chip_service):
    svc, _ = chip_service
    res = svc.run(TaiwanScreenerRequest(foreign_net_min=1_000_000))
    assert res.total == 0


def test_five_session_sums_share_one_window(chip_service):
    svc, _ = chip_service
    res = svc.run(TaiwanScreenerRequest())
    items = _items(res)

    assert items["2330.TWSE"].foreign_net_5d == 200 + 300 + 400 + 500 + 600
    assert items["2330.TWSE"].investment_trust_net_5d == 20 + 30 + 40 + 50 + 60
    assert items["0050.TWSE"].foreign_net_5d == 7 + 7 + 7 + 9_000_000 + 0
    # The window holds a session without any TPEx table.
    assert items["8069.TPEX"].foreign_net == 60
    assert items["8069.TPEX"].foreign_net_5d is None
    assert items["8069.TPEX"].investment_trust_net_5d is None
    assert res.degraded_sections == ["institutional_window_missing_tpex"]


def test_flags_cover_only_the_requested_exchanges(chip_service):
    svc, _ = chip_service
    assert svc.run(TaiwanScreenerRequest(exchange="TWSE")).degraded_sections == []
    assert svc.run(TaiwanScreenerRequest(exchange="TPEX")).degraded_sections == ["institutional_window_missing_tpex"]


def test_exchange_missing_on_the_chip_date_is_unknown_and_flagged(tmp_store, tmp_path, chip_days):  # noqa: F811
    days = chip_days
    svc = _service(
        tmp_store,
        tmp_path,
        _inst_rows("2330.TWSE", days, [1] * 6) + _inst_rows("8069.TPEX", days[:-1], [5] * 5),
        _margin_rows("2330.TWSE", days[:-1], [1] * 5) + _margin_rows("8069.TPEX", days, [1] * 6),
    )
    res = svc.run(TaiwanScreenerRequest())
    items = _items(res)

    tpex = items["8069.TPEX"]
    assert tpex.foreign_net is None
    assert tpex.institutional_date is None
    assert tpex.foreign_net_5d is None
    assert items["2330.TWSE"].foreign_net == 1
    assert items["2330.TWSE"].margin_balance is None
    assert items["8069.TPEX"].margin_balance == 1
    assert "institutional_missing_tpex" in res.degraded_sections
    assert "margin_missing_twse" in res.degraded_sections
    assert "institutional_missing_twse" not in res.degraded_sections


def test_missing_exchange_flags_follow_the_exchange_filter(tmp_store, tmp_path, chip_days):  # noqa: F811
    days = chip_days
    svc = _service(
        tmp_store,
        tmp_path,
        _inst_rows("2330.TWSE", days, [1] * 6) + _inst_rows("8069.TPEX", days[:-1], [5] * 5),
        _margin_rows("2330.TWSE", days[:-1], [1] * 5) + _margin_rows("8069.TPEX", days, [1] * 6),
    )
    assert svc.run(TaiwanScreenerRequest(exchange="TWSE")).degraded_sections == ["margin_missing_twse"]
    assert svc.run(TaiwanScreenerRequest(exchange="TPEX")).degraded_sections == ["institutional_missing_tpex"]
