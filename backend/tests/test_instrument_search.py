"""标的搜索测试: 台股 TaiwanSecurityMaster 代码/canonical symbol/繁体名称查询。

直接调用 search_instruments, 用 monkeypatch 打桩 get_security_master, 不走 HTTP/DB。
"""
from __future__ import annotations

import pytest

from app.api.kline import search_instruments


class _FakeSecurityMaster:
    """最小台股证券主档桩: 直接按 symbol/code/name 子串匹配, 不依赖真实 adapter。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def search(self, q: str, limit: int = 20) -> list[dict]:
        keyword = q.strip().upper()
        hits = [
            row for row in self._rows
            if keyword in row["symbol"].upper()
            or keyword in row["code"].upper()
            or keyword in row["name"].upper()
        ]
        return hits[:limit]


_TAIWAN_ROWS = [
    {
        "symbol": "2330.TWSE", "code": "2330", "name": "台積電",
        "exchange": "TWSE", "instrument_type": "stock", "is_supported": True,
    },
    {
        "symbol": "6488.TPEX", "code": "6488", "name": "環球晶",
        "exchange": "TPEX", "instrument_type": "stock", "is_supported": True,
    },
    {
        "symbol": "0050.TWSE", "code": "0050", "name": "元大台灣50",
        "exchange": "TWSE", "instrument_type": "etf", "is_supported": True,
    },
    {
        # 未支援标的(如权证): 即使命中查询也不应出现在结果中。
        "symbol": "7999.TWSE", "code": "7999", "name": "測試權證",
        "exchange": "TWSE", "instrument_type": "warrant", "is_supported": False,
    },
]


def _patch_security_master(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
    fake = _FakeSecurityMaster(rows)
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: fake)


def test_empty_query_returns_empty(monkeypatch):
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    assert search_instruments(q="   ")["results"] == []


def test_finds_twse_symbol_by_code(monkeypatch):
    """q=2330 → 2330.TWSE, market 字段为 "taiwan"。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    rows = search_instruments(q="2330", limit=20)["results"]
    assert [r["symbol"] for r in rows] == ["2330.TWSE"]
    assert rows[0]["market"] == "taiwan"
    assert rows[0]["asset_type"] == "stock"


def test_finds_twse_symbol_by_canonical_symbol(monkeypatch):
    """q=2330.TWSE(完整 canonical symbol)也应命中。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    rows = search_instruments(q="2330.TWSE", limit=20)["results"]
    assert [r["symbol"] for r in rows] == ["2330.TWSE"]


def test_finds_tpex_symbol(monkeypatch):
    """q=6488 → 6488.TPEX(上柜)。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    rows = search_instruments(q="6488", limit=20)["results"]
    assert [r["symbol"] for r in rows] == ["6488.TPEX"]


def test_supports_chinese_name_search(monkeypatch):
    """支持繁体中文名称搜索(如"台積電")。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    rows = search_instruments(q="台積電", limit=20)["results"]
    assert [r["symbol"] for r in rows] == ["2330.TWSE"]


def test_excludes_unsupported_instrument_types(monkeypatch):
    """未支援标的(如权证 is_supported=False)不应出现在结果里。"""
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    rows = search_instruments(q="7999", limit=20)["results"]
    assert rows == []


def test_respects_limit(monkeypatch):
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    rows = search_instruments(q="TWSE", limit=1)["results"]
    assert len(rows) == 1
