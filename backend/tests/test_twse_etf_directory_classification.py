"""TWSE OpenAPI ETF 目錄的槓桿/反向分類回歸測試。

_official_twse_etf_directory() 是 security master 的實際取數路徑 (html_content
為 None 時), 它曾經只看「是否包含國外成分股」分類, 於是把 00631L 歸成一般
domestic_equity 且 leverage_multiplier 停在 1.0 —— 法定漲跌幅因此被低估。
"""
from __future__ import annotations

import pytest

from app.taiwan.universe import adapters


def _row(code: str, name: str, fund_type: str, foreign: str = "否") -> dict:
    return {
        "基金代號": code,
        "基金簡稱": name,
        "基金類型": fund_type,
        "是否包含國外成分股": foreign,
        "上市日期": "20140000",
    }


@pytest.fixture
def _fake_directory(monkeypatch):
    def _install(rows):
        monkeypatch.setattr(adapters, "_get_json", lambda url: rows)
    return _install


def test_leveraged_etf_gets_multiplier_and_category(_fake_directory):
    _fake_directory([_row("00631L", "元大台灣50正2", "槓桿/反向指數股票型基金")])
    inst = adapters._official_twse_etf_directory()[0]
    assert inst.etf_category == "leveraged"
    assert inst.leverage_multiplier == 2.0
    assert inst.classification_source == "official_metadata"


def test_inverse_etf_gets_negative_multiplier(_fake_directory):
    _fake_directory([_row("00632R", "元大台灣50反1", "槓桿/反向指數股票型基金")])
    inst = adapters._official_twse_etf_directory()[0]
    assert inst.etf_category == "inverse"
    assert inst.leverage_multiplier == -1.0
    assert inst.classification_source == "official_metadata"


def test_plain_domestic_etf_is_unchanged(_fake_directory):
    _fake_directory([_row("0050", "元大台灣50", "國內成分證券指數股票型基金")])
    inst = adapters._official_twse_etf_directory()[0]
    assert inst.etf_category == "domestic_equity"
    assert inst.leverage_multiplier == 1.0
    assert inst.classification_source == "official_metadata"


def test_foreign_component_etf_is_unchanged(_fake_directory):
    _fake_directory([
        _row("00646", "元大S&P500", "國外成分證券指數股票型基金", foreign="是"),
    ])
    inst = adapters._official_twse_etf_directory()[0]
    assert inst.etf_category == "foreign_equity"
    assert inst.underlying_scope == "foreign"
    assert inst.leverage_multiplier == 1.0


def test_leveraged_price_limit_band_follows_multiplier(_fake_directory):
    """The statutory band is 10% * |multiplier|; this is why the bug mattered."""
    from app.taiwan.universe.models import MarketProfileBridge

    _fake_directory([
        _row("00631L", "元大台灣50正2", "槓桿/反向指數股票型基金"),
        _row("00632R", "元大台灣50反1", "槓桿/反向指數股票型基金"),
    ])
    by_code = {i.code: i for i in adapters._official_twse_etf_directory()}
    assert MarketProfileBridge.get_price_limit_pct(by_code["00631L"]) == pytest.approx(0.20)
    assert MarketProfileBridge.get_price_limit_pct(by_code["00632R"]) == pytest.approx(0.10)


def test_multiplier_helper_is_shared_with_provenance_classifier():
    """Both intake paths must agree, or the ISIN and OpenAPI routes drift."""
    for code, name, expected in (
        ("00631L", "元大台灣50正2", 2.0),
        ("00632R", "元大台灣50反1", -1.0),
        ("0050", "元大台灣50", 1.0),
    ):
        assert adapters.leverage_multiplier_of(code, name) == expected
        assert adapters.classify_etf_provenance(code, name, None, None)[3] == expected
