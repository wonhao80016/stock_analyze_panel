from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import backtest as api
from app.backtest.strategy import TAIWAN_STAMP_TAX_OVERRIDE_ERROR


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
        objective="total_return",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(("train_days", "test_days"), [(127, 63), (63, 127)])
async def test_walkforward_guard_limits_fold_window_in_trading_sessions(
    capture_worker, monkeypatch, train_days, test_days,
) -> None:
    monkeypatch.setattr(api.settings, "backtest_range_guard", True)

    response = await api.walkforward_stream(
        _Request(),
        strategy_id="test",
        param_grid='{"window":[5]}',
        start="2024-01-01",
        end="2026-01-01",
        train_days=train_days,
        test_days=test_days,
        step_days=63,
    )

    body = await _consume(response)
    assert "event: error" in body
    assert "单折窗口最多 126 个交易日" in body
    assert capture_worker == []


@pytest.mark.asyncio
async def test_walkforward_guard_admits_half_year_of_sessions(capture_worker, monkeypatch) -> None:
    monkeypatch.setattr(api.settings, "backtest_range_guard", True)

    response = await api.walkforward_stream(
        _Request(),
        strategy_id="test",
        param_grid='{"window":[5]}',
        start="2024-01-01",
        end="2026-01-01",
        train_days=126,
        test_days=63,
        step_days=63,
    )

    assert "event: done" in await _consume(response)
    assert capture_worker[0][1].train_days == 126


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


_SHORT_RANGE = {"start": "2026-01-01", "end": "2026-01-10"}
_GRID = {"param_grid": '{"window":[5]}'}


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        (
            "post",
            "/api/backtest/strategy/run",
            {"json": {
                "strategy_id": "test",
                "symbols": ["2330.TWSE"],
                "stamp_tax_pct": 0.003,
                **_SHORT_RANGE,
            }},
        ),
        (
            "get",
            "/api/backtest/strategy/stream",
            {"params": {
                "strategy_id": "test",
                "symbols": "2317.TWSE, 2330.TWSE",
                "stamp_tax_pct": 0.0,
                **_SHORT_RANGE,
            }},
        ),
        (
            "get",
            "/api/backtest/optimize/stream",
            {"params": {
                "strategy_id": "test",
                "symbols": "8069.TPEX",
                "stamp_tax_pct": 0.003,
                **_GRID,
                **_SHORT_RANGE,
            }},
        ),
        (
            "get",
            "/api/backtest/walkforward/stream",
            {"params": {
                "strategy_id": "test",
                "symbols": "2330.TWSE",
                "stamp_tax_pct": 0.003,
                "train_days": 2,
                "test_days": 1,
                "step_days": 1,
                **_GRID,
                **_SHORT_RANGE,
            }},
        ),
    ],
)
def test_taiwan_backtest_routes_reject_single_stamp_tax_override(
    capture_worker,
    method: str,
    path: str,
    kwargs: dict,
) -> None:
    response = getattr(_client(), method)(path, **kwargs)

    assert response.status_code == 400
    assert response.json()["detail"] == TAIWAN_STAMP_TAX_OVERRIDE_ERROR
    assert capture_worker == []
    assert api._running_jobs == {}


