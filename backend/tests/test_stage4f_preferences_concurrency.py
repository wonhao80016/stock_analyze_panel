"""preferences.json must survive concurrent read-modify-write and never be rebuilt from a failed parse."""
from __future__ import annotations

import json
import logging
import threading

import pytest

from app.services import preferences

THREADS = 8
WRITES_PER_THREAD = 40


@pytest.fixture
def prefs_path(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(preferences, "_path", lambda: path)
    preferences._invalidate_cache()
    yield path
    preferences._invalidate_cache()


def test_concurrent_saves_keep_every_key_and_never_read_a_torn_file(prefs_path, caplog):
    seed = {f"seed_{i}": "x" * 200 for i in range(40)}
    prefs_path.write_text(json.dumps(seed, indent=2), encoding="utf-8")
    start = threading.Barrier(THREADS + 2)
    stop = threading.Event()
    errors: list[BaseException] = []
    empty_reads = 0

    def writer(worker: int) -> None:
        try:
            start.wait()
            for n in range(WRITES_PER_THREAD):
                preferences.save({f"w{worker}_{n}": n})
        except BaseException as exc:
            errors.append(exc)

    def interval_writer() -> None:
        try:
            start.wait()
            for n in range(WRITES_PER_THREAD):
                preferences.set_realtime_quote_interval(float(n))
        except BaseException as exc:
            errors.append(exc)

    def reader() -> None:
        nonlocal empty_reads
        start.wait()
        while not stop.is_set():
            if "seed_0" not in preferences.load():
                empty_reads += 1

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(THREADS)]
    threads.append(threading.Thread(target=interval_writer))
    read_thread = threading.Thread(target=reader)
    caplog.set_level(logging.WARNING, logger=preferences.logger.name)
    read_thread.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    read_thread.join()

    assert errors == []
    final = json.loads(prefs_path.read_text(encoding="utf-8"))
    expected = set(seed) | {f"w{w}_{n}" for w in range(THREADS) for n in range(WRITES_PER_THREAD)}
    assert expected - set(final) == set()
    assert final["realtime_quote_interval"] == float(WRITES_PER_THREAD - 1)
    assert empty_reads == 0
    assert "malformed" not in caplog.text


def test_save_refuses_to_rebuild_a_malformed_file_and_keeps_it(prefs_path, caplog):
    broken = '{"realtime_quotes_enabled": true, "daily_data_provider": "tai'
    prefs_path.write_text(broken, encoding="utf-8")
    caplog.set_level(logging.ERROR, logger=preferences.logger.name)

    with pytest.raises(preferences.PreferencesCorruptError):
        preferences.save({"last_fetch_ms": 1.0})
    with pytest.raises(preferences.PreferencesCorruptError):
        preferences.set_realtime_quote_interval(3.0)

    assert prefs_path.read_text(encoding="utf-8") == broken
    assert "preferences.json" in caplog.text


def test_save_refuses_a_file_that_is_not_an_object(prefs_path):
    prefs_path.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(preferences.PreferencesCorruptError):
        preferences.save({"last_fetch_ms": 1.0})

    assert prefs_path.read_text(encoding="utf-8") == "[1, 2]"


def test_save_still_creates_a_missing_file(prefs_path):
    assert preferences.save({"a": 1}) == {"a": 1}
    assert json.loads(prefs_path.read_text(encoding="utf-8")) == {"a": 1}


def test_poller_writes_do_not_raise_or_rewrite_a_malformed_file(prefs_path):
    from app.services import quote_service

    broken = "{not json"
    prefs_path.write_text(broken, encoding="utf-8")
    quote_service._last_fetch_written_at_ms = 0.0

    quote_service._persist_last_fetch(10_000_000.0)
    quote_service.QuoteService._save_enabled(True)

    assert prefs_path.read_text(encoding="utf-8") == broken
