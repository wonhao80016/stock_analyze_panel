from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from app.api import screener as screener_api


class _MonitorEngine:
    def __init__(self, results=None):
        self.results = results or {}

    def latest_strategy_results(self):
        return self.results


def _request(tmp_path, monitor_results=None):
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    state = SimpleNamespace(repo=repo, monitor_engine=_MonitorEngine(monitor_results))
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_cached_summary_omits_rows_and_counts_realtime_expirations(monkeypatch, tmp_path):
    # No post-close result for strategy_a: one of the same session would take precedence over realtime.
    cached = {
        "as_of": "2026-07-20",
        "results": {},
        "today_ever_rows": {
            "strategy_a": {
                "000001.SZ": {"symbol": "000001.SZ"},
                "600000.SH": {"symbol": "600000.SH"},
            },
        },
        "updated_at": 1,
    }
    realtime = {
        "strategy_a": {
            "as_of": "2026-07-20",
            "total": 2,
            "rows": [{"symbol": "000002.SZ"}, {"symbol": "300001.SZ"}],
        },
    }
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_args: cached)

    payload = screener_api.get_cached_summary(_request(tmp_path, realtime))

    assert payload["results"] == {"strategy_a": {"total": 2, "as_of": "2026-07-20"}}
    assert payload["today_ever_counts"] == {"strategy_a": 4}
    assert "rows" not in payload["results"]["strategy_a"]


def test_cached_result_returns_only_requested_rows_with_ext_and_strategy_membership(monkeypatch, tmp_path):
    cached = {
        "as_of": "2026-07-20",
        "results": {
            "strategy_a": {
                "as_of": "2026-07-20",
                "total": 1,
                "rows": [{"symbol": "000001.SZ"}],
            },
            "strategy_b": {
                "as_of": "2026-07-20",
                "total": 2,
                "rows": [{"symbol": "000001.SZ"}, {"symbol": "600000.SH"}],
            },
        },
        "today_ever_rows": {
            "strategy_a": {
                "000001.SZ": {"symbol": "000001.SZ"},
                "000002.SZ": {"symbol": "000002.SZ"},
            },
        },
        "updated_at": 1,
    }
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_args: cached)
    monkeypatch.setattr(
        screener_api,
        "_load_ext_value_maps",
        lambda *_args: {"concept.concept": {"000001.SZ": "银行", "000002.SZ": "科技"}},
    )

    payload = screener_api.get_cached_result(
        "strategy_a",
        _request(tmp_path),
        ext_columns="concept.concept",
    )

    assert payload["result"]["strategy"] == "strategy_a"
    assert payload["result"]["rows"] == [{"symbol": "000001.SZ", "concept.concept": "银行"}]
    assert payload["today_ever_rows"]["000002.SZ"]["concept.concept"] == "科技"
    assert payload["strategy_ids_by_symbol"] == {"000001.SZ": ["strategy_a", "strategy_b"]}


def _bars(data_dir, last_close: float, count: int = 25):
    import polars as pl

    from app.taiwan.benchmarks import TAIEX
    from app.taiwan.daily_store import TaiwanDailyStore

    days: list[date] = []
    d = date(2026, 9, 30)
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.sort()
    for day in days:
        part = data_dir / "taiwan" / "daily" / f"date={day.isoformat()}"
        part.mkdir(parents=True)
        (part / "part.parquet").touch()
    closes = [100.0] * (count - 1) + [last_close]
    frame = pl.DataFrame(
        {
            "symbol": [TAIEX.symbol] * count, "date": days, "open": closes, "high": closes,
            "low": closes, "close": closes, "volume": [None] * count, "amount": [None] * count,
            "quote_ts": [None] * count,
        },
        schema_overrides={"volume": pl.Float64, "amount": pl.Float64, "quote_ts": pl.Float64},
    )
    TaiwanDailyStore(data_dir / "taiwan" / "index_daily").write_batch(frame)


def _picks(n: int, as_of: str = "2026-09-30") -> dict:
    rows = [{"symbol": f"{1000 + i}.TWSE", "score": float(100 - i)} for i in range(n)]
    return {"as_of": as_of, "total": n, "rows": rows}


def _cached_request(tmp_path, monkeypatch, cached, monitor_results=None):
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_args: cached)
    monkeypatch.setattr(screener_api, "_attach_instrument_names", lambda _repo, payload: payload)
    request = _request(tmp_path, monitor_results)
    request.app.state.strategy_engine = None
    return request


def _get_cached(tmp_path, monkeypatch, cached, monitor_results=None):
    request = _cached_request(tmp_path, monkeypatch, cached, monitor_results)
    return screener_api.get_cached(request, ext_columns=None)


def _grades(result: dict) -> list[tuple]:
    return [(r["grade"], r["grade_reason"]) for r in result["rows"]]


