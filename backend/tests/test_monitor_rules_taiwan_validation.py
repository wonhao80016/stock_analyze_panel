"""监控规则 API 的台股 Security Master 存在性/可交易性校验 (A2)。

背景: A 引擎 (taiwan/realtime/monitor_engine.py) 建规则时会验证标的存在且可交易,
B 引擎的 API 层此前只检查 symbols 非空。绑定不存在或不可交易 (下市/权证等) 的
标的时规则会永远无法触发 (静默失效), 因此在 API 层补上与 A 等价的校验。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.monitor_rules import router
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument


@pytest.fixture
def mock_sec_master(tmp_path):
    master = TaiwanSecurityMaster(cache_path=tmp_path / "sec_master.parquet")
    tsmc = TaiwanInstrument(
        symbol="2330.TWSE", code="2330", exchange="TWSE", name="台積電",
        instrument_type="stock", listing_status="active", is_supported=True,
        listing_date="1994/09/05", isin="TW0002330008", industry="半導體業",
        cfi_code="ESVUFR", raw_category="股票", source="TWSE_ISIN", updated_at="2026-08-30",
    )
    warrant = TaiwanInstrument(
        symbol="03001.TWSE", code="03001", exchange="TWSE", name="台積電認購01",
        instrument_type="warrant", listing_status="active", is_supported=False,
        listing_date="2020/01/01", isin="TW0000300100", industry=None,
        cfi_code="RWST", raw_category="認購權證", source="TWSE_ISIN", updated_at="2026-08-30",
    )
    master._instruments = {tsmc.symbol: tsmc, warrant.symbol: warrant}
    master._loaded = True
    return master


class _FakeRepo:
    def __init__(self, data_dir):
        self.store = SimpleNamespace(data_dir=data_dir)

    def resolve_asset_type(self, symbol):
        return "stock"


@pytest.fixture
def client(tmp_path, mock_sec_master, monkeypatch):
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: mock_sec_master)
    app = FastAPI()
    app.include_router(router)
    app.state.repo = _FakeRepo(tmp_path)
    return TestClient(app)


def _price_rule(rid: str, symbol: str) -> dict:
    return {
        "id": rid, "name": rid, "type": "price", "asset_type": "stock",
        "scope": "symbols", "symbols": [symbol], "logic": "and",
        "conditions": [{"field": "close", "op": ">", "value": 10}],
        "cooldown_seconds": 0,
    }


def test_save_rule_rejects_nonexistent_taiwan_symbol(client):
    resp = client.post("/api/monitor-rules", json=_price_rule("r1", "9999.TWSE"))
    assert resp.status_code == 400
    assert "9999.TWSE" in resp.json()["detail"]


def test_save_rule_rejects_unsupported_taiwan_symbol(client):
    resp = client.post("/api/monitor-rules", json=_price_rule("r2", "03001.TWSE"))
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "03001.TWSE" in detail
    assert "warrant" in detail


def test_save_rule_accepts_supported_taiwan_symbol(client):
    resp = client.post("/api/monitor-rules", json=_price_rule("r3", "2330.TWSE"))
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_save_rule_non_taiwan_symbol_bypasses_security_master(client):
    """A 股格式 symbol 不受台股 Security Master 管辖, 不应被这项新校验挡下。"""
    resp = client.post("/api/monitor-rules", json=_price_rule("r4", "600000.SH"))
    assert resp.status_code == 200
