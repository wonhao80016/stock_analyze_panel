"""The walk-forward route rejects overlapping OOS windows before any job or worker exists."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import backtest as api


def test_walkforward_stream_rejects_step_shorter_than_test_window(monkeypatch):
    spawned = []
    monkeypatch.setattr(
        "app.backtest.worker.run_worker_task",
        lambda *args, **kwargs: spawned.append(args) or {"ok": True},
    )
    api._running_jobs.clear()
    app = FastAPI()
    app.include_router(api.router)

    try:
        response = TestClient(app).get(
            "/api/backtest/walkforward/stream",
            params={
                "strategy_id": "test",
                "param_grid": '{"window":[5]}',
                "objective": "total_return",
                "train_days": 10,
                "test_days": 5,
                "step_days": 2,
                "start": "2026-01-01",
                "end": "2026-01-10",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "step_days (2) 不能小于 test_days (5): "
            "样本外窗口会重叠, 复利收益与一致性会重复计入同一段区间"
        )
        assert spawned == []
        assert api._running_jobs == {}
    finally:
        api._running_jobs.clear()
