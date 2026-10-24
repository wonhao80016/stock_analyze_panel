"""Benchmark index rules (t00.TWSE, o00.TPEX) are not in the security master but must save and evaluate."""
from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.monitor_rules import router
from app.strategy import monitor_rules
from app.strategy.monitor import MonitorRuleEngine


class _FakeRepo:
    def __init__(self, data_dir):
        self.store = SimpleNamespace(data_dir=data_dir)

    def resolve_asset_type(self, symbol):
        return "index" if symbol in {"t00.TWSE", "o00.TPEX"} else "stock"


@pytest.fixture
def client(tmp_path, monkeypatch):
    master = SimpleNamespace(get_instrument=lambda symbol: None)
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: master)
    app = FastAPI()
    app.include_router(router)
    app.state.repo = _FakeRepo(tmp_path)
    app.state.monitor_engine = MonitorRuleEngine()
    return TestClient(app), tmp_path, app.state.monitor_engine


def _index_rule(symbol: str) -> dict:
    return {
        "id": "r_index", "name": "加權指數", "type": "price", "asset_type": "index",
        "scope": "symbols", "symbols": [symbol], "logic": "and",
        "conditions": [{"field": "change_pct", "op": ">=", "value": 0.01}],
        "cooldown_seconds": 0,
    }


@pytest.mark.parametrize("symbol", ["t00.TWSE", "o00.TPEX"])
def test_benchmark_index_rule_saves(client, symbol):
    http, data_dir, _ = client

    resp = http.post("/api/monitor-rules", json=_index_rule(symbol))

    assert resp.status_code == 200, resp.json()
    assert monitor_rules.load_one(data_dir, "r_index")["symbols"] == [symbol]


def test_unknown_taiwan_symbol_is_still_rejected(client):
    http, _, _ = client

    resp = http.post("/api/monitor-rules", json={**_index_rule("9999.TWSE"), "asset_type": "stock"})

    assert resp.status_code == 400


def test_saved_benchmark_rule_fires_on_the_index_round(client):
    http, _, engine = client
    assert http.post("/api/monitor-rules", json=_index_rule("t00.TWSE")).status_code == 200

    index_frame = pl.DataFrame({
        "symbol": ["t00.TWSE", "o00.TPEX"], "close": [23000.0, 260.0], "change_pct": [0.012, 0.0],
    })
    assert engine.evaluate(index_frame, asset_type="stock") == []
    events = engine.evaluate(index_frame, asset_type="index")

    assert [e["symbol"] for e in events] == ["t00.TWSE"]


def test_benchmark_symbol_case_is_normalised(client):
    http, data_dir, engine = client

    resp = http.post("/api/monitor-rules", json=_index_rule("T00.twse"))

    assert resp.status_code == 200, resp.json()
    assert monitor_rules.load_one(data_dir, "r_index")["symbols"] == ["t00.TWSE"]
    frame = pl.DataFrame({"symbol": ["t00.TWSE"], "close": [23000.0], "change_pct": [0.012]})
    assert [e["symbol"] for e in engine.evaluate(frame, asset_type="index")] == ["t00.TWSE"]


@pytest.mark.parametrize(("asset_type", "symbols"), [
    ("stock", ["2330.TWSE", "t00.TWSE"]),
    ("etf", ["o00.TPEX"]),
])
def test_benchmark_symbol_outside_an_index_rule_is_rejected(client, asset_type, symbols):
    http, data_dir, _ = client

    resp = http.post("/api/monitor-rules", json={**_index_rule("t00.TWSE"), "asset_type": asset_type, "symbols": symbols})

    assert resp.status_code == 400
    assert "指數" in resp.json()["detail"]
    assert monitor_rules.load_one(data_dir, "r_index") is None


_TAIEX_ONLY_PRICE = "台股指數只支援價格與漲跌幅條件"


@pytest.mark.parametrize("condition", [
    {"field": "signal_ma_golden_5_20", "op": "truth"},
    {"field": "ma20", "op": ">", "value": 100},
    {"field": "volume", "op": ">=", "value": 1000},
])
def test_taiwan_index_rule_rejects_signal_indicator_and_volume_conditions(client, condition):
    http, data_dir, _ = client

    resp = http.post("/api/monitor-rules", json={**_index_rule("t00.TWSE"), "type": "signal", "conditions": [condition]})

    assert resp.status_code == 400
    assert _TAIEX_ONLY_PRICE in resp.json()["detail"]
    assert monitor_rules.load_one(data_dir, "r_index") is None


@pytest.mark.parametrize("condition", [
    {"field": "close", "op": ">=", "value": 23000},
    {"field": "change_pct", "op": ">=", "value": 0.01},
])
def test_taiwan_index_rule_accepts_price_and_change_conditions(client, condition):
    http, _, _ = client

    resp = http.post("/api/monitor-rules", json={**_index_rule("o00.TPEX"), "conditions": [condition]})

    assert resp.status_code == 200, resp.json()


def test_saved_taiwan_index_rule_with_indicator_is_flagged_in_list(client):
    http, data_dir, _ = client
    monitor_rules.save_one(data_dir, monitor_rules.normalize({
        **_index_rule("t00.TWSE"), "conditions": [{"field": "ma20", "op": ">", "value": 100}],
    }))

    [rule] = http.get("/api/monitor-rules").json()["rules"]

    assert _TAIEX_ONLY_PRICE in rule["runtime_warning"]