def test_taiwan_strategy_run_without_stamp_tax_executes(capture_worker) -> None:
    response = _client().post(
        "/api/backtest/strategy/run",
        json={"strategy_id": "test", "symbols": ["2330.TWSE"], **_SHORT_RANGE},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    kind, config = capture_worker[0]
    assert kind == "backtest"
    assert config.symbols == ["2330.TWSE"]
    assert config.stamp_tax_pct is None


def test_non_taiwan_strategy_run_keeps_explicit_stamp_tax(capture_worker) -> None:
    response = _client().post(
        "/api/backtest/strategy/run",
        json={
            "strategy_id": "test",
            "symbols": ["600519.SH"],
            "stamp_tax_pct": 0.0005,
            **_SHORT_RANGE,
        },
    )

    assert response.status_code == 200
    assert capture_worker[0][1].stamp_tax_pct == pytest.approx(0.0005)


@pytest.mark.parametrize("rate", [-0.06, 0.25])
@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/api/backtest/strategy/run", {"json": {"strategy_id": "test", **_SHORT_RANGE}}),
        ("get", "/api/backtest/strategy/stream", {"params": {"strategy_id": "test", **_SHORT_RANGE}}),
        (
            "get",
            "/api/backtest/optimize/stream",
            {"params": {"strategy_id": "test", **_GRID, **_SHORT_RANGE}},
        ),
        (
            "get",
            "/api/backtest/walkforward/stream",
            {"params": {
                "strategy_id": "test", "train_days": 2, "test_days": 1, "step_days": 1,
                **_GRID, **_SHORT_RANGE,
            }},
        ),
    ],
)
def test_backtest_routes_reject_unreasonable_risk_free_rate(
    capture_worker,
    method: str,
    path: str,
    kwargs: dict,
    rate: float,
) -> None:
    key = "json" if "json" in kwargs else "params"
    request_kwargs = {key: {**kwargs[key], "risk_free_rate": rate}}

    response = getattr(_client(), method)(path, **request_kwargs)

    assert response.status_code == 400
    assert "risk_free_rate" in response.json()["detail"]
    assert capture_worker == []
    assert api._running_jobs == {}


def test_strategy_routes_propagate_risk_free_rate(capture_worker) -> None:
    client = _client()
    assert client.post(
        "/api/backtest/strategy/run",
        json={"strategy_id": "test", "risk_free_rate": 0.015, **_SHORT_RANGE},
    ).status_code == 200
    with client.stream(
        "GET",
        "/api/backtest/strategy/stream",
        params={"strategy_id": "test", "risk_free_rate": 0.015, **_SHORT_RANGE},
    ) as response:
        assert "event: done" in response.read().decode("utf-8")
    for path, extra in (
        ("/api/backtest/optimize/stream", {}),
        (
            "/api/backtest/walkforward/stream",
            {"objective": "total_return", "train_days": 2, "test_days": 1, "step_days": 1},
        ),
    ):
        with client.stream(
            "GET",
            path,
            params={"strategy_id": "test", "risk_free_rate": 0.015, **_GRID, **extra, **_SHORT_RANGE},
        ) as response:
            assert "event: done" in response.read().decode("utf-8")

    kinds = [kind for kind, _ in capture_worker]
    assert kinds == ["backtest", "backtest", "optimize", "walkforward"]
    assert capture_worker[0][1].risk_free_rate == 0.015
    assert capture_worker[1][1].risk_free_rate == 0.015
    assert capture_worker[2][1].backtest_kwargs["risk_free_rate"] == 0.015
    assert capture_worker[3][1].backtest_kwargs["risk_free_rate"] == 0.015


def test_job_keys_distinguish_risk_free_rate() -> None:
    base = dict(
        strategy_id="s", symbols=None, start="2026-01-01", end="2026-01-10",
        matching="open_t+1", entry_fill=None, exit_fill=None, fees_pct=0.0002,
        slippage_bps=5.0, max_positions=10, max_exposure_pct=1.0, initial_capital=1e6,
        position_sizing="equal", params=None, overrides=None,
    )
    assert api._make_job_key(**base, risk_free_rate=0.0) != api._make_job_key(**base, risk_free_rate=0.015)


def test_walkforward_rejects_annualized_objective_with_short_training_window(capture_worker) -> None:
    response = _client().get(
        "/api/backtest/walkforward/stream",
        params={
            "strategy_id": "test", "objective": "sharpe", "train_days": 59, "test_days": 20,
            "step_days": 20, **_GRID, **_SHORT_RANGE,
        },
    )

    assert response.status_code == 400
    assert "at least 60 trading sessions" in response.json()["detail"]
    assert capture_worker == []
    assert api._running_jobs == {}
