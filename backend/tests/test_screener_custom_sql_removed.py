from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from app.main import app
from app.services.screener import ScreenerService

_RAW_SQL_PARAMETERS = {"conditions", "order_by", "where", "sql"}


def test_custom_sql_screener_endpoint_is_not_routed():
    client = TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)

    resp = client.post(
        "/api/screener/run",
        json={"conditions": ["1=1"], "order_by": "symbol", "limit": 1},
    )

    assert resp.status_code in (404, 405)


def test_screener_service_has_no_method_taking_sql_fragments():
    offenders = {
        name: sorted(_RAW_SQL_PARAMETERS & set(inspect.signature(member).parameters))
        for name, member in inspect.getmembers(ScreenerService, inspect.isfunction)
        if _RAW_SQL_PARAMETERS & set(inspect.signature(member).parameters)
    }

    assert offenders == {}
