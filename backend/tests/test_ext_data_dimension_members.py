import polars as pl
import pytest
from fastapi import HTTPException

from app.api.ext_data import _display_ext_label, _filter_dimension_member_rows
from app.services.ext_data import normalize_symbol
from app.taiwan.universe import TaiwanSecurityMaster


def test_filter_dimension_member_rows_matches_complete_tags() -> None:
    rows = pl.DataFrame({
        "symbol": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
        "所属概念": ["人工智能;芯片", "人工智能体;机器人", "芯片 / 人工智能", None],
    })

    result = _filter_dimension_member_rows(rows, "所属概念", "人工智能")

    assert result.get_column("symbol").to_list() == ["000001.SZ", "000003.SZ"]


def test_filter_dimension_member_rows_matches_industry_hierarchy() -> None:
    rows = pl.DataFrame({
        "symbol": ["000001.SZ", "000002.SZ", "000003.SZ"],
        "所属行业": ["金融-银行-股份制银行", "电子-半导体-数字芯片", "电子元件"],
    })

    result = _filter_dimension_member_rows(rows, "所属行业", "电子")

    assert result.get_column("symbol").to_list() == ["000002.SZ"]


def test_filter_dimension_member_rows_rejects_unknown_field() -> None:
    rows = pl.DataFrame({"symbol": ["000001.SZ"]})

    with pytest.raises(HTTPException, match="字段 '所属行业' 不存在"):
        _filter_dimension_member_rows(rows, "所属行业", "银行")


# TAIWAN_LOCALIZATION_POLISH follow-up — 已存在的 data/ext_data/*/config.json 可能
# 仍留有旧版简体 label ("扩展概念"/"扩展行业"); display 层需正规化为正体中文,
# 但不得连带影响未知的其他 label 值 (不是通用简繁转换)。
def test_display_ext_label_normalizes_known_legacy_labels() -> None:
    assert _display_ext_label("扩展概念") == "擴展概念"
    assert _display_ext_label("扩展行业") == "擴展行業"


def test_display_ext_label_passes_through_unknown_labels_unchanged() -> None:
    # 新建 config (ext_presets.py 已是正体默认) 与使用者自订 label 都应原样返回。
    assert _display_ext_label("擴展概念") == "擴展概念"
    assert _display_ext_label("我的自訂表") == "我的自訂表"
    assert _display_ext_label("") == ""


# Taiwan resolution path for app.services.ext_data.normalize_symbol — reference
# codes/names taken from tests/test_taiwan_symbol.py's REFERENCE_STOCKS.
_MASTER_ROWS = [
    {"symbol": "2330.TWSE", "code": "2330", "exchange": "TWSE", "name": "台積電",
     "instrument_type": "stock", "listing_status": "active"},
    {"symbol": "006208.TWSE", "code": "006208", "exchange": "TWSE", "name": "富邦台50",
     "instrument_type": "etf", "listing_status": "active"},
    {"symbol": "8069.TPEX", "code": "8069", "exchange": "TPEX", "name": "元太",
     "instrument_type": "stock", "listing_status": "active"},
]


@pytest.fixture
def taiwan_master(tmp_path, monkeypatch) -> TaiwanSecurityMaster:
    cache = tmp_path / "security_master.parquet"
    pl.DataFrame(_MASTER_ROWS).write_parquet(cache)
    master = TaiwanSecurityMaster(cache_path=cache)
    assert master.load_cache() is True
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: master)
    return master


def test_normalize_symbol_resolves_bare_taiwan_codes(taiwan_master) -> None:
    series = pl.Series(["2330", "006208", "8069", "0050.TWSE"])

    assert normalize_symbol(series).to_list() == [
        "2330.TWSE", "006208.TWSE", "8069.TPEX", "0050.TWSE",
    ]


def test_normalize_symbol_leaves_unresolvable_codes_unchanged(taiwan_master) -> None:
    series = pl.Series(["999999", "2330", ""])

    assert normalize_symbol(series).to_list() == ["999999", "2330.TWSE", ""]


def test_normalize_symbol_still_prefers_the_instruments_lookup(taiwan_master) -> None:
    series = pl.Series(["600519"])

    assert normalize_symbol(series, {"600519": "600519.SH"}).to_list() == ["600519.SH"]
