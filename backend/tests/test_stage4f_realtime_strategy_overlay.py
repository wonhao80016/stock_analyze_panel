"""Intraday monitor results may only replace post-close strategy results from an earlier session."""
from __future__ import annotations

from types import SimpleNamespace

from app.api import screener as screener_api


def _request(tmp_path, monkeypatch, cached, realtime):
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_args: cached)
    monitor = SimpleNamespace(latest_strategy_results=lambda: realtime)
    state = SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)), monitor_engine=monitor,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _full_market(as_of: str) -> dict:
    rows = [{"symbol": f"{1000 + i}.TWSE"} for i in range(50)]
    return {"as_of": as_of, "total": len(rows), "rows": rows}


def _pool_subset(as_of: str) -> dict:
    return {"as_of": as_of, "total": 1, "rows": [{"symbol": "2330.TWSE"}]}


def test_post_close_cache_of_the_same_session_wins_over_the_closing_round(tmp_path, monkeypatch):
    cached = {"as_of": "2026-10-23", "results": {"s": _full_market("2026-10-23")}, "updated_at": 1}
    realtime = {"s": _pool_subset("2026-10-23")}

    payload = screener_api._cached_with_realtime(_request(tmp_path, monkeypatch, cached, realtime))

    assert payload["results"]["s"]["total"] == 50
    assert payload["updated_at"] == 1


def test_post_close_cache_wins_over_an_older_closing_round(tmp_path, monkeypatch):
    cached = {"as_of": "2026-10-23", "results": {"s": _full_market("2026-10-23")}, "updated_at": 1}
    realtime = {"s": _pool_subset("2026-10-22")}

    payload = screener_api._cached_with_realtime(_request(tmp_path, monkeypatch, cached, realtime))

    assert payload["results"]["s"]["total"] == 50


def test_intraday_round_replaces_the_previous_session_cache(tmp_path, monkeypatch):
    cached = {
        "as_of": "2026-10-22",
        "results": {"s": _full_market("2026-10-22"), "other": _full_market("2026-10-22")},
        "updated_at": 1,
    }
    realtime = {"s": _pool_subset("2026-10-23")}

    payload = screener_api._cached_with_realtime(_request(tmp_path, monkeypatch, cached, realtime))

    assert payload["results"]["s"] == realtime["s"]
    assert payload["results"]["other"]["total"] == 50
    assert payload["updated_at"] > 1


def test_realtime_fills_a_strategy_missing_from_the_post_close_cache(tmp_path, monkeypatch):
    cached = {
        "as_of": "2026-10-23",
        "results": {},
        "failures": {"s": {"error": "boom"}, "t": {"error": "boom"}},
        "updated_at": 1,
    }
    realtime = {"s": _pool_subset("2026-10-23")}

    payload = screener_api._cached_with_realtime(_request(tmp_path, monkeypatch, cached, realtime))

    assert payload["results"]["s"] == realtime["s"]
    assert payload["failures"] == {"t": {"error": "boom"}}
