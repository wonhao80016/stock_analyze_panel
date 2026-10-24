from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import alerts as alerts_api
from app.services import alert_store


def _event(ts: int, symbol: str, **extra) -> dict:
    return {
        "ts": ts,
        "rule_id": "r1",
        "source": "signal",
        "type": "signal",
        "symbol": symbol,
        "message": f"{symbol} hit",
        **extra,
    }


def _file(data_dir: Path) -> Path:
    return data_dir / "user_data" / "alerts.jsonl"


def _write_legacy(data_dir: Path, events: list[dict]) -> None:
    p = _file(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


@pytest.fixture()
def ts_now() -> int:
    return int(time.time() * 1000)


def test_new_alerts_are_persisted_with_unique_ids(tmp_path, ts_now):
    alert_store.append_many(tmp_path, [_event(ts_now, "2330.TWSE"), _event(ts_now, "2317.TWSE")])
    alert_store.append(tmp_path, _event(ts_now, "2454.TWSE"))

    raw = [json.loads(line) for line in _file(tmp_path).read_text(encoding="utf-8").splitlines()]
    ids = [r.get("id") for r in raw]
    assert all(isinstance(i, str) and i for i in ids)
    assert len(set(ids)) == 3
    assert [e["id"] for e in alert_store.list_recent(tmp_path)] != []
    assert {e["id"] for e in alert_store.list_recent(tmp_path)} == set(ids)


def test_delete_by_id_keeps_other_alert_with_same_ts(tmp_path, ts_now):
    alert_store.append_many(tmp_path, [_event(ts_now, "2330.TWSE"), _event(ts_now, "2317.TWSE")])
    listed = alert_store.list_recent(tmp_path)
    second = next(e for e in listed if e["symbol"] == "2317.TWSE")

    assert alert_store.delete_one(tmp_path, second["id"]) is True

    remaining = alert_store.list_recent(tmp_path)
    assert [e["symbol"] for e in remaining] == ["2330.TWSE"]


def test_legacy_rows_get_stable_distinct_ids(tmp_path, ts_now):
    _write_legacy(tmp_path, [
        _event(ts_now, "2330.TWSE"),
        _event(ts_now, "2317.TWSE"),
        _event(ts_now, "2317.TWSE"),
    ])
    first = alert_store.list_recent(tmp_path)
    second = alert_store.list_recent(tmp_path)
    ids = [e["id"] for e in first]
    assert len(set(ids)) == 3
    assert ids == [e["id"] for e in second]
    assert alert_store.list_recent(tmp_path, source="signal")[0]["id"] == ids[0]


def test_legacy_delete_by_id_removes_the_clicked_row(tmp_path, ts_now):
    _write_legacy(tmp_path, [_event(ts_now, "2330.TWSE"), _event(ts_now, "2317.TWSE")])
    listed = alert_store.list_recent(tmp_path)
    keep = next(e for e in listed if e["symbol"] == "2330.TWSE")
    target = next(e for e in listed if e["symbol"] == "2317.TWSE")

    assert alert_store.delete_one(tmp_path, target["id"]) is True

    remaining = alert_store.list_recent(tmp_path)
    assert [(e["symbol"], e["id"]) for e in remaining] == [("2330.TWSE", keep["id"])]


def test_delete_rewrites_through_write_atomically(tmp_path, ts_now, monkeypatch):
    alert_store.append_many(tmp_path, [_event(ts_now, "2330.TWSE"), _event(ts_now, "2317.TWSE")])
    target = alert_store.list_recent(tmp_path)[0]
    calls: list[Path] = []
    real = alert_store.write_atomically

    def spy(path, write):
        calls.append(path)
        real(path, write)

    monkeypatch.setattr(alert_store, "write_atomically", spy)
    assert alert_store.delete_one(tmp_path, target["id"]) is True
    assert calls == [_file(tmp_path)]


def test_delete_unknown_id_returns_false(tmp_path, ts_now):
    alert_store.append(tmp_path, _event(ts_now, "2330.TWSE"))
    assert alert_store.delete_one(tmp_path, "missing") is False
    assert len(alert_store.list_recent(tmp_path)) == 1


def _client(data_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(alerts_api.router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    return TestClient(app)


def test_api_delete_by_id_keeps_same_ts_sibling(tmp_path, ts_now):
    alert_store.append_many(tmp_path, [_event(ts_now, "2330.TWSE"), _event(ts_now, "2317.TWSE")])
    client = _client(tmp_path)
    listed = client.get("/api/alerts").json()["alerts"]
    target = next(e for e in listed if e["symbol"] == "2317.TWSE")

    assert client.delete(f"/api/alerts/{target['id']}").status_code == 200

    remaining = client.get("/api/alerts").json()["alerts"]
    assert [e["symbol"] for e in remaining] == ["2330.TWSE"]
    assert client.delete(f"/api/alerts/{target['id']}").status_code == 404


def test_api_legacy_ts_delete_refuses_ambiguous_ts(tmp_path, ts_now):
    alert_store.append_many(tmp_path, [_event(ts_now, "2330.TWSE"), _event(ts_now, "2317.TWSE")])
    alert_store.append(tmp_path, _event(ts_now + 1, "2454.TWSE"))
    client = _client(tmp_path)

    assert client.delete(f"/api/alerts/{ts_now}").status_code == 409
    assert len(alert_store.list_recent(tmp_path)) == 3

    assert client.delete(f"/api/alerts/{ts_now + 1}").status_code == 200
    assert sorted(e["symbol"] for e in alert_store.list_recent(tmp_path)) == ["2317.TWSE", "2330.TWSE"]
