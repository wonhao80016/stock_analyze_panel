"""System notifications leave the poll thread through a bounded background queue."""
from __future__ import annotations

import queue
import threading
import time

import pytest

from app.services import notify_adapter
from app.services.quote_service import QuoteService


class _BlockingBackend:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.sent: list[tuple[str, str]] = []

    def __call__(self, title: str, message: str) -> bool:
        self.release.wait(timeout=1.5)
        self.sent.append((title, message))
        return True


@pytest.fixture
def backend(monkeypatch):
    blocking = _BlockingBackend()
    monkeypatch.setattr(notify_adapter, "_detect_backend", lambda: "osascript")
    monkeypatch.setattr(notify_adapter, "_notify_osascript", blocking)
    monkeypatch.setattr(notify_adapter, "_queue", queue.Queue(maxsize=50), raising=False)
    monkeypatch.setattr(notify_adapter, "_worker", None, raising=False)
    yield blocking
    blocking.release.set()


def _alerts(n: int) -> list[dict]:
    return [
        {"source": "price", "symbol": f"{2330 + i}.TWSE", "name": "n", "message": f"alert {i}"}
        for i in range(n)
    ]


def test_poll_thread_does_not_wait_on_the_notification_backend(backend, monkeypatch):
    monkeypatch.setattr("app.services.preferences.get_system_notify_enabled", lambda: True)
    svc = QuoteService.__new__(QuoteService)

    started = time.perf_counter()
    svc._maybe_send_system_notifications(_alerts(3))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    backend.release.set()
    deadline = time.monotonic() + 5
    while len(backend.sent) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert [body for _title, body in backend.sent] == [
        "2330.TWSE n alert 0", "2331.TWSE n alert 1", "2332.TWSE n alert 2",
    ]


def test_full_queue_drops_instead_of_blocking(backend, monkeypatch):
    monkeypatch.setattr(notify_adapter, "_queue", queue.Queue(maxsize=2))

    started = time.perf_counter()
    accepted = [notify_adapter.notify_async("title", f"body {i}") for i in range(6)]
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert accepted[:2] == [True, True]
    assert accepted.count(True) <= 3
    assert accepted[-1] is False


def test_notify_async_rejects_an_empty_title(backend):
    assert notify_adapter.notify_async("  ", "body") is False
