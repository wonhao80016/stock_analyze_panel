"""Monitor rule storage: path-safe rule ids and the enabled-only toggle endpoint."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.monitor_rules import router
from app.strategy import monitor_rules

# ── B173: strategy_monitor_ids and rule ids never leave the rules directory ──

def test_migrate_skips_strategy_ids_that_would_escape_the_rules_dir(tmp_path):
    touched = monitor_rules.migrate_strategy_monitors(
        tmp_path, ["x/../../../escaped", "good_one"], {},
    )

    assert [rule["id"] for rule in touched] == ["mr_strategy_good_one"]
    assert not (tmp_path / "escaped.json").exists()
    written = sorted(p.name for p in (tmp_path / "user_data" / "monitor_rules").rglob("*.json"))
    assert written == ["mr_strategy_good_one.json"]


def test_migrate_keeps_hyphenated_strategy_ids(tmp_path):
    touched = monitor_rules.migrate_strategy_monitors(tmp_path, ["My-Strategy_2"], {})

    assert [rule["id"] for rule in touched] == ["mr_strategy_My-Strategy_2"]
    assert monitor_rules.load_one(tmp_path, "mr_strategy_My-Strategy_2") is not None


@pytest.mark.parametrize("rule_id", ["../escaped", "a/b", "a\\b", "", "..", "x" * 200])
def test_save_one_rejects_unsafe_rule_id(tmp_path, rule_id):
    with pytest.raises(ValueError):
        monitor_rules.save_one(tmp_path, {"id": rule_id, "name": "x"})
    assert not (tmp_path / "user_data" / "escaped.json").exists()


def test_load_and_delete_refuse_unsafe_rule_id(tmp_path):
    outside = tmp_path / "user_data" / "escaped.json"
    outside.parent.mkdir(parents=True)
    outside.write_text('{"id": "escaped", "name": "x"}', encoding="utf-8")

    assert monitor_rules.load_one(tmp_path, "../escaped") is None
    assert monitor_rules.delete_one(tmp_path, "../escaped") is False
    assert outside.exists()


# ── B270: disabling a rule must not depend on its bindings still existing ──

class _FakeRepo:
    def __init__(self, data_dir):
        self.store = SimpleNamespace(data_dir=data_dir)

    def resolve_asset_type(self, symbol):
        return "stock"


class _Engine:
    def __init__(self):
        self.loaded: list[list[dict]] = []

    def set_rules(self, rules):
        self.loaded.append(rules)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.watchlist.list_groups", lambda: [])
    app = FastAPI()
    app.include_router(router)
    app.state.repo = _FakeRepo(tmp_path)
    app.state.monitor_engine = _Engine()
    return TestClient(app), tmp_path, app.state.monitor_engine


def _group_rule() -> dict:
    return monitor_rules.normalize({
        "id": "r_group", "name": "分組規則", "type": "price", "scope": "watchlist_group",
        "group_id": "deleted_group", "logic": "and",
        "conditions": [{"field": "close", "op": ">", "value": 10}],
    })


def test_disable_rule_whose_group_was_deleted(client):
    http, data_dir, engine = client
    monitor_rules.save_one(data_dir, _group_rule())

    resp = http.put("/api/monitor-rules/r_group/enabled", json={"enabled": False})

    assert resp.status_code == 200
    assert resp.json()["rule"]["enabled"] is False
    stored = monitor_rules.load_one(data_dir, "r_group")
    assert stored["enabled"] is False
    assert stored["group_id"] == "deleted_group"
    assert engine.loaded and all(r["id"] != "r_group" or r["enabled"] is False for r in engine.loaded[-1])


def test_enable_still_runs_full_validation(client):
    http, data_dir, _ = client
    monitor_rules.save_one(data_dir, {**_group_rule(), "enabled": False})

    resp = http.put("/api/monitor-rules/r_group/enabled", json={"enabled": True})

    assert resp.status_code == 400
    assert monitor_rules.load_one(data_dir, "r_group")["enabled"] is False


def test_toggle_unknown_or_invalid_rule(client):
    http, _, _ = client

    assert http.put("/api/monitor-rules/missing/enabled", json={"enabled": False}).status_code == 404
    assert http.put("/api/monitor-rules/Bad-Id!/enabled", json={"enabled": False}).status_code == 400


# ── Migrated strategy rule ids keep upper case and hyphens through every endpoint ──

class _StrategyEngine:
    def get(self, strategy_id):
        return SimpleNamespace(meta={"id": strategy_id, "name": strategy_id})

    def validate_context(self, strategy, context):
        return None


@pytest.mark.parametrize("rule_id", [
    "mr_strategy_x/../escaped", "mr_strategy_..", "mr_strategy_C:evil", "mr_strategy_a\b",
    "mr_strategy_ok\n",
])
def test_migrated_prefix_does_not_admit_path_tricks(rule_id):
    assert not monitor_rules.is_valid_rule_id(rule_id)


def test_hyphenated_migrated_rule_can_be_disabled_enabled_and_deleted(client):
    http, data_dir, _ = client
    http.app.state.strategy_engine = _StrategyEngine()
    [rule] = monitor_rules.migrate_strategy_monitors(data_dir, ["My-Strategy_2"], {})
    rule_id = rule["id"]
    assert rule_id == "mr_strategy_My-Strategy_2"

    off = http.put(f"/api/monitor-rules/{rule_id}/enabled", json={"enabled": False})
    assert off.status_code == 200
    assert monitor_rules.load_one(data_dir, rule_id)["enabled"] is False

    on = http.put(f"/api/monitor-rules/{rule_id}/enabled", json={"enabled": True})
    assert on.status_code == 200, on.json()
    assert monitor_rules.load_one(data_dir, rule_id)["enabled"] is True

    assert http.delete(f"/api/monitor-rules/{rule_id}").status_code == 200
    assert monitor_rules.load_one(data_dir, rule_id) is None


# ── Legacy rule files whose id is outside the whitelist are skipped, not fatal ──

def _write_raw_rule(data_dir, file_name: str, rule: dict) -> None:
    import json

    rules_dir = data_dir / "user_data" / "monitor_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / file_name).write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")


def test_load_all_skips_rules_with_an_invalid_id(tmp_path):
    _write_raw_rule(tmp_path, "bad.json", {"id": "Bad Id!", "name": "x", "type": "price"})
    _write_raw_rule(tmp_path, "mr_ok.json", {"id": "mr_ok", "name": "x", "type": "price"})

    assert [r["id"] for r in monitor_rules.load_all(tmp_path)] == ["mr_ok"]


def test_migration_continues_past_a_legacy_rule_with_an_invalid_id(tmp_path):
    _write_raw_rule(tmp_path, "mr_strategy_bad.json", {
        "id": "mr_strategy_bad id", "name": "x", "type": "strategy", "strategy_id": "bad id", "enabled": True,
    })
    _write_raw_rule(tmp_path, "mr_strategy_old.json", {
        "id": "mr_strategy_old", "name": "x", "type": "strategy", "strategy_id": "old", "enabled": True,
    })

    touched = monitor_rules.migrate_strategy_monitors(tmp_path, ["fresh"], {})

    assert [r["id"] for r in touched] == ["mr_strategy_fresh"]
    assert monitor_rules.load_one(tmp_path, "mr_strategy_old")["enabled"] is False
