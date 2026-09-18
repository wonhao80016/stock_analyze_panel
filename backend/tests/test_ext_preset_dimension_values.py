from __future__ import annotations

from app.services.ext_presets import _flatten_concept_rows, _flatten_industry_rows


def test_concept_flatten_drops_missing_value_placeholders():
    rows = _flatten_concept_rows([{
        "symbol": "600000.SH",
        "name": "浦发银行",
        "concepts": ["银行", "nan", None, float("nan"), " null ", "金融科技"],
    }])

    assert rows[0]["所属概念"] == "银行;金融科技"


def test_industry_flatten_drops_missing_value_placeholders():
    rows = _flatten_industry_rows([{
        "symbol": "600000.SH",
        "name": "浦发银行",
        "industries": ["金融", "None", "银行"],
    }])

    assert rows[0]["所属同花顺行业"] == "金融-银行"
