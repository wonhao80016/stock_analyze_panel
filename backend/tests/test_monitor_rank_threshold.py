"""Strategy monitor rules filter on the day's strategy rank; the legacy relative score range still applies."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.monitor_rules import router
from app.strategy import monitor_rules
from app.strategy.engine import StrategyEngine, StrategyResult
from app.strategy.monitor import MonitorRuleEngine

DAY = date(2026, 7, 24)
MARKET = ["1101.TWSE", "2317.TWSE", "2330.TWSE", "2454.TWSE"]


def _rule(**overrides) -> dict:
    return monitor_rules.normalize({
        "id": "rank_rule",
        "name": "排名監控",
        "type": "strategy",
        "asset_type": "stock",
        "scope": "all",
        "strategy_id": "demo",
        "notify_events": ["buy_signal", "sell_signal", "pool_entry", "pool_exit"],
        "cooldown_seconds": 0,
        **overrides,
    })


def _ranked(scores: dict[str, float | None]) -> list[dict]:
    """Rows in the order the strategy engine ranks them for the screener page."""
    frame = pl.DataFrame(
        {"symbol": list(scores), "score": list(scores.values())},
        schema={"symbol": pl.Utf8, "score": pl.Float64},
    )
    return StrategyEngine._sort_ranked(frame, "score", True).to_dicts()


def _result(rows: list[dict], buys: tuple[str, ...] = (), sells: tuple[str, ...] = ()) -> StrategyResult:
    return StrategyResult(
        as_of=DAY,
        strategy_id="demo",
        rows=rows,
        total=len(rows),
        scores={row["symbol"]: row.get("score") for row in rows},
        entry_signal_hits=[{"symbol": s, "signals": ["signal_buy"]} for s in buys],
        exit_signal_hits=[{"symbol": s, "signals": ["signal_sell"]} for s in sells],
    )


class _Strategies:
    def __init__(self, results: list[StrategyResult]):
        self.results = list(results)
        self.run_symbols: list[list[str]] = []

    def get(self, strategy_id: str):
        return SimpleNamespace(
            meta={"id": strategy_id, "name": "示例策略"},
            execution_backend="polars_expr",
            filter_history_fn=None,
        )

    def run(self, strategy_id: str, context, **kwargs):
        self.run_symbols.append(context.current["symbol"].to_list())
        return self.results.pop(0)


def _quotes() -> pl.DataFrame:
    return pl.DataFrame({"symbol": MARKET, "close": [10.0] * len(MARKET)})


def _events(rule: dict, *results: StrategyResult) -> tuple[set[tuple[str, str]], _Strategies]:
    """Events of the last round; the first result only sets the pool baseline."""
    strategies = _Strategies([_result([]), *results])
    engine = MonitorRuleEngine()
    engine.set_strategy_engine(strategies)
    engine.set_rules([rule])
    events: list[dict] = []
    with patch("app.strategy.monitor.time.time", side_effect=range(100, 100 + len(results) + 1)):
        assert engine.evaluate(_quotes()) == []
        for _ in results:
            events = engine.evaluate(_quotes())
    return {(event["type"], event["symbol"]) for event in events}, strategies


def test_top_n_ranks_qualify_and_the_next_rank_does_not():
    rows = _ranked({"2330.TWSE": 100.0, "2317.TWSE": 60.0, "1101.TWSE": 0.0})

    events, _ = _events(
        _rule(max_rank=2),
        _result(rows, buys=("2330.TWSE", "2317.TWSE", "1101.TWSE"), sells=("1101.TWSE",)),
    )

    assert events == {
        ("pool_entry", "2330.TWSE"),
        ("pool_entry", "2317.TWSE"),
        ("buy_signal", "2330.TWSE"),
        ("buy_signal", "2317.TWSE"),
        ("sell_signal", "1101.TWSE"),
    }


def test_tied_scores_take_the_engine_symbol_order_and_missing_scores_never_qualify():
    rows = _ranked({"2330.TWSE": 80.0, "2317.TWSE": 80.0, "1101.TWSE": None})
    assert [row["symbol"] for row in rows] == ["2317.TWSE", "2330.TWSE", "1101.TWSE"]

    first, _ = _events(_rule(max_rank=1), _result(rows))
    all_ranks, _ = _events(_rule(max_rank=3), _result(rows))

    assert first == {("pool_entry", "2317.TWSE")}
    assert all_ranks == {("pool_entry", "2317.TWSE"), ("pool_entry", "2330.TWSE")}


def test_single_candidate_is_rank_one_whatever_its_relative_score():
    crowded = _ranked({"2330.TWSE": 100.0, "2317.TWSE": 50.0, "1101.TWSE": 0.0})
    alone = _ranked({"2454.TWSE": 50.0})

    by_rank, _ = _events(_rule(max_rank=1), _result(crowded), _result(alone))
    by_legacy_score, _ = _events(_rule(score_min=60), _result(alone))

    assert by_rank == {("pool_exit", "2330.TWSE"), ("pool_entry", "2454.TWSE")}
    assert by_legacy_score == set()


def test_unscored_strategy_rows_never_rank():
    rows = [{"symbol": symbol, "close": 10.0} for symbol in ("2330.TWSE", "2317.TWSE")]

    events, _ = _events(_rule(max_rank=5), _result(rows, buys=("2330.TWSE",)))

    assert events == set()


class _CountingStrategies(_Strategies):
    def __init__(self, rows: list[dict]):
        super().__init__([])
        self.rows = rows
        self.runs: list[tuple[str, int]] = []

    def run(self, strategy_id: str, context, **kwargs):
        self.runs.append((strategy_id, context.current.height))
        return _result(self.rows)


def test_whole_market_rules_of_one_strategy_share_one_run_per_round():
    strategies = _CountingStrategies(_ranked({"2330.TWSE": 100.0, "2317.TWSE": 50.0, "1101.TWSE": 0.0}))
    engine = MonitorRuleEngine()
    engine.set_strategy_engine(strategies)
    engine.set_rules([
        _rule(id="whole_market"),
        _rule(id="rank_one", max_rank=1, scope="symbols", symbols=["2330.TWSE"]),
        _rule(id="rank_three", max_rank=3, scope="symbols", symbols=["1101.TWSE"]),
        _rule(id="scoped", scope="symbols", symbols=["2317.TWSE"]),
        _rule(id="other_strategy", strategy_id="other"),
    ])

    engine.evaluate(_quotes())
    engine.evaluate(_quotes())

    per_round = [("demo", len(MARKET)), ("demo", 1), ("other", len(MARKET))]
    assert strategies.runs == per_round * 2
    assert engine._round_strategy_runs is None


def test_scoped_rule_ranks_the_whole_market_and_alerts_only_its_scope():
    rows = _ranked({"2330.TWSE": 100.0, "2317.TWSE": 50.0, "1101.TWSE": 0.0})

    def scoped(symbol: str) -> tuple[set[tuple[str, str]], _Strategies]:
        return _events(
            _rule(max_rank=2, scope="symbols", symbols=[symbol]),
            _result(rows, sells=("2330.TWSE", "2317.TWSE", "1101.TWSE")),
        )

    inside, strategies = scoped("2317.TWSE")
    outside, _ = scoped("1101.TWSE")

    assert inside == {("pool_entry", "2317.TWSE"), ("sell_signal", "2317.TWSE")}
    assert outside == {("sell_signal", "1101.TWSE")}
    assert all(symbols == MARKET for symbols in strategies.run_symbols)


def test_legacy_score_range_still_filters_and_loads_without_a_rank(tmp_path):
    monitor_rules.save_one(tmp_path, {
        "id": "legacy", "name": "舊規則", "type": "strategy", "scope": "all",
        "strategy_id": "demo", "notify_events": ["pool_entry"], "score_min": 40, "score_max": 90,
    })
    legacy = monitor_rules.load_one(tmp_path, "legacy")
    assert legacy is not None
    assert (legacy["score_min"], legacy["score_max"], legacy["max_rank"]) == (40, 90, None)
    monitor_rules.validate(legacy)

    rows = _ranked({"2330.TWSE": 100.0, "2317.TWSE": 50.0, "1101.TWSE": 0.0})
    events, strategies = _events({**legacy, "scope": "symbols", "symbols": MARKET[:3]}, _result(rows))

    assert events == {("pool_entry", "2317.TWSE")}
    assert all(symbols == MARKET[:3] for symbols in strategies.run_symbols)
    with pytest.raises(ValueError, match="排名門檻不能與舊的評分範圍同時使用"):
        monitor_rules.validate({**legacy, "max_rank": 5})


def test_rank_edit_resets_the_rule_state():
    signature = MonitorRuleEngine._rule_state_signature

    assert signature(_rule(max_rank=5)) != signature(_rule(max_rank=10))
    assert "max_rank" not in monitor_rules.normalize({**_rule(max_rank=5), "type": "price"})


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "3"])
def test_validate_rejects_rank_that_is_not_a_positive_integer(value):
    with pytest.raises(ValueError, match="排名門檻必須是大於等於 1 的整數"):
        monitor_rules.validate(_rule(max_rank=value))


class _FakeRepo:
    def __init__(self, data_dir):
        self.store = SimpleNamespace(data_dir=data_dir)

    def resolve_asset_type(self, symbol):
        return "stock"


class _StrategyCatalog:
    def get(self, strategy_id: str):
        return SimpleNamespace(meta={"id": strategy_id})

    def validate_context(self, strategy, context) -> None:
        return None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.watchlist.list_groups", lambda: [])
    app = FastAPI()
    app.include_router(router)
    app.state.repo = _FakeRepo(tmp_path)
    app.state.strategy_engine = _StrategyCatalog()
    return TestClient(app), tmp_path


def _payload(**overrides) -> dict:
    return {
        "id": "rank_rule", "name": "排名監控", "type": "strategy", "scope": "all",
        "strategy_id": "demo", "notify_events": ["pool_entry"], **overrides,
    }


def test_api_saves_rank_and_defaults_it_to_none(client):
    http, data_dir = client

    assert http.post("/api/monitor-rules", json=_payload()).status_code == 200
    assert monitor_rules.load_one(data_dir, "rank_rule")["max_rank"] is None

    response = http.post("/api/monitor-rules", json=_payload(max_rank=20))
    assert response.status_code == 200
    assert response.json()["rule"]["max_rank"] == 20
    assert monitor_rules.load_one(data_dir, "rank_rule")["max_rank"] == 20


@pytest.mark.parametrize("value", [0, -3, 2.5, "5", True])
def test_api_rejects_rank_that_is_not_a_positive_integer(client, value):
    http, data_dir = client

    assert http.post("/api/monitor-rules", json=_payload(max_rank=value)).status_code == 422
    assert monitor_rules.load_one(data_dir, "rank_rule") is None


def test_api_keeps_or_removes_a_legacy_score_range_but_never_sets_one(client):
    http, data_dir = client

    rejected = http.post("/api/monitor-rules", json=_payload(score_min=70))
    assert rejected.status_code == 400
    assert "排名前 N 名" in rejected.json()["detail"]

    monitor_rules.save_one(data_dir, monitor_rules.normalize(_payload(score_min=70.0)))
    assert http.post("/api/monitor-rules", json=_payload(score_min=70.0, name="改名")).status_code == 200
    assert http.post("/api/monitor-rules", json=_payload(score_min=80.0)).status_code == 400
    assert http.post("/api/monitor-rules", json=_payload(score_min=70.0, max_rank=5)).status_code == 400

    removed = http.post("/api/monitor-rules", json=_payload(max_rank=5))
    assert removed.status_code == 200
    stored = monitor_rules.load_one(data_dir, "rank_rule")
    assert (stored["score_min"], stored["max_rank"]) == (None, 5)
