"""A strategy that fails in the cached run is reported with its reason instead of vanishing."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.api import screener as screener_api
from app.services import strategy_cache
from app.strategy.engine import StrategyDataContext, StrategyEngine, StrategyResult

pytestmark = pytest.mark.usefixtures("deny_network")

T = date(2026, 9, 30)
FAILURE = "unsupported matrix feature: turnover_rate"


def _strategy_code(strategy_id: str, body: str) -> str:
    return f'''import polars as pl
META = {{
    "id": "{strategy_id}",
    "name": "{strategy_id}",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {{"enabled": False}},
}}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    {body}
'''


def _engine(tmp_path, *, failing: tuple[str, ...]) -> StrategyEngine:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    for sid in ("good_pick", "broken_pick", "spare_pick"):
        body = f'raise ValueError("{FAILURE}")' if sid in failing else "return pl.lit(True)"
        (strategy_dir / f"{sid}.py").write_text(_strategy_code(sid, body), encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[strategy_dir])
    assert engine.load_errors() == []
    return engine


def _compute(tmp_path, monkeypatch, engine, ids=None):
    current = pl.DataFrame({"symbol": ["2330.TWSE"], "date": [T], "close": [1000.0]})
    monkeypatch.setattr(
        screener_api.ScreenerService,
        "build_strategy_context",
        lambda self, engine, as_of, ids, **kwargs: StrategyDataContext(
            asset_type="stock", timeframe="1d", as_of=as_of, current=current
        ),
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    as_of, results = screener_api.compute_and_cache_all(repo, engine, as_of=T, strategy_ids=ids)
    return data_dir, as_of, results


def _get_cached(data_dir, monkeypatch, monitor_results=None):
    monkeypatch.setattr(screener_api, "_attach_instrument_names", lambda _repo, payload: payload)
    monitor = SimpleNamespace(latest_strategy_results=lambda: monitor_results or {})
    state = SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=data_dir)),
        monitor_engine=monitor,
        strategy_engine=None,
    )
    return screener_api.get_cached(
        SimpleNamespace(app=SimpleNamespace(state=state)), ext_columns=None
    )


def test_a_failing_strategy_is_listed_with_its_reason_beside_the_others(tmp_path, monkeypatch):
    engine = _engine(tmp_path, failing=("broken_pick",))

    data_dir, _, results = _compute(tmp_path, monkeypatch, engine)

    assert set(results) == {"good_pick", "spare_pick"}
    cached = strategy_cache.read_cache(data_dir)
    assert set(cached["results"]) == {"good_pick", "spare_pick"}
    assert cached["failures"] == {"broken_pick": {"error": FAILURE}}
    payload = _get_cached(data_dir, monkeypatch)
    assert payload["failures"] == {"broken_pick": {"error": FAILURE}}
    assert "broken_pick" not in payload["results"]


def test_every_strategy_failing_still_records_why(tmp_path, monkeypatch):
    engine = _engine(tmp_path, failing=("good_pick", "broken_pick", "spare_pick"))

    data_dir, as_of, results = _compute(tmp_path, monkeypatch, engine)

    assert results == {}
    cached = strategy_cache.read_cache(data_dir)
    assert cached["as_of"] == str(as_of)
    assert cached["results"] == {}
    assert set(cached["failures"]) == {"good_pick", "broken_pick", "spare_pick"}


def test_realtime_results_hide_the_post_close_failure_of_the_same_strategy(tmp_path, monkeypatch):
    engine = _engine(tmp_path, failing=("broken_pick", "spare_pick"))
    data_dir, _, _ = _compute(tmp_path, monkeypatch, engine)
    realtime = {"broken_pick": {"as_of": str(T), "total": 1, "rows": [{"symbol": "2330.TWSE"}]}}

    payload = _get_cached(data_dir, monkeypatch, realtime)

    assert payload["failures"] == {"spare_pick": {"error": FAILURE}}
    assert set(payload["results"]) == {"good_pick", "broken_pick"}


_COMPOSITE_CODE = """META = {
    "id": "blend",
    "name": "blend",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"enabled": False},
    "params": [
        {"id": "merge_mode", "type": "select", "options": ["union", "intersect"], "default": "union"},
        {"id": "min_confirm", "type": "int", "default": 0},
    ],
    "children": [
        {"strategy_id": "good_pick", "weight": 1.0},
        {"strategy_id": "broken_pick", "weight": 1.0},
    ],
}
EXECUTION_BACKEND = "composite"
"""


def test_a_composite_whose_child_fails_is_listed_with_the_child_s_reason(tmp_path, monkeypatch):
    engine = _engine(tmp_path, failing=("broken_pick",))
    composite_dir = tmp_path / "composite"
    composite_dir.mkdir()
    (composite_dir / "blend.py").write_text(_COMPOSITE_CODE, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[tmp_path / "strategies", composite_dir])
    assert engine.load_errors() == []

    data_dir, _, results = _compute(tmp_path, monkeypatch, engine, ids=["good_pick", "blend"])

    assert set(results) == {"good_pick"}
    assert strategy_cache.read_cache(data_dir)["failures"] == {
        "blend": {"error": f"子策略「broken_pick」無法執行：{FAILURE}"}  # noqa: RUF001
    }


class _ReportingEngine:
    """Skips ``flaky`` in the batch run and reports why through the failures map."""

    def __init__(self):
        self.calls: list[tuple[list[str], bool]] = []

    def list_strategies(self):
        return [{"id": "steady"}, {"id": "flaky"}]

    def run_all(
        self,
        context,
        *,
        params_map=None,
        overrides_map=None,
        strategy_ids=None,
        skip_failed=False,
        failures=None,
    ):
        self.calls.append((list(strategy_ids), skip_failed))
        if failures is not None:
            failures["flaky"] = FAILURE
        return {
            sid: StrategyResult(as_of=context.as_of, strategy_id=sid, rows=[], total=0)
            for sid in strategy_ids
            if sid != "flaky"
        }


def test_a_skipped_strategy_is_reported_from_the_batch_run_without_running_again(
    tmp_path, monkeypatch
):
    engine = _ReportingEngine()

    data_dir, _, results = _compute(tmp_path, monkeypatch, engine)

    assert set(results) == {"steady"}
    assert engine.calls == [(["steady", "flaky"], True)]
    cached = strategy_cache.read_cache(data_dir)
    assert set(cached["results"]) == {"steady"}
    assert cached["failures"] == {"flaky": {"error": FAILURE}}


def _result(*symbols: str, as_of: str = "2026-07-20") -> dict:
    return {"total": len(symbols), "as_of": as_of, "rows": [{"symbol": s} for s in symbols]}


def test_same_day_success_clears_an_earlier_failure_and_a_failure_drops_an_earlier_result(tmp_path):
    strategy_cache.write_cache(
        tmp_path,
        "2026-07-20",
        {"strategy_a": _result("000001.SZ")},
        {"strategy_b": {"error": "boom"}},
    )
    strategy_cache.write_cache(
        tmp_path,
        "2026-07-20",
        {"strategy_b": _result("600000.SH")},
        {"strategy_a": {"error": "later boom"}},
    )

    cached = strategy_cache.read_cache(tmp_path)

    assert set(cached["results"]) == {"strategy_b"}
    assert cached["failures"] == {"strategy_a": {"error": "later boom"}}


def test_failures_reset_on_a_new_day_and_survive_writes_that_report_none(tmp_path):
    strategy_cache.write_cache(tmp_path, "2026-07-20", {}, {"strategy_a": {"error": "boom"}})
    strategy_cache.write_cache(tmp_path, "2026-07-20", {"strategy_b": _result("600000.SH")})
    assert strategy_cache.read_cache(tmp_path)["failures"] == {"strategy_a": {"error": "boom"}}

    strategy_cache.write_cache(
        tmp_path, "2026-07-21", {"strategy_b": _result("600000.SH", as_of="2026-07-21")}
    )
    assert strategy_cache.read_cache(tmp_path)["failures"] == {}


def test_a_cache_written_before_failures_existed_still_reads_and_merges(tmp_path, monkeypatch):
    legacy = {
        "as_of": "2026-07-20",
        "results": {"strategy_a": _result("000001.SZ")},
        "today_ever_matched": {"strategy_a": ["000001.SZ"]},
        "today_ever_rows": {"strategy_a": {"000001.SZ": {"symbol": "000001.SZ"}}},
        "updated_at": 1,
    }
    path = tmp_path / "user_data" / "strategy_cache.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(legacy), encoding="utf-8")

    payload = _get_cached(tmp_path, monkeypatch)
    assert payload.get("failures") is None
    assert set(payload["results"]) == {"strategy_a"}

    strategy_cache.write_cache(tmp_path, "2026-07-20", {}, {"strategy_b": {"error": "boom"}})
    cached = strategy_cache.read_cache(tmp_path)
    assert set(cached["results"]) == {"strategy_a"}
    assert cached["failures"] == {"strategy_b": {"error": "boom"}}
