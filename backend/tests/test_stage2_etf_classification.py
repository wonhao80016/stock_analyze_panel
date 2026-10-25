"""ETF classification in the security master must match the official TWSE/TPEx price-limit rules.

TWSE trading rules (https://www.twse.com.tw/zh/products/system/trading.html): domestic-component
ETFs, active ones included, have a 10% daily limit; foreign-component (incl. active), offshore and
futures-trust ETFs on foreign commodity futures have none. TPEx CFI codes carry no country.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.taiwan.enrichment.models import EtfCategory
from app.taiwan.market_rules import TaxClass
from app.taiwan.universe import adapters
from app.taiwan.universe.models import MarketProfileBridge

pytestmark = pytest.mark.usefixtures("deny_network")


def _row(code: str, name: str, fund_type: str, foreign: str) -> dict:
    return {
        "基金代號": code,
        "基金簡稱": name,
        "基金類型": fund_type,
        "是否包含國外成分股": foreign,
        "上市日期": "1150826",
    }


@pytest.fixture
def directory(monkeypatch):
    def _load(*rows: dict):
        monkeypatch.setattr(adapters, "_get_json", lambda url: list(rows))
        return {inst.code: inst for inst in adapters._official_twse_etf_directory()}

    return _load


def _tpex_html(*rows: tuple[str, str, str]) -> str:
    body = "".join(
        f"<tr><td>{code}　{name}</td><td>TW0000000000</td><td>2021/03/31</td>"
        f"<td>上櫃</td><td></td><td>{cfi}</td><td></td></tr>"
        for code, name, cfi in rows
    )
    return f'<table><tr><td colspan="7"><b>ETF</b></td></tr>{body}</table>'


def _tpex(*rows: tuple[str, str, str]):
    items = adapters.parse_isin_html(_tpex_html(*rows), exchange="TPEX", source_name="TPEX_ISIN")
    return {inst.code: inst for inst in items}


def test_domestic_active_equity_etf_has_ten_percent_limit(directory):
    inst = directory(
        _row("00981A", "主動統一台股增長", "國內成分證券主動式交易所交易基金(股票)", "否")
    )["00981A"]

    assert inst.classification_source == "official_metadata"
    assert inst.etf_category == "domestic_equity"
    assert inst.underlying_scope == "domestic"
    assert inst.etf_management_style == "active"
    assert MarketProfileBridge.get_price_limit_pct(inst) == pytest.approx(0.10)


@pytest.mark.parametrize(
    ("code", "name", "fund_type", "category"),
    [
        ("00983A", "主動中信ARK創新", "國外成分證券主動式交易所交易基金(股票)", "foreign_equity"),
        ("00982D", "主動富邦動態入息", "國外成分證券主動式交易所交易基金(債券)", "bond"),
        ("0080", "恒中國", "境外指數股票型基金", "foreign_equity"),
        ("0061", "元大寶滬深", "連結式證券指數股票型基金", "foreign_equity"),
        ("00981T", "平衡凱基雙核收息", "國外成分證券平衡型指數股票型基金", "foreign_equity"),
        ("00636K", "國泰中國A50+U", "國外成份/加掛外幣證券指數股票型基金", "foreign_equity"),
    ],
)
def test_foreign_component_fund_types_have_no_limit(directory, code, name, fund_type, category):
    inst = directory(_row(code, name, fund_type, "是"))[code]

    assert inst.classification_source == "official_metadata"
    assert inst.underlying_scope == "foreign"
    assert inst.etf_category == category
    assert MarketProfileBridge.get_price_limit_pct(inst) is None


def test_futures_trust_etf_is_futures_without_limit(directory):
    inst = directory(_row("00635U", "期元大S&P黃金", "指數股票型期貨信託基金", "是"))["00635U"]

    assert EtfCategory.FUTURES.value == "futures"
    assert inst.etf_category == "futures"
    assert inst.classification_source == "official_metadata"
    assert inst.leverage_multiplier == 1.0
    assert MarketProfileBridge.get_price_limit_pct(inst) is None
    assert MarketProfileBridge.get_tax_class(inst) == TaxClass.DOMESTIC_ETF


def test_inverse_futures_trust_etf_is_inverse_without_limit(directory):
    inst = directory(_row("00716R", "華頓S&P布蘭特反1", "指數股票型期貨信託基金", "是"))["00716R"]

    assert inst.etf_category == "inverse"
    assert inst.leverage_multiplier == -1.0
    assert inst.classification_source == "official_metadata"
    assert MarketProfileBridge.get_price_limit_pct(inst) is None


def test_missing_foreign_flag_keeps_new_fund_types_unconfirmed(directory):
    inst = directory(_row("00999A", "主動測試", "國內成分證券主動式交易所交易基金(股票)", ""))[
        "00999A"
    ]

    assert inst.classification_source is None
    with pytest.raises(ValueError):
        MarketProfileBridge.get_price_limit_pct(inst)


def test_ceogeu_alone_does_not_confirm_domestic_scope():
    inst = _tpex(("00999", "測試全球ETF", "CEOGEU"))["00999"]

    assert adapters.classify_etf_provenance("00877", "復華中國5G", "CEOGEU") == (
        "unknown",
        "unknown",
        "unknown",
        1.0,
    )
    assert inst.classification_source == "unknown"
    assert inst.underlying_scope == "unknown"


def test_unaudited_ceogeu_etf_is_refused():
    inst = _tpex(("00999", "測試全球ETF", "CEOGEU"))["00999"]

    with pytest.raises(ValueError):
        MarketProfileBridge.get_price_limit_pct(inst)


@pytest.mark.parametrize(
    ("code", "name", "cfi", "expected"),
    [
        ("006201", "元大富櫃50", "CEOGEU", 0.10),
        ("00888", "永豐台灣ESG", "CEOIEU", 0.10),
        ("00928", "中信上櫃ESG 30", "CEOIEU", 0.10),
        ("00877", "復華中國5G", "CEOGEU", None),
        ("00886", "永豐美國科技", "CEOGEU", None),
        ("00887", "永豐中國科技50大", "CEOGEU", None),
        ("009822", "華南永昌未來金融", "CEOGEU", None),
    ],
)
def test_audited_tpex_etfs_use_official_limit(code, name, cfi, expected):
    inst = _tpex((code, name, cfi))[code]

    pct = MarketProfileBridge.get_price_limit_pct(inst)
    if expected is None:
        assert pct is None
    else:
        assert pct == pytest.approx(expected)


def test_ceogbu_is_bond_without_limit():
    inst = _tpex(("00694B", "富邦美債1-3年", "CEOGBU"))["00694B"]

    assert (inst.etf_category, inst.classification_source) == ("bond", "cfi_code")
    assert MarketProfileBridge.get_price_limit_pct(inst) is None


@pytest.mark.parametrize(
    ("code", "name", "category", "multiplier"),
    [
        ("00635U", "期元大S&P黃金", "futures", 1.0),
        ("00682U", "期元大美元指數", "futures", 1.0),
        ("00716R", "華頓S&P布蘭特反1", "inverse", -1.0),
    ],
)
def test_futures_trust_scope_comes_from_foreign_flag_on_both_paths(
    directory, code, name, category, multiplier
):
    meta = adapters.OfficialEtfProductMeta("指數股票型期貨信託基金", "美元指數", "foreign")
    via_isin = adapters.classify_etf_provenance(code, name, None, {code: meta})
    via_directory = directory(_row(code, name, "指數股票型期貨信託基金", "是"))[code]

    assert via_isin == (category, "official_metadata", "foreign", multiplier)
    assert (
        via_directory.etf_category,
        via_directory.classification_source,
        via_directory.underlying_scope,
        via_directory.leverage_multiplier,
    ) == via_isin


@pytest.mark.parametrize(("flag", "scope"), [("否", "domestic"), ("", "unknown")])
def test_futures_trust_without_foreign_flag_fails_closed(directory, flag, scope):
    inst = directory(_row("00682U", "期元大美元指數", "指數股票型期貨信託基金", flag))["00682U"]
    meta = adapters.OfficialEtfProductMeta("指數股票型期貨信託基金", "美元指數", scope)

    assert inst.classification_source is None
    assert adapters.classify_etf_provenance("00682U", "期元大美元指數", None, {"00682U": meta})[
        1
    ] == ("unknown")
    with pytest.raises(ValueError):
        MarketProfileBridge.get_price_limit_pct(inst)


@pytest.mark.parametrize(
    ("code", "name", "cfi", "category", "scope"),
    [
        ("006201", "元大富櫃50", "CEOGEU", "domestic_equity", "domestic"),
        ("00888", "永豐台灣ESG", "CEOIEU", "domestic_equity", "domestic"),
        ("00877", "復華中國5G", "CEOGEU", "foreign_equity", "foreign"),
        ("009822", "華南永昌未來金融", "CEOGEU", "foreign_equity", "foreign"),
    ],
)
def test_audited_profile_backfills_master_identity(code, name, cfi, category, scope):
    inst = _tpex((code, name, cfi))[code]

    assert (inst.etf_category, inst.underlying_scope, inst.classification_source) == (
        category,
        scope,
        "official_metadata",
    )


def test_domestic_bond_etf_follows_domestic_ten_percent_rule():
    inst = _tpex(("00694B", "富邦美債1-3年", "CEOGBU"))["00694B"]
    domestic = dataclasses.replace(inst, underlying_scope="domestic")

    assert MarketProfileBridge.get_price_limit_pct(inst) is None
    assert MarketProfileBridge.get_price_limit_pct(domestic) == pytest.approx(0.10)


def test_bond_scope_follows_foreign_flag_on_both_paths(directory):
    fund_type = "債券成分證券指數股票型基金"
    meta = adapters.OfficialEtfProductMeta(fund_type, "臺灣公債指數", "domestic")
    via_isin = adapters.classify_etf_provenance("00999B", "測試國內債", None, {"00999B": meta})
    via_directory = directory(_row("00999B", "測試國內債", fund_type, "否"))["00999B"]

    assert via_isin == ("bond", "official_metadata", "domestic", 1.0)
    assert (via_directory.etf_category, via_directory.underlying_scope) == ("bond", "domestic")
    assert MarketProfileBridge.get_price_limit_pct(via_directory) == pytest.approx(0.10)


def test_bond_without_flag_keeps_foreign_default_on_isin_path():
    meta = adapters.OfficialEtfProductMeta("債券成分證券指數股票型基金", "美國公債指數")

    assert (
        adapters.classify_etf_provenance("00999B", "測試美債", None, {"00999B": meta})[2]
        == "foreign"
    )


@pytest.mark.parametrize("code", ["00411A", "00858", "009806", "00998A"])
def test_tpex_foreign_component_list_is_audited_no_limit(code):
    inst = _tpex((code, "測試", "CEOIEU"))[code]

    assert (inst.etf_category, inst.underlying_scope, inst.classification_source) == (
        "foreign_equity",
        "foreign",
        "official_metadata",
    )
    assert MarketProfileBridge.get_price_limit_pct(inst) is None
