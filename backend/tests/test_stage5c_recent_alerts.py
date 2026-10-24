"""Detail page alerts filter by symbol before truncating and read the configured data dir (B191)."""
from __future__ import annotations

import time

import pytest

from app.config import settings
from app.services import alert_store
from app.strategy import monitor_rules
from app.taiwan.detail_service import TaiwanStockDetailService


@pytest.fixture
def svc():
    return TaiwanStockDetailService()


def _event(symbol: str, ts: int, price: float = 10.0) -> dict:
    return {"ts": ts, "symbol": symbol, "rule_id": "r1", "rule_name": "rule", "type": "price",
            "severity": "warning", "price": price, "message": f"{symbol} hit", "source": "monitor"}


def test_alert_older_than_200_market_alerts_is_still_listed(svc, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    now = int(time.time() * 1000)
    events = [_event("1410.TWSE", now - 60_000)] + [_event("2330.TWSE", now - i) for i in range(250)]
    alert_store.append_many(tmp_path, events)

    alerts = svc._aggregate_recent_alerts("1410.TWSE")

    assert [a.message for a in alerts] == ["1410.TWSE hit"]
    assert alerts[0].alert_id == events[0]["id"]


def test_recent_alerts_read_the_configured_data_dir(svc, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    alert_store.append_many(tmp_path, [_event("1410.TWSE", int(time.time() * 1000))])

    assert len(svc._aggregate_recent_alerts("1410.TWSE")) == 1


def test_missing_data_dir_never_falls_back_to_a_relative_path(svc, monkeypatch):
    seen: list[object] = []
    monkeypatch.setattr(settings, "data_dir", None)
    monkeypatch.setattr(alert_store, "list_recent", lambda data_dir, **kw: seen.append(data_dir) or [])
    monkeypatch.setattr(monitor_rules, "load_all", lambda data_dir: seen.append(data_dir) or [])
    monkeypatch.setattr("app.config.Settings", lambda *a, **k: settings)

    assert svc._aggregate_recent_alerts("1410.TWSE") == []
    svc._aggregate_monitor_summary("1410.TWSE")

    assert seen == []
