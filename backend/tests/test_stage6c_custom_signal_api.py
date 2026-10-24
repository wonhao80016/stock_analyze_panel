"""Custom signal API: create versus update, references from monitor rules, field units."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import signals as signals_api
from app.strategy import custom_signals


def _signal(signal_id: str = "low_touches_ma5", name: str = "跌至MA5", **extra) -> dict:
    return {
        "id": signal_id,
        "name": name,
        "kind": "entry",
        "conditions": [{"left": "low", "op": "<=", "right": "field:ma5", "leftDays": 0, "rightDays": 0}],
        "enabled": True,
        **extra,
    }


def _rule(rule_id: str, field: str) -> dict:
    return {
        "id": rule_id,
        "name": f"規則 {rule_id}",
        "type": "signal",
        "scope": "all",
        "conditions": [{"field": field, "op": "truth"}],
    }


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(signals_api.router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path), clear_cache=lambda: None)
    with TestClient(app) as test_client:
        test_client.data_dir = tmp_path
        yield test_client


def _write_rule(data_dir, rule: dict) -> None:
    rules_dir = data_dir / "user_data" / "monitor_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / f"{rule['id']}.json").write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")


# ── B013 ────────────────────────────────────────────────


def test_creating_a_signal_with_an_existing_id_is_a_conflict_and_keeps_the_old_one(client):
    assert client.post("/api/custom-signals", json=_signal(name="舊訊號")).status_code == 200

    response = client.post("/api/custom-signals", json=_signal(name="新訊號"))

    assert response.status_code == 409
    saved = custom_signals.load_all(client.data_dir)
    assert [s["name"] for s in saved] == ["舊訊號"]


def test_updating_an_existing_signal_goes_through_put(client):
    client.post("/api/custom-signals", json=_signal(name="舊訊號"))

    response = client.put("/api/custom-signals/low_touches_ma5", json=_signal(name="新訊號"))

    assert response.status_code == 200
    assert [s["name"] for s in custom_signals.load_all(client.data_dir)] == ["新訊號"]


def test_put_refuses_a_missing_signal_and_a_mismatched_id(client):
    assert client.put("/api/custom-signals/low_touches_ma5", json=_signal()).status_code == 404
    client.post("/api/custom-signals", json=_signal())
    response = client.put("/api/custom-signals/low_touches_ma5", json=_signal(signal_id="other"))
    assert response.status_code == 400


# ── B014 ────────────────────────────────────────────────


def test_references_lists_the_monitor_rules_using_the_signal(client):
    client.post("/api/custom-signals", json=_signal())
    _write_rule(client.data_dir, _rule("uses_it", "csg_low_touches_ma5"))
    _write_rule(client.data_dir, _rule("other_signal", "csg_low_touches_ma50"))

    response = client.get("/api/custom-signals/low_touches_ma5/references")

    assert response.status_code == 200
    assert response.json()["monitor_rules"] == [{"id": "uses_it", "name": "規則 uses_it"}]


def test_deleting_a_referenced_signal_is_refused_with_the_rule_list(client):
    client.post("/api/custom-signals", json=_signal())
    _write_rule(client.data_dir, _rule("uses_it", "csg_low_touches_ma5"))

    response = client.delete("/api/custom-signals/low_touches_ma5")

    assert response.status_code == 409
    assert response.json()["detail"]["monitor_rules"] == [{"id": "uses_it", "name": "規則 uses_it"}]
    assert [s["id"] for s in custom_signals.load_all(client.data_dir)] == ["low_touches_ma5"]


def test_force_deletes_a_referenced_signal(client):
    client.post("/api/custom-signals", json=_signal())
    _write_rule(client.data_dir, _rule("uses_it", "csg_low_touches_ma5"))

    response = client.delete("/api/custom-signals/low_touches_ma5", params={"force": "true"})

    assert response.status_code == 200
    assert custom_signals.load_all(client.data_dir) == []


def test_disabling_a_referenced_signal_is_refused_without_force(client):
    client.post("/api/custom-signals", json=_signal())
    _write_rule(client.data_dir, _rule("uses_it", "csg_low_touches_ma5"))

    refused = client.put("/api/custom-signals/low_touches_ma5", json=_signal(enabled=False))
    assert refused.status_code == 409
    assert refused.json()["detail"]["monitor_rules"][0]["id"] == "uses_it"
    assert custom_signals.load_all(client.data_dir)[0]["enabled"] is True

    forced = client.put(
        "/api/custom-signals/low_touches_ma5", params={"force": "true"}, json=_signal(enabled=False)
    )
    assert forced.status_code == 200
    assert custom_signals.load_all(client.data_dir)[0]["enabled"] is False


def test_an_unreferenced_signal_deletes_without_force(client):
    client.post("/api/custom-signals", json=_signal())
    assert client.delete("/api/custom-signals/low_touches_ma5").status_code == 200


# ── B093 ────────────────────────────────────────────────


def test_options_give_every_field_its_unit(client):
    options = client.get("/api/custom-signals/options").json()

    units = {f["key"]: f["unit"] for f in options["fields"]}
    assert set(units) == set(custom_signals.ALLOWED_FIELDS)
    assert units["turnover_rate"] == "percent"
    assert units["change_pct"] == "decimal"
    assert units["momentum_20d"] == "decimal"
    assert units["distance_to_upper_limit"] == "decimal"
    assert units["volume"] == "shares"
    assert units["amount"] == "TWD"
    assert units["close"] == "TWD"
    grouped = {f["key"]: f["unit"] for g in options["groups"] for f in g["fields"]}
    assert grouped["turnover_rate"] == "percent"
    assert set(options["units"]) >= {"percent", "decimal", "shares", "TWD"}
