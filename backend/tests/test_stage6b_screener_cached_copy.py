"""GET /api/screener/cached must not write names into rows shared with the monitor engine."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.api import screener as screener_api


def test_get_cached_leaves_monitor_results_and_cache_rows_untouched(monkeypatch, tmp_path):
    cached_rows = [{"symbol": "2330.TWSE", "close": 600.0}]
    realtime_rows = [{"symbol": "2317.TWSE", "close": 100.0}]
    cached = {
        "as_of": "2026-07-20",
        "results": {"strategy_a": {"as_of": "2026-07-20", "total": 1, "rows": cached_rows}},
        "updated_at": 1,
    }
    realtime = {"strategy_b": {"as_of": "2026-07-20", "total": 1, "rows": realtime_rows}}
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_args: cached)
    monkeypatch.setattr(screener_api, "_load_ext_value_maps", lambda *_args: {})
    monkeypatch.setattr(
        screener_api.pick_grades, "grade_cached_payload", lambda payload, engine, data_dir: payload,
    )
    engine = SimpleNamespace(latest_strategy_results=lambda: realtime)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path), get_instruments=MagicMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=repo, monitor_engine=engine, strategy_engine=None,
    )))
    master = MagicMock()
    master.get_instrument.side_effect = {
        "2330.TWSE": SimpleNamespace(name="台積電"),
        "2317.TWSE": SimpleNamespace(name="鴻海"),
    }.get

    with patch("app.taiwan.universe.get_security_master", return_value=master):
        payload = screener_api.get_cached(request, ext_columns=None)

    assert payload["results"]["strategy_a"]["rows"][0]["name"] == "台積電"
    assert payload["results"]["strategy_b"]["rows"][0]["name"] == "鴻海"
    assert "name" not in realtime_rows[0]
    assert "name" not in cached_rows[0]
