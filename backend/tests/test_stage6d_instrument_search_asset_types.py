"""/api/kline/instruments/search must filter by asset_types and serve benchmark indices."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.kline import search_instruments


def _symbols(q: str, asset_types: str) -> list[str]:
    rows = search_instruments(q=q, limit=20, asset_types=asset_types)["results"]
    return [r["symbol"] for r in rows]


def test_index_search_by_name_returns_benchmark():
    rows = search_instruments(q="加權", limit=20, asset_types="index")["results"]

    assert [r["symbol"] for r in rows] == ["t00.TWSE"]
    assert rows[0]["asset_type"] == "index"
    assert rows[0]["market"] == "taiwan"


def test_index_search_by_code_returns_benchmark():
    assert _symbols("o00", "index") == ["o00.TPEX"]


def test_index_search_excludes_stocks_and_etfs():
    assert _symbols("台灣50", "index") == []


def test_stock_search_excludes_etfs():
    assert _symbols("0050", "stock") == []
    assert _symbols("2330", "stock") == ["2330.TWSE"]


def test_stock_and_etf_search_includes_etfs():
    assert _symbols("0050", "stock,etf") == ["0050.TWSE"]


def test_filtered_search_is_not_starved_by_higher_ranked_other_types():
    rows = search_instruments(q="0", limit=1, asset_types="stock")["results"]

    assert [r["asset_type"] for r in rows] == ["stock"]


def test_etf_only_search_excludes_stocks():
    assert "2330.TWSE" not in _symbols("2330", "etf")


def test_unknown_asset_type_is_rejected():
    with pytest.raises(HTTPException) as ei:
        search_instruments(q="2330", limit=20, asset_types="bond")
    assert ei.value.status_code == 400


def test_http_query_parameter_is_applied():
    from app.main import app

    response = TestClient(app).get(
        "/api/kline/instruments/search", params={"q": "櫃買", "asset_types": "index"}
    )

    assert response.status_code == 200
    assert [r["symbol"] for r in response.json()["results"]] == ["o00.TPEX"]
