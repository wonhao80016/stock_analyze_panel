"""Follow-ups on the preferences store: cache races, failure memo, API mapping and realtime fill-in dates."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import preferences


@pytest.fixture
def prefs_path(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(preferences, "_path", lambda: path)
    monkeypatch.setattr(preferences, "_unreadable_logged_at", {})
    preferences._invalidate_cache()
    yield path
    preferences._invalidate_cache()


def test_reader_racing_a_same_signature_save_does_not_cache_the_old_content(prefs_path, monkeypatch):
    preferences.save({"a": 0, "b": 0})
    preferences._invalidate_cache()
    sig_a = prefs_path.stat()
    real_read = preferences._read_text
    read_old_text = threading.Event()
    resume = threading.Event()

    def paused_read(path):
        text = real_read(path)
        if threading.current_thread().name == "stale-reader":
            read_old_text.set()
            resume.wait(5)
        return text

    monkeypatch.setattr(preferences, "_read_text", paused_read)
    reader = threading.Thread(target=preferences.load, name="stale-reader")
    reader.start()
    assert read_old_text.wait(5)
    preferences.save({"a": 1})
    # Same size and mtime as the old file, which coarse filesystem timestamps produce in practice.
    os.utime(prefs_path, ns=(sig_a.st_atime_ns, sig_a.st_mtime_ns))
    assert prefs_path.stat().st_size == sig_a.st_size
    resume.set()
    reader.join(5)

    assert preferences.load()["a"] == 1
    preferences.save({"b": 1})
    assert json.loads(prefs_path.read_text(encoding="utf-8")) == {"a": 1, "b": 1}


def test_corrupt_file_is_read_and_logged_once_per_signature(prefs_path, monkeypatch, caplog):
    prefs_path.write_text("{broken", encoding="utf-8")
    reads = []
    real_read = preferences._read_text
    monkeypatch.setattr(preferences, "_read_text", lambda p: reads.append(p) or real_read(p))
    caplog.set_level(logging.ERROR, logger=preferences.logger.name)

    for _ in range(3):
        assert preferences.load() == {}

    assert len(reads) == 1
    assert len([r for r in caplog.records if r.name == preferences.logger.name]) == 1

    prefs_path.write_text('{"fixed": true}', encoding="utf-8")
    assert preferences.load() == {"fixed": True}


def test_permission_denied_is_reported_as_unreadable_not_corrupt(prefs_path, monkeypatch, caplog):
    prefs_path.write_text('{"keep": 1}', encoding="utf-8")
    real_read_text = Path.read_text
    attempts = []

    def denied(self, *args, **kwargs):
        if self == prefs_path:
            attempts.append(1)
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr(preferences, "_READ_RETRY_SECONDS", 0.0)
    caplog.set_level(logging.ERROR, logger=preferences.logger.name)

    assert preferences.load() == {}
    attempts_after_first_load = len(attempts)
    assert preferences.load() == {}
    assert len(attempts) > attempts_after_first_load
    with pytest.raises(preferences.PreferencesUnreadableError):
        preferences.save({"x": 1})

    messages = [r.getMessage() for r in caplog.records if r.name == preferences.logger.name]
    assert len(messages) == 1
    assert "permission" in messages[0].lower() and "malformed" not in messages[0].lower()
    monkeypatch.setattr(Path, "read_text", real_read_text)
    assert json.loads(prefs_path.read_text(encoding="utf-8")) == {"keep": 1}


def test_a_transient_permission_error_is_not_remembered(prefs_path, monkeypatch):
    prefs_path.write_text('{"realtime_quotes_enabled": true}', encoding="utf-8")
    real_read_text = Path.read_text
    locked = {"on": True}

    def maybe_locked(self, *args, **kwargs):
        if self == prefs_path and locked["on"]:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", maybe_locked)
    monkeypatch.setattr(preferences, "_READ_RETRY_SECONDS", 0.0)

    assert preferences.load() == {}
    locked["on"] = False
    assert preferences.load() == {"realtime_quotes_enabled": True}


def test_failed_last_fetch_persist_is_throttled_like_a_success(monkeypatch):
    from app.services import quote_service

    calls = []

    def failing_save(updates):
        calls.append(updates)
        raise preferences.PreferencesCorruptError("broken")

    monkeypatch.setattr(preferences, "save", failing_save)
    monkeypatch.setattr(quote_service, "_last_fetch_written_at_ms", 0.0)

    quote_service._persist_last_fetch(100_000.0)
    quote_service._persist_last_fetch(110_000.0)
    quote_service._persist_last_fetch(131_000.0)

    assert len(calls) == 2


def _api_client(monkeypatch, quote_service=None):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(app.state, "quote_service", quote_service, raising=False)
    return TestClient(app, base_url="http://127.0.0.1:3018", client=("127.0.0.1", 50000))


def test_settings_write_on_a_corrupt_file_answers_503_with_a_reason(prefs_path, monkeypatch):
    prefs_path.write_text("{broken", encoding="utf-8")

    res = _api_client(monkeypatch).put(
        "/api/settings/preferences/minute-sync",
        json={"minute_sync_enabled": True, "minute_sync_days": 5},
    )

    assert res.status_code == 503, res.text
    assert "preferences.json" in res.json()["detail"]
    assert prefs_path.read_text(encoding="utf-8") == "{broken"


def test_disallowed_realtime_toggle_still_stops_the_service_when_saving_fails(prefs_path, monkeypatch):
    prefs_path.write_text("{broken", encoding="utf-8")
    disabled = []
    qs = SimpleNamespace(is_realtime_allowed=lambda: False, disable=lambda: disabled.append(True))

    res = _api_client(monkeypatch, qs).put(
        "/api/settings/preferences/realtime-quotes", json={"realtime_quotes_enabled": True},
    )

    assert res.status_code == 503, res.text
    assert disabled == [True]


def test_manual_realtime_off_stops_the_service_even_when_saving_fails(prefs_path, monkeypatch):
    prefs_path.write_text("{broken", encoding="utf-8")
    disabled = []
    qs = SimpleNamespace(
        is_realtime_allowed=lambda: True,
        is_paused=lambda: False,
        realtime_mode=lambda: "market",
        disable=lambda: disabled.append(True),
    )

    res = _api_client(monkeypatch, qs).put(
        "/api/settings/preferences/realtime-quotes", json={"realtime_quotes_enabled": False},
    )

    assert res.status_code == 503, res.text
    assert disabled == [True]
    assert prefs_path.read_text(encoding="utf-8") == "{broken"


def test_realtime_does_not_fill_a_missing_strategy_from_an_older_session(tmp_path, monkeypatch):
    from app.api import screener as screener_api

    cached = {
        "as_of": "2026-10-23",
        "results": {},
        "failures": {"s": {"error": "boom"}},
        "updated_at": 1,
    }
    realtime = {"s": {"as_of": "2026-10-22", "total": 1, "rows": [{"symbol": "2330.TWSE"}]}}
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_args: cached)
    state = SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        monitor_engine=SimpleNamespace(latest_strategy_results=lambda: realtime),
    )

    payload = screener_api._cached_with_realtime(SimpleNamespace(app=SimpleNamespace(state=state)))

    assert "s" not in payload["results"]
    assert payload["failures"] == {"s": {"error": "boom"}}
    assert payload["updated_at"] == 1
