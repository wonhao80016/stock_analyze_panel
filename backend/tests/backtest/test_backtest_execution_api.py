from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import backtest as api


class _ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target

    def start(self):
        self.target()


class _NoopTimer:
    daemon = False

    def __init__(self, interval, callback):
        self.callback = callback

    def start(self):
        return None


class _Request:
    def __init__(self):
        self.app = SimpleNamespace(state=SimpleNamespace(repo=SimpleNamespace()))

    async def is_disconnected(self):
        return False


async def _consume(response) -> str:
    body = b""
    async for chunk in response.body_iterator:
        body += chunk.encode("utf-8") if isinstance(chunk, str) else chunk
    return body.decode("utf-8")


@pytest.fixture
def capture_worker(monkeypatch):
    captured = []

    def make_worker_task(kind, data_dir, config):
        captured.append((kind, config))
        return {"kind": kind}

    def run_worker_task(task, progress_cb=None, cancel_event=None):
        return {"ok": True}

    monkeypatch.setattr("app.backtest.worker.make_worker_task", make_worker_task)
    monkeypatch.setattr("app.backtest.worker.run_worker_task", run_worker_task)
    monkeypatch.setattr(api.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(api.threading, "Timer", _NoopTimer)
    api._running_jobs.clear()
    yield captured
    api._running_jobs.clear()


def _assert_execution_fields(config) -> None:
    assert config.fees_pct == pytest.approx(0.0002)
    assert config.commission_pct is None
    assert config.commission_discount == 0.5
    assert config.minimum_holding_sessions == 0
    assert config.day_trade_eligible_symbols == ("2330.TWSE",)


def test_strategy_post_propagates_execution_fields(capture_worker) -> None:
    request = api.StrategyBacktestRequest(
        strategy_id="test",
        start="2026-01-01",
        end="2026-01-02",
        commission_discount=0.5,
        minimum_holding_sessions=0,
        day_trade_eligible_symbols=["2330.TWSE"],
    )

    assert api.strategy_run(request, _Request()) == {"ok": True}
    assert capture_worker[0][0] == "backtest"
    _assert_execution_fields(capture_worker[0][1])


@pytest.mark.asyncio
async def test_strategy_sse_propagates_execution_fields(capture_worker) -> None:
    response = await api.strategy_stream(
        _Request(),
        strategy_id="test",
        start="2026-01-01",
        end="2026-01-02",
        commission_discount=0.5,
        minimum_holding_sessions=0,
        day_trade_eligible_symbols="2330.TWSE",
    )

    assert "event: done" in await _consume(response)
    assert capture_worker[0][0] == "backtest"
    _assert_execution_fields(capture_worker[0][1])


@pytest.mark.asyncio
async def test_optimizer_sse_propagates_execution_fields(capture_worker) -> None:
    response = await api.optimize_stream(
        _Request(),
        strategy_id="test",
        param_grid='{"window":[5]}',
        start="2026-01-01",
        end="2026-01-02",
        commission_discount=0.5,
        minimum_holding_sessions=0,
        day_trade_eligible_symbols="2330.TWSE",
    )

    assert "event: done" in await _consume(response)
    assert capture_worker[0][0] == "optimize"
    _assert_execution_fields(SimpleNamespace(**capture_worker[0][1].backtest_kwargs))


@pytest.mark.asyncio
async def test_walkforward_sse_propagates_execution_fields(capture_worker) -> None:
    response = await api.walkforward_stream(
        _Request(),
        strategy_id="test",
        param_grid='{"window":[5]}',
        start="2026-01-01",
        end="2026-01-10",
        train_days=2,
        test_days=1,
        step_days=1,
        commission_discount=0.5,
        minimum_holding_sessions=0,
        day_trade_eligible_symbols="2330.TWSE",
    )

    assert "event: done" in await _consume(response)
    assert capture_worker[0][0] == "walkforward"
    _assert_execution_fields(SimpleNamespace(**capture_worker[0][1].backtest_kwargs))
