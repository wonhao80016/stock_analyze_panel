"""Security-master listing dates are ISO YYYY-MM-DD whatever official format they arrive in."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import polars as pl
import pytest

from app.taiwan.market_rules import ListingSessions
from app.taiwan.universe import TaiwanSecurityMaster, adapters
from app.taiwan.universe.models import ListingDateUnknownError, MarketProfileBridge

pytestmark = pytest.mark.usefixtures("deny_network")

SESSIONS = ListingSessions(
    (
        date(2026, 8, 26),
        date(2026, 8, 27),
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    )
)


def _company_directory(monkeypatch, listed: str):
    monkeypatch.setattr(
        adapters,
        "_get_json",
        lambda url: [
            {"公司代號": "2454", "公司簡稱": "聯發科", "上市日期": listed, "產業別": "24"},
        ],
    )
    return adapters._official_company_directory("TWSE", adapters.TWSE_COMPANIES_URL)[0]


def _twse_etf_directory(monkeypatch, listed: str):
    monkeypatch.setattr(
        adapters,
        "_get_json",
        lambda url: [
            {
                "基金代號": "0050",
                "基金簡稱": "元大台灣50",
                "上市日期": listed,
                "基金類型": "國內成分證券指數股票型基金",
                "是否包含國外成分股": "否",
            }
        ],
    )
    return adapters._official_twse_etf_directory()[0]


def _isin_html(listed: str):
    html = f"""
    <table class="h4">
      <tr><td colspan="7"><b>ETF</b></td></tr>
      <tr><td>00999　測試ETF</td><td>TW0000099900</td><td>{listed}</td><td>上櫃</td><td></td><td>CEOIBU</td><td></td></tr>
    </table>
    """
    return adapters.parse_isin_html(html, exchange="TPEX", source_name="TPEX_ISIN")[0]


def test_company_directory_yyyymmdd_is_iso(monkeypatch):
    assert _company_directory(monkeypatch, "20010723").listing_date == "2001-07-23"


def test_twse_etf_directory_roc_date_is_iso(monkeypatch):
    assert _twse_etf_directory(monkeypatch, "0920630").listing_date == "2003-06-30"


def test_isin_html_slash_date_is_iso():
    assert _isin_html("2026/08/26").listing_date == "2026-08-26"


@pytest.mark.parametrize("raw", ["abc", "", "20261340"])
def test_unparseable_listing_date_is_none_on_every_write_path(monkeypatch, raw):
    assert _company_directory(monkeypatch, raw).listing_date is None
    assert _twse_etf_directory(monkeypatch, raw).listing_date is None
    assert _isin_html(raw).listing_date is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1150826", "2026-08-26"),
        (" 20010723 ", "2001-07-23"),
        ("2026-08-26", "2026-08-26"),
        (None, None),
        (float("nan"), None),
    ],
)
def test_normalize_listing_date_edge_inputs(raw, expected):
    assert adapters.normalize_listing_date(raw) == expected


def test_unknown_listing_date_still_blocks_the_ipo_limit_rule(monkeypatch):
    unknown = _company_directory(monkeypatch, "abc")
    with pytest.raises(ListingDateUnknownError):
        MarketProfileBridge.get_price_limit_pct(
            unknown, trade_date=date(2026, 8, 27), sessions=SESSIONS
        )


def test_iso_listing_date_places_the_ipo_window(monkeypatch):
    listed = _company_directory(monkeypatch, "20260826")
    assert listed.listing_date == "2026-08-26"
    window = [
        MarketProfileBridge.get_price_limit_pct(listed, trade_date=d, sessions=SESSIONS)
        for d in SESSIONS.trading_dates
    ]
    assert window == [None] * 5 + [0.10] * 2


def test_old_cache_reads_back_iso_without_rewriting(tmp_path, monkeypatch):
    template = _company_directory(monkeypatch, "20010723")
    raw_dates = {
        "2454.TWSE": "20010723",
        "0050.TWSE": "0920630",
        "00999.TPEX": "2026/08/26",
        "2330.TWSE": "1994-09-05",
        "1101.TWSE": "abc",
        "1102.TWSE": None,
    }
    rows = [
        replace(template, symbol=symbol, code=symbol.split(".")[0], listing_date=raw).to_dict()
        for symbol, raw in raw_dates.items()
    ]
    cache = tmp_path / "security_master.parquet"
    pl.DataFrame(rows).write_parquet(cache)
    original = cache.read_bytes()

    master = TaiwanSecurityMaster(cache_path=cache)
    assert master.load_cache() is True

    assert {symbol: master.get_instrument(symbol).listing_date for symbol in raw_dates} == {
        "2454.TWSE": "2001-07-23",
        "0050.TWSE": "2003-06-30",
        "00999.TPEX": "2026-08-26",
        "2330.TWSE": "1994-09-05",
        "1101.TWSE": None,
        "1102.TWSE": None,
    }
    assert cache.read_bytes() == original
