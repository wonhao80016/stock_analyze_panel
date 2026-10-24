"""Intraday signal monitoring is offered only when a custom minute plugin resolves."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.monitor_rules import router
from app.market_time import CN_TZ
from app.services import kline_sync
from app.services.kline_sync import fetch_intraday_monitor_batch, intraday_monitor_support
from app.strategy import monitor_rules
from app.tickflow.policy import detect_capabilities

NO_MINUTE_SOURCE = "台股沒有分鐘資料源, 分時訊號需要設定分鐘資料插件"


def _intraday_rule(rid: str = "intraday_rule") -> dict:
    return {
        "id": rid, "name": "分時監控", "enabled": True,
        "type": "signal", "asset_type": "stock", "scope": "symbols",
        "symbols": ["600000.SH"], "logic": "and",
        "conditions": [{"field": "signal_intraday_avg_cross_up", "op": "truth"}],
    }


@pytest.mark.parametrize("provider", ["taiwan", "tickflow", "missing_plugin"])
def test_support_is_unavailable_without_a_custom_minute_plugin(monkeypatch, provider):
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: provider)
    monkeypatch.setattr(
        kline_sync, "_resolve_minute_provider",
        lambda name: (None, True, "no such provider" if name == "missing_plugin" else None),
    )

    support = intraday_monitor_support(detect_capabilities())

    assert support["available"] is False
    assert support["source"] is None
    assert support["max_symbols"] == 0
    assert support["reason"] == NO_MINUTE_SOURCE


def test_support_is_available_with_a_custom_minute_plugin(monkeypatch):
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "my_minutes")
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda name: (object(), False, None))

    support = intraday_monitor_support(None)

    assert support["available"] is True
    assert support["source"] == "custom_minute"


def test_fetch_never_reaches_tickflow_without_a_custom_minute_plugin(monkeypatch):
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "taiwan")

    def _no_client():
        raise AssertionError("TickFlow client must not be used")

    monkeypatch.setattr(kline_sync, "get_client", _no_client)
    monkeypatch.setattr(kline_sync, "sync_minute_batch", lambda *a, **k: _no_client())

    result = fetch_intraday_monitor_batch(
        ["600000.SH"], detect_capabilities(), now=datetime(2026, 7, 17, 10, 0, tzinfo=CN_TZ),
    )
    assert result.is_empty()


class _FakeRepo:
    def __init__(self, data_dir):
        self.store = SimpleNamespace(data_dir=data_dir)

    def resolve_asset_type(self, symbol):
        return "stock"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "taiwan")
    app = FastAPI()
    app.include_router(router)
    app.state.repo = _FakeRepo(tmp_path)
    app.state.capabilities = detect_capabilities()
    return TestClient(app), tmp_path


def test_options_report_intraday_signals_unavailable_in_taiwan_mode(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr("app.strategy.custom_signals.load_all", lambda data_dir: [])

    support = http.get("/api/monitor-rules/options").json()["intraday_signal_support"]

    assert support["available"] is False
    assert support["reason"] == NO_MINUTE_SOURCE


def test_save_rejects_intraday_rule_in_taiwan_mode(client):
    http, data_dir = client

    resp = http.post("/api/monitor-rules", json=_intraday_rule())

    assert resp.status_code == 403
    assert resp.json()["detail"] == NO_MINUTE_SOURCE
    assert monitor_rules.load_one(data_dir, "intraday_rule") is None


def test_list_flags_saved_intraday_rule_with_runtime_warning(client):
    http, data_dir = client
    monitor_rules.save_one(data_dir, monitor_rules.normalize(_intraday_rule()))

    rules = http.get("/api/monitor-rules").json()["rules"]

    assert rules[0]["runtime_warning"] == NO_MINUTE_SOURCE


def test_custom_minute_plugin_path_still_fetches(monkeypatch):
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "my_minutes")
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda name: (object(), False, None))
    frame = pl.DataFrame({"symbol": ["600000.SH"], "close": [10.0]})
    monkeypatch.setattr(kline_sync, "sync_minute_batch", lambda *a, **k: frame)

    result = fetch_intraday_monitor_batch(
        ["600000.SH"], None, now=datetime(2026, 7, 17, 10, 0, tzinfo=CN_TZ),
    )
    assert result.equals(frame)
