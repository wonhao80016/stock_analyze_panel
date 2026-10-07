"""Taiwan depositary receipts carry an explicit security_class in the security master.

TWSE's company directory (t187ap03_L) files every TDR under 產業別 "91", the same ten codes the
ISIN table lists under 臺灣存託憑證(TDR); "91" is not an industry, so a TDR has no industry.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import polars as pl
import pytest

from app.taiwan.market_rules import ListingSessions
from app.taiwan.universe import TaiwanSecurityMaster, adapters
from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

SESSIONS = (
    date(2026, 9, 8),
    date(2026, 9, 9),
    date(2026, 9, 10),
    date(2026, 9, 11),
    date(2026, 9, 14),
    date(2026, 9, 15),
    date(2026, 9, 16),
    date(2026, 9, 17),
    date(2026, 9, 18),
)
CALENDAR = ListingSessions(SESSIONS)
WINDOW = SESSIONS[2:7]


def _directory(monkeypatch, exchange: str, rows: list[dict]) -> list[TaiwanInstrument]:
    monkeypatch.setattr(adapters, "_get_json", lambda url: rows)
    url = adapters.TWSE_COMPANIES_URL if exchange == "TWSE" else adapters.TPEX_COMPANIES_URL
    return adapters._official_company_directory(exchange, url)


def _stock(
    symbol: str, name: str, industry: str | None, listing_date: str = "2026-09-10"
) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=name,
        instrument_type="stock",
        listing_status="active",
        listing_date=listing_date,
        isin=None,
        industry=industry,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source="TWSE_OPENAPI",
        updated_at="2026-10-07T16:00:00+08:00",
        underlying_scope="domestic",
    )


def _legacy_file(tmp_path, rows: dict[str, list]) -> TaiwanSecurityMaster:
    path = tmp_path / "legacy_master.parquet"
    pl.DataFrame(rows).write_parquet(path)
    master = TaiwanSecurityMaster(cache_path=path)
    assert master.load_cache() is True
    return master


def test_directory_industry_91_marks_a_four_digit_tdr_without_dr_name(monkeypatch):
    [tdr] = _directory(
        monkeypatch,
        "TWSE",
        [{"公司代號": "9103", "公司簡稱": "美德醫療", "上市日期": "20260910", "產業別": "91"}],
    )
    assert tdr.security_class == "tdr"
    assert tdr.industry is None
    assert tdr.instrument_type == "stock"
    assert MarketProfileBridge.is_tdr(tdr) is True
    assert MarketProfileBridge.initial_listing_rule_applies(tdr) is False
    assert MarketProfileBridge.ipo_no_limit_window(tdr, CALENDAR) is None
    assert [MarketProfileBridge.get_price_limit_pct(tdr, d, CALENDAR) for d in WINDOW] == [0.10] * 5


def test_directory_ordinary_stock_has_no_security_class(monkeypatch):
    [twse] = _directory(
        monkeypatch,
        "TWSE",
        [{"公司代號": "1101", "公司簡稱": "台泥", "上市日期": "19620209", "產業別": "01"}],
    )
    [tpex] = _directory(
        monkeypatch,
        "TPEX",
        [
            {
                "SecuritiesCompanyCode": "6488",
                "CompanyAbbreviation": "環球晶",
                "DateOfListing": "20191218",
                "SecuritiesIndustryCode": "24",
            }
        ],
    )
    assert (twse.security_class, twse.industry) == (None, "水泥工業")
    assert (tpex.security_class, tpex.industry) == (None, "半導體業")
    assert MarketProfileBridge.is_tdr(twse) is False
    assert MarketProfileBridge.initial_listing_rule_applies(twse) is True


def test_explicit_security_class_exempts_the_initial_listing_rule():
    tdr = replace(_stock("1234.TWSE", "測試公司", None), security_class="tdr")
    assert MarketProfileBridge.is_tdr(tdr) is True
    assert MarketProfileBridge.ipo_no_limit_window(tdr, CALENDAR) is None
    ordinary = _stock("1234.TWSE", "測試公司", None)
    assert MarketProfileBridge.is_tdr(ordinary) is False
    assert MarketProfileBridge.ipo_no_limit_window(ordinary, CALENDAR) == (SESSIONS[2], SESSIONS[6])


@pytest.mark.parametrize("industry", ["未知產業\uff0891\uff09", "91"])
def test_legacy_file_with_industry_91_reads_as_tdr(tmp_path, industry):
    master = _legacy_file(
        tmp_path,
        {
            "symbol": ["9103.TWSE", "2330.TWSE"],
            "code": ["9103", "2330"],
            "exchange": ["TWSE", "TWSE"],
            "name": ["美德醫療", "台積電"],
            "instrument_type": ["stock", "stock"],
            "listing_status": ["active", "active"],
            "listing_date": ["20021213", "19940905"],
            "industry": [industry, "半導體業"],
            "is_supported": [True, True],
        },
    )
    tdr = master.get_instrument("9103.TWSE")
    assert (tdr.security_class, tdr.industry) == ("tdr", None)
    assert MarketProfileBridge.initial_listing_rule_applies(tdr) is False
    stock = master.get_instrument("2330.TWSE")
    assert (stock.security_class, stock.industry) == (None, "半導體業")
    assert MarketProfileBridge.initial_listing_rule_applies(stock) is True


def test_legacy_file_tdr_code_or_dr_name_reads_as_tdr(tmp_path):
    master = _legacy_file(
        tmp_path,
        {
            "symbol": ["910861.TWSE", "9136.TWSE"],
            "code": ["910861", "9136"],
            "exchange": ["TWSE", "TWSE"],
            "name": ["神州", "巨騰-DR"],
            "instrument_type": ["stock", "stock"],
            "listing_status": ["active", "active"],
            "industry": [None, None],
            "is_supported": [True, True],
        },
    )
    assert master.get_instrument("910861.TWSE").security_class == "tdr"
    assert master.get_instrument("9136.TWSE").security_class == "tdr"


def test_security_class_survives_a_save_and_load_roundtrip(tmp_path):
    source = TaiwanSecurityMaster(cache_path=tmp_path / "source.parquet")
    source._replace_instruments(
        {
            "9103.TWSE": replace(_stock("9103.TWSE", "美德醫療", None), security_class="tdr"),
            "2330.TWSE": _stock("2330.TWSE", "台積電", "半導體業"),
        }
    )
    path = source.save_cache()
    stored = pl.read_parquet(path)
    assert stored.schema["security_class"] == pl.String
    assert dict(zip(stored["symbol"], stored["security_class"], strict=True)) == {
        "9103.TWSE": "tdr",
        "2330.TWSE": None,
    }

    reloaded = TaiwanSecurityMaster(cache_path=path)
    assert reloaded.load_cache() is True
    assert reloaded.get_instrument("9103.TWSE").security_class == "tdr"
    assert reloaded.get_instrument("2330.TWSE").security_class is None
    assert reloaded.get_instrument("2330.TWSE").industry == "半導體業"


def test_legacy_file_without_the_column_loads(tmp_path):
    master = _legacy_file(
        tmp_path,
        {
            "symbol": ["2330.TWSE", "0050.TWSE"],
            "code": ["2330", "0050"],
            "exchange": ["TWSE", "TWSE"],
            "name": ["台積電", "元大台灣50"],
            "instrument_type": ["stock", "etf"],
            "listing_status": ["active", "active"],
            "industry": ["半導體業", None],
            "is_supported": [True, True],
        },
    )
    assert master.get_instrument("2330.TWSE").security_class is None
    assert master.get_instrument("0050.TWSE").security_class is None
    assert "security_class" in master.to_dataframe().columns
