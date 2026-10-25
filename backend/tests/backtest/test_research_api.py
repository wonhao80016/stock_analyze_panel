from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import backtest as api


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/backtest/run"),
        ("POST", "/api/backtest/factor/run"),
        ("POST", "/api/backtest/factor/batch"),
        ("GET", "/api/backtest/factor/columns"),
    ],
)
def test_removed_legacy_backtest_routes_are_not_registered(method, path):
    app = FastAPI()
    app.include_router(api.router)

    assert path not in {route.path for route in api.router.routes}
    assert TestClient(app).request(method, path).status_code == 404
