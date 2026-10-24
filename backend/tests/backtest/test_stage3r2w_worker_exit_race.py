"""A terminal message sent just before the worker exits must still be read."""
from __future__ import annotations

import queue
from types import SimpleNamespace

import pytest

from app.backtest import worker as worker_module
from app.backtest.worker import BacktestWorkerError, run_worker_task


class _Process:
    def __init__(self):
        self.alive = True
        self.exitcode = None

    def start(self):
        pass

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def terminate(self):
        self.alive = False
        self.exitcode = -15


class _Event:
    def set(self):
        pass


def _install(monkeypatch, process, events):
    context = SimpleNamespace(
        Queue=lambda: events,
        Event=_Event,
        Process=lambda **_kwargs: process,
    )
    monkeypatch.setattr(worker_module.mp, "get_context", lambda _method: context)


def test_result_enqueued_while_get_times_out_is_not_dropped(monkeypatch, tmp_path):
    process = _Process()

    class Queue:
        def __init__(self):
            self.pending: list[dict] = []
            self.timeouts: list[float] = []

        def get(self, timeout):
            self.timeouts.append(timeout)
            if self.pending:
                return self.pending.pop(0)
            if process.alive:
                # The child flushes its result and exits while this get is timing out.
                self.pending.append({"type": "result", "payload": {"status": "ok"}})
                process.alive = False
                process.exitcode = 0
            raise queue.Empty

        def close(self):
            pass

        def join_thread(self):
            pass

    events = Queue()
    _install(monkeypatch, process, events)

    result = run_worker_task({"kind": "mining", "data_dir": str(tmp_path), "config": {}})

    assert result["status"] == "ok"
    assert result["worker"]["worker_exitcode"] == 0
    assert result["worker"]["worker_exit_forcibly"] is False


def test_dead_worker_with_empty_queue_still_reports_missing_result(monkeypatch, tmp_path):
    process = _Process()

    class Queue:
        def __init__(self):
            self.calls = 0

        def get(self, timeout):
            self.calls += 1
            process.alive = False
            process.exitcode = 3
            raise queue.Empty

        def close(self):
            pass

        def join_thread(self):
            pass

    events = Queue()
    _install(monkeypatch, process, events)

    with pytest.raises(BacktestWorkerError, match="exitcode=3"):
        run_worker_task({"kind": "mining", "data_dir": str(tmp_path), "config": {}})
    assert events.calls >= 2
