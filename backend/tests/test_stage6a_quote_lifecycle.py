"""QuoteService start/enable/stop: at most one poll thread, and no new one before the old exits."""
from __future__ import annotations

import threading
import time

import pytest

from app.services import preferences
from app.services.quote_service import QuoteService


class _PollProbe:
    """Records every thread running a poll round and the most seen alive at once."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.threads: list[threading.Thread] = []
        self.max_alive = 0
        self.hold_first = threading.Event()
        self.release = threading.Event()

    def alive(self) -> list[threading.Thread]:
        with self.lock:
            return [t for t in self.threads if t.is_alive()]

    def fetch(self, *, final: bool = False) -> bool:
        me = threading.current_thread()
        with self.lock:
            if me not in self.threads:
                self.threads.append(me)
            first = self.threads[0] is me
            self.max_alive = max(self.max_alive, sum(t.is_alive() for t in self.threads))
        if first and self.hold_first.is_set():
            self.release.wait(timeout=15)
        time.sleep(0.01)
        return True


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def service(monkeypatch):
    probe = _PollProbe()
    svc = QuoteService()
    monkeypatch.setattr(svc, "_save_enabled", lambda _enabled: None)
    monkeypatch.setattr(svc, "_fetch_quotes", probe.fetch)
    monkeypatch.setattr(svc, "_market_phase", lambda: "regular")
    monkeypatch.setattr(svc, "_should_fetch_for_phase", lambda _phase: True)
    monkeypatch.setattr(svc, "_holding_for_suspected_closure", lambda: False)
    monkeypatch.setattr(svc, "_clamp_interval", lambda _interval: 0.0)
    monkeypatch.setattr(QuoteService, "is_realtime_allowed", classmethod(lambda cls: True))
    monkeypatch.setattr(QuoteService, "STOP_JOIN_TIMEOUT", 0.2, raising=False)
    yield svc, probe
    probe.release.set()
    svc.stop(persist=False)
    for t in probe.threads:
        t.join(timeout=5)


def test_enable_after_a_timed_out_stop_waits_for_the_old_thread(service):
    svc, probe = service
    probe.hold_first.set()
    svc.start(interval=1.0)
    assert _wait_until(lambda: len(probe.threads) == 1)
    old = probe.threads[0]

    svc.stop(persist=False)
    assert old.is_alive()
    assert svc.enable() is True
    time.sleep(0.3)

    assert probe.alive() == [old]
    assert svc.status()["enabled"] is True

    probe.release.set()
    assert _wait_until(lambda: not old.is_alive())
    assert _wait_until(lambda: len(probe.alive()) == 1 and probe.alive()[0] is not old)
    assert probe.max_alive == 1
    assert svc.status()["running"] is True


def test_stop_during_a_pending_restart_cancels_it(service):
    svc, probe = service
    probe.hold_first.set()
    svc.start(interval=1.0)
    assert _wait_until(lambda: len(probe.threads) == 1)
    old = probe.threads[0]

    svc.stop(persist=False)
    svc.enable()
    svc.stop(persist=False)
    probe.release.set()
    assert _wait_until(lambda: not old.is_alive())
    time.sleep(0.3)

    assert probe.threads == [old]
    assert svc.status()["running"] is False


def test_concurrent_enables_start_one_poll_thread(service, monkeypatch):
    svc, probe = service
    real_interval = preferences.get_realtime_quote_interval

    def slow_interval():
        time.sleep(0.05)
        return real_interval()

    monkeypatch.setattr(preferences, "get_realtime_quote_interval", slow_interval)
    barrier = threading.Barrier(6)

    def enable():
        barrier.wait()
        svc.enable()

    callers = [threading.Thread(target=enable) for _ in range(6)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(timeout=5)
    time.sleep(0.3)

    assert len(probe.threads) == 1
    assert probe.max_alive == 1


def test_concurrent_start_and_stop_leave_no_orphan_thread(service):
    svc, probe = service
    for _ in range(5):
        workers = [
            threading.Thread(target=svc.enable),
            threading.Thread(target=lambda: svc.stop(persist=False)),
            threading.Thread(target=svc.enable),
        ]
        for t in workers:
            t.start()
        for t in workers:
            t.join(timeout=5)
    time.sleep(0.3)

    alive = probe.alive()
    assert len(alive) <= 1
    if svc.status()["running"]:
        assert _wait_until(lambda: len(probe.alive()) == 1)
    else:
        assert alive == []
    assert probe.max_alive == 1
