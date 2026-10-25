from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import backtest as api
from app.backtest.strategy import TAIWAN_STAMP_TAX_OVERRIDE_ERROR


class _Request:
    def __init__(self):
        self.app = SimpleNamespace(state=SimpleNamespace(repo=SimpleNamespace()))


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
    return captured


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


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


_SHORT_RANGE = {"start": "2026-01-01", "end": "2026-01-10"}


def test_taiwan_strategy_run_rejects_single_stamp_tax_override(capture_worker) -> None:
    response = _client().post(
        "/api/backtest/strategy/run",
        json={
            "strategy_id": "test",
            "symbols": ["2330.TWSE"],
            "stamp_tax_pct": 0.003,
            **_SHORT_RANGE,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == TAIWAN_STAMP_TAX_OVERRIDE_ERROR
    assert capture_worker == []


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
def test_strategy_run_rejects_unreasonable_risk_free_rate(capture_worker, rate: float) -> None:
    response = _client().post(
        "/api/backtest/strategy/run",
        json={"strategy_id": "test", "risk_free_rate": rate, **_SHORT_RANGE},
    )

    assert response.status_code == 400
    assert "risk_free_rate" in response.json()["detail"]
    assert capture_worker == []


def test_strategy_run_propagates_risk_free_rate(capture_worker) -> None:
    assert _client().post(
        "/api/backtest/strategy/run",
        json={"strategy_id": "test", "risk_free_rate": 0.015, **_SHORT_RANGE},
    ).status_code == 200

    assert [kind for kind, _ in capture_worker] == ["backtest"]
    assert capture_worker[0][1].risk_free_rate == 0.015


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, 20.0),
        ({"lot_mode": "board_lot"}, 20.0),
        ({"lot_mode": "odd_lot"}, 1.0),
        ({"lot_mode": "odd_lot", "minimum_commission": 0}, 0.0),
        ({"lot_mode": "board_lot", "minimum_commission": 5}, 5.0),
    ],
)
def test_strategy_run_resolves_minimum_commission_by_lot_mode(
    capture_worker,
    extra: dict,
    expected: float,
) -> None:
    assert _client().post(
        "/api/backtest/strategy/run",
        json={"strategy_id": "test", "symbols": ["2330.TWSE"], **extra, **_SHORT_RANGE},
    ).status_code == 200

    assert [kind for kind, _ in capture_worker] == ["backtest"]
    assert capture_worker[0][1].minimum_commission == expected