def test_cached_rows_are_graded_and_downgraded_below_the_market_average(monkeypatch, tmp_path):
    _bars(tmp_path, last_close=50.0)
    cached = {"as_of": "2026-09-30", "results": {"s": _picks(6)}, "updated_at": 1}

    payload = _get_cached(tmp_path, monkeypatch, cached)

    assert payload["market_state"]["as_of"] == payload["as_of"] == "2026-09-30"
    assert payload["market_state"]["below_ma"] is True
    assert payload["grading"] == {"top_n": 5, "market_filter": "applied"}
    assert _grades(payload["results"]["s"]) == (
        [("watch", "market_below_ma20")] * 5 + [("watch", "outside_top_n")]
    )


def test_cached_rows_stay_buy_when_the_market_is_above_its_average(monkeypatch, tmp_path):
    _bars(tmp_path, last_close=150.0)
    cached = {"as_of": "2026-09-30", "results": {"s": _picks(2)}, "updated_at": 1}

    payload = _get_cached(tmp_path, monkeypatch, cached)

    assert payload["grading"]["market_filter"] == "passed"
    assert _grades(payload["results"]["s"]) == [("buy", None)] * 2


def test_cached_without_index_data_is_unavailable_and_not_downgraded(monkeypatch, tmp_path):
    cached = {"as_of": "2026-09-30", "results": {"s": _picks(2)}, "updated_at": 1}

    payload = _get_cached(tmp_path, monkeypatch, cached)

    assert payload["market_state"]["status"] == "unavailable"
    assert payload["grading"]["market_filter"] == "unavailable"
    assert _grades(payload["results"]["s"]) == [("buy", None)] * 2
    assert not (tmp_path / "taiwan").exists()


def test_cached_still_answers_when_the_market_state_raises(monkeypatch, tmp_path):
    from app.services import pick_grades

    def fail(*_args):
        raise RuntimeError("parquet unreadable")

    monkeypatch.setattr(pick_grades, "compute_market_state", fail)
    cached = {"as_of": "2026-09-30", "results": {"s": _picks(1)}, "updated_at": 1}

    payload = _get_cached(tmp_path, monkeypatch, cached)

    assert payload["market_state"] is None
    assert payload["grading"]["market_filter"] == "unavailable"
    assert _grades(payload["results"]["s"]) == [("buy", None)]


def test_empty_cache_still_reports_grading(monkeypatch, tmp_path):
    payload = _get_cached(tmp_path, monkeypatch, None)

    assert payload == {
        "as_of": None,
        "results": {},
        "updated_at": None,
        "market_state": None,
        "grading": {"top_n": 5, "market_filter": "unavailable"},
    }


def test_realtime_results_are_graded_against_the_last_close_without_touching_monitor_rows(
    monkeypatch, tmp_path
):
    _bars(tmp_path, last_close=50.0)
    cached = {"as_of": "2026-09-30", "results": {"s": _picks(2)}, "updated_at": 1}
    realtime = {"s": _picks(3, as_of="2026-10-01")}
    monitor_rows = realtime["s"]["rows"]

    payload = _get_cached(tmp_path, monkeypatch, cached, realtime)

    assert payload["results"]["s"]["as_of"] == "2026-10-01"
    assert payload["market_state"]["as_of"] == "2026-09-30"
    assert _grades(payload["results"]["s"]) == [("watch", "market_below_ma20")] * 3
    assert all("grade" not in row for row in monitor_rows)


def test_realtime_results_without_a_cache_are_graded_by_rank_only(monkeypatch, tmp_path):
    realtime = {"s": _picks(6, as_of="2026-10-01")}

    payload = _get_cached(tmp_path, monkeypatch, None, realtime)

    assert payload["market_state"] is None
    assert payload["grading"]["market_filter"] == "unavailable"
    assert _grades(payload["results"]["s"]) == [("buy", None)] * 5 + [("watch", "outside_top_n")]


def test_cached_endpoint_answers_200_for_strategies_the_engine_cannot_load(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class _Engine:
        def has(self, sid):
            return sid == "broken"

        def get(self, sid):
            raise ValueError(f"unknown strategy: {sid}")

    cached = {
        "as_of": "2026-09-30",
        "results": {
            "broken": {"total": 1, "rows": [{"symbol": "2330.TWSE", "score": None}]},
            "gone": {"total": 1, "rows": [{"symbol": "2317.TWSE"}]},
        },
        "updated_at": 1,
    }
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_args: cached)
    monkeypatch.setattr(screener_api, "_attach_instrument_names", lambda _repo, payload: payload)
    app = FastAPI()
    app.include_router(screener_api.router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.state.monitor_engine = None
    app.state.strategy_engine = _Engine()

    response = TestClient(app).get("/api/screener/cached")

    assert response.status_code == 200
    body = response.json()
    assert body["results"]["broken"]["rows"][0]["grade"] == "buy"
    assert body["results"]["gone"]["rows"][0]["grade"] == "buy"
